# Origin: LLM-Trading-Arena@7563724 apps/arena/main.py (adapted) - stop event, shutdown deadline,
# hard exit when a worker does not end
"""The service loop (`trading-bot run`, Spec 008, ADR-0017).

One process, one writer: the WriterLock is held for the whole run. Every minute a paper round
runs in a worker thread, so the main thread stays free to ping the systemd watchdog, to notice a
stop signal and to enforce the stall limit and the stop deadline.
"""

import logging
import os
import signal
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol

from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.due import iso
from llm_trading_bot.llm.openai_client import SignalModel
from llm_trading_bot.market.snapshot import MarketClient
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import SchemaError, ensure_portfolios, open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.lock import WriterLock, WriterLockBusy
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.logs import redact
from llm_trading_bot.service.notify import Notifier
from llm_trading_bot.service.schedule import OFFSET, PERIOD, next_slot, skipped
from llm_trading_bot.service.status import ServiceStatus, status_path, write_status
from llm_trading_bot.web.credentials import load_env_file

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_LOCKED = 3
EXIT_STALLED = 4

STALL_SECONDS = 300
"""A round that runs longer is hanging: exit, systemd restarts the service. Above the worst case
of a GPT request with retries (~3 min), so a slow answer is not mistaken for a hang."""
STOP_SECONDS = 75
"""After a stop signal the running round may finish for this long; below TimeoutStopSec=90."""
PING_SECONDS = 20
"""Watchdog interval; a third of WatchdogSec=60."""
SLICE_SECONDS = 1.0
"""How often the main thread looks at the stop flag and the clocks while it waits."""


class Timer(Protocol):
    def now(self) -> float:
        """Wall clock (epoch seconds): slots and timestamps."""

    def monotonic(self) -> float:
        """Durations, stall limit and stop deadline."""

    def wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        """Wait up to ``seconds`` for a stop request; True if one came."""

    def wait_done(self, done: threading.Event, seconds: float) -> bool:
        """Wait up to ``seconds`` for the round to finish; True if it did."""


class RealTimer:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        return stop.wait(seconds)

    def wait_done(self, done: threading.Event, seconds: float) -> bool:
        return done.wait(seconds)


def _hard_exit(code: int) -> NoReturn:
    # The worker thread may be inside a request or a transaction: SQLite rolls an unfinished
    # transaction back on the next open, and an unsettled GPT reservation keeps blocking its
    # budget (ADR-0017). The OS frees the WriterLock with the process.
    logging.shutdown()
    os._exit(code)


def install_stop_signals(stop: threading.Event) -> None:
    """SIGTERM (systemd stop) and SIGINT (Ctrl+C) only set the flag; the loop does the rest.

    Python runs signal handlers in the main thread only, which is why rounds run in a worker.
    """

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


Cycle = Callable[..., CycleReport]


def load_settings(env: Mapping[str, str], secrets: Path) -> tuple[BotConfig, Costs]:
    """Environment and ``secrets.env`` as `paper-cycle` reads them: the environment wins."""
    merged = {**load_env_file(secrets), **env}
    return load_config(merged), Costs.from_env(merged)


@dataclass
class _Round:
    done: threading.Event
    report: CycleReport | None = None
    error: BaseException | None = None


class Service:
    def __init__(
        self,
        db: Path,
        client: MarketClient,
        *,
        env: Mapping[str, str],
        secrets: Path,
        stop: threading.Event,
        log: logging.Logger,
        notifier: Notifier,
        timer: Timer | None = None,
        model_factory: Callable[[str], SignalModel],
        cycle: Cycle = paper_cycle,
        hard_exit: Callable[[int], NoReturn] = _hard_exit,
    ) -> None:
        self.db, self.client, self.env, self.secrets = db, client, env, secrets
        self.stop, self.log, self.notifier = stop, log, notifier
        self.timer = timer or RealTimer()
        self.model_factory, self.cycle, self.hard_exit = model_factory, cycle, hard_exit
        self.status_file = status_path(db)
        self.status: ServiceStatus | None = None
        self.con: sqlite3.Connection | None = None
        self.config: BotConfig | None = None
        self.costs = Costs()
        self.model: SignalModel | None = None
        self.key: str | None = None
        self.warning = ""
        self.stopping = False

    # ------------------------------------------------------------------ start and end

    def run(self) -> int:
        try:
            self.config, self.costs = load_settings(self.env, self.secrets)
        except (ValueError, OSError) as exc:
            self.log.error("configuration error: %s", exc)
            return EXIT_INVALID
        self._use_key(self.config)
        try:
            with WriterLock(self.db):
                return self._run_locked()
        except WriterLockBusy as exc:
            self.log.error("refused: %s", exc)
            return EXIT_LOCKED

    def _run_locked(self) -> int:
        assert self.config is not None
        now = self.timer.now()
        self.status = ServiceStatus("starting", os.getpid(), iso(now), iso(now), PERIOD, OFFSET)
        try:
            # The round runs in a worker thread; only one thread uses the connection at a time.
            self.con = open_db(
                self.db, agents=(*self.config.agents, BUYHOLD), check_same_thread=False
            )
        except (SchemaError, sqlite3.DatabaseError) as exc:
            # Never repair or migrate: the history in that file must stay as it is (ADR-0012).
            self.log.error("database refused: %s: %s", type(exc).__name__, exc)
            self._update(state="stopped", last_error=f"{type(exc).__name__}: {exc}")
            return EXIT_INVALID
        try:
            return self._loop()
        finally:
            self.con.close()

    def _loop(self) -> int:
        assert self.config is not None
        self.log.info(
            "service started: db %s, depots %s, a round every %d s at :%02d",
            self.db.name,
            ", ".join(self.config.agents),
            PERIOD,
            OFFSET,
        )
        self._update(state="running")
        self.notifier.ready(f"running, depots {', '.join(self.config.agents)}")
        last_slot: float | None = None
        while not self.stop.is_set():
            slot = next_slot(self.timer.now(), after=last_slot)
            if last_slot is not None and (missed := skipped(last_slot, slot)):
                self.log.warning("round overran: %d slot(s) skipped, no catch-up", missed)
            self._update(next_round_at=iso(slot))
            if self._wait_until(slot):
                break
            last_slot = slot
            self._round()
        self._begin_stop()
        self._update(state="stopped", next_round_at=None)
        self.log.info("service stopped")
        return EXIT_OK

    def _begin_stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.log.info("stop requested")
        self.notifier.stopping()
        self._update(state="stopping")

    # ------------------------------------------------------------------ waiting

    def _wait_until(self, slot: float) -> bool:
        """Wait for the slot; True if a stop came first. Pings the watchdog meanwhile."""
        last_ping = self.timer.monotonic()
        while (remaining := slot - self.timer.now()) > 0:
            if self.timer.wait_stop(self.stop, min(remaining, SLICE_SECONDS)):
                return True
            if self.timer.monotonic() - last_ping >= PING_SECONDS:
                self.notifier.watchdog()
                last_ping = self.timer.monotonic()
        self.notifier.watchdog()
        return self.stop.is_set()

    # ------------------------------------------------------------------ one round

    def _round(self) -> None:
        started_at, started = self.timer.now(), self.timer.monotonic()
        job = _Round(threading.Event())
        worker = threading.Thread(target=self._work, args=(job,), name="paper-round", daemon=True)
        worker.start()
        last_ping, stop_seen = started, None
        while not self.timer.wait_done(job.done, SLICE_SECONDS):
            now = self.timer.monotonic()
            if now - last_ping >= PING_SECONDS:
                self.notifier.watchdog()
                last_ping = now
            if self.stop.is_set():
                if stop_seen is None:
                    stop_seen = now
                    self._begin_stop()
                if now - stop_seen >= STOP_SECONDS:
                    self.log.error(
                        "round still running %d s after the stop request: exiting hard",
                        STOP_SECONDS,
                    )
                    self._update(state="stopped", last_error="hard stop during a round")
                    self.hard_exit(EXIT_STALLED)
            if now - started >= STALL_SECONDS:
                self.log.error(
                    "round hangs for %d s: exiting so that systemd restarts the service",
                    STALL_SECONDS,
                )
                self._update(state="stalled", last_error=f"round hangs > {STALL_SECONDS} s")
                self.notifier.status("stalled")
                self.hard_exit(EXIT_STALLED)
        self.notifier.watchdog()
        self._finish(job, started_at, self.timer.monotonic() - started)

    def _work(self, job: _Round) -> None:
        """Runs in the worker thread: settings, then one paper cycle."""
        try:
            self._reload()
            assert self.con is not None and self.config is not None
            job.report = self.cycle(
                self.con,
                self.client,
                self.config,
                costs=self.costs,
                model=self.model,
                clock=self.timer.now,
                stop_requested=self.stop.is_set,
            )
        except BaseException as exc:  # reported by the main thread; never kills the service
            job.error = exc
        finally:
            job.done.set()

    def _reload(self) -> None:
        """Settings saved in the web UI apply from the next round on (Spec 008 AK3)."""
        assert self.con is not None and self.config is not None
        try:
            config, costs = load_settings(self.env, self.secrets)
        except (ValueError, OSError) as exc:
            message = redact(f"{exc}")
            if message != self.warning:
                self.log.warning("configuration invalid, keeping the last valid one: %s", message)
            self.warning = message
            return
        if self.warning:
            self.log.info("configuration valid again")
            self.warning = ""
        if config.agents != self.config.agents:
            new = ensure_portfolios(self.con, (*config.agents, BUYHOLD))
            self.log.info(
                "depots now %s%s",
                ", ".join(config.agents),
                f" (new with start cash: {', '.join(new)})" if new else "",
            )
        if self._use_key(config):
            self.log.info("new OpenAI key: new client")
        self.config, self.costs = config, costs

    def _use_key(self, config: BotConfig) -> bool:
        """A client per key; True if the key changed while the service was running."""
        key = config.llm.api_key if config.llm else None
        if key == self.key:
            return False
        changed = self.key is not None and key is not None
        self.key = key
        self.model = self.model_factory(key) if key else None
        return changed

    def _finish(self, job: _Round, started_at: float, seconds: float) -> None:
        assert self.status is not None
        if job.error is not None or job.report is None:
            exc = job.error or RuntimeError("round returned nothing")
            result, error = "error", f"{type(exc).__name__}: {exc}"
            self.log.error("round failed after %.1f s: %s", seconds, error, exc_info=exc)
        else:
            report = job.report
            result = "ok" if report.complete else "incomplete" if report.equity else "no_quotes"
            error = _first_problem(report)
            self._log_round(report, result, seconds)
        failures = 0 if result == "ok" else self.status.failures + 1
        state = "stopping" if self.stopping else "running" if result == "ok" else "degraded"
        self._update(
            state=state,
            last_round_at=iso(started_at),
            last_round_seconds=round(seconds, 2),
            last_result=result,
            last_error=error,
            failures=failures,
            config_warning=self.warning,
        )
        self.notifier.status(
            f"{state}, last round {result} at {iso(started_at)[11:19]} UTC"
            + (f", {failures} failing in a row" if failures else "")
        )

    def _log_round(self, report: CycleReport, result: str, seconds: float) -> None:
        for m in report.markets:
            if m.new and m.traded:
                self.log.info(
                    "trade %s %s %s target %.2f - %s",
                    m.agent,
                    m.symbol.market,
                    m.action,
                    m.target,
                    m.reason,
                )
            elif m.error:
                self.log.warning("%s %s: no decision - %s", m.agent, m.symbol.market, m.error)
        if report.buyhold_bought:
            # The benchmark buys once per market; its trades are not part of report.markets.
            self.log.info(
                "trade %s bought %d market(s) (benchmark)", BUYHOLD, report.buyhold_bought
            )
        if report.quotes_error:
            self.log.warning("quotes: %s", report.quotes_error)
        llm = ", ".join(
            f"{o.agent} {o.status}"
            + (f" {o.cost_eur:.4f} EUR" if o.cost_eur is not None else "")
            + (f" ({o.detail})" if o.status == "ERROR" and o.detail else "")
            for o in report.llm
        )
        trades = sum(1 for m in report.markets if m.new and m.traded)
        equity = ", ".join(f"{e.agent} {e.value:.2f}" for e in report.equity)
        self.log.log(
            logging.INFO if result == "ok" else logging.WARNING,
            "round %s in %.1f s | gpt %s | trades %d | equity %s",
            result,
            seconds,
            llm or "-",
            trades,
            equity or "-",
        )

    def _update(self, **changes: object) -> None:
        assert self.status is not None
        self.status = self.status.but(updated_at=iso(self.timer.now()), **changes)
        try:
            write_status(self.status_file, self.status)
        except OSError as exc:
            # The status file is only a display; the round itself must go on.
            self.log.warning("status file not written: %s: %s", type(exc).__name__, exc)


def _first_problem(report: CycleReport) -> str:
    if report.quotes_error:
        return f"quotes: {report.quotes_error}"
    for m in report.markets:
        if m.error:
            return f"{m.agent} {m.symbol.market}: {m.error}"
    for o in report.llm:
        if o.status == "ERROR":
            return f"{o.agent}: {o.detail or 'ERROR'}"
    return ""
