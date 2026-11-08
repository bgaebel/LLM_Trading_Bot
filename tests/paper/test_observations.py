"""Spec 015 AK3/AK4 (ADR-0027): what a round records, and how observations.db is written."""

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeTransport, ScenarioClient, bitvavo_handler
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, entry, valid_reply
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.llm.contract import REASON_MAX
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import Candle, Quote, QuoteBook, Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import (
    BUSY_TIMEOUT_MS,
    ObservationBuffer,
    ObservationWriter,
    changed_windows,
    ignored_message,
    insert,
    note,
    observations_path,
    release_sha,
    rows_sha256,
)
from llm_trading_bot.paper.observations_schema import OBS_FILE, OBS_TABLES, create
from llm_trading_bot.strategy.signals import STRATEGY_VERSION
from service_fakes import MovingClient

SHA = "0123456789abcdef0123456789abcdef01234567"
TREND = BotConfig(("trend",), None)


def js(value: object) -> Any:
    """A JSON column of a buffered row or a stored decision."""
    assert isinstance(value, str)
    return json.loads(value)


def gpt_config(agents: str = "gpt-luna") -> BotConfig:
    return load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": agents, "BOT_USDC_AGENTS": ""})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(
        tmp_path / "bot.db",
        agents=("trend", "breakout", "meanrev", "ensemble", "gpt-luna", "gpt-sol", BUYHOLD),
    )
    yield connection
    connection.close()


def observed(
    con: sqlite3.Connection,
    client: Any,
    config: BotConfig,
    clock: Callable[[], float] = lambda: NOW,
    model: Any = None,
) -> ObservationBuffer:
    buffer = ObservationBuffer(SHA)
    paper_cycle(con, client, config, costs=Costs(), model=model, clock=clock, observations=buffer)
    assert buffer.dropped == 0
    return buffer


def rows_of(path: Path, table: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        con.close()


# ------------------------------------------------------------------ buffer and helpers


def test_note_swallows_every_recorder_failure() -> None:
    buffer = ObservationBuffer()

    def broken(_b: ObservationBuffer) -> None:
        raise ValueError("bug in the recorder")

    note(buffer, broken)
    note(None, broken)
    note(buffer, lambda b: b.snapshot(Symbol.BTCEUR, "ok", None, True))  # begin() missing
    assert buffer.dropped == 2 and buffer.snapshots == []


def test_rows_sha256_is_the_documented_canonical_form() -> None:
    rows = (Candle(1_800_000_000, 100, 101, 99, 100, 1),)
    text = "[[1800000000,100.0,101.0,99.0,100.0,1.0]]"
    assert rows_sha256(rows) == hashlib.sha256(text.encode()).hexdigest()
    # Equal values give the same hash, whether they came as int or float.
    assert rows_sha256(rows) == rows_sha256((Candle(1_800_000_000, 100.0, 101, 99, 100, 1.0),))
    assert rows_sha256(()) == hashlib.sha256(b"[]").hexdigest()


def test_release_sha_comes_from_the_install_path(tmp_path: Path) -> None:
    venv = tmp_path / "releases" / SHA / ".venv"
    venv.mkdir(parents=True)
    assert release_sha(str(venv)) == SHA
    assert release_sha(str(tmp_path / "releases" / "current" / ".venv")) == "unknown"
    assert release_sha(str(tmp_path / "releases" / SHA[:-1] / ".venv")) == "unknown"
    assert release_sha(str(tmp_path / "releases" / f"{SHA[:-1]}g" / ".venv")) == "unknown"
    assert release_sha() in ("unknown", Path(sys.prefix).resolve().parent.name)


def test_observations_live_next_to_the_db(tmp_path: Path) -> None:
    assert observations_path(tmp_path / "bot.db") == tmp_path / OBS_FILE


# ------------------------------------------------------------------ recording points


def sized_bitvavo(now: int) -> Callable[[str], object]:
    """Bitvavo fake with book sizes and a still-forming 1m bar."""
    base = bitvavo_handler(now)

    def handler(url: str) -> object:
        data = base(url)
        if url.endswith("/ticker/book"):
            assert isinstance(data, list)
            return [{**q, "bidSize": "0.5", "askSize": "2"} for q in data]
        if "interval=1m" in url:
            assert isinstance(data, list)
            return [[now // 60 * 60 * 1000, "100", "101", "99", "100", "3"], *data]
        return data

    return handler


def test_records_books_windows_and_snapshots_from_bitvavo(db: sqlite3.Connection) -> None:
    client = BitvavoClient(FakeTransport(sized_bitvavo(NOW)))
    buffer = observed(db, client, TREND)
    assert buffer.round_at == NOW and buffer.release == SHA
    stamp = "2027-01-15T08:00:30+00:00"
    assert buffer.books == [
        (stamp, "BTCEUR", "start", 66000.0, 66005.0, 0.5, 2.0),
        (stamp, "ETHEUR", "start", 3200.0, 3200.2, 0.5, 2.0),
        (stamp, "BTCEUR", "snapshot", 66000.0, 66005.0, 0.5, 2.0),
        (stamp, "ETHEUR", "snapshot", 3200.0, 3200.2, 0.5, 2.0),
    ]
    assert [(w[1], w[2]) for w in buffer.windows] == [
        (m, tf) for m in ("BTCEUR", "ETHEUR") for tf in (1, 15, 60, 240)
    ]
    one = buffer.windows[0]
    closed = client.closed_candles(Symbol.BTCEUR, 1, now_epoch=NOW)
    # Received before the forming bar was dropped: 200 closed + the forming one.
    last_closed = NOW // 60 * 60 - 60
    assert one == (
        stamp,
        "BTCEUR",
        1,
        201,
        last_closed - 199 * 60,
        NOW // 60 * 60,
        1,
        1.0,
        rows_sha256(closed),
    )
    assert all(w[3] == 200 and w[6] == 0 for w in buffer.windows if w[2] != 1)
    assert [s[1:] for s in buffer.snapshots] == [
        ("BTCEUR", "ok", None, 1),
        ("ETHEUR", "ok", None, 1),
    ]


class SlowCandles(ScenarioClient):
    """Each candle request takes 3 s: the start quotes get too old before the decisions."""

    def __init__(self, clock: list[float]) -> None:
        super().__init__()
        self.clock = clock

    def closed_candles(self, symbol: Symbol, interval: int, *, now_epoch: int) -> Any:
        self.clock[0] += 3
        return super().closed_candles(symbol, interval, now_epoch=now_epoch)


def test_records_every_book_fetch_with_its_purpose(db: sqlite3.Connection) -> None:
    now = [float(NOW)]
    client = SlowCandles(now)

    def ask(markets: list[str]) -> ModelReply:
        now[0] += 5
        return valid_reply(markets)

    buffer = observed(
        db,
        client,
        load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "trend,gpt-luna", "BOT_USDC_AGENTS": ""}),
        clock=lambda: now[0],
        model=FakeModel({"gpt-6-luna": ask}),
    )
    purposes = [(b[2], b[1]) for b in buffer.books]
    assert purposes == [
        (p, m) for p in ("start", "snapshot", "recheck", "after_llm") for m in ("BTCEUR", "ETHEUR")
    ]
    times = {b[2]: b[0] for b in buffer.books}
    assert times["start"] == "2027-01-15T08:00:30+00:00"
    assert times["snapshot"] == "2027-01-15T08:00:54+00:00"  # after 8 candle requests
    assert buffer.books[0][5:] == (None, None)  # this client sends no sizes


def test_records_snapshot_codes_and_whether_a_decision_fell(db: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    first = observed(db, client, TREND)
    assert [s[1:] for s in first.snapshots] == [
        ("BTCEUR", "ok", None, 1),
        ("ETHEUR", "http", "HTTPFailure: HTTP 500", 0),
    ]
    # Same bar again: the snapshot is fine, but nothing new is decided.
    again = observed(db, client, TREND, clock=lambda: NOW + 5)
    assert again.snapshots[0][1:] == ("BTCEUR", "ok", None, 0)


def test_records_the_code_of_a_failed_start_fetch(db: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.fail_quotes = True
    buffer = observed(db, client, TREND)
    assert [s[1:3] for s in buffer.snapshots] == [("BTCEUR", "http"), ("ETHEUR", "http")]
    assert buffer.books == [] and buffer.windows == []


def test_records_quote_missing_when_no_market_has_a_quote(db: sqlite3.Connection) -> None:
    client = ScenarioClient()
    client.bad_quotes = {Symbol.BTCEUR, Symbol.ETHEUR}  # a book without codes: no reason known
    buffer = observed(db, client, TREND)
    assert [s[1:3] for s in buffer.snapshots] == [
        ("BTCEUR", "quote_missing"),
        ("ETHEUR", "quote_missing"),
    ]
    assert buffer.books == [] and buffer.windows == []


def test_records_decision_meta_with_unrounded_indicators(db: sqlite3.Connection) -> None:
    buffer = observed(db, ScenarioClient(), TREND)
    assert [(d[1], d[2], d[4], d[5], d[6]) for d in buffer.decisions] == [
        ("trend", "BTCEUR", "trend", STRATEGY_VERSION, SHA),
        ("trend", "ETHEUR", "trend", STRATEGY_VERSION, SHA),
    ]
    stored = db.execute(
        "SELECT bar_ts, inputs FROM decisions WHERE agent='trend' AND symbol='BTCEUR'"
    ).fetchone()
    assert buffer.decisions[0][3] == stored["bar_ts"] and buffer.decisions[0][0] == (
        "2027-01-15T08:00:30+00:00"
    )
    unrounded = js(buffer.decisions[0][7])
    rounded = js(stored["inputs"])["indicators"]
    assert set(unrounded) == set(rounded)
    for key, value in unrounded.items():
        assert (round(value, 5) if isinstance(value, float) else value) == rounded[key], key
    assert any(round(v, 5) != v for v in unrounded.values() if isinstance(v, float))


def test_records_llm_attempts_with_trigger_raw_text_and_unclamped_targets(
    db: sqlite3.Connection,
) -> None:
    client = MovingClient()
    buy = FakeModel(
        {
            "gpt-6-luna": lambda m: valid_reply(
                m, **{k: entry("BUY", 0.45) for k in ("BTC_EUR", "ETH_EUR")}
            )
        }
    )
    first = observed(db, client, gpt_config(), model=buy)
    (attempt,) = first.attempts
    at, call_id, agent, trigger, detail, cadence, raw, targets = attempt
    assert at == "2027-01-15T08:00:30+00:00"  # when the answer arrived
    assert (call_id, agent, trigger, js(detail)) == (1, "gpt-luna", "first", {})
    assert js(cadence) == {
        "min_interval": 3600,
        "event_cooldown": 600,
        "move_threshold": 0.018,
        "max_calls_per_hour": 4,
    }
    assert raw == buy.replies["gpt-6-luna"](["BTC-EUR", "ETH-EUR"]).text
    assert js(targets)["BTCEUR"]["target_fraction"] == 0.45
    assert [(d[1], d[2], d[4]) for d in first.decisions] == [
        ("gpt-luna", "BTCEUR", "gpt-luna"),
        ("gpt-luna", "ETHEUR", "gpt-luna"),
    ]
    # An hour later the model says HOLD at 0: the decision keeps the current weight, the
    # observation keeps what the model said.
    hold = FakeModel({"gpt-6-luna": lambda m: valid_reply(m, BTC_EUR=entry("HOLD", 0))})
    later = observed(db, client, gpt_config(), clock=lambda: NOW + 3600, model=hold)
    (attempt,) = later.attempts
    assert attempt[3] == "interval" and js(attempt[4]) == {"elapsed": 3600}
    assert js(attempt[7])["BTCEUR"]["target_fraction"] == 0
    target = db.execute(
        "SELECT target FROM decisions WHERE agent='gpt-luna' AND symbol='BTCEUR'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert target > 0.4


def test_records_raw_text_of_an_invalid_answer(db: sqlite3.Connection) -> None:
    def broken(markets: list[str]) -> ModelReply:
        reply = valid_reply(markets)
        return ModelReply("completed", "not json at all", None, None, "r", None, reply.usage)

    buffer = observed(db, ScenarioClient(), gpt_config(), model=FakeModel({"gpt-6-luna": broken}))
    (attempt,) = buffer.attempts
    assert attempt[6] == "not json at all" and js(attempt[7]) == {}
    assert buffer.decisions == []


def test_records_an_attempt_without_reply(db: sqlite3.Connection) -> None:
    import httpx2
    import openai

    def fails(_markets: list[str]) -> ModelReply:
        raise openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com"))

    buffer = observed(db, ScenarioClient(), gpt_config(), model=FakeModel({"gpt-6-luna": fails}))
    (attempt,) = buffer.attempts
    assert attempt[3] == "first" and attempt[6] is None and js(attempt[7]) == {}


def test_a_cycle_without_buffer_records_nothing_and_needs_none(db: sqlite3.Connection) -> None:
    report = paper_cycle(db, ScenarioClient(), TREND, costs=Costs(), clock=lambda: NOW)
    assert report.complete


# ------------------------------------------------------------------ writer


def full_buffer(at: int = NOW) -> ObservationBuffer:
    buffer = ObservationBuffer(SHA)
    buffer.begin(at)
    book = QuoteBook({Symbol.BTCEUR: Quote(1.0, 1.0001)}, depth={Symbol.BTCEUR: (0.1, None)})
    buffer.book("start", at, book)
    buffer.window(Symbol.BTCEUR, 1, at, (Candle(at - 90, 1, 1, 1, 1, 2),))
    buffer.snapshot(Symbol.BTCEUR, "ok", None, True)
    # The same decision in every buffer: it must be observed once (natural key).
    buffer.decision("trend", Symbol.BTCEUR, NOW - 90, "t", "trend", "v", {"adx_1h": 1 / 3})
    buffer.llm_attempt("t", 7, "gpt-luna", "first", {}, {}, "raw", {})
    return buffer


def counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in OBS_TABLES}
    finally:
        con.close()


def test_writer_creates_a_missing_file_and_appends(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    writer = ObservationWriter(path)
    writer.start("2027-01-15T08:00:00+00:00", SHA, 42)
    writer.write(full_buffer())
    writer.write(full_buffer(NOW + 60))
    writer.write(ObservationBuffer())  # nothing to write: no transaction, no count
    # The second buffer repeats the decision and the attempt: written, but said.
    assert (writer.written, writer.failed, writer.last_error, writer.off) == (
        3,
        0,
        ignored_message(2),
        False,
    )
    assert counts(path) == {
        "service_starts": 1,
        "rounds": 2,
        "books": 2,
        "snapshots": 2,
        "windows": 2,
        "decision_meta": 1,  # the same decision twice is observed once
        "llm_attempts": 1,
    }
    (book,) = rows_of(path, "books")[:1]
    assert tuple(book)[1:] == (
        1,
        "2027-01-15T08:00:30+00:00",
        "BTCEUR",
        "start",
        1.0,
        1.0001,
        0.1,
        None,
    )
    assert tuple(rows_of(path, "service_starts")[0])[1:] == ("2027-01-15T08:00:00+00:00", SHA, 42)
    assert js(rows_of(path, "decision_meta")[0]["indicators"]) == {"adx_1h": 1 / 3}


def test_writer_refuses_another_version_and_switches_off(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    create(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version=99")
    con.close()
    writer = ObservationWriter(path)
    writer.write(full_buffer())
    assert writer.off and writer.failed == 1 and writer.written == 0
    assert writer.last_error is not None and "recording off: SchemaError" in writer.last_error
    writer.write(full_buffer())  # no second attempt on the file
    assert writer.failed == 2
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 99
        assert con.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 0
    finally:
        con.close()


def test_writer_never_recreates_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    garbage = b"this is not an sqlite database" * 200
    path.write_bytes(garbage)
    writer = ObservationWriter(path)
    writer.start("t", SHA, 1)
    writer.write(full_buffer())
    assert writer.off and (writer.written, writer.failed) == (0, 2)
    assert writer.last_error is not None and "DatabaseError" in writer.last_error
    assert path.read_bytes() == garbage


def test_writer_counts_a_busy_file_and_writes_again_later(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    create(path)
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    writer = ObservationWriter(path)
    started = time.monotonic()
    writer.write(full_buffer())
    waited = time.monotonic() - started
    assert writer.failed == 1 and not writer.off and writer.written == 0
    assert writer.last_error is not None and "locked" in writer.last_error
    assert BUSY_TIMEOUT_MS <= 1000 and waited < 3
    blocker.execute("ROLLBACK")
    blocker.close()
    writer.write(full_buffer())
    assert (writer.written, writer.failed) == (1, 1)
    assert counts(path)["rounds"] == 1


def test_writer_counts_an_unopenable_path(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    path.mkdir()  # a directory where the file should be
    writer = ObservationWriter(path)
    writer.write(full_buffer())
    writer.write(full_buffer())
    assert writer.failed == 2 and not writer.off and path.is_dir()


def test_writer_rolls_back_a_round_that_fails_halfway(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    buffer = full_buffer()
    buffer.snapshots.append(("too", "short"))  # inserted after rounds, books and windows
    writer = ObservationWriter(path)
    writer.write(buffer)
    assert writer.failed == 1 and not writer.off
    assert counts(path)["rounds"] == 0 and counts(path)["books"] == 0
    assert counts(path)["windows"] == 0


def test_writer_writes_a_window_only_when_it_changed(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    writer = ObservationWriter(path)
    first = full_buffer()
    writer.write(first)
    written = counts(path)["windows"]
    assert written == len(first.windows) > 0
    writer.write(full_buffer())  # same answers from Bitvavo: nothing new to keep
    assert counts(path)["windows"] == written and counts(path)["rounds"] == 2
    changed = full_buffer()
    changed.windows[0] = (*changed.windows[0][:8], "0" * 64)
    writer.write(changed)
    assert counts(path)["windows"] == written + 1


def test_writer_keeps_windows_of_a_lost_round_for_the_next(tmp_path: Path) -> None:
    path = tmp_path / OBS_FILE
    writer = ObservationWriter(path)
    broken = full_buffer()
    broken.books.append(("too", "short"))  # this round's insert fails after the windows
    writer.write(broken)
    assert writer.failed == 1 and counts(path)["windows"] == 0
    writer.write(full_buffer())
    assert counts(path)["windows"] == len(full_buffer().windows)


def test_writer_survives_a_buffer_that_raises(tmp_path: Path) -> None:
    class Raising(ObservationBuffer):
        def empty(self) -> bool:
            raise RuntimeError("broken buffer")

    writer = ObservationWriter(tmp_path / OBS_FILE)
    writer.write(Raising())
    assert writer.failed == 1 and writer.last_error == "RuntimeError: broken buffer"


# ------------------------------------------------------------------ size


def test_size_of_one_day(tmp_path: Path) -> None:
    """Spec 015 AK4: one synthetic day at the real row rate - 1440 rounds, 2 markets, 4 intervals,
    four classic and two GPT depots at the hourly cap (4 attempts/h each, worst case)."""
    con = open_db(tmp_path / "bot.db", agents=("trend", BUYHOLD))
    snap_buffer = observed(con, ScenarioClient(), TREND)
    con.close()
    windows = snap_buffer.windows  # real rows: 500-row windows have the same row size
    indicators = js(snap_buffer.decisions[0][7])
    reason = "x" * REASON_MAX
    raw = json.dumps(
        {m: {**entry(), "reason": reason} for m in ("BTC-EUR", "ETH-EUR")}, ensure_ascii=False
    )
    detail = {"elapsed": 600.0, "moves": {"BTC-EUR": 0.0123, "ETH-EUR": -0.004}, "changed": {}}
    cadence = {"min_interval": 3600, "event_cooldown": 600, "move_threshold": 0.018}
    book = QuoteBook(
        {Symbol.BTCEUR: Quote(66000.12, 66005.34), Symbol.ETHEUR: Quote(3200.1, 3200.2)},
        depth={Symbol.BTCEUR: (0.123456, 1.5), Symbol.ETHEUR: (12.3456, 7.5)},
    )
    path = tmp_path / OBS_FILE
    create(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    call_id = 0
    seen: dict[tuple[str, int], tuple[object, ...]] = {}
    for minute in range(1440):
        at = NOW + 60 * minute
        buffer = ObservationBuffer(SHA)
        buffer.begin(at)
        buffer.book("start", at, book)
        buffer.book("snapshot", at, book)
        gpt = minute % 15 == 0  # 4 attempts per hour and depot
        if gpt:
            buffer.book("after_llm", at, book)
        # As live: a window changes once per bar of its interval (the 1m one every round).
        for row in windows:
            bar = minute // int(str(row[2]))
            sha = hashlib.sha256(f"{row[1]}-{row[2]}-{bar}".encode()).hexdigest()
            buffer.windows.append((*row[:8], sha))
        for symbol in (Symbol.BTCEUR, Symbol.ETHEUR):
            buffer.snapshot(symbol, "ok", None, True)
            strategies = ["breakout", "meanrev", "ensemble"] if minute % 15 == 0 else []
            strategies += ["trend"] if minute % 60 == 0 else []
            strategies += ["gpt-luna", "gpt-sol"] if gpt else []
            for agent in strategies:
                buffer.decision(agent, symbol, at + len(agent), "t", agent, "v0.3.1", indicators)
        if gpt:
            for agent in ("gpt-luna", "gpt-sol"):
                call_id += 1
                targets = {"BTCEUR": entry(), "ETHEUR": entry()}
                buffer.llm_attempt("t", call_id, agent, "move", detail, cadence, raw, targets)
        fresh = changed_windows(seen, buffer.windows)
        insert(db, buffer, fresh)
        seen.update({(str(r[1]), int(str(r[2]))): (*r[3:7], r[8]) for r in fresh})
    db.execute("COMMIT")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    totals = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in OBS_TABLES}
    db.close()
    size_mb = os.path.getsize(path) / 1_000_000
    print(f"\nobservations.db, one day: {size_mb:.2f} MB, rows {totals}")
    per_symbol = sum(1440 // interval for interval in (1, 15, 60, 240))
    assert (
        totals["rounds"] == 1440
        and totals["windows"] == 2 * per_symbol == len(windows) // 4 * per_symbol
    )
    assert totals["llm_attempts"] == 2 * 4 * 24
    # Measured 2026-11-08: about 3.9 MB/day with every window written, see the Verlauf of
    # Spec 015 for the figure with change-only windows; the bound catches an accidental blow-up.
    assert size_mb < 4
