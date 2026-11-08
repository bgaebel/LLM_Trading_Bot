"""Spec 015 AK3/AK4 (ADR-0027): edge cases of recording - rejected quotes, blocked and refused
GPT attempts, failing rounds, repeated keys, the file's creation, journal mode and errors."""

import os
import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import VALID_BOOK, FakeTransport, ScenarioClient, bitvavo_handler
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, valid_reply
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.cycle import REFUSAL_MARK
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.ops.backup import OBSERVATIONS, backup_db, restore_db
from llm_trading_bot.paper import cycle as cycle_module
from llm_trading_bot.paper import observations_schema as schema_module
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import (
    ObservationBuffer,
    ObservationWriter,
    ignored_message,
    rejected_purpose,
)
from llm_trading_bot.paper.observations_schema import OBS_FILE, OBS_SCHEMA_VERSION, create

SHA = "0123456789abcdef0123456789abcdef01234567"
TREND = BotConfig(("trend",), None)
STAMP = "2027-01-15T08:00:30+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("trend", "gpt-luna", "gpt-sol", BUYHOLD))
    yield connection
    connection.close()


def observed(
    con: sqlite3.Connection, client: Any, config: BotConfig, model: Any = None
) -> ObservationBuffer:
    buffer = ObservationBuffer(SHA)
    paper_cycle(
        con, client, config, costs=Costs(), model=model, clock=lambda: NOW, observations=buffer
    )
    assert buffer.dropped == 0
    return buffer


def counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("service_starts", "rounds", "books", "decision_meta", "llm_attempts")
        }
    finally:
        con.close()


def last_error(writer: ObservationWriter) -> str | None:
    """Read again, past mypy's narrowing from an earlier assert."""
    return writer.last_error


def small_buffer(call_id: int = 7, bar_ts: int = NOW - 90) -> ObservationBuffer:
    buffer = ObservationBuffer(SHA)
    buffer.begin(NOW)
    buffer.decision("trend", Symbol.BTCEUR, bar_ts, "t", "trend", "v", {})
    buffer.llm_attempt("t", call_id, "gpt-luna", "first", {}, {}, "raw", {})
    return buffer


# ------------------------------------------------------------------ rejected quotes


def wide_eth(now: int) -> Callable[[str], object]:
    """Bitvavo answering ETH-EUR with a 60 bp book (MAX_SPREAD_BPS is 50)."""
    base = bitvavo_handler(now)

    def handler(url: str) -> object:
        if url.endswith("/ticker/book"):
            eth = {"market": "ETH-EUR", "bid": "3200", "ask": "3219.3", "bidSize": "1.5"}
            return [VALID_BOOK[0], {**eth, "askSize": "2"}]
        return base(url)

    return handler


def test_a_wide_book_is_recorded_as_spread_with_its_rows(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    buffer = observed(db, BitvavoClient(FakeTransport(wide_eth(NOW))), TREND)
    assert [s[1:4] for s in buffer.snapshots] == [
        ("BTCEUR", "ok", None),
        (
            "ETHEUR",
            "spread",
            "MarketDataError: ETH-EUR: no quote - ticker/book: unusable quote"
            " (spread too wide: 60.1 bp)",
        ),
    ]
    eth = [b for b in buffer.books if b[1] == "ETHEUR"]
    assert eth == [
        (STAMP, "ETHEUR", purpose, 3200.0, 3219.3, 1.5, 2.0)
        for purpose in ("start:rejected:spread", "snapshot:rejected:spread")
    ]
    assert rejected_purpose("start", "spread") == "start:rejected:spread"
    assert [b[2] for b in buffer.books if b[1] == "BTCEUR"] == ["start", "snapshot"]
    # And they are storable: the round is written with them.
    path = tmp_path / OBS_FILE
    writer = ObservationWriter(path)
    writer.write(buffer)
    assert (writer.written, writer.last_error) == (1, None) and counts(path)["books"] == 4


def test_a_failed_start_fetch_is_recorded_as_http(db: sqlite3.Connection) -> None:
    client = BitvavoClient(FakeTransport(bitvavo_handler(NOW, quotes_fail=True)))
    buffer = observed(db, client, TREND)
    assert [s[1:3] for s in buffer.snapshots] == [("BTCEUR", "http"), ("ETHEUR", "http")]
    assert buffer.books == []


# ------------------------------------------------------------------ GPT attempts


def test_a_budget_blocked_attempt_is_recorded_with_its_call_id(db: sqlite3.Connection) -> None:
    config = load_config(
        {
            "OPENAI_API_KEY": KEY,
            "BOT_AGENTS": "gpt-luna",
            "BOT_USDC_AGENTS": "",
            "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "0",
        }
    )
    model = FakeModel({"gpt-6-luna": valid_reply})
    buffer = observed(db, ScenarioClient(), config, model)
    (row,) = db.execute("SELECT id, status FROM llm_calls").fetchall()
    assert row["status"] == "BUDGET_BLOCKED" and model.calls == []
    (attempt,) = buffer.attempts
    at, call_id, agent, trigger, detail, cadence, raw, targets = attempt
    assert (at, call_id, agent, trigger, raw) == (STAMP, row["id"], "gpt-luna", "first", None)
    assert (detail, targets) == ("{}", "{}")
    assert '"max_calls_per_hour": 4' in str(cadence)


def test_a_refusal_keeps_its_text_marked(db: sqlite3.Connection) -> None:
    def refuses(markets: list[str]) -> ModelReply:
        usage = valid_reply(markets).usage
        return ModelReply("completed", None, "I cannot help", None, "r", None, usage)

    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna", "BOT_USDC_AGENTS": ""})
    buffer = observed(db, ScenarioClient(), config, FakeModel({"gpt-6-luna": refuses}))
    (attempt,) = buffer.attempts
    assert attempt[6] == f"{REFUSAL_MARK}I cannot help" == "[refusal] I cannot help"
    assert buffer.decisions == []


def test_a_refusal_with_text_keeps_the_text(db: sqlite3.Connection) -> None:
    def refuses(markets: list[str]) -> ModelReply:
        usage = valid_reply(markets).usage
        return ModelReply("completed", "partial", "no", None, "r", None, usage)

    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna", "BOT_USDC_AGENTS": ""})
    buffer = observed(db, ScenarioClient(), config, FakeModel({"gpt-6-luna": refuses}))
    assert buffer.attempts[0][6] == "partial"


# ------------------------------------------------------------------ a failing round


def test_snapshots_are_recorded_when_the_round_fails_in_the_valuation(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("valuation bug")

    monkeypatch.setattr(cycle_module, "mark", broken)
    buffer = ObservationBuffer(SHA)
    with pytest.raises(RuntimeError, match="valuation bug"):
        paper_cycle(
            db, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW, observations=buffer
        )
    # The decisions fell before the valuation failed.
    assert [s[1:] for s in buffer.snapshots] == [
        ("BTCEUR", "ok", None, 1),
        ("ETHEUR", "ok", None, 1),
    ]
    assert len(buffer.decisions) == 2 and buffer.dropped == 0


# ------------------------------------------------------------------ repeated keys


def test_repeated_keys_are_counted_and_recording_stays_on(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    writer = ObservationWriter(path)
    writer.write(small_buffer())
    assert writer.last_error is None
    # As after a restore of bot.db: the same call id and bar come round again.
    writer.write(small_buffer())
    assert writer.last_error == ignored_message(2)
    assert (
        writer.last_error == "2 decision/attempt rows ignored (duplicate keys) - bot.db restored?"
    )
    assert (writer.written, writer.failed, writer.off) == (2, 0, False)
    assert counts(path)["rounds"] == 2 and counts(path)["llm_attempts"] == 1
    writer.write(small_buffer(call_id=8))  # the attempt is new, the decision again not
    assert writer.last_error == ignored_message(1)
    writer.write(small_buffer(call_id=9, bar_ts=NOW))
    assert writer.last_error is None and counts(path)["decision_meta"] == 2


# ------------------------------------------------------------------ creating the file


def test_create_leaves_only_the_file(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    create(path)
    # Checkpointed and closed: no -wal/-shm, no temporary file.
    assert sorted(p.name for p in tmp_path.iterdir()) == [OBS_FILE]
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_an_interrupted_create_leaves_no_file_and_the_next_start_creates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / OBS_FILE

    def half(con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE service_starts (id INTEGER)")
        raise RuntimeError("power cut")

    monkeypatch.setattr(schema_module, "_build", half)
    writer = ObservationWriter(path)
    writer.start("t", SHA, 1)
    assert not path.exists() and writer.failed == 1 and not writer.off
    assert writer.last_error == "RuntimeError: power cut"
    monkeypatch.undo()
    writer.start("t", SHA, 2)
    assert (writer.written, last_error(writer)) == (1, None)
    assert counts(path)["service_starts"] == 1
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(("-wal", "-shm"))) == [
        OBS_FILE
    ]


def test_a_leftover_temporary_file_is_removed_before_creating(tmp_path: Path) -> None:
    """As after a kill during create: the half-made temporary file is never taken as live."""
    path = tmp_path / OBS_FILE
    leftover = tmp_path / f".{OBS_FILE}.creating"
    leftover.write_bytes(b"half a database")
    (tmp_path / f".{OBS_FILE}.creating-journal").write_bytes(b"x")
    create(path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [OBS_FILE]
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == OBS_SCHEMA_VERSION
    finally:
        con.close()


def test_create_never_links_over_a_file_that_appeared_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / OBS_FILE
    real_link = os.link

    def racing(src: Any, dst: Any) -> None:
        Path(dst).write_bytes(b"theirs")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", racing)
    with pytest.raises(FileExistsError):
        create(path)
    assert path.read_bytes() == b"theirs"
    assert sorted(p.name for p in tmp_path.iterdir()) == [OBS_FILE]


@pytest.mark.parametrize("kind", ["empty file", "empty database"])
def test_a_blank_file_from_an_older_create_is_initialised(tmp_path: Path, kind: str) -> None:
    path = tmp_path / OBS_FILE
    if kind == "empty file":
        path.write_bytes(b"")
    else:
        sqlite3.connect(path).close()
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
        con.close()
    writer = ObservationWriter(path)
    writer.start("t", SHA, 1)
    assert (writer.written, writer.failed, writer.off) == (1, 0, False)
    assert counts(path)["service_starts"] == 1


def test_a_version_0_file_with_tables_is_refused(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE something (x)")
    con.close()
    writer = ObservationWriter(path)
    writer.start("t", SHA, 1)
    assert writer.off and writer.last_error is not None
    assert writer.last_error.startswith("recording off: SchemaError")
    con = sqlite3.connect(path)
    try:
        assert [r[0] for r in con.execute("SELECT name FROM sqlite_master")] == ["something"]
    finally:
        con.close()


# ------------------------------------------------------------------ journal mode


def test_writer_switches_a_restored_copy_to_wal(tmp_path: Path) -> None:
    source = tmp_path / "data" / OBS_FILE
    source.parent.mkdir()
    ObservationWriter(source).write(small_buffer())
    done = backup_db(source, tmp_path / "backups", profile=OBSERVATIONS)
    target = tmp_path / "restored" / OBS_FILE
    restore_db(done.path, target, OBSERVATIONS)

    def mode() -> str:
        con = sqlite3.connect(target)
        try:
            return str(con.execute("PRAGMA journal_mode").fetchone()[0])
        finally:
            con.close()

    assert mode() == "delete"
    writer = ObservationWriter(target)
    writer.write(small_buffer(call_id=8, bar_ts=NOW))
    assert (writer.written, writer.last_error) == (1, None)
    assert mode() == "wal"


# ------------------------------------------------------------------ last error


def test_last_error_is_cleared_by_the_next_write(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    create(path)
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    writer = ObservationWriter(path)
    try:
        writer.write(small_buffer())
        assert writer.last_error is not None and "locked" in writer.last_error
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    writer.write(small_buffer())
    assert (writer.written, writer.failed, last_error(writer)) == (1, 1, None)
