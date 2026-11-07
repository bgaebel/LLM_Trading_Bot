"""Spec 012 AK4/AK6/AK9: live trading stays locked (ADR-0004, ADR-0022)."""

import ast
from pathlib import Path

import pytest

import llm_trading_bot.broker.live as live_module
from llm_trading_bot.broker import LiveTradingLocked, make_broker
from llm_trading_bot.core import TradingMode

SRC = Path(live_module.__file__)


def test_make_broker_refuses_live_even_past_parse_mode() -> None:
    with pytest.raises(LiveTradingLocked, match="ADR-0004"):
        make_broker(TradingMode.LIVE)
    assert issubclass(LiveTradingLocked, NotImplementedError)


def test_live_module_holds_no_exchange_code() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"typing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            assert not methods, f"class {node.name} has methods"
