"""Deterministic paper-execution scenarios shared by the golden generator and the tests.

Spec 004 AK5 / ADR-0007. Standard library only: tools/golden/generate_execution_golden.py imports
this file next to the Arena code and plays the ops against the Arena; the tests play the same ops
against the bot. A scenario is a list of ops; after every op the observable state is captured.

Ops (JSON):
- init:    set one portfolio row directly (cash, btc, eth, peak, halted)
- decide:  record_decision(agent, symbol, bar_ts, model, sig, data, status, quotes, now, costs)
- trade:   trade_to_target(agent, symbol, target, quotes, decision_id=0, now, costs)
- buyhold: initialize_buyhold(quotes, now, costs)
- mark:    peak + equity point (the Arena's mark_and_risk without a drawdown stop, see Spec 004)
"""

import random
import sqlite3
from datetime import UTC, datetime
from typing import Any

SYMBOLS = ("BTCEUR", "ETHEUR")
AGENTS = ("trend", "buyhold")
DEFAULT_COSTS = (0.0025, 0.0005)
COST_VARIANTS = ((0.0025, 0.0005), (0.004, 0.001), (0.0, 0.0), (0.05, 0.05))
T0 = 1_793_000_000
# No cash-limit mode: with cash >= 0 and fee <= 5 % the 90 % cap always binds first
# (0.9*cash - 0.1*positions < cash/(1+fee)), so the cash term never decides a trade.
FUZZ_MODES = ("random", "threshold", "total_cap", "small_equity", "exact", "status", "direct")
FUZZ_PER_MODE = 300
FUZZ_CHUNK = 25
WALKS = 24
WALK_STEPS = 30

Op = dict[str, Any]
QuotesMap = dict[str, list[float]]
QuotesJson = QuotesMap | None


def iso(epoch: int) -> str:
    """Same text form as the Arena's utcnow()."""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def quotes(
    btc_mid: float, btc_bps: float, eth_mid: float, eth_bps: float
) -> dict[str, list[float]]:
    return {
        "BTCEUR": [btc_mid * (1 - btc_bps / 20_000), btc_mid * (1 + btc_bps / 20_000)],
        "ETHEUR": [eth_mid * (1 - eth_bps / 20_000), eth_mid * (1 + eth_bps / 20_000)],
    }


def mid(q: list[float]) -> float:
    return (q[0] + q[1]) / 2


def sig(action: str, target: float, confidence: float = 0.7, reason: str = "golden") -> Op:
    return {"action": action, "target_fraction": target, "confidence": confidence, "reason": reason}


# ---------------------------------------------------------------- named scenarios (full rows)


def named_scenarios() -> list[dict[str, Any]]:
    q1 = quotes(60_000.0, 4.0, 3_000.0, 6.0)
    q2 = quotes(62_400.0, 3.0, 2_910.0, 5.0)
    q3 = quotes(58_800.0, 8.0, 3_090.0, 2.0)
    t = T0
    d = DEFAULT_COSTS

    def decide(agent: str, symbol: str, bar: int, s: Op, q: QuotesJson, at: int, **kw: Any) -> Op:
        return {
            "op": "decide",
            "agent": agent,
            "symbol": symbol,
            "bar_ts": bar,
            "model": kw.get("model", agent.upper()),
            "sig": s,
            "data": kw.get("data", {"bar": bar, "note": "Prüfung"}),
            "status": kw.get("status", "OK"),
            "quotes": q,
            "now": iso(at),
            "costs": list(kw.get("costs", d)),
        }

    return [
        {
            "name": "buy_both_rebalance_sell",
            "ops": [
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), q1, t),
                decide("trend", "ETHEUR", 3600, sig("BUY", 0.45), q1, t + 1),
                {"op": "mark", "agent": "trend", "quotes": q2, "now": iso(t + 60)},
                decide("trend", "BTCEUR", 7200, sig("HOLD", 0.0), q2, t + 3600),
                decide("trend", "BTCEUR", 10800, sig("SELL", 0.25), q2, t + 7200),
                decide("trend", "ETHEUR", 10800, sig("BUY", 0.25), q3, t + 7201),
                decide("trend", "BTCEUR", 14400, sig("BUY", 0.45), q3, t + 10800),
                decide("trend", "ETHEUR", 14400, sig("SELL", 0.0), q3, t + 10801),
                decide("trend", "BTCEUR", 18000, sig("SELL", 0.0), q1, t + 14400),
            ],
        },
        {
            "name": "duplicate_bar_is_ignored",
            "ops": [
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.25), q1, t),
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), q2, t + 5),
                decide("trend", "BTCEUR", 3600, sig("SELL", 0.0), q3, t + 9),
            ],
        },
        {
            "name": "stale_and_partial_quotes",
            "ops": [
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), None, t),
                decide("trend", "ETHEUR", 3600, sig("BUY", 0.45), {"ETHEUR": q1["ETHEUR"]}, t),
                decide(
                    "trend", "BTCEUR", 7200, sig("BUY", 0.45, reason="  raw  "), q1, t, status="X"
                ),
                decide("trend", "BTCEUR", 10800, sig("BUY", 0.45, reason="  ok  "), q1, t + 60),
            ],
        },
        {
            "name": "buyhold_once",
            "ops": [
                {"op": "buyhold", "quotes": q1, "now": iso(t), "costs": list(d)},
                {"op": "buyhold", "quotes": q2, "now": iso(t + 60), "costs": list(d)},
                decide("buyhold", "BTCEUR", 3600, sig("BUY", 0.45), q2, t + 120, model="BUY&HOLD"),
                decide("buyhold", "ETHEUR", 3600, sig("SELL", 0.0), q2, t + 120, model="BUY&HOLD"),
                {"op": "mark", "agent": "buyhold", "quotes": q3, "now": iso(t + 180)},
            ],
        },
        {
            "name": "buyhold_needs_both_quotes",
            "ops": [
                {"op": "buyhold", "quotes": {"BTCEUR": q1["BTCEUR"]}, "now": iso(t), "costs": [*d]},
                {"op": "buyhold", "quotes": quotes(60_000, 80, 3_000, 2), "now": iso(t)}
                | {"costs": list(d)},
                {"op": "buyhold", "quotes": q1, "now": iso(t + 1), "costs": list(d)},
            ],
        },
        {
            "name": "halted_portfolio_does_not_trade",
            "ops": [
                {
                    "op": "init",
                    "agent": "trend",
                    "cash": 4_000.0,
                    "btc": 0.05,
                    "eth": 0.5,
                    "peak": 11_000.0,
                    "halted": 1,
                },
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), q1, t),
                decide("trend", "ETHEUR", 3600, sig("SELL", 0.0), q1, t),
                {
                    "op": "trade",
                    "agent": "trend",
                    "symbol": "BTCEUR",
                    "target": 0.45,
                    "quotes": q1,
                    "now": iso(t + 1),
                    "costs": list(d),
                },
                {
                    "op": "trade",
                    "agent": "trend",
                    "symbol": "ETHEUR",
                    "target": 0.0,
                    "quotes": q1,
                    "now": iso(t + 2),
                    "costs": list(d),
                },
                {"op": "mark", "agent": "trend", "quotes": q2, "now": iso(t + 3)},
            ],
        },
        {
            "name": "costs_from_configuration",
            "ops": [
                decide("trend", "BTCEUR", 3600, sig("BUY", 0.45), q1, t, costs=(0.004, 0.001)),
                decide("trend", "ETHEUR", 3600, sig("BUY", 0.25), q1, t, costs=(0.0, 0.0)),
                decide("trend", "BTCEUR", 7200, sig("SELL", 0.0), q2, t + 1, costs=(0.05, 0.05)),
            ],
        },
    ]


# ---------------------------------------------------------------- fuzz cases (digests)


def _portfolio(
    rng: random.Random, agent: str, q: dict[str, list[float]], equity: float, vb: float, ve: float
) -> Op:
    cash = max(0.0, equity - vb - ve)
    peak = (cash + vb + ve) * rng.choice((0.0, 0.95, 1.0, 1.02, 1.08))
    return {
        "op": "init",
        "agent": agent,
        "cash": cash,
        "btc": vb / mid(q["BTCEUR"]),
        "eth": ve / mid(q["ETHEUR"]),
        "peak": peak,
        "halted": 1 if rng.random() < 0.08 else 0,
    }


def _random_quotes(rng: random.Random) -> dict[str, list[float]]:
    return quotes(
        rng.uniform(25_000, 95_000),
        rng.choice((0.0, rng.uniform(0, 50))),
        rng.uniform(1_200, 5_000),
        rng.choice((0.0, rng.uniform(0, 50))),
    )


def _exact_quotes(rng: random.Random) -> dict[str, list[float]]:
    # Powers of two with zero spread keep qty * mid exact, so thresholds can be hit exactly.
    b = float(2 ** rng.choice((14, 15, 16)))
    e = float(2 ** rng.choice((10, 11, 12)))
    return {"BTCEUR": [b, b], "ETHEUR": [e, e]}


def fuzz_case(mode: str, i: int) -> list[Op]:
    rng = random.Random(f"{mode}:{i}")
    at = T0 + i * 7
    agent = "buyhold" if rng.random() < 0.15 else "trend"
    symbol = rng.choice(SYMBOLS)
    other = "ETHEUR" if symbol == "BTCEUR" else "BTCEUR"
    costs = list(rng.choice(COST_VARIANTS)) if rng.random() < 0.4 else list(DEFAULT_COSTS)
    q = _exact_quotes(rng) if mode == "exact" else _random_quotes(rng)
    equity = rng.choice((10_000.0, rng.uniform(1_000, 20_000)))
    target = rng.choice((0.0, 0.25, 0.45))
    action = rng.choice(("BUY", "SELL", "HOLD"))
    values = {symbol: 0.0, other: 0.0}
    status = "OK"
    quotes_used: QuotesJson = q
    direct_target: float | None = None

    if mode == "random":
        values = {symbol: rng.uniform(0, 0.6) * equity, other: rng.uniform(0, 0.5) * equity}
    elif mode in ("threshold", "small_equity", "exact"):
        if mode == "small_equity":
            equity = rng.uniform(1_500, 3_500)
        if mode == "exact":
            equity = rng.choice((10_000.0, 2_000.0, 2_500.0, 5_000.0))
            costs = list(rng.choice(((0.0, 0.0), (0.0, 0.0), DEFAULT_COSTS)))
        thr = max(50.0, 0.02 * equity)
        target = rng.choice((0.0, 0.25, 0.45))
        desired = min(0.45, target) * equity
        if mode == "exact":
            step = rng.choice((0.0, 0.0, 1 / 64, -1 / 64, 1.0, -1.0))
        else:
            step = rng.choice((0.0, 1e-9 * equity, -1e-9 * equity, 1e-6, -1e-6, rng.uniform(-3, 3)))
        side = 1 if target > 0 and rng.random() < 0.5 else -1
        present = desired - side * (thr + step)
        if present < 0:
            present = desired + thr + step
            side = -1
        action = "BUY" if side > 0 else "SELL"
        values = {symbol: max(0.0, present), other: rng.uniform(0, 0.4) * equity}
        if mode == "exact":
            values[other] = rng.choice((0.0, 1_000.0, 0.25 * equity))
    elif mode == "total_cap":
        # max_portfolio_notional = 0.9 * equity - other - present lands near MIN_TRADE_EUR.
        present = rng.uniform(0, 0.2) * equity
        room = 50.0 + rng.choice((0.0, 1e-6, -1e-6, rng.uniform(-20, 20), rng.uniform(50, 400)))
        values = {symbol: present, other: max(0.0, 0.9 * equity - present - room)}
        action, target = "BUY", rng.choice((0.25, 0.45))
    elif mode == "direct":
        # trade_to_target with out-of-range targets; small positions sit below the band.
        present = rng.choice((rng.uniform(0, 0.03), rng.uniform(0, 0.6))) * equity
        values = {symbol: present, other: rng.uniform(0, 0.4) * equity}
        direct_target = rng.choice((-0.1, -1.0, 0.0, 0.25, 0.45, 0.449, 0.451, 0.5, 1.0))
    elif mode == "status":
        values = {symbol: rng.uniform(0, 0.45) * equity, other: rng.uniform(0, 0.45) * equity}
        kind = rng.choice(("none", "partial", "status"))
        if kind == "none":
            quotes_used = None
        elif kind == "partial":
            quotes_used = {other: q[other]}
        else:
            status = "STALE_QUOTE"

    init = _portfolio(rng, agent, q, equity, values["BTCEUR"], values["ETHEUR"])
    if direct_target is None and rng.random() < 0.12:
        direct_target = rng.choice((target, 0.5, 1.0, -0.1, 0.449, 0.451))
    if direct_target is not None:
        op: Op = {
            "op": "trade",
            "agent": agent,
            "symbol": symbol,
            "target": direct_target,
            "quotes": q,
            "now": iso(at),
            "costs": costs,
        }
    else:
        reason = rng.choice(("golden", "  padded  ", "x" * 600, "Kurs über EMA, 50 € Abstand"))
        op = {
            "op": "decide",
            "agent": agent,
            "symbol": symbol,
            "bar_ts": T0 + i,
            "model": "BUY&HOLD" if agent == "buyhold" else agent.upper(),
            "sig": sig(action, target, rng.choice((0.0, 0.7, 1.0, rng.random())), reason),
            "data": {"mode": mode, "i": i},
            "status": status,
            "quotes": quotes_used,
            "now": iso(at),
            "costs": costs,
        }
    return [init, op]


def fuzz_chunks() -> list[tuple[str, int, list[list[Op]]]]:
    """(mode, chunk index, cases); every chunk gets its own digest so failures stay local."""
    chunks = []
    for mode in FUZZ_MODES:
        cases = [fuzz_case(mode, i) for i in range(FUZZ_PER_MODE)]
        for c in range(0, FUZZ_PER_MODE, FUZZ_CHUNK):
            chunks.append((mode, c // FUZZ_CHUNK, cases[c : c + FUZZ_CHUNK]))
    return chunks


# ---------------------------------------------------------------- random walks (digests)


def walk(seed: int) -> list[Op]:
    """A multi-step history: drifting prices, both agents, repeated bars, equity points."""
    rng = random.Random(f"walk:{seed}")
    b0, e0 = rng.uniform(40_000, 80_000), rng.uniform(2_000, 4_000)
    b, e = b0, e0
    costs = list(rng.choice(COST_VARIANTS[:3]))
    ops: list[Op] = []
    at = T0 + seed * 100_000
    for k in range(WALK_STEPS):
        # Bounded moves keep every walk clear of the Arena's 10 % drawdown stop (not ported yet).
        b = min(b0 * 1.05, max(b0 * 0.95, b * (1 + rng.gauss(0, 0.006))))
        e = min(e0 * 1.05, max(e0 * 0.95, e * (1 + rng.gauss(0, 0.008))))
        q = quotes(b, rng.uniform(0, 30), e, rng.uniform(0, 30))
        at += 60 * rng.choice((1, 15, 60))
        if k == 0 or rng.random() < 0.05:
            ops.append({"op": "buyhold", "quotes": q, "now": iso(at), "costs": costs})
        symbol = rng.choice(SYMBOLS)
        bar = k if rng.random() > 0.1 else max(0, k - 1)
        ops.append(
            {
                "op": "decide",
                "agent": "trend",
                "symbol": symbol,
                "bar_ts": bar,
                "model": "TREND",
                "sig": sig(
                    rng.choice(("BUY", "BUY", "SELL", "HOLD", "HOLD")),
                    rng.choice((0.0, 0.25, 0.45)),
                ),
                "data": {"k": k},
                "status": "OK",
                "quotes": q if rng.random() > 0.05 else None,
                "now": iso(at),
                "costs": costs,
            }
        )
        if rng.random() < 0.3:
            ops.append({"op": "mark", "agent": rng.choice(AGENTS), "quotes": q, "now": iso(at)})
    return ops


# ---------------------------------------------------------------- state capture


def capture(con: sqlite3.Connection, marks: dict[str, int]) -> dict[str, Any]:
    """Portfolios of both agents plus every row added since the last capture.

    Equity uses INSERT OR REPLACE, so a replaced point shows up as a new id; the count catches
    rows that vanish.
    """
    holders = ",".join("?" for _ in AGENTS)
    portfolios = [
        [r[0], r[1], r[2], r[3], r[4], r[5]]
        for r in con.execute(
            "SELECT agent,cash,btc,eth,peak,halted FROM portfolios "
            f"WHERE agent IN ({holders}) ORDER BY agent",
            AGENTS,
        )
    ]
    state: dict[str, Any] = {"portfolios": portfolios}
    for table in ("decisions", "trades", "equity"):
        rows = con.execute(
            f"SELECT * FROM {table} WHERE id > ? ORDER BY id", (marks.get(table, 0),)
        ).fetchall()
        state[table] = [list(r) for r in rows]
        if rows:
            marks[table] = rows[-1][0]
    state["equity_count"] = con.execute("SELECT COUNT(*) FROM equity").fetchone()[0]
    return state
