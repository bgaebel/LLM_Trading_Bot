"""Test doubles for the service loop: a fake clock that only moves when asked, a recording
notifier, scripted rounds and a hard exit that raises instead of ending pytest."""

import io
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import FakeModel
from llm_trading_bot.llm.agents import BUYHOLD_FIELD, USDC_FIELD
from llm_trading_bot.market.models import Candle, Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.logs import setup_logging
from llm_trading_bot.service.notify import Notifier
from llm_trading_bot.service.run import Service

MINUTE = 1_800_000_000  # aligned to every interval; golden NOW is 30 s later


class MovingClient(ScenarioClient):
    """Golden bars that move along with the clock, so every round sees fresh closed candles."""

    def closed_candles(
        self, symbol: Symbol, interval: int, *, now_epoch: int
    ) -> tuple[Candle, ...]:
        step = interval * 60
        shift = (now_epoch // step - NOW // step) * step
        bars = super().closed_candles(symbol, interval, now_epoch=now_epoch)
        return tuple(Candle(c.ts + shift, c.open, c.high, c.low, c.close, c.volume) for c in bars)


class FakeTimer:
    """Wall and monotonic time advance together, only through waits or ``advance``.

    ``grace``: real seconds a wait for the round gives the worker thread before fake time moves
    on; fast fake rounds finish well within it, a blocked round costs this much per slice.
    """

    def __init__(self, start: float, *, grace: float = 2.0) -> None:
        self.wall, self.mono, self.grace = start, 1000.0, grace
        self.lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            return self.wall

    def monotonic(self) -> float:
        with self.lock:
            return self.mono

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.wall += seconds
            self.mono += seconds

    def wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        if stop.is_set():
            return True
        self.advance(seconds)
        return stop.is_set()

    def wait_done(self, done: threading.Event, seconds: float) -> bool:
        if done.wait(self.grace):
            return True
        self.advance(seconds)
        return done.is_set()


class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__(None)
        self.sent: list[tuple[str, ...]] = []

    def send(self, *lines: str) -> bool:
        self.sent.append(lines)
        return True

    def lines(self) -> list[str]:
        return [line for message in self.sent for line in message]


class HardExit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def raise_hard_exit(code: int) -> NoReturn:
    raise HardExit(code)


@dataclass
class Rounds:
    """A cycle that runs the real paper_cycle (or ``action``) and stops the service after
    ``limit`` rounds; records when each round started."""

    timer: FakeTimer
    stop: threading.Event
    limit: int = 3
    action: Callable[..., CycleReport] | None = None
    started: list[float] = field(default_factory=list)
    configs: list[Any] = field(default_factory=list)
    models: list[Any] = field(default_factory=list)
    duration: float = 2.0

    def __call__(self, con: Any, client: Any, config: Any, **kw: Any) -> CycleReport:
        self.started.append(self.timer.now())
        self.configs.append(config)
        self.models.append(kw.get("model"))
        try:
            report = (self.action or paper_cycle)(con, client, config, **kw)
            self.timer.advance(self.duration)
            return report
        finally:
            if len(self.started) >= self.limit:
                self.stop.set()


@dataclass
class Harness:
    service: Service
    timer: FakeTimer
    stop: threading.Event
    notifier: RecordingNotifier
    rounds: Rounds
    out: io.StringIO
    db: Path
    secrets: Path
    keys: list[str]

    def log(self) -> str:
        return self.out.getvalue()


def harness(
    tmp_path: Path,
    *,
    env: Mapping[str, str] | None = None,
    limit: int = 3,
    action: Callable[..., CycleReport] | None = None,
    start: float = MINUTE + 30,
    grace: float = 2.0,
    client: Any = None,
    model: Any = None,
    secrets_text: str = "BOT_AGENTS=trend\n",
    usdc: str | None = "",
    buyhold: str | None = "1",
) -> Harness:
    """``usdc``: BOT_USDC_AGENTS in the environment. These tests predate Spec 020 and run
    without the USDC depots (on by default) unless a test chooses them; None leaves the field
    unset, so the bot's default applies. ``buyhold``: BOT_BUYHOLD likewise - they predate
    Spec 021 and run the euro buy & hold depot (off by default)."""
    timer = FakeTimer(start, grace=grace)
    stop = threading.Event()
    notifier = RecordingNotifier()
    rounds = Rounds(timer, stop, limit, action)
    out = io.StringIO()
    log = setup_logging("INFO", out, journal=True)
    keys: list[str] = []

    def factory(key: str) -> Any:
        keys.append(key)
        return model or FakeModel()

    db = tmp_path / "bot.db"
    secrets = tmp_path / "secrets.env"
    secrets.write_text(secrets_text, encoding="utf-8")
    service = Service(
        db,
        client or MovingClient(),
        env={
            **({} if usdc is None else {USDC_FIELD: usdc}),
            **({} if buyhold is None else {BUYHOLD_FIELD: buyhold}),
            **(env or {}),
        },
        secrets=secrets,
        stop=stop,
        log=log,
        notifier=notifier,
        timer=timer,
        model_factory=factory,
        cycle=rounds,
        hard_exit=raise_hard_exit,
    )
    return Harness(service, timer, stop, notifier, rounds, out, db, secrets, keys)


def quiet() -> None:
    logging.getLogger("llm_trading_bot.service").handlers.clear()
