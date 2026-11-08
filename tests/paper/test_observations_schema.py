"""Spec 015 AK3 (ADR-0027): the observations schema is created once and checked exactly."""

import sqlite3
from pathlib import Path

import pytest

from llm_trading_bot.paper.db import SchemaError
from llm_trading_bot.paper.observations_schema import (
    OBS_COLUMNS,
    OBS_SCHEMA_VERSION,
    check_observations_schema,
    create,
)


def test_create_makes_exactly_the_schema(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    create(path)
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == OBS_SCHEMA_VERSION
        check_observations_schema(con)
        for table, columns in OBS_COLUMNS.items():
            assert tuple(r[1] for r in con.execute(f"PRAGMA table_info({table})")) == columns
    finally:
        con.close()


def test_create_never_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    path.write_bytes(b"damaged")
    with pytest.raises(FileExistsError):
        create(path)
    assert path.read_bytes() == b"damaged"


def test_another_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    create(path)
    con = sqlite3.connect(path)
    try:
        con.execute(f"PRAGMA user_version={OBS_SCHEMA_VERSION + 98}")
        with pytest.raises(SchemaError, match="no automatic migration"):
            check_observations_schema(con)
    finally:
        con.close()


def test_a_changed_shape_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    create(path)
    con = sqlite3.connect(path)
    try:
        con.execute("ALTER TABLE books ADD COLUMN extra TEXT")
        with pytest.raises(SchemaError, match="books"):
            check_observations_schema(con)
    finally:
        con.close()
