"""Spec 020 AK4/AK5: the USDC depots run beside the v0.3.1 depots without touching them, buy &
hold USDC buys once, everything is valued in euro, and a missing USDC quote stops only them."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClock, ScenarioClient, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY
from llm_trading_bot.llm.agents import (
    CLASSIC_AGENTS,
    LLM_DEFAULTS,
    USDC_AGENTS,
    USDC_FIELD,
    BotConfig,
    load_config,
)
from llm_trading_bot.market.models import QUOTED, MarketDataError, Quote, Symbol
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.cycle import paper_cycle, quote_problem
from llm_trading_bot.paper.db import START_CASH, balance, open_db, write_tx
from llm_trading_bot.paper.execution import (
    BUYHOLD,
    BUYHOLD_USDC_MARKETS,
    Costs,
    fresh_quotes,
    initialize_buyhold_usdc,
)
from llm_trading_bot.paper.risk import Controls
from llm_trading_bot.web.credentials import SecretsFile, load_env_file

CLASSIC = ("trend", "breakout", "meanrev", "ensemble")
# Written before Spec 021: with the euro buy & hold depot beside them (off by default since).
ALL_USDC = BotConfig(CLASSIC, None, USDC_AGENTS, buyhold=True)
BUYHOLD_ON = {"BOT_BUYHOLD": "1"}


def test_config_knows_the_usdc_depots_and_adds_their_benchmark() -> None:
    assert USDC_AGENTS == ("trend-usdc", "breakout-usdc", "meanrev-usdc", "ensemble-usdc")
    config = load_config(
        {**BUYHOLD_ON, "BOT_AGENTS": "trend", USDC_FIELD: "meanrev-usdc,trend-usdc"}
    )
    assert (config.classic, config.usdc) == (("trend",), ("meanrev-usdc", "trend-usdc"))
    assert config.agents == ("trend", "meanrev-usdc", "trend-usdc")
    assert config.depots == (*config.agents, BUYHOLD, usdc.BUYHOLD_USDC)
    plain = load_config({**BUYHOLD_ON, "BOT_AGENTS": "trend", USDC_FIELD: ""})
    assert plain.usdc == () and plain.depots == ("trend", BUYHOLD)
    # The benchmark runs by itself and cannot be chosen; nor can a depot of the other list.
    for wrong, message in (
        ("buyhold-usdc", "unknown depots"),
        ("trend", "unknown depots"),
        ("trend-usdc,trend-usdc", "twice"),
    ):
        with pytest.raises(ValueError, match=message):
            load_config({"BOT_AGENTS": "trend", USDC_FIELD: wrong})


def test_usdc_depots_are_on_by_default_also_beside_an_explicit_depot_list() -> None:
    """AK4 "standardmäßig an": the server's secrets.env holds an explicit BOT_AGENTS from before
    Spec 020, so the deploy alone must start them (AK10); only a stored choice turns them off."""
    for env in ({"BOT_AGENTS": "trend,gpt-sol", "OPENAI_API_KEY": KEY}, {"OPENAI_API_KEY": KEY}):
        config = load_config(env)
        assert config.usdc == USDC_AGENTS and usdc.BUYHOLD_USDC in config.depots
    assert load_config({"BOT_AGENTS": "trend", USDC_FIELD: " "}).usdc == ()


PRE_020_DEPOTS = ("trend", "breakout", "meanrev", "ensemble", "gpt-luna", "gpt-sol")
"""What load_config of a release from before Spec 020 (e85a8d5) accepts in BOT_AGENTS."""


def test_usdc_choice_never_reaches_bot_agents_so_a_rollback_still_starts(tmp_path: Path) -> None:
    """A rollback (deploy.sh ... rollback) to a release from before Spec 020 reads the same
    secrets.env: an unknown name in BOT_AGENTS would stop every depot, GPT included."""
    assert set(PRE_020_DEPOTS) == {*CLASSIC_AGENTS, *LLM_DEFAULTS}
    secrets = SecretsFile(tmp_path / "secrets.env")
    stored = secrets.update({}, agents=["ensemble-usdc", "trend", "trend-usdc"])
    assert (stored["BOT_AGENTS"], stored[USDC_FIELD]) == ("trend", "trend-usdc,ensemble-usdc")
    assert set(load_env_file(secrets.path)["BOT_AGENTS"].split(",")) <= set(PRE_020_DEPOTS)
    assert load_config(secrets.read()).usdc == ("trend-usdc", "ensemble-usdc")
    # Switched off, "none" is stored, so the default does not turn them on again.
    assert secrets.update({}, agents=["trend"])[USDC_FIELD] == ""
    assert load_config(secrets.read()).usdc == ()
    # Only USDC depots would leave BOT_AGENTS empty, which the old release refuses too.
    with pytest.raises(ValueError, match="besides the USDC depots"):
        secrets.update({}, agents=["trend-usdc"])
    with pytest.raises(ValueError, match=f"belong in {USDC_FIELD}"):
        load_config({"BOT_AGENTS": "trend,trend-usdc"})


def db(path: Path, config: BotConfig) -> sqlite3.Connection:
    return open_db(path, agents=config.depots)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db(tmp_path / "bot.db", ALL_USDC)
    yield connection
    connection.close()


def test_every_usdc_depot_starts_with_10000_euro(con: sqlite3.Connection) -> None:
    for agent in (*USDC_AGENTS, usdc.BUYHOLD_USDC):
        p = balance(con, agent)
        assert (p["cash"], p["btc"], p["eth"], p["peak"]) == (START_CASH, 0.0, 0.0, START_CASH)
    assert START_CASH == 10_000.0


def trades(con: sqlite3.Connection, agent: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM trades WHERE agent=? ORDER BY id", (agent,)).fetchall()
    return [dict(r) for r in rows]


def test_buyhold_usdc_buys_once_btc_over_the_route_and_eth_on_eth_eur(
    con: sqlite3.Connection,
) -> None:
    client = UsdcScenarioClient()
    report = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW)
    assert (report.buyhold_bought, report.buyhold_usdc_bought) == (2, 2)
    btc, eth = trades(con, usdc.BUYHOLD_USDC)
    assert (btc["symbol"], btc["side"], btc["notional"]) == ("BTCUSDC", "BUY", 4_500.0)
    # 45 % of the equity after the BTC purchase, whose fees and slippage lowered it a little.
    assert (eth["symbol"], eth["side"]) == ("ETHEUR", "BUY") and 4_490 < eth["notional"] < 4_500
    assert btc["fee"] == pytest.approx(4_500.0 * 0.0015, rel=1e-12)
    assert eth["fee"] == pytest.approx(eth["notional"] * 0.0025, rel=1e-12)
    decisions = con.execute(
        "SELECT symbol, bar_ts, model, status FROM decisions WHERE agent=? ORDER BY id",
        (usdc.BUYHOLD_USDC,),
    ).fetchall()
    assert [tuple(d) for d in decisions] == [
        ("BTCEUR", -1, "BUY&HOLD", "OK"),
        ("ETHEUR", -1, "BUY&HOLD", "OK"),
    ]
    again = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 5)
    assert again.buyhold_usdc_bought == 0 and len(trades(con, usdc.BUYHOLD_USDC)) == 2


def test_buyhold_usdc_waits_for_its_quotes_and_for_the_end_of_a_pause(
    con: sqlite3.Connection,
) -> None:
    client = UsdcScenarioClient()
    client.drop_usdc = {Symbol.BTCUSDC}
    missing = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW)
    assert missing.buyhold_bought == 2 and missing.buyhold_usdc_bought == 0
    client.drop_usdc = set()
    paused = paper_cycle(
        con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 1, controls=Controls(paused=True)
    )
    assert paused.buyhold_usdc_bought == 0
    bought = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 2)
    assert bought.buyhold_usdc_bought == 2


def test_without_usdc_depots_there_is_no_usdc_benchmark(tmp_path: Path) -> None:
    config = BotConfig(("trend",), None, buyhold=True)
    con = db(tmp_path / "bot.db", config)
    try:
        report = paper_cycle(con, UsdcScenarioClient(), config, costs=Costs(), clock=lambda: NOW)
        assert report.buyhold_usdc_bought == 0
        assert [e.agent for e in report.equity] == ["trend", BUYHOLD]
        agents = {r[0] for r in con.execute("SELECT agent FROM portfolios")}
        assert agents == {"trend", BUYHOLD}
    finally:
        con.close()


def rows_of(con: sqlite3.Connection, agents: tuple[str, ...]) -> dict[str, list[Any]]:
    """Every row of ``agents`` without the ids, which interleaved depots shift."""
    marks = ",".join("?" for _ in agents)
    out: dict[str, list[Any]] = {}
    for table, cols in (
        ("portfolios", "agent,cash,btc,eth,peak,halted"),
        ("decisions", "ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status"),
        ("trades", "ts,agent,symbol,side,qty,fill,notional,fee,slip_rate"),
        ("equity", "ts,agent,value,cash,btc,eth"),
    ):
        order = "agent" if table == "portfolios" else "agent, ts, id"
        query = f"SELECT {cols} FROM {table} WHERE agent IN ({marks}) ORDER BY {order}"
        out[table] = [[repr(v) for v in r] for r in con.execute(query, agents)]
    return out


@pytest.mark.parametrize(
    ("btc", "eth"),
    [("up-calm-plain-s1", "up-calm-plain-s1"), ("down-calm-plain-s1", "up-wild-spike-s1")],
)
def test_v031_depots_and_buyhold_are_byte_identical_with_usdc_depots_beside_them(
    tmp_path: Path, btc: str, eth: str
) -> None:
    v031 = BotConfig(CLASSIC, None, buyhold=True)
    seen = {}
    for name, config, client in (
        ("alone", v031, ScenarioClient(btc, eth)),
        ("beside", ALL_USDC, UsdcScenarioClient(btc, eth)),
    ):
        con = db(tmp_path / f"{name}.db", config)
        try:
            clock = FakeClock(NOW)
            for step in (0, 7, 31, 45):
                clock.now = NOW + step
                paper_cycle(con, client, config, costs=Costs(), clock=clock)
            seen[name] = rows_of(con, (*CLASSIC, BUYHOLD))
            if name == "beside":
                assert trades(con, "trend-usdc")  # the USDC depots did trade meanwhile
        finally:
            con.close()
    assert seen["alone"] == seen["beside"]
    assert seen["alone"]["trades"] and seen["alone"]["equity"]


def test_usdc_depot_is_valued_in_euro_over_both_mids(con: sqlite3.Connection) -> None:
    client = UsdcScenarioClient()
    report = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW)
    p = balance(con, "trend-usdc")
    assert p["btc"] > 0
    btc_mid = client.usdc_quotes[Symbol.BTCUSDC].mid * client.usdc_quotes[Symbol.USDCEUR].mid
    eth_mid = ScenarioClient().quotes()[Symbol.ETHEUR].mid
    equity = next(e for e in report.equity if e.agent == "trend-usdc")
    assert equity.value == pytest.approx(p["cash"] + p["btc"] * btc_mid + p["eth"] * eth_mid)
    assert equity.value < START_CASH  # fees and slippage are booked in euro


def test_missing_usdc_quote_blocks_only_usdc_depots(con: sqlite3.Connection) -> None:
    client = UsdcScenarioClient()
    client.drop_usdc = {Symbol.USDCEUR}
    first = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW)
    by = {(m.agent, m.symbol): m for m in first.markets}
    # BTC of a USDC depot has no price: no decision, its bar stays open; ETH decides as usual.
    for agent in USDC_AGENTS:
        assert by[(agent, Symbol.BTCEUR)].error == (
            "MarketDataError: USDC-EUR: no fresh quote - no decision"
        )
        assert by[(agent, Symbol.BTCEUR)].bar_ts is not None and not by[(agent, Symbol.BTCEUR)].new
        assert not by[(agent, Symbol.ETHEUR)].error
    for agent in CLASSIC:
        assert not by[(agent, Symbol.BTCEUR)].error and not by[(agent, Symbol.ETHEUR)].error
    assert first.buyhold_bought == 2 and first.buyhold_usdc_bought == 0
    assert "USDC-EUR" in first.quotes_error

    # Once holding BTC, a stale USDC-EUR quote leaves the USDC depots without decision and
    # equity point, while the v0.3.1 depots and buy & hold go on.
    client.drop_usdc = set()
    second = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 1)
    assert second.buyhold_usdc_bought == 2 and balance(con, "trend-usdc")["btc"] > 0
    client.drop_usdc = {Symbol.USDCEUR}
    # 39 s after the last stored USDC-EUR quote: stale (QUOTE_MAX_AGE 20 s), so fail closed.
    third = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 40)
    holders = {a for a in (*USDC_AGENTS, usdc.BUYHOLD_USDC) if balance(con, a)["btc"] > 0}
    assert {"trend-usdc", usdc.BUYHOLD_USDC} <= holders
    assert {m.agent for m in third.unvalued} == holders
    assert {m.error for m in third.unvalued} == {
        "MarketDataError: USDC-EUR: no fresh quote for the held position - no equity point"
    }
    assert {e.agent for e in third.equity} == {*CLASSIC, BUYHOLD, *(set(USDC_AGENTS) - holders)}
    # A holder decides in neither market: the depot's value is unknown.
    stale = fresh_quotes(con, now_epoch=NOW + 40, markets=QUOTED)
    assert Symbol.USDCEUR not in stale and Symbol.BTCUSDC in stale
    p = balance(con, "trend-usdc")
    assert quote_problem(p, Symbol.ETHEUR, stale) == (
        "MarketDataError: USDC-EUR: no fresh quote for the held position"
    )
    assert quote_problem(balance(con, "trend"), Symbol.ETHEUR, stale) == ""


def test_drawdown_stop_of_a_usdc_depot_sells_over_the_route_and_spares_its_benchmark(
    con: sqlite3.Connection,
) -> None:
    client = UsdcScenarioClient()
    paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW)
    with write_tx(con):
        con.execute(
            "UPDATE portfolios SET peak=20000 WHERE agent IN (?, ?)",
            ("trend-usdc", usdc.BUYHOLD_USDC),
        )
    report = paper_cycle(con, client, ALL_USDC, costs=Costs(), clock=lambda: NOW + 5)
    assert {s.agent for s in report.risk_stops} >= {"trend-usdc"}
    assert usdc.BUYHOLD_USDC not in {s.agent for s in report.risk_stops}
    sale = [t for t in trades(con, "trend-usdc") if t["side"] == "RISK_SELL"]
    assert {t["symbol"] for t in sale} == {"BTCUSDC", "ETHEUR"}
    p = balance(con, "trend-usdc")
    assert p["halted"] == 1 and p["btc"] == 0.0 and p["eth"] == 0.0
    assert balance(con, usdc.BUYHOLD_USDC)["halted"] == 0


def test_buyhold_usdc_refuses_missing_or_broken_quotes(con: sqlite3.Connection) -> None:
    client = UsdcScenarioClient()
    book = {**client.quotes()}
    for market in BUYHOLD_USDC_MARKETS:
        without = {s: q for s, q in book.items() if s is not market}
        with pytest.raises(MarketDataError, match="needs fresh quotes"):
            initialize_buyhold_usdc(con, without, Costs(), when="2026-11-08T12:00:00+00:00")
    crossed = {**book, Symbol.USDCEUR: Quote(0.9, 0.8)}
    with pytest.raises(MarketDataError, match="invalid bid/ask"):
        initialize_buyhold_usdc(con, crossed, Costs(), when="2026-11-08T12:00:00+00:00")
    assert trades(con, usdc.BUYHOLD_USDC) == []
    # BTC-EUR is not needed: the benchmark's BTC is priced over the route.
    del book[Symbol.BTCEUR]
    assert initialize_buyhold_usdc(con, book, Costs(), when="2026-11-08T12:00:00+00:00") == 2


def test_usdc_depots_alone_decide_and_trade(tmp_path: Path) -> None:
    config = BotConfig((), None, ("trend-usdc",), buyhold=True)
    con = db(tmp_path / "bot.db", config)
    try:
        report = paper_cycle(con, UsdcScenarioClient(), config, costs=Costs(), clock=lambda: NOW)
        by = {(m.agent, m.symbol): m for m in report.markets}
        assert by[("trend-usdc", Symbol.BTCEUR)].action == "BUY"
        assert [t["symbol"] for t in trades(con, "trend-usdc")][:1] == ["BTCUSDC"]
        assert [e.agent for e in report.equity] == ["trend-usdc", BUYHOLD, usdc.BUYHOLD_USDC]
    finally:
        con.close()
