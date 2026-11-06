"""Spec 008 AK5: a stop ends the service quickly and cleanly, a hanging round after a deadline."""

import signal
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from llm_fakes import KEY, FakeModel
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.run import EXIT_OK, EXIT_STALLED, STOP_SECONDS, install_stop_signals
from llm_trading_bot.service.status import read_status, status_path
from service_fakes import HardExit, harness

GPT = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={KEY}\n"


def test_stop_while_waiting_ends_at_once_without_another_round(tmp_path: Path) -> None:
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
