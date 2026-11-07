"""Generate paper-execution golden fixtures from the Arena's own code (Spec 004 AK5, ADR-0007).

Usage:
    uv run python -I tools/golden/generate_execution_golden.py <arena>/apps/arena [--commit SHA]
        [--check]

Plays the scenarios of tests/golden_execution_cases.py against the Arena's record_decision,
trade_to_target, initialize_buyhold and mark_and_risk in an in-memory Arena database and records
the state after every op. Output: tests/paper/fixtures/execution_golden_v0.3.1.json.

- named: a few readable histories with full rows
- fuzz:  single decisions around every threshold, one SHA-256 digest per chunk of cases
- walks: multi-step histories, one digest per walk (seeds that would hit the Arena's 10 %
         drawdown stop are skipped: that stop belongs to Spec Nr. 4, not to this port)

Spec 011 (ADR-0021) ports the stop. Its cases (tests/golden_drawdown_cases.py) go to a file of
their own, tests/paper/fixtures/execution_drawdown_golden_v0.3.1.json, so the older fixture stays
byte for byte as it was (ADR-0007: fixtures are never edited). ``--check`` writes nothing and
fails unless both files are exactly what the Arena produces now.
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
FIXTURES = REPO / "tests" / "paper" / "fixtures"
OUT = FIXTURES / "execution_golden_v0.3.1.json"
OUT_DRAWDOWN = FIXTURES / "execution_drawdown_golden_v0.3.1.json"


class DrawdownStop(Exception):
    """The Arena halted a portfolio: the scenario left the range the Spec 004 fixture covers."""


def load(arena_dir: Path) -> tuple[ModuleType, ...]:
    sys.path.insert(0, str(arena_dir))
    arena = importlib.import_module("arena")
    engine = importlib.import_module("engine03")
    sys.path.insert(0, str(REPO / "tests"))
    cases = importlib.import_module("golden_execution_cases")
    scenarios = importlib.import_module("golden_scenarios")
    drawdown = importlib.import_module("golden_drawdown_cases")
    return arena, engine, cases, scenarios, drawdown


class Runner:
    def __init__(
        self, arena: ModuleType, engine: ModuleType, cases: ModuleType, *, allow_stop: bool = False
    ) -> None:
        self.arena, self.engine, self.cases = arena, engine, cases
        self.allow_stop = allow_stop
        self.stopped = False

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
                if not self.allow_stop:
                    raise DrawdownStop(op)
                self.stopped = True
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


def commit_of(arena_dir: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(arena_dir), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def build(modules: tuple[ModuleType, ...], commit: str) -> dict[str, Any]:
    """The Spec 004 fixture, unchanged since then (``--check`` proves it)."""
    arena, engine, cases, scenarios, _ = modules
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

    return {
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


def build_drawdown(modules: tuple[ModuleType, ...], commit: str) -> dict[str, Any]:
    """Spec 011 AK1: the drawdown stop; every case may stop, and each mode must contain stops."""
    arena, engine, cases, scenarios, drawdown = modules
    runner = Runner(arena, engine, cases, allow_stop=True)
    digest = scenarios.boundary_digest

    named = []
    for s in drawdown.named_scenarios():
        runner.stopped = False
        results = runner.play(s["ops"])
        if runner.stopped != s["stops"]:
            raise SystemExit(f"{s['name']}: Arena stopped={runner.stopped}, case says {s['stops']}")
        named.append(dict(s, results=results))

    fuzz = []
    coverage: dict[str, dict[str, int]] = {}
    for mode, index, chunk in drawdown.fuzz_chunks():
        con, marks = runner.fresh(), dict[str, int]()
        results = []
        for ops in chunk:
            runner.stopped = False
            results.append(runner.play(ops, con, marks))
            counts = coverage.setdefault(mode, {"stop": 0, "no_stop": 0})
            counts["stop" if runner.stopped else "no_stop"] += 1
        fuzz.append({"mode": mode, "chunk": index, "digest": digest(results)})
    for mode, counts in coverage.items():
        if not (counts["stop"] and counts["no_stop"]):
            raise SystemExit(f"drawdown fuzz mode {mode} lacks stop or no-stop cases: {counts}")

    walks = []
    stopped_walks = 0
    for seed in range(drawdown.WALKS):
        runner.stopped = False
        walks.append({"seed": seed, "digest": digest(runner.play(drawdown.walk(seed)))})
        stopped_walks += runner.stopped
    if not 0 < stopped_walks < drawdown.WALKS:
        raise SystemExit(f"drawdown walks: {stopped_walks}/{drawdown.WALKS} stopped")

    return {
        "meta": {
            "arena_commit": commit,
            "generator": "tools/golden/generate_execution_golden.py",
            "kill": drawdown.KILL,
            "fuzz_modes": list(drawdown.FUZZ_MODES),
            "fuzz_per_mode": drawdown.FUZZ_PER_MODE,
            "fuzz_chunk": drawdown.FUZZ_CHUNK,
            "walk_steps": drawdown.WALK_STEPS,
            "walks_stopped": stopped_walks,
            "coverage": coverage,
        },
        "named": named,
        "fuzz": fuzz,
        "walks": walks,
    }


def encoded(data: dict[str, Any]) -> bytes:
    # Bytes, not write_text: on Windows text mode would turn every newline into CRLF.
    return (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("arena_dir", type=Path)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--check", action="store_true", help="compare with the files, write none")
    args = parser.parse_args()
    arena_dir: Path = args.arena_dir.resolve()
    commit = args.commit or commit_of(arena_dir)
    modules = load(arena_dir)
    outputs = {OUT: build(modules, commit), OUT_DRAWDOWN: build_drawdown(modules, commit)}
    if args.check:
        differ = [p for p, d in outputs.items() if not p.exists() or p.read_bytes() != encoded(d)]
        for path in outputs:
            print(f"{'DIFFERS' if path in differ else 'same':8} {path.relative_to(REPO)}")
        return 1 if differ else 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for path, data in outputs.items():
        path.write_bytes(encoded(data))
        print(f"wrote {path.relative_to(REPO)}: {len(data['named'])} named, ", end="")
        print(f"{len(data['fuzz'])} fuzz chunks, {len(data['walks'])} walks, ", end="")
        print(f"coverage {data['meta']['coverage']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
