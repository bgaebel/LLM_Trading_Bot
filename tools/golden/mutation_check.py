"""Mutation check for the strategy golden tests (Spec 003 review, ADR-0007).

Applies one small change at a time (a threshold, weight, window, order) to a temporary copy of
the strategy code and runs tests/strategy. Every mutation must be KILLED; a SURVIVED mutation means
the golden fixtures would not notice that change. Mutation list from the independent review.

Spec 004 adds mutations of the paper execution (paper/execution.py, tests/paper).

Spec 005 adds mutations of the GPT layer (llm/*.py, tests/llm).

Spec 012 adds the broker seam and the live locks (broker/*.py, tests/broker; core.py and cli.py
with tests/test_core.py and tests/test_cli.py as group "cli"). The temporary copy also holds
tools/, deploy/, scripts/ and uv.lock, which the broker guards read - without them every broker
mutant would be "killed" by a missing file instead of by a test.

Spec 016 adds the lab's historical client (lab/history.py, tests/lab): its window, closed-bar
filter, forming bar and quote time are checked against frozen live responses (AK3). AK14 adds
the lab's USDC route (labels "lab usdc ..." and "lab fidelity ..."): synthetic route quotes,
the route's spread and sale in the backtest, the route costs and benchmarks in the fidelity check.

Spec 020 adds the USDC route as group "usdc" (labels starting with "usdc "): fees, legs, sides
of the books, the cost input of the signals and the fail-closed checks, run against the USDC
tests only. The v0.3.1 mutants above keep guarding the v0.3.1 path.

Usage: uv run python tools/golden/mutation_check.py [--only] [strategy|paper|llm|service|broker|
cli|lab|usdc]
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Equivalent mutations (cannot change any output):
# - ensemble 0.55 -> 0.6: the only vote combination scoring in [0.55, 0.6) is TRANSITION with
#   breakout BUY and meanrev BUY, which needs adx_1h >= 19 and < 18 at once - impossible for any
#   input (checked by enumerating all 27 vote combinations per regime, 2026-11-06).
# - noop control: replaces a text with itself; proves the harness reports survivors.
# - cash cap p["cash"]/(1+rate): with cash >= 0 and fee <= 5 % the 90 % cap is always smaller
#   (0.9*cash - 0.1*positions < cash/(1+fee)), so the cash term never decides (Spec 004).
# - sell clamp MAX(0, col-qty): qty = min(col, ...) <= col, and x - y >= 0 for floats y <= x.
# - total room floor max(0, ...): a negative room gives spend < MIN_TRADE_EUR, as 0 does.
EQUIVALENT = {
    "ensemble buy thr .55->.6",
    "noop control",
    # - broker noop control: replaces a text with itself; proves the broker group reports survivors.
    "broker noop control",
    "exec cash cap removed",
    "exec sell clamp at 0",
    "exec total room floor",
    # - llm confidence finite: NaN and +-inf already fail 0 <= x <= 1 (Spec 005).
    "llm confidence finite",
    # - risk peak > 0: peak = max(eq, peak) >= eq >= 0 (cash never goes negative), so with
    #   peak == 0 the stop test is 0 < 0 either way (Spec 011).
    "risk peak > 0",
    # - usdc fee legs swapped: both leg fees are charged on the same euro amount (the USDC leg's
    #   fee converted at the price the USDC were bought for), so only their sum is observable -
    #   in every trade, valuation and signal. A wrong fee on ONE leg is killed below (Spec 020).
    "usdc fee legs swapped",
    # - usdc benchmark buys once after a restart: its decisions (bar -1) are unique, so a second
    #   attempt is ignored before it can trade; the trade check only mirrors initialize_buyhold.
    "usdc benchmark buys once after a restart",
}

M = [
    (
        "signals.py",
        '{"trend": 0.45, "breakout": 0.35, "meanrev": 0.20}',
        '{"trend": 0.45, "breakout": 0.35, "meanrev": 0.21}',
        "TRANSITION meanrev weight",
    ),
    (
        "signals.py",
        '{"trend": 0.45, "breakout": 0.35, "meanrev": 0.20}',
        '{"trend": 0.45, "breakout": 0.36, "meanrev": 0.20}',
        "TRANSITION breakout weight",
    ),
    (
        "signals.py",
        '{"trend": 0.45, "breakout": 0.35, "meanrev": 0.20}',
        '{"trend": 0.46, "breakout": 0.35, "meanrev": 0.20}',
        "TRANSITION trend weight",
    ),
    (
        "signals.py",
        '{"trend": 0.20, "breakout": 0.15, "meanrev": 0.65}',
        '{"trend": 0.21, "breakout": 0.15, "meanrev": 0.65}',
        "RANGE trend weight",
    ),
    (
        "signals.py",
        '{"trend": 0.20, "breakout": 0.15, "meanrev": 0.65}',
        '{"trend": 0.20, "breakout": 0.16, "meanrev": 0.65}',
        "RANGE breakout weight",
    ),
    (
        "signals.py",
        '{"trend": 0.20, "breakout": 0.15, "meanrev": 0.65}',
        '{"trend": 0.20, "breakout": 0.15, "meanrev": 0.66}',
        "RANGE meanrev weight",
    ),
    (
        "signals.py",
        '{"trend": 0.60, "breakout": 0.30, "meanrev": 0.10}',
        '{"trend": 0.61, "breakout": 0.30, "meanrev": 0.10}',
        "TREND trend weight",
    ),
    (
        "signals.py",
        '{"trend": 0.60, "breakout": 0.30, "meanrev": 0.10}',
        '{"trend": 0.60, "breakout": 0.31, "meanrev": 0.10}',
        "TREND breakout weight",
    ),
    (
        "signals.py",
        '{"trend": 0.60, "breakout": 0.30, "meanrev": 0.10}',
        '{"trend": 0.60, "breakout": 0.30, "meanrev": 0.11}',
        "TREND meanrev weight",
    ),
    ("signals.py", "score >= 0.55", "score >= 0.6", "ensemble buy thr .55->.6"),
    ("signals.py", "score <= -0.40", "score <= -0.45", "ensemble sell thr -.40->-.45"),
    (
        "signals.py",
        'i["atr14_1h_pct"] * 4 > cost:',
        'i["atr14_1h_pct"] * 3 > cost:',
        "ensemble atr*4->3",
    ),
    ("signals.py", "up and a >= 20", "up and a >= 21", "trend adx 20->21"),
    (
        "signals.py",
        'i["plus_di_1h"] > i["minus_di_1h"]',
        'i["plus_di_1h"] >= i["minus_di_1h"]',
        "trend di > -> >=",
    ),
    (
        "signals.py",
        'if down or (current_weight > 0.02 and i["close_1h"] < i["ema72_1h"])',
        'if down or (current_weight > 0.02 and i["close_1h"] < i["ema24_1h"])',
        "trend sell ema72->ema24",
    ),
    ("signals.py", "if down or (", "if False or (", "trend drop down"),
    ("signals.py", "last > high * 1.001", "last > high * 1.002", "breakout 1.001->1.002"),
    ("signals.py", "a >= 19", "a >= 20", "breakout adx 19->20"),
    ("signals.py", ">= 1.25", ">= 1.5", "breakout vol 1.25->1.5"),
    (
        "signals.py",
        'movement_sufficient = i["atr14_1h_pct"] * 4 > cost',
        'movement_sufficient = i["atr14_1h_pct"] * 2 > cost',
        "breakout atr*4->2",
    ),
    (
        "signals.py",
        "ema(closes(s.bars[15]), 34)",
        "ema(closes(s.bars[15]), 30)",
        "breakout ema34->30",
    ),
    ("signals.py", ' or last < i["low_48h_excl_current"]', "", "breakout drop low48 cond"),
    ("signals.py", "prev_mid - 2 * prev_sd", "prev_mid - 2.2 * prev_sd", "meanrev 2sd->2.2"),
    ("signals.py", "range_ok = a < 18", "range_ok = a < 17", "meanrev adx<18->17"),
    ("signals.py", "< 0.012", "< 0.008", "meanrev ema ratio .012->.008"),
    ("signals.py", 'i["rsi14_15m"] < 42', 'i["rsi14_15m"] < 38', "meanrev rsi<42->38"),
    ("signals.py", "cost * 1.25", "cost * 1.5", "meanrev cost*1.25->1.5"),
    ("signals.py", "mid - 0.25 * sd", "mid - 0.5 * sd", "meanrev sell .25sd->.5"),
    ("signals.py", "or a > 24 or", "or a > 26 or", "meanrev sell adx 24->26"),
    ("signals.py", 'i["rsi14_15m"] > 66', 'i["rsi14_15m"] > 70', "meanrev sell rsi 66->70"),
    ("signals.py", "and turned\n", "\n", "meanrev drop turned"),
    (
        "signals.py",
        "current_weight > 0.02 and (last",
        "current_weight > 0.3 and (last",
        "breakout sell weight .02->.3",
    ),
    (
        "signals.py",
        "{distance_to_mean * 100:.1f}%",
        "{distance_to_mean * 100:.2f}%",
        "meanrev reason fmt",
    ),
    ("signals.py", "confidence: float = 0.7", "confidence: float = 0.75", "default confidence"),
    (
        "indicators.py",
        'regime = "TREND" if a >= 22',
        'regime = "TREND" if a >= 23',
        "regime 22->23",
    ),
    ("indicators.py", 'else "RANGE" if a < 18', 'else "RANGE" if a < 17', "regime 18->17"),
    ("indicators.py", "s.bars[60][-49:-1])", "s.bars[60][-48:-1])", "high48 window"),
    ("indicators.py", "s.bars[15][-21:-1])", "s.bars[15][-20:-1])", "vol mean window"),
    ("indicators.py", "strength = mean(dx[:n])", "strength = mean(dx[: n + 1])", "adx seed"),
    ("indicators.py", "return 50.0\n    d =", "return 50.0\n    d =", "noop control"),
    ("payload.py", "round(v, 5)", "round(v, 6)", "payload round 5->6"),
    ("payload.py", "(1440, 60, 24)", "(1440, 240, 6)", "payload 1440 returns"),
    (
        "payload.py",
        "sum(b.volume == 0 for b in s.bars[1][-60:])",
        "sum(b.volume == 0 for b in s.bars[1][-30:])",
        "payload filled window",
    ),
    ("payload.py", "round(current_weight, 4)", "round(current_weight, 2)", "payload weight round"),
    (
        "payload.py",
        '"allowed_weights": [0, 0.25, 0.45]',
        '"allowed_weights": [0.0, 0.25, 0.45]',
        "payload allowed int->float",
    ),
    (
        "payload.py",
        'for name in ("trend", "breakout", "meanrev")',
        'for name in ("trend", "meanrev", "breakout")',
        "payload ref order",
    ),
    # Second review round: finer mutations that changed real behaviour.
    ("signals.py", "up and a >= 20", "up and a > 20", "trend adx >= 20 -> > 20"),
    ("signals.py", "prev_mid - 2 * prev_sd", "prev_mid - 2.05 * prev_sd", "meanrev 2sd->2.05"),
    (
        "payload.py",
        "round(s.quote.spread_bps, 2)",
        "round(s.quote.spread_bps, 3)",
        "payload spread round 2->3",
    ),
    (
        "indicators.py",
        "return 100 if loss == 0",
        "return 100.0 if loss == 0",
        "rsi int 100 -> float",
    ),
    ("indicators.py", "if sd > 0 else 0,", "if sd > 0 else 0.0,", "z20 int 0 -> float"),
]


E = "paper/execution.py"
M += [
    (E, "MAX_SYMBOL_WEIGHT = 0.45", "MAX_SYMBOL_WEIGHT = 0.44", "exec symbol cap .45->.44"),
    (E, "MAX_TOTAL_WEIGHT = 0.90", "MAX_TOTAL_WEIGHT = 0.89", "exec total cap .90->.89"),
    (E, "MIN_TRADE_EUR = 50.0", "MIN_TRADE_EUR = 49.0", "exec min trade 50->49"),
    (E, "REBALANCE_BAND = 0.02", "REBALANCE_BAND = 0.021", "exec band .02->.021"),
    (E, "abs(diff) < max(", "abs(diff) <= max(", "exec band < -> <="),
    (E, "if spend < MIN_TRADE_EUR", "if spend <= MIN_TRADE_EUR", "exec min spend < -> <="),
    (E, "if notional < MIN_TRADE_EUR", "if notional <= MIN_TRADE_EUR", "exec min sell < -> <="),
    (E, "max(0.0, target)", "target", "exec target floor removed"),
    (E, "min(MAX_SYMBOL_WEIGHT, max(", "max(MAX_SYMBOL_WEIGHT, max(", "exec symbol cap min->max"),
    (E, "fill = q.ask * (1 + slip)", "fill = q.mid * (1 + slip)", "exec buy at mid"),
    (E, "fill = q.bid * (1 - slip)", "fill = q.mid * (1 - slip)", "exec sell at mid"),
    (E, "fill = q.ask * (1 + slip)", "fill = q.ask * (1 + rate)", "exec buy slip<->fee"),
    (E, "fee = spend * rate", "fee = spend * slip", "exec buy fee rate"),
    (E, "(spend + fee, qty, agent)", "(spend, qty, agent)", "exec buy fee not charged"),
    (E, "(notional - fee, qty, agent)", "(notional, qty, agent)", "exec sell fee not charged"),
    (E, "qty = min(p[col], abs(diff) / q.mid)", "qty = abs(diff) / q.mid", "exec sell qty cap"),
    (E, "{col}=MAX(0,{col}-?)", "{col}={col}-?", "exec sell clamp at 0"),
    (
        E,
        "max(0.0, MAX_TOTAL_WEIGHT * eq - other - present)",
        "MAX_TOTAL_WEIGHT * eq - other - present",
        "exec total room floor",
    ),
    (E, 'p["cash"] / (1 + rate))', 'p["cash"])', "exec cash cap removed"),
    (
        E,
        'if p["halted"] and target > current_weight',
        "if False and target > current_weight",
        "exec halted guard in trade",
    ),
    (E, 'action != "HOLD" and not p["halted"]', 'action != "HOLD"', "exec halted skip in decision"),
    (
        E,
        'if action == "HOLD":\n                    target = current',
        'if action == "HOLD":\n                    pass',
        "exec HOLD keeps weight",
    ),
    (
        E,
        'if action == "BUY" and target < current:',
        'if action == "BUY" and target < 0:',
        "exec BUY never reduces",
    ),
    (
        E,
        'if action == "SELL" and target > current:',
        'if action == "SELL" and target > 1:',
        "exec SELL never increases",
    ),
    (
        E,
        "if agent in BENCHMARKS and p[_COLUMN[symbol]] > 0:",
        "if agent in BENCHMARKS and p[_COLUMN[symbol]] > 1:",
        "exec buyhold holds once",
    ),
    (
        E,
        "if qs is None or (set(qs) != set(SYMBOLS) and not (",
        "if qs is None or (False and not (",
        "exec stale on partial",
    ),
    (E, "and not (partial and covers(", "and not (covers(", "exec partial only when asked"),
    (
        E,
        "return (symbol is None or symbol in qs) and not",
        "return not",
        "exec partial needs market",
    ),
    (E, "and not unquoted_holdings(p, qs)", "", "exec partial needs held markets"),
    (E, "    if p[_COLUMN[symbol]] == 0:\n        return 0.0\n", "", "exec worth needs quote"),
    (E, "sig.reason.strip()[:REASON_MAX]", "sig.reason[:REASON_MAX]", "exec reason strip"),
    (E, "REASON_MAX = 550", "REASON_MAX = 549", "exec reason length"),
    (E, "json.dumps(data, ensure_ascii=False)", "json.dumps(data)", "exec inputs ascii"),
    (
        E,
        '"INSERT OR IGNORE INTO decisions"',
        '"INSERT OR REPLACE INTO decisions"',
        "exec duplicate bar replaces",
    ),
    (E, 'peak = max(eq, p["peak"])', "peak = eq", "exec peak keeps max"),
    (
        E,
        "            stop = mark(con, agent, qs, now, costs)\n",
        "            stop = None\n",
        "exec mark after trade",
    ),
    # Spec 011 (ADR-0021): the drawdown stop.
    (E, "DRAWDOWN_KILL = 0.10", "DRAWDOWN_KILL = 0.11", "risk kill .10->.11"),
    (E, "eq < peak * (1 - DRAWDOWN_KILL)", "eq <= peak * (1 - DRAWDOWN_KILL)", "risk < -> <="),
    (E, 'and not p["halted"] and peak > 0', "and peak > 0", "risk halted once"),
    (
        E,
        'if agent not in BENCHMARKS and not p["halted"]',
        'if not p["halted"]',
        "risk buyhold exempt",
    ),
    (E, "and peak > 0 and eq <", "and eq <", "risk peak > 0"),
    (E, "SET halted=1 WHERE", "SET halted=0 WHERE", "risk sets halted"),
    (
        E,
        "fill = qs[symbol].bid * (1 - costs.slippage)",
        "fill = qs[symbol].mid * (1 - costs.slippage)",
        "risk sell at bid",
    ),
    (
        E,
        "fill = qs[symbol].bid * (1 - costs.slippage)",
        "fill = qs[symbol].bid * (1 - costs.fee_rate)",
        "risk sell slippage",
    ),
    (E, "fee = gross * costs.fee_rate", "fee = 0.0", "risk sell fee"),
    (E, "            if qty <= 0:\n", "            if qty < 0:\n", "risk skips empty market"),
    (E, "cash=cash+?,{col}=0 WHERE", "cash=cash+?,{col}={col}*1e-9 WHERE", "risk sells all"),
    (
        E,
        "        p = balance(con, agent)\n        eq = value(p, qs)\n    con.execute(",
        "    con.execute(",
        "risk equity after sale",
    ),
    (
        E,
        '(BUYHOLD, symbol.name, "BUY")',
        '(BUYHOLD, symbol.name, "SELL")',
        "exec buyhold trade check",
    ),
    (
        E,
        '"BUY",\n            0.45,\n            1.0,',
        '"BUY",\n            0.25,\n            1.0,',
        "exec buyhold allocation",
    ),
    (
        E,
        'con, BUYHOLD, symbol, -1, "BUY&HOLD"',
        'con, BUYHOLD, symbol, -2, "BUY&HOLD"',
        "exec buyhold sentinel bar",
    ),
    (E, '"initial_allocation_pct": 45,', '"initial_allocation_pct": 45.0,', "exec buyhold context"),
    (
        E,
        "if set(qs) != set(SYMBOLS):\n        raise",
        "if not qs:\n        raise",
        "exec buyhold needs both",
    ),
    (E, "    for quote in qs.values():\n        quote.validate()\n", "", "exec buyhold validates"),
    (
        E,
        'status, action, target = "STALE_QUOTE", "HOLD", 0',
        'status, action, target = "STALE_QUOTE", "HOLD", target',
        "exec stale target 0",
    ),
    (E, "if equity > 0 else 0", "if equity > 1e9 else 0", "exec weight zero equity"),
]


C, D, B, Y, OC, A = (
    "llm/contract.py",
    "llm/due.py",
    "llm/budget.py",
    "llm/cycle.py",
    "llm/openai_client.py",
    "llm/agents.py",
)
M += [
    (C, "REASON_MAX = 550", "REASON_MAX = 549", "llm reason length"),
    (C, "reason.strip()[:REASON_MAX]", "reason[:REASON_MAX]", "llm reason strip"),
    (
        C,
        'REGIMES = ("UPTREND", "DOWNTREND", "SIDEWAYS")',
        'REGIMES = ("UPTREND", "DOWNTREND")',
        "llm regime set",
    ),
    (C, "not 0 <= confidence <= 1", "not 0 < confidence <= 1", "llm confidence lower bound"),
    (C, "not 0 <= confidence <= 1", "not 0 <= confidence < 1", "llm confidence upper bound"),
    (
        C,
        'action == "SELL" and target != 0',
        'action == "SELL" and target > 0.3',
        "llm SELL consistency",
    ),
    (
        C,
        'action == "BUY" and target not in (0.25, 0.45)',
        'action == "BUY" and target == 0.3',
        "llm BUY consistency",
    ),
    (C, "set(data) != set(FIELDS)", "not set(FIELDS) <= set(data)", "llm exact entry fields"),
    (
        C,
        "set(data) != {s.market for s in wanted}",
        "not {s.market for s in wanted} <= set(data)",
        "llm exact markets",
    ),
    (C, "not math.isfinite(confidence) or ", "", "llm confidence finite"),
    (
        D,
        '"LLM_MIN_INTERVAL_SECONDS", "3600"',
        '"LLM_MIN_INTERVAL_SECONDS", "3599"',
        "llm interval env default",
    ),
    (
        D,
        '"LLM_EVENT_COOLDOWN_SECONDS", "600"',
        '"LLM_EVENT_COOLDOWN_SECONDS", "599"',
        "llm cooldown default",
    ),
    (D, "max(900, int(", "max(899, int(", "llm interval lower bound"),
    (
        D,
        '"LLM_EVENT_MOVE_THRESHOLD", ".018"',
        '"LLM_EVENT_MOVE_THRESHOLD", ".019"',
        "llm move threshold",
    ),
    (D, '"LLM_MAX_CALLS_PER_HOUR", "4"', '"LLM_MAX_CALLS_PER_HOUR", "5"', "llm hourly cap"),
    (
        D,
        "abs(mid / ref - 1) >= cadence.move_threshold",
        "abs(mid / ref - 1) > cadence.move_threshold",
        "llm move >= -> >",
    ),
    (
        D,
        "if elapsed >= cadence.min_interval:",
        "if elapsed > cadence.min_interval:",
        "llm interval >= -> >",
    ),
    (
        D,
        "if elapsed < cadence.event_cooldown:",
        "if elapsed <= cadence.event_cooldown:",
        "llm cooldown < -> <=",
    ),
    (
        D,
        "return any(previous.get(symbol.market) != dict(now) for symbol, now in signals.items())",
        "return False",
        "llm signal-change trigger",
    ),
    (D, "agent=? AND ts>?", "agent=? AND ts>=?", "llm rolling hour edge"),
    (D, ">= cadence.max_calls_per_hour", "> cadence.max_calls_per_hour", "llm hourly cap >= -> >"),
    (
        B,
        "if agent_used + hold > limits.agent_daily_eur:",
        "if agent_used + hold >= limits.agent_daily_eur:",
        "llm depot limit > -> >=",
    ),
    (B, "if total_used + hold > limits.total_daily_eur:", "if False:", "llm total limit"),
    (B, "rounding=ROUND_CEILING", "rounding=ROUND_FLOOR", "llm reservation rounding"),
    (B, "cost > reserved", "cost >= reserved * 2", "llm exceeded flag"),
    (
        B,
        'FREE_STATUSES = ("BUDGET_BLOCKED", "NOT_SENT")',
        'FREE_STATUSES = ("BUDGET_BLOCKED",)',
        "llm not-sent is free",
    ),
    (B, 'Decimal("Infinity")', "Decimal(0)", "llm unknown cost counts"),
    (B, "if agent_exceeded:", "if False:", "llm exceeded blocks"),
    (Y, "MAX_MID_DRIFT = 0.02", "MAX_MID_DRIFT = 0.04", "llm mid drift"),
    (Y, "MAX_SNAPSHOT_AGE = 120", "MAX_SNAPSHOT_AGE = 125", "llm snapshot age"),
    (Y, "fresh = refresh_quotes()", "fresh = qs", "llm quote recheck"),
    (Y, "current_weight(p, s, qs),", "0.0,", "llm depot weight in prompt"),
    (Y, "if trigger is None:", "if False and trigger is None:", "llm due respected"),
    (
        Y,
        "if hourly_limit_reached(",
        "if False and hourly_limit_reached(",
        "llm hourly cap respected",
    ),
    (Y, 'if p["halted"]:', "if False:", "llm halted skip"),
    (OC, "store=False", "store=True", "llm store false"),
    (OC, '"strict": True', '"strict": False', "llm strict schema"),
    (OC, "MAX_RETRIES = 2", "MAX_RETRIES = 3", "llm retries"),
    (OC, "TIMEOUT_SECONDS = 60.0", "TIMEOUT_SECONDS = 61.0", "llm timeout"),
    (Y, "decided_at - snap.fetched_at > MAX_SNAPSHOT_AGE", "False", "llm answer age recheck"),
    (
        Y,
        'return _outcome(agent, "NO_QUOTES"',
        'pass  # return _outcome(agent, "NO_QUOTES"',
        "llm fresh quotes before request",
    ),
    (
        Y,
        "run_strategy(n, snap, ind, 0.0)",
        "run_strategy(n, snap, ind, 0.25)",
        "llm triggers ignore own weight",
    ),
    (Y, "* (reply.requests - 1)", "* 0", "llm retries billed"),
    (Y, "max_requests=1 + MAX_RETRIES", "max_requests=1", "llm reservation covers retries"),
    (Y, "bar_ts = int(decided_at) * 10000", "bar_ts = int(now_epoch) * 10000", "llm decision time"),
    (
        OC,
        "except (openai.RateLimitError, openai.InternalServerError):",
        "except openai.RateLimitError:",
        "llm retry on 5xx",
    ),
    (OC, "max_retries=0", "max_retries=2", "llm no hidden sdk retries"),
    (OC, "RETRY_PAUSES = (1.0, 2.0)", "RETRY_PAUSES = (1.0, 3.0)", "llm retry pauses"),
    (
        B,
        "if self.input_per_million <= 0 or self.output_per_million <= 0:",
        "if False:",
        "llm zero price refused",
    ),
    (
        A,
        '"gpt-luna": ("gpt-6-luna", Decimal("0.50"))',
        '"gpt-luna": ("gpt-6-luna", Decimal("0.51"))',
        "llm luna share",
    ),
    (A, 'env.get("LLM_EFFORT", "low")', 'env.get("LLM_EFFORT", "medium")', "llm effort default"),
    (A, "MAX_OUTPUT_TOKENS = 8000", "MAX_OUTPUT_TOKENS = 7999", "llm output tokens"),
]


# Spec 011 (ADR-0021): trading pause and release of stopped depots.
PC, PR, SR = "paper/cycle.py", "paper/risk.py", "service/run.py"
M += [
    (
        PC,
        "mark(con, name, latest, now, costs)",
        "mark(con, name, latest, now, Costs())",
        "cycle stop costs",
    ),
    (
        PC,
        "    latest = feed.current()\n",
        "    latest = fresh_quotes(con, now_epoch=clock())\n",
        "cycle final refetch",
    ),
    (
        PC,
        "self.fetched_at = float(int(self.clock()))",
        "self.fetched_at = self.clock()",
        "cycle quote age floor",
    ),
    (
        SR,
        "ran = report.equity or report.unvalued",
        "ran = report.equity",
        "service unvalued incomplete",
    ),
    (SR, "if (agent, why) not in self.refusals:", "if True:", "service refusal once"),
    (
        SR,
        "self.refusals = {r for r in self.refusals if r[0] != agent}",
        "pass",
        "service refusal reset",
    ),
    (PC, 'status="PAUSED" if paused else "OK"', 'status="OK"', "pause classic trades"),
    (PC, "== set(SYMBOLS) and not controls.paused:", "== set(SYMBOLS):", "pause buyhold buys"),
    (
        PC,
        "paused=controls.paused,\n            ):",
        "paused=False,\n            ):",
        "pause gpt asked",
    ),
    (
        Y,
        "    if paused:\n        # ADR-0021",
        "    if False:\n        # ADR-0021",
        "llm pause skips",
    ),
    (PR, "RESUME_MAX_AGE = 24 * 3600", "RESUME_MAX_AGE = 25 * 3600", "resume max age"),
    (PR, "requested_at > RESUME_MAX_AGE", "requested_at >= RESUME_MAX_AGE", "resume age > -> >="),
    (PR, "requested_at <= epoch(traded)", "requested_at < epoch(traded)", "resume after trade"),
    (PR, "traded is not None and requested_at", "False and requested_at", "resume older trade"),
    (
        PR,
        "SELECT MAX(ts) FROM trades WHERE agent=?",
        "SELECT MAX(ts) FROM trades WHERE side='RISK_SELL' AND agent=?",
        "resume any trade",
    ),
    (PR, 'if p["btc"] != 0 or p["eth"] != 0:', "if False:", "resume needs no coins"),
    (PR, "halted=0, peak=cash WHERE", "halted=0 WHERE", "resume peak from cash"),
    (PR, "if requested_at > now_epoch:", "if requested_at > now_epoch + 60:", "resume future"),
    (
        PR,
        '{"1": True, "0": False, "": False}',
        '{"1": True, "0": False, "": False, "yes": True}',
        "pause values",
    ),
    (
        PR,
        "        stored = None\n        # Say what",
        "        stored = {}\n        # Say what",
        "pause unreadable file",
    ),
    (PR, "if PAUSE_FIELD in env:", "if False:", "pause environment wins"),
    (
        PR,
        "        return Controls(\n            True, ENVIRONMENT if",
        "        return Controls(\n            False, ENVIRONMENT if",
        "pause invalid fails closed",
    ),
]


# Spec 012 (ADR-0022): the broker seam and the live locks.
BI, BL, BP, BC, BS = (
    "broker/__init__.py",
    "broker/live.py",
    "broker/paper.py",
    "broker/contract.py",
    "broker/intents.py",
)
M += [
    (
        BL,
        "LiveTradingLocked(NotImplementedError)",
        "LiveTradingLocked(NotImplementedError)",
        "broker noop control",
    ),
    (
        BL,
        '    raise LiveTradingLocked("live trading is locked until M2 (ADR-0004)")',
        "    return None  # type: ignore[return-value]",
        "broker live raises",
    ),
    (BI, "    refuse_live()", "    return PaperBroker()", "broker make_broker live"),
    (BP, "            partial=partial,", "            partial=False,", "broker paper partial"),
    (BP, "            stops=stops,", "            stops=None,", "broker paper stops"),
    (BP, "            status=status,", '            status="OK",', "broker paper status"),
    (
        BP,
        "return execution.mark(con, agent, qs, now, costs)",
        "return execution.mark(con, agent, qs, now, Costs())",
        "broker paper mark costs",
    ),
    (
        BC,
        "if type(self.operator_id) is not int or self.operator_id <= 0:",
        "if self.operator_id <= 0:",
        "broker operator_id int",
    ),
    (
        BC,
        "uuid.UUID) or self.client_order_id.version != 4:",
        "uuid.UUID):",
        "broker uuid4",
    ),
    (
        BC,
        "if (self.amount_quote is None) == (self.amount_base is None):",
        "if False:",
        "broker exactly one amount",
    ),
    (
        BC,
        "if type(value) is not Decimal:",
        "if not isinstance(value, Decimal | float | int):",
        "broker decimal only",
    ),
    (
        BC,
        "if self.settled and (self.fee is None or not self.fee_currency):",
        "if False:",
        "broker settled fee",
    ),
    (BC, "if not with_fills and self.fills:", "if False:", "broker no fills on failure"),
    (BC, "if with_fills and not self.fills:", "if False:", "broker fills on success"),
    (
        BC,
        "return OrderState.PARTIAL if fills else OrderState.EXPIRED",
        "return OrderState.EXPIRED",
        "broker partial by fills",
    ),
    (
        BC,
        "ROUND_FLOOR if side is OrderSide.BUY else ROUND_CEILING",
        "ROUND_CEILING if side is OrderSide.BUY else ROUND_CEILING",
        "broker buy limit rounds down",
    ),
    (
        BC,
        "ROUND_FLOOR if side is OrderSide.BUY else ROUND_CEILING",
        "ROUND_FLOOR if side is OrderSide.BUY else ROUND_FLOOR",
        "broker sell limit rounds up",
    ),
    (
        BC,
        'return _money("amount", amount).quantize(step, ROUND_DOWN)',
        'return _money("amount", amount).quantize(step, ROUND_CEILING)',
        "broker amount rounds down",
    ),
    (BC, 'if self.status != "trading":', "if False:", "broker market status"),
    (BC, "base < self.min_base:", "base <= self.min_base:", "broker min base edge"),
    (BC, "if type(value) is not str:", "if False:", "broker markets strings"),
    (
        BS,
        "S.UNKNOWN: frozenset({S.FILLED, S.PARTIAL, S.EXPIRED}),",
        "S.UNKNOWN: frozenset({S.FILLED, S.PARTIAL, S.EXPIRED, S.SENT}),",
        "broker unknown resend",
    ),
    (
        BS,
        "S.PLANNED: frozenset({S.SENT, S.UNKNOWN, S.ABORTED}),",
        "S.PLANNED: frozenset({S.SENT, S.UNKNOWN, S.ABORTED, S.FILLED}),",
        "broker planned skips send",
    ),
    (
        BS,
        "return S.UNKNOWN if state is S.PLANNED else state",
        "return state",
        "broker restart planned unknown",
    ),
    (
        BS,
        "OPEN = frozenset({S.PLANNED, S.SENT, S.UNKNOWN})",
        "OPEN = frozenset({S.PLANNED, S.SENT})",
        "broker may_plan unknown",
    ),
    # Spec 016 AK3: the named mutants of the historical client.
    (
        "lab/history.py",
        "lo = max(0, hi - CANDLE_LIMIT)",
        "lo = max(0, hi - CANDLE_LIMIT + 1)",
        "lab window 500->499",
    ),
    (
        # bisect over open <= now - step is the live filter open + step <= now; one second less
        # is open + step < now.
        "lab/history.py",
        "closed = bisect_right(series.ts, now_epoch - step, lo, hi)",
        "closed = bisect_right(series.ts, now_epoch - step - 1, lo, hi)",
        "lab closed filter <= -> <",
    ),
    (
        "lab/history.py",
        "hi = bisect_right(series.ts, now_epoch)",
        "hi = bisect_right(series.ts, now_epoch - step)",
        "lab forming bar not counted",
    ),
    (
        "lab/history.py",
        "last = bisect_right(series.ts, now - MINUTE) - 1",
        "last = bisect_right(series.ts, now) - 1",
        "lab quote from the next minute",
    ),
    ("lab/history.py", "received=hi - lo,", "received=closed - lo,", "lab received count"),
    ("lab/history.py", "forming=closed < hi,", "forming=False,", "lab forming flag"),
    (
        "lab/history.py",
        "first_open=series.ts[lo] if hi > lo else None,",
        "first_open=series.ts[0] if hi > lo else None,",
        "lab first open",
    ),
    (
        "lab/history.py",
        "quote = Quote(mid * (1 - half), mid * (1 + half))",
        "quote = Quote(mid * (1 - half), mid * (1 + 2 * half))",
        "lab quote spread",
    ),
    (
        "lab/history.py",
        "table[_open_time(row)] = row",
        "table.setdefault(_open_time(row), row)",
        "lab later capture wins",
    ),
    (
        "lab/history.py",
        "if not self._start <= epoch < self._end:",
        "if not self._start <= epoch <= self._end:",
        "lab preload end exclusive",
    ),
    # Spec 016 AK14: the lab's USDC route (synthetic quotes, backtest costs, fidelity).
    (
        "lab/history.py",
        "(Symbol.BTCUSDC, btc_eur_mid / self.usdc_eur, self.btc_usdc_spread),",
        "(Symbol.BTCUSDC, btc_eur_mid / self.usdc_eur, self.usdc_eur_spread),",
        "lab usdc route btc spread",
    ),
    (
        "lab/backtest.py",
        "spread[ROUTE_SPREAD] = route.spread",
        "spread[ROUTE_SPREAD] = spread[Symbol.BTCEUR.market]",
        "lab usdc spread of the route",
    ),
    # Review finding: the route's costs and spread belong to the USDC depots' BTC, not to the
    # market - plain buy & hold runs along in the usdc variant on BTC-EUR.
    (
        "lab/model.py",
        "        if routed(agent, market):\n            return usdc.ROUTE_FEE",
        '        if self.usdc and market == "BTC-EUR":\n            return usdc.ROUTE_FEE',
        "lab usdc route cost per market",
    ),
    (
        "lab/model.py",
        "        if routed(agent, market):\n            return self.spread.get(ROUTE_SPREAD",
        '        if ROUTE_SPREAD in self.spread and market == "BTC-EUR":\n'
        "            return self.spread.get(ROUTE_SPREAD",
        "lab usdc route spread per market",
    ),
    (
        "lab/model.py",
        "return usdc.ROUTE_SLIPPAGE if routed(agent, market) else self.slippage",
        'return usdc.ROUTE_SLIPPAGE if self.usdc and market == "BTC-EUR" else self.slippage',
        "lab usdc route slippage per market",
    ),
    (
        "lab/backtest.py",
        "if qty > 0 and usdc.routed(agent, symbol):",
        "if False:",
        "lab usdc sold over the route",
    ),
    (
        "lab/fidelity.py",
        "cost = usdc.roundtrip_cost(route)",
        "cost = cost_roundtrip(snap.quote.spread_bps, fee, slip)",
        "lab fidelity route costs",
    ),
    (
        "lab/fidelity.py",
        "if agent in BENCHMARKS:",
        "if agent == BUYHOLD:",
        "lab fidelity skips buyhold-usdc",
    ),
    (
        "core.py",
        '        raise NotImplementedError("live trading is not enabled in this version")',
        "        return TradingMode.PAPER",
        "cli live refused",
    ),
    (
        "cli.py",
        '    mode = parse_mode(os.environ.get("TRADING_MODE"))',
        "    mode = parse_mode(None)",
        "cli parse_mode call",
    ),
]

# Spec 020 (ADR-0030): the USDC route. Expected values in its tests are written out by hand, so
# each of these must be killed by them alone.
U, UB, UO, UW = (
    "paper/usdc.py",
    "market/bitvavo.py",
    "paper/observations.py",
    "web/overview_page.py",
)
UA, UC, UP, UV = "llm/agents.py", "web/credentials.py", "web/pages.py", "web/overview.py"
USDC = [
    (
        U,
        "fee = spend * USDC_EUR_FEE + usdc * BTC_USDC_FEE * eur_per_usdc",
        "fee = spend * BTC_USDC_FEE + usdc * USDC_EUR_FEE * eur_per_usdc",
        "usdc fee legs swapped",
    ),
    (
        U,
        "fee = spend * USDC_EUR_FEE + usdc * BTC_USDC_FEE * eur_per_usdc",
        "fee = spend * USDC_EUR_FEE + usdc * USDC_EUR_FEE * eur_per_usdc",
        "usdc buy btc leg charged the conversion fee",
    ),
    (
        U,
        "+ notional * USDC_EUR_FEE",
        "+ notional * BTC_USDC_FEE",
        "usdc sell conversion leg charged the btc fee",
    ),
    (U, "BTC_USDC_FEE = 0.0005", "BTC_USDC_FEE = 0.0010", "usdc btc fee .05->.10"),
    (U, "USDC_EUR_FEE = 0.0010", "USDC_EUR_FEE = 0.0005", "usdc conversion fee .10->.05"),
    (U, "BTC_USDC_SLIPPAGE = 0.0005", "BTC_USDC_SLIPPAGE = 0.0006", "usdc btc slippage"),
    (U, "USDC_EUR_SLIPPAGE = 0.0001", "USDC_EUR_SLIPPAGE = 0.0002", "usdc conversion slippage"),
    (
        U,
        ".ask * (1 + USDC_EUR_SLIPPAGE)",
        ".ask * (1 + USDC_EUR_FEE)",
        "usdc conversion fee<->slippage",
    ),
    (
        U,
        "    usdc = spend / eur_per_usdc\n",
        "    usdc = spend\n",
        "usdc buy conversion leg forgotten",
    ),
    (
        U,
        "    notional = usdc * eur_per_usdc\n",
        "    notional = usdc\n",
        "usdc sell conversion leg forgotten",
    ),
    (
        U,
        "return qs[Symbol.BTCUSDC].mid * qs[Symbol.USDCEUR].mid",
        "return qs[Symbol.BTCUSDC].mid",
        "usdc valuation conversion forgotten",
    ),
    (
        U,
        "qs[Symbol.USDCEUR].ask * (1 + USDC_EUR_SLIPPAGE)",
        "qs[Symbol.USDCEUR].bid * (1 + USDC_EUR_SLIPPAGE)",
        "usdc buy conversion at the bid",
    ),
    (
        U,
        "qs[Symbol.BTCUSDC].ask * (1 + BTC_USDC_SLIPPAGE)",
        "qs[Symbol.BTCUSDC].bid * (1 + BTC_USDC_SLIPPAGE)",
        "usdc buy btc at the bid",
    ),
    (
        U,
        "qs[Symbol.BTCUSDC].bid * (1 - BTC_USDC_SLIPPAGE)",
        "qs[Symbol.BTCUSDC].ask * (1 - BTC_USDC_SLIPPAGE)",
        "usdc sell btc at the ask",
    ),
    (
        U,
        "qs[Symbol.USDCEUR].bid * (1 - USDC_EUR_SLIPPAGE)",
        "qs[Symbol.USDCEUR].ask * (1 - USDC_EUR_SLIPPAGE)",
        "usdc sell conversion at the ask",
    ),
    (
        U,
        "].ask * (1 + USDC_EUR_SLIPPAGE)",
        "].ask * (1 - USDC_EUR_SLIPPAGE)",
        "usdc buy conversion slippage sign",
    ),
    (
        U,
        "].ask * (1 + BTC_USDC_SLIPPAGE)",
        "].ask * (1 - BTC_USDC_SLIPPAGE)",
        "usdc buy btc slippage sign",
    ),
    (
        U,
        "].bid * (1 - BTC_USDC_SLIPPAGE)",
        "].bid * (1 + BTC_USDC_SLIPPAGE)",
        "usdc sell btc slippage sign",
    ),
    (
        U,
        "].bid * (1 - USDC_EUR_SLIPPAGE)",
        "].bid * (1 + USDC_EUR_SLIPPAGE)",
        "usdc sell conversion slippage sign",
    ),
    # "Gebuehr vertauscht" as the spec means it: the BTC leg charged the fee of BTC-EUR.
    (U, "BTC_USDC_FEE = 0.0005", "BTC_USDC_FEE = 0.0025", "usdc btc leg with the btc-eur fee"),
    (
        U,
        "ROUTE_FEE = BTC_USDC_FEE + USDC_EUR_FEE",
        "ROUTE_FEE = BTC_USDC_FEE",
        "usdc route fee without the conversion leg",
    ),
    (
        U,
        "ROUTE_SLIPPAGE = BTC_USDC_SLIPPAGE + USDC_EUR_SLIPPAGE",
        "ROUTE_SLIPPAGE = BTC_USDC_SLIPPAGE",
        "usdc route slippage without the conversion leg",
    ),
    (
        U,
        "return symbol is Symbol.BTCEUR and is_usdc(agent)",
        "return is_usdc(agent)",
        "usdc only btc is routed",
    ),
    (
        U,
        "return agent.removesuffix(USDC_SUFFIX)",
        'return agent.removesuffix(USDC_SUFFIX).replace("breakout", "trend")',
        "usdc rules of the own base strategy",
    ),
    (
        U,
        "fees = BTC_USDC_FEE + BTC_USDC_SLIPPAGE + USDC_EUR_FEE + USDC_EUR_SLIPPAGE",
        "fees = BTC_USDC_FEE + BTC_USDC_SLIPPAGE",
        "usdc signal cost without the conversion leg",
    ),
    (
        U,
        " + qs[Symbol.USDCEUR].spread_bps / 10000",
        "",
        "usdc signal cost without the usdc-eur spread",
    ),
    (
        PC,
        "roundtrip = usdc.roundtrip_cost(qs)",
        "roundtrip = cost_roundtrip(snap.quote.spread_bps, costs.fee_rate, costs.slippage)",
        "usdc signals priced with eur costs",
    ),
    (
        PC,
        "fee_rate, slippage = usdc.ROUTE_FEE, usdc.ROUTE_SLIPPAGE",
        "fee_rate, slippage = costs.fee_rate, costs.slippage",
        "usdc context with eur costs",
    ),
    (PC, "sig = replace(sig, version=usdc.USDC_VERSION)", "pass", "usdc version recorded"),
    (
        PC,
        "for agent in (*config.classic, *config.usdc):",
        "for agent in config.classic:",
        "usdc depots decide",
    ),
    (
        PC,
        "classic_qs = feed.current() if config.classic or config.usdc else {}",
        "classic_qs = feed.current() if config.classic else {}",
        "usdc depots alone get quotes",
    ),
    (
        PC,
        'usdc.route_markets(p.get("agent", ""), symbol)',
        "(symbol,)",
        "usdc decision needs the route quotes",
    ),
    (
        PC,
        "markets=QUOTED if config.usdc else SYMBOLS",
        "markets=QUOTED",
        "usdc quotes stored only with usdc depots",
    ),
    (
        PC,
        "if all(m in start_qs for m in BUYHOLD_USDC_MARKETS):",
        "if True:",
        "usdc benchmark waits for its quotes",
    ),
    (
        E,
        "if usdc.is_usdc(agent) and qs is not None and not covers(p, qs, symbol):",
        "if False:",
        "usdc stale without route quotes",
    ),
    (E, "for m in usdc.route_markets(agent, s)", "for m in (s,)", "usdc held btc needs the route"),
    (
        E,
        'if usdc.routed(p.get("agent", ""), symbol):\n        return _routed_worth(p, qs)\n',
        "",
        "usdc valued over the route",
    ),
    (
        E,
        "    if usdc.routed(agent, symbol):\n        _trade_routed(",
        "    if False:\n        _trade_routed(",
        "usdc traded over the route",
    ),
    (E, "            Symbol.BTCUSDC.name,", "            Symbol.BTCEUR.name,", "usdc trade market"),
    (
        E,
        "            usdc.ROUTE_SLIPPAGE,\n            decision_id,",
        "            ARENA_SLIPPAGE,\n            decision_id,",
        "usdc trade slip rate",
    ),
    (
        E,
        "market, slip = Symbol.BTCUSDC, usdc.ROUTE_SLIPPAGE",
        "market, slip = Symbol.BTCEUR, usdc.ROUTE_SLIPPAGE",
        "usdc risk sale market",
    ),
    (
        E,
        "market, slip = Symbol.BTCUSDC, usdc.ROUTE_SLIPPAGE",
        "market, slip = Symbol.BTCUSDC, costs.slippage",
        "usdc risk sale slip rate",
    ),
    (
        E,
        "return all(m in qs for m in usdc.ROUTE_MARKETS) and covers(p, qs)",
        "return all(m in qs for m in usdc.ROUTE_MARKETS)",
        "usdc route covers the other holdings too",
    ),
    (
        E,
        '    if p["btc"] == 0:\n        return 0.0\n    missing',
        "    if False:\n        return 0.0\n    missing",
        "usdc no btc needs no route quote",
    ),
    (
        E,
        '(agent, usdc.trade_market(agent, symbol).name, "BUY")',
        '(agent, symbol.name, "BUY")',
        "usdc benchmark buys once after a restart",
    ),
    (
        E,
        "    diff = desired - present\n    if abs(diff) < max(MIN_TRADE_EUR, REBALANCE_BAND * eq):"
        "\n        return\n    if diff > 0:\n        other = sum(_worth(p, s, qs) for s in SYMBOLS"
        " if s is not",
        "    diff = desired - present\n    if abs(diff) <= max(MIN_TRADE_EUR, REBALANCE_BAND * eq):"
        "\n        return\n    if diff > 0:\n        other = sum(_worth(p, s, qs) for s in SYMBOLS"
        " if s is not",
        "usdc routed band < -> <=",
    ),
    (
        E,
        "if spend < MIN_TRADE_EUR:\n            return\n        done = usdc.buy",
        "if spend <= MIN_TRADE_EUR:\n            return\n        done = usdc.buy",
        "usdc routed min spend < -> <=",
    ),
    (
        E,
        "if done.notional < MIN_TRADE_EUR:",
        "if done.notional <= MIN_TRADE_EUR:",
        "usdc routed min sell < -> <=",
    ),
    (
        E,
        'qty = min(p["btc"], abs(diff) / usdc.btc_mid(qs))',
        "qty = abs(diff) / usdc.btc_mid(qs)",
        "usdc routed sell qty cap",
    ),
    (
        E,
        "other = sum(_worth(p, s, qs) for s in SYMBOLS if s is not Symbol.BTCEUR)",
        "other = 0.0",
        "usdc routed total cap",
    ),
    (E, "(spend + done.fee, done.qty, agent)", "(spend, done.qty, agent)", "usdc routed buy fee"),
    (
        E,
        "(done.notional - done.fee, qty, agent)",
        "(done.notional, qty, agent)",
        "usdc routed sell fee",
    ),
    (
        E,
        "            if usdc.routed(agent, symbol):\n                sale = usdc.sell(qty, qs)",
        "            if False:\n                sale = usdc.sell(qty, qs)",
        "usdc risk sale over the route",
    ),
    (
        E,
        "BENCHMARKS = (BUYHOLD, usdc.BUYHOLD_USDC)",
        "BENCHMARKS = (BUYHOLD,)",
        "usdc benchmark exempt and holds",
    ),
    (
        E,
        "    if not all(m in qs for m in BUYHOLD_USDC_MARKETS):\n        raise",
        "    if False:\n        raise",
        "usdc benchmark needs its quotes",
    ),
    (
        E,
        "    for m in BUYHOLD_USDC_MARKETS:\n        qs[m].validate()\n",
        "",
        "usdc benchmark validates",
    ),
    (
        E,
        '            if r["symbol"] not in wanted:\n                continue\n',
        "",
        "usdc fresh quotes default to v0.3.1 markets",
    ),
    (
        UB,
        "by_market = {symbol.market: symbol for symbol in QUOTED}",
        "by_market = {symbol.market: symbol for symbol in SYMBOLS}",
        "usdc quote markets parsed",
    ),
    (
        UB,
        "        for s in SYMBOLS:\n            if s not in result and s not in errors:",
        "        for s in QUOTED:\n            if s not in result and s not in errors:",
        "usdc missing named only for candle markets",
    ),
    (
        UO,
        "        for symbol in QUOTED:\n            quote = book.get(symbol)",
        "        for symbol in SYMBOLS:\n            quote = book.get(symbol)",
        "usdc books observed",
    ),
    (UW, "        if d.usdc:\n", "        if False:\n", "usdc badge shown"),
    (
        UA,
        "return (BUYHOLD, BUYHOLD_USDC) if self.usdc else (BUYHOLD,)",
        "return (BUYHOLD,)",
        "usdc benchmark runs with the usdc depots",
    ),
    (
        UA,
        '",".join(USDC_AGENTS), USDC_AGENTS))',
        '",".join(USDC_AGENTS), USDC_AGENTS[:1]))',
        "usdc every depot selectable",
    ),
    (UA, '",".join(USDC_AGENTS), USDC_AGENTS))', '"", USDC_AGENTS))', "usdc on by default"),
    (
        UA,
        '"BOT_AGENTS", DEFAULT_AGENTS, (*CLASSIC_AGENTS, *LLM_DEFAULTS))',
        '"BOT_AGENTS", DEFAULT_AGENTS, (*CLASSIC_AGENTS, *USDC_AGENTS, *LLM_DEFAULTS))',
        "usdc depots refused in BOT_AGENTS",
    ),
    (
        UC,
        "AGENT_CHOICES = (*LLM_DEFAULTS, *CLASSIC_AGENTS, *USDC_AGENTS)",
        "AGENT_CHOICES = (*LLM_DEFAULTS, *CLASSIC_AGENTS)",
        "usdc depots offered in the settings",
    ),
    (
        UC,
        'values["BOT_AGENTS"] = ",".join(a for a in chosen if a not in USDC_AGENTS)',
        'values["BOT_AGENTS"] = ",".join(chosen)',
        "usdc choice kept out of BOT_AGENTS (rollback)",
    ),
    (
        UC,
        'values[USDC_FIELD] = ",".join(a for a in chosen if a in USDC_AGENTS)',
        "pass",
        "usdc choice none stored",
    ),
    (
        UP,
        'selected += values.get(USDC_FIELD, ",".join(USDC_AGENTS)).split(",")',
        'selected += values.get(USDC_FIELD, "").split(",")',
        "usdc tiles ticked by default",
    ),
    (
        UV,
        "and (in_use or now - epoch(ts) <= DELAYED_SECONDS)",
        "",
        "usdc unused quotes hidden",
    ),
    (
        UV,
        "(in_use or now - epoch(ts) <= DELAYED_SECONDS)",
        "(now - epoch(ts) <= DELAYED_SECONDS)",
        "usdc quotes in use stay visible",
    ),
    (UV, "if s.name in shown and ", "if ", "usdc unused quotes left out of the ticker"),
]
M += USDC
USDC_LABELS = {label for *_, label in USDC}

# Top-level modules and the tests that cover them (Spec 012).
TOP_LEVEL = {"core.py": "cli", "cli.py": "cli"}
GROUP_TESTS = {
    "cli": ["tests/test_core.py", "tests/test_cli.py"],
    "usdc": [
        "tests/market/test_usdc_quotes.py",
        "tests/paper/test_usdc_execution.py",
        "tests/paper/test_usdc_depots.py",
        "tests/strategy/test_usdc_costs.py",
        "tests/web/test_usdc_overview.py",
    ],
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for item in ("src", "tests", "tools", "deploy", "scripts", "pyproject.toml", "uv.lock"):
            source = REPO / item
            if source.is_dir():
                shutil.copytree(source, root / item, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(source, root / item)
        package = root / "src" / "llm_trading_bot"
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        failures = []
        # Spec 016 AK3 names the group as a plain argument; "--only GROUP" works as well.
        args = [a for a in sys.argv[1:] if a != "--only"]
        only = args[0] if args else None

        def group_of(fname: str, label: str) -> str:
            if label in USDC_LABELS:
                return "usdc"
            if fname in TOP_LEVEL:
                return TOP_LEVEL[fname]
            return fname.split("/")[0] if "/" in fname else "strategy"

        selected = [m for m in M if only is None or group_of(m[0], m[3]) == only]
        for fname, old, new, label in selected:
            group = group_of(fname, label)
            path = package / (fname if "/" in fname or fname in TOP_LEVEL else f"strategy/{fname}")
            tests = GROUP_TESTS.get(group, [f"tests/{group}"])
            original = path.read_text(encoding="utf-8")
            if old not in original:
                print(f"{'PATTERN MISSING':16} {label}")
                failures.append(label)
                continue
            if group == "usdc" and original.count(old) != 1:
                # A docstring repeating the code would take the mutation instead (Spec 020).
                print(f"{'PATTERN AMBIGUOUS':16} {label}")
                failures.append(label)
                continue
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "-x",
                        "-p",
                        "no:cacheprovider",
                        *tests,
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
            finally:
                path.write_text(original, encoding="utf-8")
            status = "KILLED" if result.returncode else "SURVIVED"
            expected_survivor = label in EQUIVALENT
            note = "  (equivalent, expected)" if expected_survivor else ""
            print(f"{status:16} {label}{note}")
            if status == "SURVIVED" and not expected_survivor:
                failures.append(label)
        print()
        print(
            f"{len(selected) - len(failures)}/{len(selected)} as expected; "
            f"unexpected survivors: {failures}"
        )
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
