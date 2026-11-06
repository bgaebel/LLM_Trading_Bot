"""Plays the shared execution scenarios against the bot (counterpart of the Arena generator)."""

import json
import sqlite3
from functools import cache
from pathlib import Path
from typing import Any, cast

import golden_execution_cases as cases
from llm_trading_bot.market.models import Quote, Symbol
from llm_trading_bot.paper.db import open_db, write_tx
from llm_trading_bot.paper.execution import (
    Costs,
    initialize_buyhold,
    mark,
    record_decision,
    trade_to_target,
)
from llm_trading_bot.strategy.signals import Action, Signal

FIXTURE = Path(__file__).parent / "paper" / "fixtures" / "execution_golden_v0.3.1.json"


@cache
def golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def to_quotes(raw: dict[str, list[float]] | None) -> dict[Symbol, Quote] | None:
    if raw is None:
        return None
    return {Symbol[s]: Quote(float(b), float(a)) for s, (b, a) in raw.items()}


def to_signal(raw: dict[str, Any], agent: str) -> Signal:
    return Signal(
        cast(Action, raw["action"]),
        float(raw["target_fraction"]),
        float(raw["confidence"]),
        raw["reason"],
        agent,
    )


class BotRunner:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.count = 0

    def fresh(self) -> sqlite3.Connection:
        self.count += 1
        con = open_db(self.directory / f"golden{self.count}.db", agents=cases.AGENTS)
        # Test databases only: thousands of commits would otherwise wait for fsync.
        con.execute("PRAGMA synchronous=OFF")
        return con

    def apply(self, con: sqlite3.Connection, op: dict[str, Any]) -> Any:
        costs = Costs(*op.get("costs", cases.DEFAULT_COSTS))
        kind = op["op"]
        qs = to_quotes(op.get("quotes"))
        if kind == "init":
            with write_tx(con):
                con.execute(
                    "UPDATE portfolios SET cash=?,btc=?,eth=?,peak=?,halted=? WHERE agent=?",
                    (op["cash"], op["btc"], op["eth"], op["peak"], op["halted"], op["agent"]),
                )
            return None
        if kind == "decide":
            return record_decision(
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
        assert kind == "buyhold" or qs is not None, op
        if kind == "trade":
            assert qs is not None
            with write_tx(con):
                trade_to_target(
                    con,
                    op["agent"],
                    Symbol[op["symbol"]],
                    op["target"],
                    qs,
                    0,
                    costs,
                    executed_at=op["now"],
                )
            return None
        if kind == "buyhold":
            try:
                return initialize_buyhold(con, qs or {}, costs, when=op["now"])
            except ValueError:
                return "ValueError"
        if kind == "mark":
            assert qs is not None
            with write_tx(con):
                mark(con, op["agent"], qs, op["now"])
            return None
        raise ValueError(kind)

    def play(
        self,
        ops: list[dict[str, Any]],
        con: sqlite3.Connection | None = None,
        marks: dict[str, int] | None = None,
    ) -> list[Any]:
        own = con is None
        con = con or self.fresh()
        marks = {} if marks is None else marks
        results = []
        try:
            for op in ops:
                ret = self.apply(con, op)
                if op["op"] != "init":
                    results.append({"ret": ret, **cases.capture(con, marks)})
        finally:
            if own:
                con.close()
        return results
