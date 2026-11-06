"""ADR-0007 / Spec 004 AK5: virtual execution identical to Arena v0.3.1 (record_decision,
trade_to_target, initialize_buyhold, peak and equity), bit for bit on the digested cases."""

import json
from pathlib import Path
from typing import Any

import pytest

import golden_execution_cases as cases
from golden import assert_same
from golden_execution_runner import BotRunner, golden
from golden_scenarios import boundary_digest


def test_fixture_comes_from_the_expected_arena_commit_and_case_set() -> None:
    meta = golden()["meta"]
    assert meta["arena_commit"] == "7563724"
    assert meta["fuzz_modes"] == list(cases.FUZZ_MODES)
    assert meta["fuzz_per_mode"] == cases.FUZZ_PER_MODE
    assert meta["fuzz_chunk"] == cases.FUZZ_CHUNK
    assert meta["walk_steps"] == cases.WALK_STEPS
    assert [(f["mode"], f["chunk"]) for f in golden()["fuzz"]] == [
        (m, c) for m, c, _ in cases.fuzz_chunks()
    ]
    assert len(golden()["walks"]) == cases.WALKS
    for mode, counts in meta["coverage"].items():
        assert counts["trade"] > 0 and counts["no_trade"] > 0, mode


@pytest.mark.parametrize("expected", golden()["named"], ids=lambda s: s["name"])
def test_named_histories_match_arena(expected: dict[str, Any], tmp_path: Path) -> None:
    rebuilt = {s["name"]: s["ops"] for s in cases.named_scenarios()}[expected["name"]]
    # The ops in the fixture are the ones the Arena saw; the builder must not have drifted.
    assert json.loads(json.dumps(rebuilt)) == expected["ops"]
    actual = BotRunner(tmp_path).play(rebuilt)
    assert_same(json.loads(json.dumps(actual)), expected["results"], expected["name"])


def _fuzz_ids(entry: dict[str, Any]) -> str:
    return f"{entry['mode']}-{entry['chunk']}"


@pytest.mark.parametrize("expected", golden()["fuzz"], ids=_fuzz_ids)
def test_threshold_cases_match_arena_exactly(expected: dict[str, Any], tmp_path: Path) -> None:
    chunk = next(
        c for m, i, c in cases.fuzz_chunks() if (m, i) == (expected["mode"], expected["chunk"])
    )
    runner = BotRunner(tmp_path)
    con, marks = runner.fresh(), dict[str, int]()
    try:
        results = [runner.play(ops, con, marks) for ops in chunk]
    finally:
        con.close()
    assert boundary_digest(results) == expected["digest"]


@pytest.mark.parametrize("expected", golden()["walks"], ids=lambda w: f"walk-{w['seed']}")
def test_multi_step_histories_match_arena_exactly(expected: dict[str, Any], tmp_path: Path) -> None:
    results = BotRunner(tmp_path).play(cases.walk(expected["seed"]))
    assert boundary_digest(results) == expected["digest"]
