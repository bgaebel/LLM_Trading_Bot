"""Generate paper-execution golden fixtures from the Arena's own code (Spec 004 AK5, ADR-0007).

Usage:
    uv run python -I tools/golden/generate_execution_golden.py <arena>/apps/arena [--commit SHA]

Plays the scenarios of tests/golden_execution_cases.py against the Arena's record_decision,
trade_to_target, initialize_buyhold and mark_and_risk in an in-memory Arena database and records
the state after every op. Output: tests/paper/fixtures/execution_golden_v0.3.1.json.

- named: a few readable histories with full rows
- fuzz:  single decisions around every threshold, one SHA-256 digest per chunk of cases
- walks: multi-step histories, one digest per walk (seeds that would hit the Arena's 10 %
         drawdown stop are skipped: that stop belongs to Spec Nr. 4, not to this port)
"""

import argparse
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "paper" / "fixtures" / "execution_golden_v0.3.1.json"


class DrawdownStop(Exception):
    """The Arena halted a portfolio: the scenario left the range this port covers."""


def load(arena_dir: Path) -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    sys.path.insert(0, str(arena_dir))
    arena = importlib.import_module("arena")
    engine = importlib.import_module("engine03")
    sys.path.insert(0, str(REPO / "tests"))
    cases = importlib.import_module("golden_execution_cases")
    scenarios = importlib.import_module("golden_scenarios")
    return arena, engine, cases, scenarios


class Runner:
    def __init__(self, arena: ModuleType, engine: ModuleType, cases: ModuleType) -> None:
        self.arena, self.engine, self.cases = arena, engine, cases

    def fresh(self) -> sqlite3.Connection:
        con: sqlite3.Connection = self.arena.open_db(":memory:")
        return con

    def quotes(self, raw: dict[str, list[float]] | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        return {s: self.arena.Quote(float(b), float(a)) for s, (b, a) in raw.items()}

    def apply(self, con: sqlite3.Connection, op: dict[str, Any]) -> Any:
        fee, slip = op.get("costs", self.cases.DEFAULT_COSTS)
        # The Arena reads its costs from the environment on every call.
        os.environ["TAKER_FEE"], os.environ["SLIPPAGE"] = repr(fee), repr(slip)
        kind = op["op"]
        qs = self.quotes(op.get("quotes"))
        if kind == "init":
            con.execute(
                "UPDATE portfolios SET cash=?,btc=?,eth=?,peak=?,halted=? WHERE agent=?",
                (op["cash"], op["btc"], op["eth"], op["peak"], op["halted"], op["agent"]),
            )
            con.commit()
            return None
        if kind == "decide":
            return self.engine.record_decision(
                con,
                op["agent"],
                op["symbol"],
                op["bar_ts"],
                op["model"],
                dict(op["sig"]),
                op["data"],
                status=op["status"],
                now=op["now"],
                qs=qs,
            )
        if kind == "trade":
            with con:
                con.execute("BEGIN IMMEDIATE")
                self.arena.trade_to_target(
                    con, op["agent"], op["symbol"], op["target"], qs, 0, executed_at=op["now"]
                )
            return None
        if kind == "buyhold":
            try:
                return self.engine.initialize_buyhold(con, qs or {}, when=op["now"])
            except ValueError:
                return "ValueError"
        if kind == "mark":
            with con:
                con.execute("BEGIN IMMEDIATE")
                self.arena.mark_and_risk(con, op["agent"], qs, op["now"])
            return None
        raise ValueError(kind)

    def play(
        self,
        ops: list[dict[str, Any]],
        con: sqlite3.Connection | None = None,
        marks: dict[str, int] | None = None,
    ) -> list[Any]:
        con = con or self.fresh()
        marks = {} if marks is None else marks
        results = []
        for op in ops:
            before = self.halted(con)
            ret = self.apply(con, op)
            if op["op"] == "init":
                continue
            after = self.halted(con)
            if any(after[a] and not before[a] for a in after):
                raise DrawdownStop(op)
            results.append({"ret": ret, **self.cases.capture(con, marks)})
        return results

    def halted(self, con: sqlite3.Connection) -> dict[str, int]:
        rows = con.execute("SELECT agent, halted FROM portfolios").fetchall()
        return {r[0]: r[1] for r in rows}


def outcome_counts(results: list[Any]) -> dict[str, int]:
    counts = {"trade": 0, "no_trade": 0}
    for r in results:
        counts["trade" if r["trades"] else "no_trade"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("arena_dir", type=Path)
    parser.add_argument("--commit", default=None)
    args = parser.parse_args()
    arena_dir: Path = args.arena_dir.resolve()
    commit = (
        args.commit
        or subprocess.run(
            ["git", "-C", str(arena_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    arena, engine, cases, scenarios = load(arena_dir)
    runner = Runner(arena, engine, cases)
    digest = scenarios.boundary_digest

    named = [dict(s, results=runner.play(s["ops"])) for s in cases.named_scenarios()]

    fuzz = []
    coverage: dict[str, dict[str, int]] = {}
    for mode, index, chunk in cases.fuzz_chunks():
        con, marks = runner.fresh(), dict[str, int]()
        results = [runner.play(ops, con, marks) for ops in chunk]
        flat = [r for case in results for r in case]
        for key, n in outcome_counts(flat).items():
            coverage.setdefault(mode, {"trade": 0, "no_trade": 0})[key] += n
        fuzz.append({"mode": mode, "chunk": index, "digest": digest(results)})
    for mode, counts in coverage.items():
        if not (counts["trade"] and counts["no_trade"]):
            raise SystemExit(f"fuzz mode {mode} lacks trade or no-trade outcomes: {counts}")

    walks: list[dict[str, Any]] = []
    seed = 0
    skipped = []
    while len(walks) < cases.WALKS:
        try:
            walks.append({"seed": seed, "digest": digest(runner.play(cases.walk(seed)))})
        except DrawdownStop:
            skipped.append(seed)
        seed += 1

    data = {
        "meta": {
            "arena_commit": commit,
            "generator": "tools/golden/generate_execution_golden.py",
            "fuzz_modes": list(cases.FUZZ_MODES),
            "fuzz_per_mode": cases.FUZZ_PER_MODE,
            "fuzz_chunk": cases.FUZZ_CHUNK,
            "walk_steps": cases.WALK_STEPS,
            "walks_skipped_for_drawdown_stop": skipped,
            "coverage": coverage,
        },
        "named": named,
        "fuzz": fuzz,
        "walks": walks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}: {len(named)} named, {len(fuzz)} fuzz chunks, ", end="")
    print(f"{len(walks)} walks (skipped {skipped}), coverage {coverage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
