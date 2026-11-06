"""Shared helpers for the operations tests (Spec 009)."""

import hashlib
from collections.abc import Callable
from pathlib import Path

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs


def at(epoch: float) -> Callable[[], float]:
    return lambda: epoch


def bot_db(path: Path, cycles: int = 2) -> Path:
    """A database written by real paper cycles (classic depots, fake market)."""
    con = open_db(path, agents=("trend", BUYHOLD))
    try:
        for n in range(cycles):
            paper_cycle(
                con,
                ScenarioClient(),
                BotConfig(("trend",), None),
                costs=Costs(),
                clock=at(NOW + 60 * n),
            )
    finally:
        con.close()
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
