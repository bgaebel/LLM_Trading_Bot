"""Spec 020 AK2: BTC of a USDC depot is bought EUR -> USDC -> BTC and sold back, each leg at its
own book with its own fee and slippage; cash, limits and fees stay in euro; ETH is unchanged.

The expected numbers are written out by hand (no constants of ``paper.usdc``), so a changed fee,
slippage, side of the book or a forgotten leg shows up here.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import golden_execution_cases as cases
from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.db import balance, open_db, write_tx
from llm_trading_bot.paper.execution import (
    Costs,
    covers,
    current_weight,
    mark,
    record_decision,
    trade_to_target,
    unquoted_holdings,
    value,
)
from llm_trading_bot.strategy.signals import Signal

BTC_USDC = Quote(73_500.0, 73_515.0)
USDC_EUR = Quote(0.8975, 0.8976)
ETH_EUR = Quote(3_200.0, 3_200.2)
QS = {Symbol.BTCUSDC: BTC_USDC, Symbol.USDCEUR: USDC_EUR, Symbol.ETHEUR: ETH_EUR}
AGENT = "trend-usdc"
NOW = "2026-11-08T12:00:00+00:00"


def close(actual: float, expected: float) -> None:
    assert actual == pytest.approx(expected, rel=1e-12, abs=0.0)


# ------------------------------------------------------------------ the legs, by hand


def test_buy_converts_euro_to_usdc_at_the_ask_then_buys_btc_at_the_ask() -> None:
    done = usdc.buy(1_000.0, QS)
    eur_per_usdc = 0.8976 * 1.0001  # leg 1: USDC-EUR ask + 0.01 % slippage
    usdc_amount = 1_000.0 / eur_per_usdc
    usdc_per_btc = 73_515.0 * 1.0005  # leg 2: BTC-USDC ask + 0.05 % slippage
    close(done.qty, usdc_amount / usdc_per_btc)
    close(done.fill, eur_per_usdc * usdc_per_btc)
    assert done.notional == 1_000.0
    # 0.10 % on the euro amount (leg 1) + 0.05 % on the USDC amount, in euro (leg 2)
    close(done.fee, 1_000.0 * 0.0010 + usdc_amount * 0.0005 * eur_per_usdc)
    close(done.fee, 1.5)


def test_sell_sells_btc_at_the_bid_then_converts_usdc_at_the_bid() -> None:
    done = usdc.sell(0.01, QS)
    usdc_per_btc = 73_500.0 * 0.9995  # leg 1: BTC-USDC bid - 0.05 % slippage
    usdc_amount = 0.01 * usdc_per_btc
    eur_per_usdc = 0.8975 * 0.9999  # leg 2: USDC-EUR bid - 0.01 % slippage
    assert done.qty == 0.01
    close(done.notional, usdc_amount * eur_per_usdc)
    close(done.fill, usdc_per_btc * eur_per_usdc)
    close(done.fee, usdc_amount * 0.0005 * eur_per_usdc + usdc_amount * eur_per_usdc * 0.0010)


def test_btc_is_valued_at_both_mids_in_euro() -> None:
    close(usdc.btc_mid(QS), 73_507.5 * 0.89755)
    p = {"agent": AGENT, "cash": 100.0, "btc": 0.5, "eth": 2.0}
    close(value(p, QS), 100.0 + 0.5 * 73_507.5 * 0.89755 + 2.0 * 3_200.1)
    close(current_weight(p, Symbol.BTCEUR, QS), 0.5 * 73_507.5 * 0.89755 / value(p, QS))


def test_a_round_trip_over_the_route_costs_less_than_over_btc_eur() -> None:
    bought = usdc.buy(4_500.0, QS)
    sold = usdc.sell(bought.qty, QS)
    route_loss = 4_500.0 + bought.fee - (sold.notional - sold.fee)
    # Same move over BTC-EUR at the same euro price with the v0.3.1 costs (0.25 % + 0.05 %).
    btc_eur = Quote(73_500.0 * 0.8975, 73_515.0 * 0.8976)
    qty = 4_500.0 / (btc_eur.ask * 1.0005)
    eur_loss = 4_500.0 * 1.0025 - qty * btc_eur.bid * 0.9995 * (1 - 0.0025)
    assert route_loss < eur_loss
    # Fees and slippage of both legs, both sides: 0.42 % of the amount plus the two spreads.
    spreads = (15 / 73_507.5) + (0.0001 / 0.89755)
    assert route_loss / 4_500.0 == pytest.approx(0.0042 + spreads, rel=2e-3)


# ------------------------------------------------------------------ in the paper database


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "bot.db", agents=(AGENT, "trend"))
    yield connection
    connection.close()


def decide(
    con: sqlite3.Connection,
    symbol: Symbol,
    action: str,
    target: float,
    qs: dict[Symbol, Quote] | None,
    bar: int,
    *,
    partial: bool = True,
) -> bool:
    sig = Signal(action, target, 0.7, "test", "trend", usdc.USDC_VERSION)  # type: ignore[arg-type]
    return record_decision(
        con, AGENT, symbol, bar, "TREND-USDC", sig, {}, costs=Costs(), now=NOW, qs=qs,
        partial=partial,
    )  # fmt: skip


def trades(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r) for r in con.execute("SELECT * FROM trades WHERE agent=? ORDER BY id", (AGENT,))
    ]


def test_buy_and_sell_of_btc_are_stored_in_euro_as_market_btc_usdc(con: sqlite3.Connection) -> None:
    assert decide(con, Symbol.BTCEUR, "BUY", 0.45, QS, 1)
    (buy,) = trades(con)
    expected = usdc.buy(4_500.0, QS)  # 45 % of 10 000 EUR, inside the 90 % room
    assert (buy["symbol"], buy["side"], buy["notional"]) == ("BTCUSDC", "BUY", 4_500.0)
    close(buy["qty"], 4_500.0 / (0.8976 * 1.0001) / (73_515.0 * 1.0005))
    close(buy["fill"], 0.8976 * 1.0001 * 73_515.0 * 1.0005)
    close(buy["fee"], 4_500.0 * 0.0015)
    close(buy["slip_rate"], 0.0006)
    p = balance(con, AGENT)
    close(p["cash"], 10_000.0 - 4_500.0 - 6.75)
    assert p["btc"] == expected.qty and p["eth"] == 0.0

    assert decide(con, Symbol.BTCEUR, "SELL", 0.0, QS, 2)
    sell = trades(con)[1]
    assert (sell["symbol"], sell["side"]) == ("BTCUSDC", "SELL")
    assert sell["qty"] == expected.qty  # everything: the target is 0
    close(sell["notional"], expected.qty * 73_500.0 * 0.9995 * 0.8975 * 0.9999)
    close(sell["fee"], sell["notional"] * 0.0015)
    after = balance(con, AGENT)
    close(after["cash"], p["cash"] + sell["notional"] - sell["fee"])
    assert after["btc"] == 0.0


def test_eth_of_a_usdc_depot_keeps_the_v031_route_and_costs(con: sqlite3.Connection) -> None:
    assert decide(con, Symbol.ETHEUR, "BUY", 0.25, QS, 1)
    (buy,) = trades(con)
    assert (buy["symbol"], buy["notional"]) == ("ETHEUR", 2_500.0)
    close(buy["fill"], 3_200.2 * 1.0005)
    close(buy["fee"], 2_500.0 * 0.0025)
    assert buy["slip_rate"] == 0.0005


def test_minimum_trade_and_caps_stay_in_euro(con: sqlite3.Connection) -> None:
    with write_tx(con):
        # 40 % ETH already: only 50 % of equity is left for BTC under the 90 % total cap, the
        # 45 % symbol cap binds; a second BUY to 45 % is inside the 2 % band and is skipped.
        con.execute(
            "UPDATE portfolios SET cash=6000, eth=? WHERE agent=?", (4_000 / 3_200.1, AGENT)
        )
        trade_to_target(con, AGENT, Symbol.BTCEUR, 0.45, QS, None, Costs(), executed_at=NOW)
        trade_to_target(con, AGENT, Symbol.BTCEUR, 0.45, QS, None, Costs(), executed_at=NOW)
    (buy,) = trades(con)
    close(buy["notional"], 0.45 * 10_000.0)
    with write_tx(con):
        con.execute("UPDATE portfolios SET cash=60, btc=0, eth=0 WHERE agent=?", (AGENT,))
        trade_to_target(con, AGENT, Symbol.BTCEUR, 0.45, QS, None, Costs(), executed_at=NOW)
    assert len(trades(con)) == 1  # 27 EUR would be below the 50 EUR minimum


def test_without_a_route_quote_the_btc_decision_is_stale_and_nothing_trades(
    con: sqlite3.Connection,
) -> None:
    eur_only = {Symbol.BTCEUR: Quote(66_000.0, 66_005.0), Symbol.ETHEUR: ETH_EUR}
    # Exactly the v0.3.1 markets: complete for v0.3.1, not for the USDC route - also unpartial.
    assert decide(con, Symbol.BTCEUR, "BUY", 0.45, eur_only, 1, partial=False)
    no_usdc_eur = {Symbol.BTCUSDC: BTC_USDC, Symbol.ETHEUR: ETH_EUR}
    assert decide(con, Symbol.BTCEUR, "BUY", 0.45, no_usdc_eur, 2)
    statuses = [r[0] for r in con.execute("SELECT status FROM decisions WHERE agent=?", (AGENT,))]
    assert statuses == ["STALE_QUOTE", "STALE_QUOTE"] and trades(con) == []


def test_held_btc_without_route_quotes_blocks_valuation_and_eth(con: sqlite3.Connection) -> None:
    p = {"agent": AGENT, "cash": 100.0, "btc": 0.1, "eth": 0.0}
    eth_only = {Symbol.ETHEUR: ETH_EUR, Symbol.BTCEUR: Quote(66_000.0, 66_005.0)}
    assert unquoted_holdings(p, eth_only) == [Symbol.BTCUSDC, Symbol.USDCEUR]
    assert not covers(p, eth_only, Symbol.ETHEUR)
    with pytest.raises(ValueError, match="BTC-USDC, USDC-EUR: no fresh quote"):
        value(p, eth_only)
    # Without BTC no route quote is needed: ETH decides on ETH-EUR alone.
    empty = {**p, "btc": 0.0}
    assert covers(empty, {Symbol.ETHEUR: ETH_EUR}, Symbol.ETHEUR)
    assert value(empty, {Symbol.ETHEUR: ETH_EUR}) == 100.0
    # A v0.3.1 depot with the same holdings still needs BTC-EUR, not the route.
    assert unquoted_holdings({**p, "agent": "trend"}, QS) == [Symbol.BTCEUR]


def test_drawdown_stop_sells_btc_over_the_route_at_the_bids(con: sqlite3.Connection) -> None:
    with write_tx(con):
        con.execute("UPDATE portfolios SET cash=5000, btc=0.1, peak=20000 WHERE agent=?", (AGENT,))
        stop = mark(con, AGENT, QS, NOW, Costs())
    assert stop is not None and stop.agent == AGENT
    (sale,) = trades(con)
    assert (sale["symbol"], sale["side"], sale["qty"]) == ("BTCUSDC", "RISK_SELL", 0.1)
    close(sale["notional"], 0.1 * 73_500.0 * 0.9995 * 0.8975 * 0.9999)
    close(sale["fee"], sale["notional"] * 0.0015)
    p = balance(con, AGENT)
    assert p["halted"] == 1 and p["btc"] == 0.0
    close(p["cash"], 5_000.0 + sale["notional"] - sale["fee"])


# ------------------------------------------------------------------ same limits as v0.3.1


def _neutral(monkeypatch: pytest.MonkeyPatch, costs: Costs) -> None:
    """A route that is BTC-EUR in disguise: USDC-EUR at 1/1 without costs, BTC-USDC with the
    costs of v0.3.1. Then every number of a USDC depot must equal its v0.3.1 twin bit for bit."""
    monkeypatch.setattr(usdc, "BTC_USDC_FEE", costs.fee_rate)
    monkeypatch.setattr(usdc, "BTC_USDC_SLIPPAGE", costs.slippage)
    monkeypatch.setattr(usdc, "USDC_EUR_FEE", 0.0)
    monkeypatch.setattr(usdc, "USDC_EUR_SLIPPAGE", 0.0)
    monkeypatch.setattr(usdc, "ROUTE_FEE", costs.fee_rate)
    monkeypatch.setattr(usdc, "ROUTE_SLIPPAGE", costs.slippage)


def _quotes(raw: dict[str, list[float]] | None, routed: bool) -> dict[Symbol, Quote] | None:
    if raw is None:
        return None
    qs = {Symbol[s]: Quote(float(b), float(a)) for s, (b, a) in raw.items()}
    if routed and Symbol.BTCEUR in qs:
        # BTC-EUR itself is left out: the route must not read it.
        qs[Symbol.BTCUSDC] = qs.pop(Symbol.BTCEUR)
        qs[Symbol.USDCEUR] = Quote(1.0, 1.0)
    return qs


def _play(
    con: sqlite3.Connection, ops: list[dict[str, Any]], agent: str, mp: pytest.MonkeyPatch
) -> list[Any]:
    """The golden ops of the classic depot ``trend`` played as ``agent`` (buy & hold left out)."""
    routed = usdc.is_usdc(agent)
    seen: list[Any] = []
    marks = {"decisions": 0, "trades": 0, "equity": 0}
    for op in ops:
        if op.get("agent", "buyhold") != "trend":
            continue
        costs = Costs(*op.get("costs", cases.DEFAULT_COSTS))
        _neutral(mp, costs)
        qs = _quotes(op.get("quotes"), routed)
        kind = op["op"]
        if kind == "init":
            with write_tx(con):
                con.execute(
                    "UPDATE portfolios SET cash=?,btc=?,eth=?,peak=?,halted=? WHERE agent=?",
                    (op["cash"], op["btc"], op["eth"], op["peak"], op["halted"], agent),
                )
            continue
        if kind == "decide":
            sig = Signal(
                op["sig"]["action"],
                float(op["sig"]["target_fraction"]),
                float(op["sig"]["confidence"]),
                op["sig"]["reason"],
                "trend",
            )
            ret: Any = record_decision(
                con, agent, Symbol[op["symbol"]], op["bar_ts"], op["model"], sig, op["data"],
                costs=costs, now=op["now"], qs=qs, status=op["status"], partial=routed,
            )  # fmt: skip
        elif kind == "trade":
            assert qs is not None
            with write_tx(con):
                trade_to_target(
                    con, agent, Symbol[op["symbol"]], op["target"], qs, 0, costs,
                    executed_at=op["now"],
                )  # fmt: skip
            ret = None
        else:
            assert kind == "mark" and qs is not None
            with write_tx(con):
                ret = mark(con, agent, qs, op["now"], costs)
        state: list[Any] = [ret is None if kind == "mark" else ret]
        state.append(
            list(con.execute("SELECT * FROM portfolios WHERE agent=?", (agent,)).fetchone())[1:]
        )
        for table in ("decisions", "trades", "equity"):
            for row in con.execute(
                f"SELECT * FROM {table} WHERE id>? ORDER BY id", (marks[table],)
            ):
                marks[table] = row["id"]
                cells = {k: row[k] for k in row.keys() if k not in ("id", "agent")}  # noqa: SIM118
                if cells.get("symbol") == "BTCUSDC":
                    cells["symbol"] = "BTCEUR"
                seen.append((table, [repr(v) for v in cells.values()]))
        seen.append(state)
    return seen


@pytest.mark.parametrize("mode", cases.FUZZ_MODES)
def test_usdc_depot_with_a_neutral_route_matches_v031_bit_for_bit(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops = [op for i in range(cases.FUZZ_PER_MODE) for op in cases.fuzz_case(mode, i)]
    walks = [op for seed in range(4) for op in cases.walk(seed)] if mode == "random" else []
    results = {}
    for agent in ("trend", AGENT):
        con = open_db(tmp_path / f"{agent}.db", agents=(agent,))
        con.execute("PRAGMA synchronous=OFF")
        try:
            results[agent] = _play(con, ops, agent, monkeypatch)
            results[agent] += _play(con, walks, agent, monkeypatch)
        finally:
            con.close()
    assert results["trend"] == results[AGENT]
    # The cases do trade BTC over the route: a comparison of nothing would prove nothing.
    routed = [e for e in results[AGENT] if e[0] == "trades" and "'BTCEUR'" in e[1]]
    assert len(routed) > 10, len(routed)
