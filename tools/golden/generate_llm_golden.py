"""Generate LLM-contract golden fixtures from the Arena's own code (Spec 005 AK1-AK3, ADR-0007).

Usage:
    uv run python -I tools/golden/generate_llm_golden.py <arena>/apps/arena [--commit SHA]

Records the Arena's SYSTEM prompt and SIGNAL_SCHEMA, how its strict_response + validate_signal judge
a set of raw answers (around every rule), and how llm_due decides around every cadence threshold.
Output: tests/llm/fixtures/llm_golden_v0.3.1.json.
"""

import argparse
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "llm" / "fixtures" / "llm_golden_v0.3.1.json"
OPENAI_URL = "https://api.openai.com/v1/responses"
CADENCE_ENV = ("LLM_MIN_INTERVAL_SECONDS", "LLM_EVENT_COOLDOWN_SECONDS", "LLM_EVENT_MOVE_THRESHOLD")


def load(arena_dir: Path) -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    sys.path.insert(0, str(arena_dir))
    arena = importlib.import_module("arena")
    engine = importlib.import_module("engine03")
    provenance = importlib.import_module("provenance")
    sys.path.insert(0, str(REPO / "tests"))
    cases = importlib.import_module("golden_llm_cases")
    return arena, engine, provenance, cases


def judge(arena: ModuleType, provenance: ModuleType, text: str) -> dict[str, Any]:
    """Arena path for one raw answer: strict_response inside http_json, then validate_signal."""
    response = {"output": [{"content": [{"type": "output_text", "text": text}]}]}
    try:
        provenance.strict_response(response, OPENAI_URL)
        chunks = [
            part["text"]
            for msg in response["output"]
            for part in msg["content"]
            if part.get("type") == "output_text"
        ]
        signal = arena.validate_signal(json.loads("".join(chunks)))
    except (ValueError, TypeError, KeyError) as exc:
        return {"accepted": False, "error": type(exc).__name__}
    return {"accepted": True, "signal": signal}


def due(engine: ModuleType, arena: ModuleType, case: dict[str, Any]) -> bool:
    for name in (
        "LLM_MIN_INTERVAL_SECONDS",
        "LLM_EVENT_COOLDOWN_SECONDS",
        "LLM_EVENT_MOVE_THRESHOLD",
    ):
        os.environ.pop(name, None)
    os.environ.update(case["env"])
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " agent TEXT NOT NULL, symbol TEXT NOT NULL, ref_mid REAL NOT NULL, status TEXT NOT NULL,"
        " message TEXT)"
    )
    for row in case["rows"]:
        ts = datetime.fromtimestamp(row["at"], UTC).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO llm_calls(ts,agent,symbol,ref_mid,status) VALUES (?,?,?,?,?)",
            (ts, row["agent"], row["symbol"], row["ref_mid"], "OK"),
        )
    result: bool = engine.llm_due(con, "gpt", "BTCEUR", case["mid"], case["now"])
    return bool(result)


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
    arena, engine, provenance, cases = load(arena_dir)
    answers = [{"text": t, **judge(arena, provenance, t)} for t in cases.answer_texts()]
    # Only the outcomes are stored; the tests rebuild the situations from golden_llm_cases.
    due_results = [due(engine, arena, c) for c in cases.due_cases()]
    for flag in (True, False):
        if flag not in due_results:
            raise SystemExit(f"due cases lack outcome {flag}")
    if not any(a["accepted"] for a in answers) or all(a["accepted"] for a in answers):
        raise SystemExit("answer cases need accepted and rejected outcomes")
    data = {
        "meta": {"arena_commit": commit, "generator": "tools/golden/generate_llm_golden.py"},
        "system": arena.SYSTEM,
        "signal_schema": arena.SIGNAL_SCHEMA,
        "answers": answers,
        "due": due_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    accepted = sum(a["accepted"] for a in answers)
    print(f"wrote {OUT.relative_to(REPO)}: {len(answers)} answers ({accepted} accepted), ", end="")
    print(f"{len(due_results)} due cases ({sum(due_results)} due)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
