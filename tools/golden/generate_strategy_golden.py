"""Generate strategy golden fixtures from the Arena's own code (Spec 003, ADR-0007).

Usage:
    uv run python -I tools/golden/generate_strategy_golden.py <arena>/apps/arena [--commit SHA]

The Arena code (strategies.py, arena.py, engine03.py) is imported from the given path only while
generating; the bot never imports it. Output: tests/strategy/fixtures/strategy_golden_v0.3.1.json.

Contents:
- scenarios: synthetic snapshots (rebuilt from parameters) with indicators, signals and LLM payload
- indicator_cases: short-series fallbacks of the raw indicator functions
- boundary: indicator dicts with one condition at its threshold (per base: digest of seed n at n)
"""

import argparse
import importlib
import itertools
import json
import os
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
PAYLOAD_WEIGHTS = (0.0, 0.25, 0.45, 0.123456789)
STRATEGIES = ("trend", "breakout", "meanrev", "ensemble", "buyhold")
REQUIRED = {(s, a) for s in STRATEGIES[:4] for a in ("BUY", "SELL", "HOLD")} | {("buyhold", "BUY")}
REGIMES = {"TREND", "RANGE", "TRANSITION"}
# ADX windows around the regime thresholds 18 and 22 (indicators.py).
ADX_WINDOWS = ((17.0, 18.0), (18.0, 18.6), (21.4, 22.0), (22.0, 22.6))
BOUNDARY_SEEDS_PER_BASE = 800


def load_arena(arena_dir: Path) -> tuple[ModuleType, ModuleType, ModuleType]:
    # engine03 imports `arena`, `strategies` and `bitvavo` by their flat names.
    sys.path.insert(0, str(arena_dir))
    os.environ["TAKER_FEE"] = str(FEE)
    os.environ["SLIPPAGE"] = str(SLIPPAGE)
    arena = importlib.import_module("arena")
    strategies = importlib.import_module("strategies")
    engine = importlib.import_module("engine03")
    return arena, strategies, engine


def load_scenarios() -> ModuleType:
    sys.path.insert(0, str(REPO / "tests"))
    return importlib.import_module("golden_scenarios")


class Env:
    def __init__(self, arena: ModuleType, st: ModuleType, engine: ModuleType, sc: ModuleType):
        self.arena, self.st, self.engine, self.sc = arena, st, engine, sc

    def snapshot(self, p: dict[str, Any]) -> Any:
        rows = self.sc.build_bars(p)
        bars = {tf: tuple(self.st.Candle(*r) for r in rows[tf]) for tf in rows}
        bid, ask = self.sc.quote_for(p, rows)
        quote = self.arena.Quote(bid, ask)
        return self.st.MultiSnapshot(p.get("symbol", "BTCEUR"), bars, quote, self.sc.NOW)

    def evaluate(self, p: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot(p)
        cost = self.engine.cost_roundtrip(snap)
        ind = self.st.indicators(snap, cost)
        payloads = {
            str(w): self.st.feature_payload(snap, ind, w, FEE, SLIPPAGE) for w in PAYLOAD_WEIGHTS
        }
        return {
            "params": self.sc.as_jsonable(p),
            "roundtrip_cost_frac": cost,
            "indicators": ind,
            "signals": {
                str(w): {n: self.st.run_strategy(n, snap, ind, w) for n in STRATEGIES}
                for w in WEIGHTS
            },
            "payload": payloads,
            "llm_json_sha": {
                w: self.sc.boundary_digest([self.sc.llm_json(pl)]) for w, pl in payloads.items()
            },
        }

    def anchors(self, snap: Any) -> dict[str, float]:
        c15 = [b.close for b in snap.bars[15]]
        return {
            "ema34_15m": self.st.ema(c15, 34),
            "ema30_15m": self.st.ema(c15, 30),
            "prev_close_15m": snap.bars[15][-2].close,
        }

    def boundary_sha(self, p: dict[str, Any], seed: int) -> str:
        snap = self.snapshot(p)
        base = self.st.indicators(snap, self.engine.cost_roundtrip(snap))
        i = self.sc.sample_indicators(base, self.anchors(snap), seed)
        rows = []
        for w in self.sc.BOUNDARY_WEIGHTS:
            for n in STRATEGIES[:4]:
                s = self.st.run_strategy(n, snap, i, w)
                rows.append([w, n, s["action"], s["target_fraction"], s["confidence"], s["reason"]])
        return str(self.sc.boundary_digest(rows))


def branches(case: dict[str, Any]) -> set[tuple[str, str]]:
    return {(n, s["action"]) for per_w in case["signals"].values() for n, s in per_w.items()}


def indicator_cases(st: ModuleType) -> list[dict[str, Any]]:
    """Short-series fallbacks and edge values of the raw indicator functions."""
    flat = [100.0] * 20
    rising = [100.0 + i for i in range(20)]
    short = [100.0, 101.0, 99.0]

    def rows(n: int) -> list[list[float]]:
        return [[i * 60, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1.0] for i in range(n)]

    def candles(n: int) -> tuple[Any, ...]:
        return tuple(st.Candle(*r) for r in rows(n))

    cases: list[dict[str, Any]] = []
    for values, n in [(short, 14), (flat, 14), (rising, 14), (rising[:15], 14)]:
        cases.append({"fn": "rsi", "values": values, "n": n, "expected": st.rsi(values, n)})
    for values, n in [(short, 3), (rising, 5), ([5.0], 10)]:
        cases.append({"fn": "ema", "values": values, "n": n, "expected": st.ema(values, n)})
    for count in (5, 15, 40):
        bars = candles(count)
        cases.append({"fn": "atr", "rows": rows(count), "n": 14, "expected": st.atr(bars, 14)})
        cases.append(
            {"fn": "adx", "rows": rows(count), "n": 14, "expected": list(st.adx(bars, 14))}
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("arena_dir", type=Path, help="path to <LLM-Trading-Arena>/apps/arena")
    parser.add_argument("--commit", help="Arena commit; default: git rev-parse in arena_dir")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    env = Env(*load_arena(args.arena_dir.resolve()), load_scenarios())
    sc = env.sc
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

    cases = [env.evaluate(p) for p in sc.grid()]
    hit = set().union(*(branches(c) for c in cases))
    regimes = {c["indicators"]["regime"] for c in cases}

    # 1) Targeted, deterministic search for branches the base grid does not reach.
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
        name = f"search-d{drift}-v{vol}-{special or 'plain'}-s{seed}"
        case = env.evaluate(sc.make(name, seed, drift, vol, special))
        if (branches(case) & missing[0]) or ({case["indicators"]["regime"]} & missing[1]):
            cases.append(case)
            hit |= branches(case)
            regimes.add(case["indicators"]["regime"])

    # 2) ADX right around the regime thresholds 18 and 22.
    windows_open = list(ADX_WINDOWS)
    adx_search = itertools.product(
        range(1, 3000), (0.0, 0.0005, -0.0005, 0.001, -0.001), (0.002, 0.004)
    )
    for seed, drift, vol in adx_search:
        if not windows_open:
            break
        p = sc.make(f"adx-d{drift}-v{vol}-s{seed}", seed, drift, vol, "")
        adx = env.st.adx(env.snapshot(p).bars[60])[0]
        for lo, hi in windows_open:
            if lo <= adx < hi:
                cases.append(env.evaluate(p))
                windows_open.remove((lo, hi))
                break

    # 3) Specials the base grid lacks: dip between 2 and 2.2 sigma, zero-volume minutes, ETH.
    extra = [
        {**sc.make("flat-calm-dip2.1-s1", 1, 0.0, 0.002, "dip"), "dip_k": 2.1},
        {**sc.make("flat-calm-dip1.9-s1", 1, 0.0, 0.002, "dip"), "dip_k": 1.9},
        sc.make("flat-calm-zerovol-s1", 1, 0.0, 0.002, "zerovol"),
        {
            **sc.make("eth-flat-calm-dip-s1", 1, 0.0, 0.002, "dip"),
            "symbol": "ETHEUR",
            "start": 3000.0,
        },
    ]
    cases.extend(env.evaluate(p) for p in extra)

    # 4) Boundary cases on a few bases (dip bases make meanrev's snapshot conditions true).
    by_name = {c["params"]["name"]: c["params"] for c in cases}
    bases = [
        "flat-calm-dip-s1",
        "flat-calm-dip2.1-s1",
        "flat-calm-dip1.9-s1",
        "up-calm-plain-s1",
        "down-wild-plain-s1",
        "eth-flat-calm-dip-s1",
    ]
    # Compact: per base, the digest of seed n is at index n.
    boundary = {
        b: [env.boundary_sha(by_name[b], seed) for seed in range(BOUNDARY_SEEDS_PER_BASE)]
        for b in bases
    }

    missing_final = (
        sorted(REQUIRED - hit) + sorted(REGIMES - regimes) + [f"adx{w}" for w in windows_open]
    )
    if missing_final:
        print(f"WARNING: not reached: {missing_final}", file=sys.stderr)

    data = {
        "meta": {
            "arena_commit": commit,
            "source": "apps/arena/strategies.py (Strategy v0.3.1), arena.py Quote, "
            "engine03.cost_roundtrip",
            "generator": "tools/golden/generate_strategy_golden.py",
            "fee_rate": FEE,
            "slippage": SLIPPAGE,
            "weights": list(WEIGHTS),
            "payload_weights": list(PAYLOAD_WEIGHTS),
            "boundary_weights": list(sc.BOUNDARY_WEIGHTS),
            "arena_min_bars": {str(k): v for k, v in env.st.MIN_BARS.items()},
            "branches_missing": missing_final,
        },
        "indicator_cases": indicator_cases(env.st),
        "scenarios": cases,
        "boundary": boundary,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"wrote {args.out.relative_to(REPO)}: {len(cases)} scenarios, "
        f"{sum(map(len, boundary.values()))} boundary cases, missing={missing_final}"
    )
    return 0 if not missing_final else 1


if __name__ == "__main__":
    sys.exit(main())
