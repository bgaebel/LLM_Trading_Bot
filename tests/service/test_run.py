"""Spec 008 AK1, AK2, AK4: the service loop - timing, one writer, failing and hanging rounds."""

import hashlib
import io
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from fakes import ScenarioClient
from llm_fakes import KEY
from llm_trading_bot.cli import EXIT_LOCKED as CLI_LOCKED
from llm_trading_bot.cli import run_paper_cycle
from llm_trading_bot.llm.due import iso
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.run import (
    EXIT_INVALID,
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_STALLED,
    STALL_SECONDS,
)
from llm_trading_bot.service.schedule import OFFSET, PERIOD
from llm_trading_bot.service.status import read_status, status_path
from llm_trading_bot.web.readonly import reading
from service_fakes import MINUTE, FakeTimer, HardExit, MovingClient, harness


def test_rounds_start_five_seconds_after_each_minute(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=3)
    assert h.service.run() == EXIT_OK
    assert h.rounds.started == [MINUTE + 65, MINUTE + 125, MINUTE + 185]
    with sqlite3.connect(h.db) as con:
        # One equity point per depot (trend, buyhold) and round.
        assert con.execute("SELECT COUNT(*) FROM equity").fetchone()[0] == 6
    con.close()


def test_an_overrun_skips_slots_and_says_so(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=2)
    h.rounds.duration = 130  # the first round ends after two more slots have passed
    assert h.service.run() == EXIT_OK
    assert h.rounds.started == [MINUTE + 65, MINUTE + 245]
    assert "round overran: 2 slot(s) skipped, no catch-up" in h.log()


class JumpingClock(FakeTimer):
    """After round 1 the wall clock jumps by ``jump`` seconds (an NTP step) at the first wait."""

    def __init__(self, start: float, rounds: list[float], jump: float) -> None:
        super().__init__(start)
        self.rounds, self.jump, self.jumped = rounds, jump, False
        self.mono_at: list[float] = []

    def wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        if len(self.rounds) == 1 and not self.jumped:
            self.jumped = True
            with self.lock:
                self.wall += self.jump
        return super().wait_stop(stop, seconds)


def _jump(tmp_path: Path, jump: float) -> tuple[Any, JumpingClock, list[float]]:
    mono: list[float] = []

    def cycle(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        mono.append(h.timer.monotonic())
        return paper_cycle(con, client, config, **kw)

    h = harness(tmp_path, limit=2, action=cycle)
    clock = JumpingClock(h.timer.now(), h.rounds.started, jump)
    h.service.timer = h.rounds.timer = h.timer = clock
    assert h.service.run() == EXIT_OK
    return h, clock, mono


def test_a_clock_jump_back_re_plans_instead_of_waiting_it_out(tmp_path: Path) -> None:
    h, clock, mono = _jump(tmp_path, -3600)
    assert clock.jumped
    # Round 2 within about a minute of real (monotonic) time, not an hour later.
    assert mono[1] - mono[0] <= 2 * PERIOD
    assert (h.rounds.started[1] - OFFSET) % PERIOD == 0  # still on a slot of the new time
    assert "wall clock jumped back: next round re-planned" in h.log()


def test_a_clock_jump_forward_still_skips_without_catch_up(tmp_path: Path) -> None:
    h, clock, mono = _jump(tmp_path, 3600)
    # The slot planned before the jump is now in the past: the round runs at once, no burst.
    assert clock.jumped and mono[1] - mono[0] <= PERIOD
    assert len(h.rounds.started) == 2 and "jumped back" not in h.log()


def test_round_line_shows_each_gpt_result_with_its_cost(tmp_path: Path) -> None:
    secrets = f"BOT_AGENTS=gpt-luna,gpt-sol\nOPENAI_API_KEY={KEY}\n"
    h = harness(tmp_path, limit=1, secrets_text=secrets)
    assert h.service.run() == EXIT_OK
    line = next(x for x in h.log().splitlines() if x.startswith("INFO round "))
    assert re.search(r"\| gpt gpt-luna ASKED 0\.\d{4} EUR, gpt-sol ASKED 0\.\d{4} EUR \|", line)
    assert KEY not in h.log()


def test_each_round_logs_one_summary_line_and_its_trades(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=2)
    h.service.run()
    lines = h.log().splitlines()
    rounds = [line for line in lines if line.startswith("INFO round ")]
    assert len(rounds) == 2
    assert rounds[0].startswith("INFO round ok in 2.0 s | gpt - | trades 2 | equity trend ")
    assert "buyhold" in rounds[0] and "| trades 0 |" in rounds[1]
    assert lines.count("INFO trade buyhold bought 2 market(s) (benchmark)") == 1
    trades = [line for line in lines if line.startswith("INFO trade trend ")]
    assert [t.split(" target ")[0] for t in trades] == [
        "INFO trade trend BTC-EUR BUY",
        "INFO trade trend ETH-EUR BUY",
    ]


# ------------------------------------------------------------------ AK2: one writer


def test_second_writer_is_refused_while_the_service_runs(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def during(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        out = io.StringIO()
        seen["cli"] = run_paper_cycle(h.db, ScenarioClient(), env={"BOT_AGENTS": "trend"}, out=out)
        seen["cli_out"] = out.getvalue()
        (tmp_path / "other").mkdir(exist_ok=True)
        second = harness(tmp_path / "other", limit=1)
        second.service.db = h.db  # the same database, another service
        seen["service"] = second.service.run()
        seen["service_log"] = second.log()
        return paper_cycle(con, client, config, **kw)

    h = harness(tmp_path, limit=1, action=during)
    assert h.service.run() == EXIT_OK
    assert seen["cli"] == CLI_LOCKED and "refused" in seen["cli_out"]
    assert seen["service"] == EXIT_LOCKED
    assert "refused: another process owns" in seen["service_log"]


def test_the_lock_is_free_again_after_a_stop(tmp_path: Path) -> None:
    assert harness(tmp_path, limit=1).service.run() == EXIT_OK
    assert harness(tmp_path, limit=1).service.run() == EXIT_OK


def test_web_reads_while_the_service_runs(tmp_path: Path) -> None:
    seen: list[int] = []

    def during(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        report = paper_cycle(con, client, config, **kw)
        with reading(h.db) as ro:
            seen.append(ro.execute("SELECT COUNT(*) FROM equity").fetchone()[0])
        return report

    h = harness(tmp_path, limit=2, action=during)
    assert h.service.run() == EXIT_OK
    assert seen == [2, 4]


# ------------------------------------------------------------------ AK4: errors


def test_failing_round_is_logged_reported_and_the_next_round_runs(tmp_path: Path) -> None:
    calls: list[int] = []
    states: list[tuple[str, int, str | None]] = []

    def flaky(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        calls.append(1)
        if len(calls) > 1:
            status = read_status(status_path(h.db))
            assert status is not None
            states.append((status.state, status.failures, status.last_result))
        if len(calls) in (1, 2):
            raise ConnectionError("bitvavo unreachable")
        return paper_cycle(con, client, config, **kw)

    h = harness(tmp_path, limit=3, action=flaky)
    assert h.service.run() == EXIT_OK
    assert len(calls) == 3
    assert states == [("degraded", 1, "error"), ("degraded", 2, "error")]
    assert "ERROR round failed after" in h.log()
    assert "ConnectionError: bitvavo unreachable" in h.log()
    final = read_status(status_path(h.db))
    assert final is not None
    assert (final.state, final.failures, final.last_result) == ("stopped", 0, "ok")


def test_risk_stop_is_logged_once(tmp_path: Path) -> None:
    """Spec 011 AK3: a stopped depot is no failure - the service keeps running, rounds stay ok."""
    states: list[tuple[str, int, str | None]] = []

    def stopping(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        if not states and not con.execute("SELECT 1 FROM equity").fetchone():
            con.execute("UPDATE portfolios SET peak=20000 WHERE agent='trend'")
        status = read_status(status_path(h.db))
        assert status is not None
        states.append((status.state, status.failures, status.last_result))
        return paper_cycle(con, client, config, **kw)

    h = harness(tmp_path, limit=3, action=stopping)
    assert h.service.run() == EXIT_OK
    stops = [line for line in h.log().splitlines() if "risk stop" in line]
    assert len(stops) == 1
    assert stops[0].startswith("WARNING risk stop trend: equity ")
    assert "below 90 % of its peak 20000.00 EUR - sold out and halted" in stops[0]
    assert states[1:] == [("running", 0, "ok"), ("running", 0, "ok")]
    assert "no decision" not in h.log()
    with reading(h.db) as ro:
        assert ro.execute("SELECT halted FROM portfolios WHERE agent='trend'").fetchone()[0] == 1


def test_failing_round_without_quotes_counts_as_degraded(tmp_path: Path) -> None:
    client = MovingClient()
    client.fail_quotes = True
    h = harness(tmp_path, limit=2, client=client)
    assert h.service.run() == EXIT_OK
    status = read_status(status_path(h.db))
    assert status is not None
    assert (status.failures, status.last_result) == (2, "no_quotes")
    assert "HTTP 503" in status.last_error
    assert "WARNING round no_quotes" in h.log()


def test_schema_mismatch_exits_1_without_touching_the_db(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    open_db(db, agents=("trend",)).close()
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version=7")
    raw.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    h = harness(tmp_path)
    assert h.service.run() == EXIT_INVALID
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert h.rounds.started == []
    assert "database refused: SchemaError" in h.log()


def test_invalid_configuration_at_start_exits_1(tmp_path: Path) -> None:
    h = harness(tmp_path, secrets_text="BOT_AGENTS=nonsense\n")
    assert h.service.run() == EXIT_INVALID
    assert "configuration error" in h.log() and not h.db.exists()


def test_stalled_round_exits_4_and_says_stalled(tmp_path: Path) -> None:
    release = threading.Event()

    def hang(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        release.wait(30)
        raise RuntimeError("released")

    h = harness(tmp_path, limit=5, action=hang, grace=0.001)
    try:
        with pytest.raises(HardExit) as exited:
            h.service.run()
    finally:
        release.set()
    assert exited.value.code == EXIT_STALLED
    status = read_status(status_path(h.db))
    assert status is not None and status.state == "stalled"
    assert f"round hangs for {STALL_SECONDS} s" in h.log()
    # The watchdog was still pinged while the round hung, until the stall limit.
    assert h.notifier.lines().count("WATCHDOG=1") >= STALL_SECONDS // 20


def test_status_file_describes_the_service(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=1)
    h.service.run()
    data = json.loads(status_path(h.db).read_text(encoding="utf-8"))
    assert data["state"] == "stopped"
    assert data["period_seconds"] == 60 and data["offset_seconds"] == 5
    assert data["last_round_at"] == iso(MINUTE + 65)
    assert data["last_result"] == "ok" and data["failures"] == 0
    assert data["next_round_at"] is None
