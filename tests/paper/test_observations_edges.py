"""Spec 015 AK3/AK4 (ADR-0027): edge cases of recording - rejected quotes, blocked and refused
GPT attempts, failing rounds, repeated keys, the file's creation, journal mode and errors."""

import sqlite3
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
from llm_trading_bot.paper import cycle as cycle_module
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import (
    ObservationBuffer,
    ObservationWriter,
    rejected_purpose,
)
from llm_trading_bot.paper.observations_schema import OBS_FILE

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
        {"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna", "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "0"}
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

    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna"})
    buffer = observed(db, ScenarioClient(), config, FakeModel({"gpt-6-luna": refuses}))
    (attempt,) = buffer.attempts
    assert attempt[6] == f"{REFUSAL_MARK}I cannot help" == "[refusal] I cannot help"
    assert buffer.decisions == []


def test_a_refusal_with_text_keeps_the_text(db: sqlite3.Connection) -> None:
    def refuses(markets: list[str]) -> ModelReply:
        usage = valid_reply(markets).usage
        return ModelReply("completed", "partial", "no", None, "r", None, usage)

    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": "gpt-luna"})
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
