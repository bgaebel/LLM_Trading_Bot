"""Spec 012 AK2: the golden histories run through the PaperBroker with the same results.

``decide``, ``mark`` and ``buyhold`` go through the broker; ``trade`` ops have no broker port
(trade_to_target is internal to the Arena sizing) and stay direct. golden_execution_runner.py is
not touched, so the Spec 004 / Spec 011 tests keep proving the direct path.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

import golden_drawdown_cases as dd
import golden_execution_cases as cases
from golden import assert_same
from golden_execution_runner import BotRunner, to_quotes, to_signal
from golden_execution_runner import golden as execution_golden
from golden_scenarios import boundary_digest
from llm_trading_bot.broker import PaperBroker
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.db import write_tx
from llm_trading_bot.paper.execution import Costs

DRAWDOWN = (
    Path(__file__).parents[1] / "paper" / "fixtures" / "execution_drawdown_golden_v0.3.1.json"
)


@cache
def drawdown_golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DRAWDOWN.read_text(encoding="utf-8"))
    return data


class BrokerRunner(BotRunner):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.broker = PaperBroker()
        self.through_broker = 0

    def apply(self, con: Any, op: dict[str, Any]) -> Any:
        costs = Costs(*op.get("costs", cases.DEFAULT_COSTS))
        qs = to_quotes(op.get("quotes"))
        kind = op["op"]
        if kind == "decide":
            self.through_broker += 1
            return self.broker.record_decision(
                con,
                op["agent"],
                Symbol[op["symbol"]],
                op["bar_ts"],
                op["model"],
                to_signal(op["sig"], op["agent"]),
                op["data"],
                costs=costs,
                now=op["now"],
                qs=qs,
                status=op["status"],
            )
        if kind == "mark":
            assert qs is not None
            self.through_broker += 1
            with write_tx(con):
                self.broker.mark(con, op["agent"], qs, op["now"], costs)
            return None
        if kind == "buyhold":
            self.through_broker += 1
            try:
                return self.broker.initialize_buyhold(con, qs or {}, costs, when=op["now"])
            except ValueError:
                return "ValueError"
        return super().apply(con, op)


def _named(data: dict[str, Any], builder: Any) -> list[tuple[str, Any, Any]]:
    rebuilt = {s["name"]: s["ops"] for s in builder()}
    return [(s["name"], rebuilt[s["name"]], s["results"]) for s in data["named"]]


NAMED = [
    *(("spec004", *n) for n in _named(execution_golden(), cases.named_scenarios)),
    *(("drawdown", *n) for n in _named(drawdown_golden(), dd.named_scenarios)),
]


@pytest.mark.parametrize(("kind", "name", "ops", "expected"), NAMED, ids=lambda x: str(x)[:40])
def test_named_histories_through_the_broker_match_arena(
    kind: str, name: str, ops: list[Any], expected: list[Any], tmp_path: Path
) -> None:
    runner = BrokerRunner(tmp_path)
    actual = runner.play(ops)
    assert_same(json.loads(json.dumps(actual)), expected, f"{kind}:{name}")
    assert runner.through_broker > 0


def _fuzz(data: dict[str, Any], chunks: Any) -> list[tuple[dict[str, Any], list[Any]]]:
    by_key = {(m, i): c for m, i, c in chunks()}
    return [(f, by_key[(f["mode"], f["chunk"])]) for f in data["fuzz"]]


FUZZ = [*_fuzz(execution_golden(), cases.fuzz_chunks), *_fuzz(drawdown_golden(), dd.fuzz_chunks)]


@pytest.mark.parametrize(
    ("expected", "chunk"),
    FUZZ,
    ids=lambda x: f"{x['mode']}-{x['chunk']}" if isinstance(x, dict) else "",
)
def test_fuzz_through_the_broker_matches_arena(
    expected: dict[str, Any], chunk: list[Any], tmp_path: Path
) -> None:
    runner = BrokerRunner(tmp_path)
    con, marks = runner.fresh(), dict[str, int]()
    try:
        results = [runner.play(ops, con, marks) for ops in chunk]
    finally:
        con.close()
    assert boundary_digest(results) == expected["digest"]


WALKS = [
    *((w, cases.walk) for w in execution_golden()["walks"]),
    *((w, dd.walk) for w in drawdown_golden()["walks"]),
]


@pytest.mark.parametrize(
    ("expected", "walk"), WALKS, ids=lambda x: f"walk-{x['seed']}" if isinstance(x, dict) else ""
)
def test_walks_through_the_broker_match_arena(
    expected: dict[str, Any], walk: Any, tmp_path: Path
) -> None:
    results = BrokerRunner(tmp_path).play(walk(expected["seed"]))
    assert boundary_digest(results) == expected["digest"]
