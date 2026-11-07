"""Drawdown-stop scenarios shared by the golden generator and the tests (Spec 011 AK1, ADR-0021).

Standard library only, like golden_execution_cases (whose ops and capture this file reuses). The
older fixture skipped every history that hit the Arena's 10 % stop; these cases aim at it: the
exact threshold, one ulp either side, halted and buy & hold depots, forced sales below the minimum
order, a stop right after a trade, all cost variants and long walks with wide price moves.

Every named scenario says whether the Arena must stop it (``stops``); the generator refuses a
scenario that does not, so a drifting case cannot silently lose its coverage.
"""

import math
import random
from typing import Any

from golden_execution_cases import (
    AGENTS,
    COST_VARIANTS,
    DEFAULT_COSTS,
    SYMBOLS,
    T0,
    Op,
    QuotesJson,
    iso,
    mid,
    quotes,
    sig,
)

# The Arena's constant, repeated here so the cases do not depend on the code under test.
KILL = 0.10
FUZZ_MODES = ("edge", "band", "decide")
FUZZ_PER_MODE = 300
FUZZ_CHUNK = 25
WALKS = 24
WALK_STEPS = 40

# Powers of two with zero spread: value = cash + qty * mid is exact, thresholds can be hit exactly.
EXACT = {"BTCEUR": [32_768.0, 32_768.0], "ETHEUR": [2_048.0, 2_048.0]}


def _init(agent: str, cash: float, btc: float, eth: float, peak: float, halted: int = 0) -> Op:
    return {
        "op": "init",
        "agent": agent,
        "cash": cash,
        "btc": btc,
        "eth": eth,
        "peak": peak,
        "halted": halted,
    }


def _mark(agent: str, q: QuotesJson, at: int, costs: Any = DEFAULT_COSTS) -> Op:
    return {"op": "mark", "agent": agent, "quotes": q, "now": iso(at), "costs": list(costs)}


def _decide(agent: str, symbol: str, bar: int, s: Op, q: QuotesJson, at: int, costs: Any) -> Op:
    return {
        "op": "decide",
        "agent": agent,
        "symbol": symbol,
        "bar_ts": bar,
        "model": "BUY&HOLD" if agent == "buyhold" else agent.upper(),
        "sig": s,
        "data": {"bar": bar},
        "status": "OK",
        "quotes": q,
        "now": iso(at),
        "costs": list(costs),
    }


def equity(cash: float, btc: float, eth: float, q: dict[str, list[float]]) -> float:
    """Same expression and order as value() in the Arena."""
    return float(cash + sum(x * mid(q[s]) for s, x in zip(SYMBOLS, (btc, eth), strict=True)))


def hits(eq: float, peak: float) -> bool:
    return peak > 0 and eq < peak * (1 - KILL)


def named_scenarios() -> list[dict[str, Any]]:
    t = T0 + 50_000_000
    d = DEFAULT_COSTS
    # 0.0625 BTC and 1 ETH at the exact quotes are worth 2048 EUR each.
    btc, eth = 0.0625, 1.0
    cash = 9_000.0 - 4_096.0
    assert equity(cash, btc, eth, EXACT) == 10_000.0 * (1 - KILL)
    above = math.nextafter(10_000.0, 0.0)
    below = math.nextafter(10_000.0, math.inf)
    spread = quotes(61_000.0, 9.0, 2_950.0, 7.0)
    near = equity(4_000.0, 0.05, 0.6, spread)
    out: list[dict[str, Any]] = [
        {
            "name": "exactly_90_percent_does_not_stop",
            "stops": False,
            "ops": [_init("trend", cash, btc, eth, 10_000.0), _mark("trend", EXACT, t)],
        },
        {
            "name": "one_ulp_below_threshold_stops",
            "stops": True,
            "ops": [_init("trend", cash, btc, eth, below), _mark("trend", EXACT, t)],
        },
        {
            "name": "one_ulp_above_threshold_does_not_stop",
            "stops": False,
            "ops": [_init("trend", cash, btc, eth, above), _mark("trend", EXACT, t)],
        },
        {
            "name": "cash_one_ulp_below_stops",
            "stops": True,
            "ops": [
                _init("trend", math.nextafter(9_000.0, 0.0), 0.0, 0.0, 10_000.0),
                _mark("trend", EXACT, t),
            ],
        },
        {
            "name": "already_halted_never_stops_again",
            "stops": False,
            "ops": [_init("trend", 1_000.0, 0.05, 0.6, 10_000.0, 1), _mark("trend", spread, t)],
        },
        {
            "name": "buyhold_is_never_stopped",
            "stops": False,
            "ops": [_init("buyhold", 1_000.0, 0.05, 0.6, 10_000.0), _mark("buyhold", spread, t)],
        },
        {
            "name": "empty_depot_with_zero_peak",
            "stops": False,
            "ops": [_init("trend", 0.0, 0.0, 0.0, 0.0), _mark("trend", spread, t)],
        },
        {
            "name": "forced_sale_below_minimum_order",
            "stops": True,
            "ops": [
                _init("trend", 8_000.0, 20.0 / mid(spread["BTCEUR"]), 0.001, 10_000.0),
                _mark("trend", spread, t),
            ],
        },
        {
            "name": "only_eth_held",
            "stops": True,
            "ops": [_init("trend", 4_000.0, 0.0, 1.5, 10_000.0), _mark("trend", spread, t)],
        },
        {
            "name": "stop_right_after_a_trade",
            "stops": True,
            # 90.5 % of the peak; a 45 % buy at 5 % fee and slippage costs about 4.5 % more.
            "ops": [
                _init("trend", 9_050.0, 0.0, 0.0, 10_000.0),
                _decide("trend", "ETHEUR", 3600, sig("BUY", 0.45), spread, t, (0.05, 0.05)),
                _decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), spread, t + 1, (0.05, 0.05)),
                _mark("trend", spread, t + 60, (0.05, 0.05)),
            ],
        },
        {
            "name": "halted_depot_keeps_deciding_without_trades",
            "stops": True,
            "ops": [
                _init("trend", 4_000.0, 0.05, 0.6, near / 0.85),
                _mark("trend", spread, t),
                _decide("trend", "BTCEUR", 7200, sig("BUY", 0.45), spread, t + 60, d),
                _decide("trend", "ETHEUR", 7200, sig("SELL", 0.0), spread, t + 61, d),
                {
                    "op": "trade",
                    "agent": "trend",
                    "symbol": "BTCEUR",
                    "target": 0.45,
                    "quotes": spread,
                    "now": iso(t + 62),
                    "costs": list(d),
                },
                _mark("trend", quotes(70_000.0, 4.0, 3_500.0, 4.0), t + 120),
                _mark("trend", quotes(70_000.0, 4.0, 3_500.0, 4.0), t + 120),
            ],
        },
        {
            "name": "peak_rises_then_the_fall_stops",
            "stops": True,
            "ops": [
                _init("trend", 1_000.0, 0.09, 1.2, 9_000.0),
                *(
                    _mark("trend", quotes(60_000.0 * f, 6.0, 3_000.0 * f, 6.0), t + 60 * k)
                    for k, f in enumerate((1.0, 1.04, 1.08, 1.02, 0.97, 0.95, 0.93, 0.9))
                ),
            ],
        },
    ]
    for i, costs in enumerate(COST_VARIANTS):
        out.append(
            {
                "name": f"forced_sale_costs_{i}",
                "stops": True,
                "ops": [
                    _init("trend", 2_000.0, 0.06, 0.9, 10_000.0),
                    _mark("trend", spread, t + i, costs),
                ],
            }
        )
    for s in out:
        first = s["ops"][0]
        q = s["ops"][1]["quotes"]
        if s["ops"][1]["op"] == "mark" and len(s["ops"]) == 2:
            eq = equity(first["cash"], first["btc"], first["eth"], q)
            peak = max(eq, first["peak"])
            expected = hits(eq, peak) and not first["halted"] and first["agent"] != "buyhold"
            assert expected == s["stops"], s["name"]
    return out


# ---------------------------------------------------------------- fuzz (digests)


def _fuzz_quotes(rng: random.Random) -> dict[str, list[float]]:
    return quotes(
        rng.uniform(25_000, 95_000),
        rng.choice((0.0, rng.uniform(0, 50))),
        rng.uniform(1_200, 5_000),
        rng.choice((0.0, rng.uniform(0, 50))),
    )


def fuzz_case(mode: str, i: int) -> list[Op]:
    rng = random.Random(f"drawdown:{mode}:{i}")
    at = T0 + 60_000_000 + i * 7
    agent = "buyhold" if rng.random() < 0.15 else "trend"
    costs = list(rng.choice(COST_VARIANTS))
    q = _fuzz_quotes(rng)
    eq_target = rng.choice((10_000.0, rng.uniform(500, 30_000)))
    held = rng.choice(("both", "btc", "eth", "none", "dust"))
    wb = rng.uniform(0, 0.5) if held in ("both", "btc") else 0.0
    we = rng.uniform(0, 0.45) if held in ("both", "eth") else 0.0
    if held == "dust":
        wb, we = rng.uniform(0, 0.004), rng.uniform(0, 0.004)
    btc = wb * eq_target / mid(q["BTCEUR"])
    eth = we * eq_target / mid(q["ETHEUR"])
    cash = max(0.0, eq_target * (1 - wb - we))
    eq = equity(cash, btc, eth, q)
    if mode == "edge":
        # Peak where peak * 0.9 lands right on eq; a few ulps either way decide the stop.
        peak = eq / (1 - KILL)
        for _ in range(rng.choice((0, 1, 2, 3))):
            peak = math.nextafter(peak, rng.choice((0.0, math.inf)))
    else:
        peak = eq / rng.uniform(0.84, 0.96)
    peak = rng.choice((peak, peak, peak, 0.0)) if mode == "band" else peak
    ops: list[Op] = [_init(agent, cash, btc, eth, peak, 1 if rng.random() < 0.1 else 0)]
    if mode == "decide":
        symbol = rng.choice(SYMBOLS)
        action = rng.choice(("BUY", "BUY", "SELL", "HOLD"))
        ops.append(
            _decide(agent, symbol, T0 + i, sig(action, rng.choice((0.0, 0.25, 0.45))), q, at, costs)
        )
    ops.append(_mark(agent, q, at + 1, costs))
    return ops


def fuzz_chunks() -> list[tuple[str, int, list[list[Op]]]]:
    chunks = []
    for mode in FUZZ_MODES:
        cases = [fuzz_case(mode, i) for i in range(FUZZ_PER_MODE)]
        for c in range(0, FUZZ_PER_MODE, FUZZ_CHUNK):
            chunks.append((mode, c // FUZZ_CHUNK, cases[c : c + FUZZ_CHUNK]))
    return chunks


# ---------------------------------------------------------------- walks (digests)


def walk(seed: int) -> list[Op]:
    """A long history with unbounded, wide moves: depots stop, stay halted, keep deciding."""
    rng = random.Random(f"drawdown-walk:{seed}")
    b, e = rng.uniform(40_000, 80_000), rng.uniform(2_000, 4_000)
    costs = list(rng.choice(COST_VARIANTS))
    ops: list[Op] = []
    at = T0 + 70_000_000 + seed * 100_000
    for k in range(WALK_STEPS):
        b *= 1 + rng.gauss(-0.004, 0.03)
        e *= 1 + rng.gauss(-0.006, 0.04)
        q = quotes(b, rng.uniform(0, 30), e, rng.uniform(0, 30))
        at += 60 * rng.choice((1, 15, 60))
        if k == 0:
            ops.append({"op": "buyhold", "quotes": q, "now": iso(at), "costs": costs})
        for symbol in SYMBOLS:
            ops.append(
                _decide(
                    "trend",
                    symbol,
                    k,
                    sig(rng.choice(("BUY", "BUY", "SELL", "HOLD")), rng.choice((0.25, 0.45))),
                    q,
                    at,
                    costs,
                )
            )
        for agent in AGENTS:
            ops.append(_mark(agent, q, at, costs))
    return ops
