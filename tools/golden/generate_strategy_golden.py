"""Generate strategy golden fixtures from the Arena's own strategies.py (Spec 003, ADR-0007).

Usage:
    uv run python -I tools/golden/generate_strategy_golden.py <arena>/apps/arena [--commit SHA]

The Arena code is loaded from the given path only while generating; the bot never imports it.
Output: tests/strategy/fixtures/strategy_golden_v0.3.1.json (scenario params + expected outputs).
"""

import argparse
import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "strategy" / "fixtures" / "strategy_golden_v0.3.1.json"
FEE = 0.0025
SLIPPAGE = 0.0005
WEIGHTS = (0.0, 0.25, 0.45)
STRATEGIES = ("trend", "breakout", "meanrev", "ensemble", "buyhold")
REQUIRED = {(s, a) for s in STRATEGIES[:4] for a in ("BUY", "SELL", "HOLD")} | {("buyhold", "BUY")}
REGIMES = {"TREND", "RANGE", "TRANSITION"}


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


class Quote:
    """Same formulas as llm_trading_bot.market.models.Quote."""

    def __init__(self, bid: float, ask: float) -> None:
        self.bid, self.ask = bid, ask

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return 10_000 * (self.ask - self.bid) / self.mid


def evaluate(arena: ModuleType, sc: ModuleType, p: dict[str, Any]) -> dict[str, Any]:
    rows = sc.build_bars(p)
    bars = {tf: tuple(arena.Candle(*r) for r in rows[tf]) for tf in rows}
    bid, ask = sc.quote_for(p, rows)
    quote = Quote(bid, ask)
    snap = arena.MultiSnapshot("BTCEUR", bars, quote, sc.NOW)
    # Same expression as engine03.cost_roundtrip.
    cost = 2 * (FEE + SLIPPAGE) + quote.spread_bps / 10000
    ind = arena.indicators(snap, cost)
    return {
        "params": sc.as_jsonable(p),
        "roundtrip_cost_frac": cost,
        "indicators": ind,
        "signals": {
            str(w): {name: arena.run_strategy(name, snap, ind, w) for name in STRATEGIES}
            for w in WEIGHTS
        },
        "payload": {str(w): arena.feature_payload(snap, ind, w, FEE, SLIPPAGE) for w in WEIGHTS},
    }


def branches(case: dict[str, Any]) -> set[tuple[str, str]]:
    return {(n, s["action"]) for per_w in case["signals"].values() for n, s in per_w.items()}


def indicator_cases(arena: ModuleType) -> list[dict[str, Any]]:
    """Short-series fallbacks and edge values of the raw indicator functions."""
    flat = [100.0] * 20
    rising = [100.0 + i for i in range(20)]
    short = [100.0, 101.0, 99.0]

    def rows(n: int) -> list[list[float]]:
        return [[i * 60, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1.0] for i in range(n)]

    def candles(n: int) -> tuple[Any, ...]:
        return tuple(arena.Candle(*r) for r in rows(n))

    cases: list[dict[str, Any]] = []
    for name, values, n in [
        ("rsi", short, 14),
        ("rsi", flat, 14),
        ("rsi", rising, 14),
        ("rsi", rising[:15], 14),
    ]:
        cases.append({"fn": name, "values": values, "n": n, "expected": arena.rsi(values, n)})
    for values, n in [(short, 3), (rising, 5), ([5.0], 10)]:
        cases.append({"fn": "ema", "values": values, "n": n, "expected": arena.ema(values, n)})
    for count in (5, 15, 40):
        cases.append(
            {"fn": "atr", "rows": rows(count), "n": 14, "expected": arena.atr(candles(count), 14)}
        )
        cases.append(
            {
                "fn": "adx",
                "rows": rows(count),
                "n": 14,
                "expected": list(arena.adx(candles(count), 14)),
            }
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("arena_dir", type=Path, help="path to <LLM-Trading-Arena>/apps/arena")
    parser.add_argument("--commit", help="Arena commit; default: git rev-parse in arena_dir")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    arena = load("arena_strategies", args.arena_dir / "strategies.py")
    sc = load("golden_scenarios", REPO / "tests" / "golden_scenarios.py")
    commit = (
        args.commit
        or subprocess.run(
            ["git", "-C", str(args.arena_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
    )

    cases = [evaluate(arena, sc, p) for p in sc.grid()]
    hit = set().union(*(branches(c) for c in cases))
    regimes = {c["indicators"]["regime"] for c in cases}

    # Targeted, deterministic search for branches the base grid does not reach.
    search = itertools.product(
        range(2, 400),
        (0.004, -0.004, 0.0, 0.0015, -0.0015, 0.0005),
        (0.001, 0.002, 0.006),
        ("", "spike", "dip", "dump"),
    )
    for seed, drift, vol, special in search:
        missing = (REQUIRED - hit, REGIMES - regimes)
        if not any(missing):
            break
        p = sc.make(
            f"search-d{drift}-v{vol}-{special or 'plain'}-s{seed}", seed, drift, vol, special
        )
        case = evaluate(arena, sc, p)
        new = (branches(case) & missing[0]) or ({case["indicators"]["regime"]} & missing[1])
        if new:
            cases.append(case)
            hit |= branches(case)
            regimes.add(case["indicators"]["regime"])

    missing_final = sorted(REQUIRED - hit) + sorted(REGIMES - regimes)
    if missing_final:
        print(f"WARNING: branches not reached: {missing_final}", file=sys.stderr)

    data = {
        "meta": {
            "arena_commit": commit,
            "source": "apps/arena/strategies.py (Strategy v0.3.1)",
            "generator": "tools/golden/generate_strategy_golden.py",
            "fee_rate": FEE,
            "slippage": SLIPPAGE,
            "weights": list(WEIGHTS),
            "branches_missing": missing_final,
        },
        "indicator_cases": indicator_cases(arena),
        "scenarios": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {args.out.relative_to(REPO)}: {len(cases)} scenarios, missing={missing_final}")
    return 0 if not missing_final else 1


if __name__ == "__main__":
    sys.exit(main())
