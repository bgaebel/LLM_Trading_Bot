"""Spec 022 AK8 (ADR-0032): ``purge-euro-depots`` deletes exactly the rows of the seven euro depots,
once, in one checked transaction, only while it holds the WriterLock; its dry run changes
nothing. Played on databases written by real paper cycles of both releases."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClock, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import EURO_DEPOTS, USDC_AGENTS, BotConfig, load_config
from llm_trading_bot.ops import purge
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import COLUMNS, open_db, write_tx
from llm_trading_bot.paper.execution import Costs
from llm_trading_bot.paper.lock import WriterLock, WriterLockBusy
from llm_trading_bot.paper.usdc import BUYHOLD_USDC, is_usdc

BEFORE = {
    "OPENAI_API_KEY": KEY,
    "BOT_AGENTS": "trend,breakout,meanrev,ensemble,gpt-luna,gpt-sol",
    "BOT_EURO_DEPOTS": "1",
    "BOT_BUYHOLD": "1",
}
"""Every depot of Spec 021 (with buy & hold switched on): the server's history before 022."""
AFTER = {"OPENAI_API_KEY": KEY}
"""The default of Spec 022: only USDC depots, GPT over USDC."""
OTHERS = (*USDC_AGENTS, "gpt-luna-usdc", "gpt-sol-usdc", BUYHOLD_USDC)


def play(con: sqlite3.Connection, config: BotConfig, *steps: int) -> None:
    clock = FakeClock(NOW)
    for step in steps:
        clock.now = NOW + step
        paper_cycle(
            con,
            UsdcScenarioClient("up-calm-plain-s1", "down-calm-plain-s1"),
            config,
            costs=Costs(),
            model=FakeModel(),
            clock=clock,
        )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Rounds of Spec 021, then of Spec 022: rows of every depot in every per-depot table."""
    path = tmp_path / "bot.db"
    before, after = load_config(BEFORE), load_config(AFTER)
    con = open_db(path, agents=before.depots)
    try:
        play(con, before, 0, 61)
        con.close()
        con = open_db(path, agents=after.depots)
        play(con, after, 90, 120)  # while the fake candles are fresh
    finally:
        con.close()
    return path


def rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, every column, as stored (repr keeps floats bit for bit)."""
    con = sqlite3.connect(path)
    try:
        return {
            t: [tuple(repr(v) for v in r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in COLUMNS
        }
    finally:
        con.close()


def split(all_rows: dict[str, list[tuple[Any, ...]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(euro rows, every other row) per table; ``market`` has no depot and is all kept."""
    euro: dict[str, Any] = {}
    kept: dict[str, Any] = {}
    for table, found in all_rows.items():
        if "agent" not in COLUMNS[table]:
            kept[table] = found
            continue
        at = COLUMNS[table].index("agent")
        names = {repr(a) for a in EURO_DEPOTS}
        euro[table] = [r for r in found if r[at] in names]
        kept[table] = [r for r in found if r[at] not in names]
    return euro, kept


def test_the_fixture_has_rows_of_every_depot_in_every_table(db: Path) -> None:
    euro, kept = split(rows(db))
    assert all(euro[t] for t in purge.PER_DEPOT_TABLES), {t: len(v) for t, v in euro.items()}
    assert all(kept[t] for t in COLUMNS)
    con = sqlite3.connect(db)
    agents = {r[0] for r in con.execute("SELECT agent FROM portfolios")}
    called = {r[0] for r in con.execute("SELECT DISTINCT agent FROM llm_calls")}
    con.close()
    assert agents == {*EURO_DEPOTS, *OTHERS}
    assert called == {"gpt-luna", "gpt-sol", "gpt-luna-usdc", "gpt-sol-usdc"}


def test_purge_scope_is_exactly_the_euro_depots() -> None:
    assert EURO_DEPOTS == (
        "trend",
        "breakout",
        "meanrev",
        "ensemble",
        "gpt-luna",
        "gpt-sol",
        "buyhold",
    )
    assert not any(is_usdc(a) for a in EURO_DEPOTS)
    assert purge.PER_DEPOT_TABLES == ("trades", "decisions", "equity", "llm_calls", "portfolios")
    assert {t for t, cols in COLUMNS.items() if "agent" in cols} == set(purge.PER_DEPOT_TABLES)
    assert purge.KEPT_TABLES == ("market",)


def test_dry_run_changes_nothing(db: Path) -> None:
    before = db.read_bytes()
    euro, _ = split(rows(db))
    found = purge.dry_run(db)
    assert found == {t: len(euro[t]) for t in purge.PER_DEPOT_TABLES}
    assert all(found.values())
    assert db.read_bytes() == before
    assert purge.dry_run(db) == found  # and again: still the same


def test_execute_removes_exactly_the_euro_rows(db: Path) -> None:
    euro, kept = split(rows(db))
    expected = purge.dry_run(db)
    assert purge.execute(db) == expected == {t: len(euro[t]) for t in purge.PER_DEPOT_TABLES}
    after_euro, after_kept = split(rows(db))
    assert all(not v for v in after_euro.values())
    assert after_kept == kept  # every other row, ids and floats included, and the market table
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        con.close()
    # The release of Spec 022 goes on on the purged database without recreating a euro depot.
    after = load_config(AFTER)
    con = open_db(db, agents=after.depots)
    try:
        play(con, after, 3_822)
        agents = {r[0] for r in con.execute("SELECT agent FROM portfolios")}
        assert agents == set(OTHERS)
    finally:
        con.close()


def test_execute_is_refused_while_the_lock_is_held(db: Path) -> None:
    before = db.read_bytes()
    with WriterLock(db), pytest.raises(WriterLockBusy):
        purge.execute(db)
    assert db.read_bytes() == before
    # The dry run needs no lock: it only reads (ADR-0016).
    with WriterLock(db):
        assert all(purge.dry_run(db).values())


def test_second_run_deletes_nothing(db: Path) -> None:
    purge.execute(db)
    state = rows(db)
    assert purge.execute(db) == dict.fromkeys(purge.PER_DEPOT_TABLES, 0)
    assert purge.dry_run(db) == dict.fromkeys(purge.PER_DEPOT_TABLES, 0)
    assert rows(db) == state


def test_wrong_schema_is_refused(db: Path, tmp_path: Path) -> None:
    con = sqlite3.connect(db)
    with con:
        con.execute("PRAGMA user_version=3")
    con.close()
    state = rows(db)
    for run in (purge.dry_run, purge.execute):
        with pytest.raises(purge.PurgeRefused, match="schema version 3"):
            run(db)
    assert rows(db) == state
    missing = tmp_path / "none" / "bot.db"
    for run in (purge.dry_run, purge.execute):
        with pytest.raises(purge.PurgeRefused):
            run(missing)
    assert not missing.exists() and not missing.parent.exists()


def test_foreign_reference_to_a_euro_decision_is_refused(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        with con:
            (decision,) = con.execute(
                "SELECT id FROM decisions WHERE agent='trend' ORDER BY id LIMIT 1"
            ).fetchone()
            con.execute(
                "INSERT INTO trades(ts,agent,symbol,side,qty,fill,notional,fee,slip_rate,"
                "decision_id) VALUES ('2026-01-01T00:00:00+00:00','trend-usdc','ETHEUR','BUY',"
                "1,1,1,0,0,?)",
                (decision,),
            )
    finally:
        con.close()
    state = rows(db)
    for run in (purge.dry_run, purge.execute):
        with pytest.raises(purge.PurgeRefused, match="refers to a euro decision"):
            run(db)
    assert rows(db) == state


def test_a_failed_check_rolls_every_delete_back(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The checks after the deletes run inside the transaction: one failing undoes them all."""
    state = rows(db)
    real = purge.kept_digest
    calls: list[str] = []

    def drifting(con: sqlite3.Connection) -> str:
        calls.append("x")
        return real(con) + ("" if len(calls) == 1 else "changed")

    monkeypatch.setattr(purge, "kept_digest", drifting)
    with pytest.raises(purge.PurgeRefused, match="another depot or a market changed"):
        purge.execute(db)
    assert rows(db) == state


def test_kept_digest_sees_every_other_row(db: Path) -> None:
    con = sqlite3.connect(db, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        base = purge.kept_digest(con)
        for table, change in (
            ("equity", "UPDATE equity SET value=value+1e-9 WHERE agent='trend-usdc'"),
            ("market", "UPDATE market SET bid=bid*(1+1e-12) WHERE symbol='BTCUSDC'"),
            ("llm_calls", "UPDATE llm_calls SET error='x' WHERE agent='gpt-sol-usdc'"),
        ):
            with write_tx(con):
                con.execute(change)
            assert purge.kept_digest(con) != base, table
            base = purge.kept_digest(con)
        with write_tx(con):
            con.execute("UPDATE equity SET value=value+1 WHERE agent='trend'")  # a euro row
        assert purge.kept_digest(con) == base
    finally:
        con.close()
