"""Spec 008 AK1, AK2, AK4: the service loop - timing, one writer, failing and hanging rounds."""

import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from fakes import ScenarioClient
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
from llm_trading_bot.service.status import read_status, status_path
from llm_trading_bot.web.readonly import reading
from service_fakes import MINUTE, HardExit, MovingClient, harness


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


def test_second_writer_is_refused_and_the_lock_is_free_after_a_stop(tmp_path: Path) -> None:
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
