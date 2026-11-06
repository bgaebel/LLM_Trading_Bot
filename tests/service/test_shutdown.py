"""Spec 008 AK5: a stop ends the service quickly and cleanly, a hanging round after a deadline."""

import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any

import pytest

from llm_fakes import KEY, FakeModel
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.run import (
    EXIT_OK,
    EXIT_STALLED,
    SLICE_SECONDS,
    STOP_SECONDS,
    install_stop_signals,
)
from llm_trading_bot.service.status import read_status, status_path
from service_fakes import FakeTimer, HardExit, harness

GPT = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={KEY}\n"


class StopDuringWait(FakeTimer):
    """Sets the stop flag ``after`` seconds into the wait that follows round 1."""

    def __init__(self, start: float, rounds: list[float], stop: threading.Event) -> None:
        super().__init__(start)
        self.rounds, self.stop, self.waited = rounds, stop, 0.0
        self.stopped_at: float | None = None
        self.after = 12.0

    def wait_stop(self, stop: threading.Event, seconds: float) -> bool:
        if self.rounds and self.stopped_at is None:
            self.waited += seconds
            if self.waited >= self.after:
                self.stopped_at = self.now()
                self.stop.set()
                return True
        return super().wait_stop(stop, seconds)


def test_stop_while_waiting_ends_at_once_without_another_round(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=5)
    timer = StopDuringWait(h.timer.now(), h.rounds.started, h.stop)
    h.service.timer = h.rounds.timer = timer
    assert h.service.run() == EXIT_OK
    assert len(h.rounds.started) == 1
    assert timer.stopped_at is not None
    # The wait was cut short: no time passed after the stop, round 2 (a minute later) never came.
    assert timer.now() - timer.stopped_at <= SLICE_SECONDS
    status = read_status(status_path(h.db))
    assert status is not None and status.state == "stopped"
    assert "service stopped" in h.log()


def test_a_signal_while_waiting_never_deadlocks_the_main_thread() -> None:
    # LEARNING(#python): Signal-Handler setzt Event, auf das der Hauptthread wartet
    # Many SIGINTs land while the main thread is inside RealTimer.wait_stop; with Event.wait()
    # this deadlocked within a few signals. A subprocess, so a regression fails instead of hanging.
    script = textwrap.dedent(
        """
        import _thread, signal, threading, time
        from llm_trading_bot.service.run import RealTimer, install_stop_signals
        stop = threading.Event()
        install_stop_signals(stop)
        hits = [0]
        previous = signal.getsignal(signal.SIGINT)
        def counting(signum, frame):
            hits[0] += 1
            previous(signum, frame)
        signal.signal(signal.SIGINT, counting)
        def fire():
            for _ in range(400):
                _thread.interrupt_main(signal.SIGINT)
                time.sleep(0.0005)
        threading.Thread(target=fire, daemon=True).start()
        timer = RealTimer()
        while hits[0] < 300:
            timer.wait_stop(stop, 0.0)
        print("ok", stop.is_set())
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, encoding="utf-8"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok True"


def test_stop_after_a_round_ends_without_waiting_for_the_next(tmp_path: Path) -> None:
    h = harness(tmp_path, limit=1)
    assert h.service.run() == EXIT_OK
    # The stop came with round 1; the loop never waited for round 2.
    assert len(h.rounds.started) == 1
    status = read_status(status_path(h.db))
    assert status is not None and status.state == "stopped"
    assert h.notifier.lines()[-2:] == ["STOPPING=1", "STATUS=stopping"]
    assert "service stopped" in h.log()


def test_a_stop_during_a_round_skips_gpt_values_the_depots_and_exits_0(tmp_path: Path) -> None:
    model = FakeModel()

    def signal_arrives(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> Any:
        h.stop.set()  # SIGTERM while the round is running
        return paper_cycle(con, client, config, **kw)

    h = harness(tmp_path, limit=5, action=signal_arrives, secrets_text=GPT, model=model)
    assert h.service.run() == EXIT_OK
    assert model.calls == []
    assert "gpt gpt-luna STOPPING" in h.log()
    con = sqlite3.connect(h.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM equity").fetchone()[0] == 2  # still valued
    finally:
        con.close()
    with WriterLock(h.db):  # released
        pass


def test_a_round_that_ignores_the_stop_ends_hard_after_the_deadline(tmp_path: Path) -> None:
    release = threading.Event()

    def stuck(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        h.stop.set()
        release.wait(30)
        raise RuntimeError("released")

    h = harness(tmp_path, limit=5, action=stuck, grace=0.001)
    started = h.timer.monotonic()
    try:
        with pytest.raises(HardExit) as exited:
            h.service.run()
    finally:
        release.set()
    assert exited.value.code == EXIT_STALLED
    waited = h.timer.monotonic() - started
    assert STOP_SECONDS <= waited - 35 <= STOP_SECONDS + 2  # 35 s until the first slot
    assert f"round still running {STOP_SECONDS} s after the stop request" in h.log()
    status = read_status(status_path(h.db))
    assert status is not None and status.state == "stopped"


def test_stop_signals_set_the_stop_flag() -> None:
    stop = threading.Event()
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        install_stop_signals(stop)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert stop.is_set()
    finally:
        for s, h in previous.items():
            signal.signal(s, h)
