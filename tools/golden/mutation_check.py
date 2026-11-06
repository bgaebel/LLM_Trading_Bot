"""Mutation check for the strategy golden tests (Spec 003 review, ADR-0007).

Applies one small change at a time (a threshold, weight, window, order) to a temporary copy of
the strategy code and runs tests/strategy. Every mutation must be KILLED; a SURVIVED mutation means
the golden fixtures would not notice that change. Mutation list from the independent review.

Spec 004 adds mutations of the paper execution (paper/execution.py, tests/paper).

Usage: uv run python tools/golden/mutation_check.py [--only strategy|paper]
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
    "exec cash cap removed",
    "exec sell clamp at 0",
    "exec total room floor",
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
        "if agent == BUYHOLD and p[_COLUMN[symbol]] > 0:",
        "if agent == BUYHOLD and p[_COLUMN[symbol]] > 1:",
        "exec buyhold holds once",
    ),
    (E, "if qs is None or set(qs) != set(SYMBOLS):", "if qs is None:", "exec stale on partial"),
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
    (E, "            mark(con, agent, qs, now)\n", "", "exec mark after trade"),
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


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for item in ("src", "tests", "pyproject.toml"):
            source = REPO / item
            if source.is_dir():
                shutil.copytree(source, root / item, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(source, root / item)
        package = root / "src" / "llm_trading_bot"
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        failures = []
        only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
        selected = [
            m for m in M if only is None or (m[0].startswith("paper/") == (only == "paper"))
        ]
        for fname, old, new, label in selected:
            group = "paper" if fname.startswith("paper/") else "strategy"
            path = package / (fname if group == "paper" else f"strategy/{fname}")
            original = path.read_text(encoding="utf-8")
            if old not in original:
                print(f"{'PATTERN MISSING':16} {label}")
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
                        f"tests/{group}",
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
