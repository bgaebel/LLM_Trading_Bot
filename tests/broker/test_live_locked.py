"""Spec 012 AK4/AK6/AK9: live trading stays locked (ADR-0004, ADR-0022)."""

import ast
import sqlite3
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


# ------------------------------------------------------------------ AK6: no switch in the config

REPO = Path(__file__).resolve().parents[2]
NAMES = ("TRADING_MODE", "BITVAVO_", "LIVE_")


def test_config_has_no_field_for_mode_or_exchange_keys() -> None:
    from llm_trading_bot.web.credentials import FIELDS, parse

    assert not [f for f in FIELDS if f.startswith(NAMES)]
    text = "TRADING_MODE=live\nBITVAVO_API_KEY=k\nBITVAVO_API_SECRET=s\nLIVE_TRADING=1\n"
    assert parse(text) == {}


def test_config_server_files_set_no_mode_and_no_exchange_keys() -> None:
    files = [p for d in ("deploy", "scripts") for p in sorted((REPO / d).glob("*")) if p.is_file()]
    assert len(files) >= 5
    for path in files:
        text = path.read_text(encoding="utf-8")
        for name in (*NAMES, "EnvironmentFile="):
            assert name not in text, f"{path.name}: {name}"


# ------------------------------------------------------------------ AK9: a foreign database stays


def _content(path: Path) -> tuple[int, list[str]]:
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return ro.execute("PRAGMA user_version").fetchone()[0], list(ro.iterdump())
    finally:
        ro.close()


@pytest.mark.parametrize("version", [100, 101, 3, 1])
def test_foreign_database_is_refused_and_left_as_it_is(tmp_path: Path, version: int) -> None:
    """A later live DB (user_version >= 100) - or any other version - in WAL mode with entries
    not yet written back: open_db and the web UI refuse it and change nothing in it."""
    from llm_trading_bot.paper.db import SchemaError, open_db
    from llm_trading_bot.web.readonly import DbUnavailable, reading

    path = tmp_path / "live.db"
    keep = sqlite3.connect(path, isolation_level=None)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute(f"PRAGMA user_version={version}")
    keep.execute("CREATE TABLE intents (id INTEGER PRIMARY KEY, client_order_id TEXT, state TEXT)")
    keep.execute("INSERT INTO intents (client_order_id, state) VALUES ('c1', 'UNKNOWN')")
    try:
        before = _content(path)
        assert (path.parent / "live.db-wal").exists()  # entries still only in the WAL
        with pytest.raises(SchemaError):
            open_db(path, agents=("gpt-luna",))
        with pytest.raises(DbUnavailable), reading(path):
            pass
        assert _content(path) == before
    finally:
        keep.close()
    assert _content(path) == before
