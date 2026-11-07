"""Spec 015 AK2: the GPT trigger has a reason, and its truth value is exactly the old bool."""

import itertools
import json
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path

import pytest

from llm_trading_bot.llm.due import (
    DUE_REASONS,
    Cadence,
    ReferenceSignals,
    due_reason,
    iso,
    llm_due,
    trigger_state,
)
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.db import open_db, write_tx

T = 1_793_000_000
CADENCE = Cadence.from_env({})
CALM = {"trend": "HOLD", "breakout": "HOLD", "meanrev": "HOLD"}
BUY = {"trend": "BUY", "breakout": "HOLD", "meanrev": "HOLD"}
_REF = {Symbol.BTCEUR: "ref_mid_btc", Symbol.ETHEUR: "ref_mid_eth"}


def old_llm_due(
    con: sqlite3.Connection,
    agent: str,
    mids: Mapping[Symbol, float],
    signals: ReferenceSignals,
    now_epoch: float,
    cadence: Cadence,
) -> bool:
    """llm_due as it was before Spec 015 (logic of commit 3c00c83) - the reference."""
    row = con.execute(
        "SELECT ts,ref_mid_btc,ref_mid_eth,trigger_signals FROM llm_calls WHERE agent=?"
        " ORDER BY id DESC LIMIT 1",
        (agent,),
    ).fetchone()
    if not row:
        return True
    elapsed = max(0, now_epoch - datetime.fromisoformat(row["ts"]).timestamp())
    if elapsed >= cadence.min_interval:
        return True
    if elapsed < cadence.event_cooldown:
        return False
    for symbol, mid in mids.items():
        ref = row[_REF[symbol]]
        if ref is not None and ref > 0 and abs(mid / ref - 1) >= cadence.move_threshold:
            return True
    try:
        previous = json.loads(row["trigger_signals"])
    except (ValueError, TypeError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    return any(previous.get(symbol.market) != dict(now) for symbol, now in signals.items())


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=("gpt-luna", "buyhold"))
    yield connection
    connection.close()


def store(con: sqlite3.Connection, at: float, btc: float | None, signals: str) -> None:
    with write_tx(con):
        con.execute("DELETE FROM llm_calls")
        con.execute(
            "INSERT INTO llm_calls(ts,agent,markets,ref_mid_btc,ref_mid_eth,status,model,"
            "prompt_version,prompt_sha256,max_output_tokens,effort,context,trigger_signals)"
            " VALUES (?,'gpt-luna','BTC-EUR,ETH-EUR',?,10.0,'OK','m','llm-v1','h',8000,'low',"
            "'{}',?)",
            (iso(at), btc, signals),
        )


ELAPSED = (0, 299, 300, 599, 600, 601, 3599, 3600, 7200)
MOVES = (0.0, 0.0179, 0.018, -0.018, 0.05)  # BTC mid against its reference 100
REFS = (100.0, None, 0.0)
STORED = (
    trigger_state({Symbol.BTCEUR: CALM, Symbol.ETHEUR: CALM}),
    trigger_state({Symbol.BTCEUR: BUY, Symbol.ETHEUR: CALM}),  # BTC signal changed
    trigger_state({Symbol.BTCEUR: CALM}),  # ETH was not part of the last request
    "not json",
    "[]",
)


def test_reason_truth_equals_old_bool(con: sqlite3.Connection) -> None:
    """Time x move x reference x stored signals x markets, also without any attempt."""
    signals = {Symbol.BTCEUR: CALM, Symbol.ETHEUR: CALM}
    cadences = (CADENCE, Cadence(900, 300, 0.005, 4))
    checked = 0
    for cadence in cadences:
        mids = {Symbol.BTCEUR: 100.0, Symbol.ETHEUR: 10.0}
        assert due_reason(con, "gpt-luna", mids, signals, T, cadence)[0] == "first"
        assert old_llm_due(con, "gpt-luna", mids, signals, T, cadence)
    for elapsed, move, ref, stored, cadence, markets in itertools.product(
        ELAPSED, MOVES, REFS, STORED, cadences, ((Symbol.BTCEUR, Symbol.ETHEUR), (Symbol.BTCEUR,))
    ):
        store(con, T, ref, stored)
        mids = {s: {Symbol.BTCEUR: 100.0 * (1 + move), Symbol.ETHEUR: 10.0}[s] for s in markets}
        now = {s: signals[s] for s in markets}
        reason, _detail = due_reason(con, "gpt-luna", mids, now, T + elapsed, cadence)
        old = old_llm_due(con, "gpt-luna", mids, now, T + elapsed, cadence)
        assert (reason is not None) is old, (elapsed, move, ref, stored, cadence, markets)
        assert llm_due(con, "gpt-luna", mids, now, T + elapsed, cadence) is old
        assert reason is None or reason in DUE_REASONS
        checked += 1
    assert checked == len(ELAPSED) * len(MOVES) * len(REFS) * len(STORED) * 2 * 2


def test_each_reason_with_its_values(con: sqlite3.Connection) -> None:
    calm = {Symbol.BTCEUR: CALM, Symbol.ETHEUR: CALM}
    mids = {Symbol.BTCEUR: 100.0, Symbol.ETHEUR: 10.0}
    store(con, T, 100.0, trigger_state(calm))
    assert due_reason(con, "gpt-luna", mids, calm, T + 3600, CADENCE) == (
        "interval",
        {"elapsed": 3600.0},
    )
    reason, detail = due_reason(con, "gpt-luna", mids, calm, T + 100, CADENCE)
    assert (reason, detail) == (None, {"elapsed": 100.0})
    moved = {Symbol.BTCEUR: 102.0, Symbol.ETHEUR: 10.0}
    reason, detail = due_reason(con, "gpt-luna", moved, calm, T + 600, CADENCE)
    assert reason == "move" and detail["elapsed"] == 600.0
    assert detail["moves"] == pytest.approx({"BTC-EUR": 0.02, "ETH-EUR": 0.0})
    changed = {Symbol.BTCEUR: BUY, Symbol.ETHEUR: CALM}
    reason, detail = due_reason(con, "gpt-luna", mids, changed, T + 600, CADENCE)
    assert reason == "signal_change"
    assert detail["changed"] == {"BTC-EUR": {"before": CALM, "now": BUY}}
    store(con, T, 100.0, trigger_state({Symbol.BTCEUR: CALM}))
    reason, detail = due_reason(con, "gpt-luna", mids, calm, T + 600, CADENCE)
    assert reason == "new_market"
    assert detail["changed"] == {"ETH-EUR": {"before": None, "now": CALM}}
    reason, detail = due_reason(con, "gpt-luna", mids, {Symbol.BTCEUR: CALM}, T + 600, CADENCE)
    assert (reason, detail["changed"]) == (None, {})
    json.dumps(detail)  # stored as JSON in the observations


def test_first_attempt_has_no_values(con: sqlite3.Connection) -> None:
    mids = {Symbol.BTCEUR: 100.0}
    assert due_reason(con, "gpt-luna", mids, {Symbol.BTCEUR: CALM}, T, CADENCE) == ("first", {})
