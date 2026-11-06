"""Mutation check for the strategy golden tests (Spec 003 review, ADR-0007).

Applies one small change at a time (a threshold, weight, window, order) to a temporary copy of
the strategy code and runs tests/strategy. Every mutation must be KILLED; a SURVIVED mutation means
the golden fixtures would not notice that change. Mutation list from the independent review.

Usage: uv run python tools/golden/mutation_check.py
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
EQUIVALENT = {"ensemble buy thr .55->.6", "noop control"}

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
        strategy = root / "src" / "llm_trading_bot" / "strategy"
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        failures = []
        for fname, old, new, label in M:
            path = strategy / fname
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
                        "tests/strategy",
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
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
        print(f"{len(M) - len(failures)}/{len(M)} as expected; unexpected survivors: {failures}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
