"""Spec 008 AK7: the status file - atomic, private, no secrets, strict to read."""

import json
import stat
import sys
from pathlib import Path

import pytest

from llm_fakes import KEY
from llm_trading_bot.service.status import (
    FILE_NAME,
    ServiceStatus,
    read_status,
    status_path,
    write_status,
)

BASE = ServiceStatus(
    "running", 1234, "2027-01-15T08:00:00+00:00", "2027-01-15T08:00:00+00:00", 60, 5
)


def test_status_lives_next_to_the_db(tmp_path: Path) -> None:
    assert status_path(tmp_path / "data" / "bot.db") == tmp_path / "data" / FILE_NAME


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    status = BASE.but(last_result="ok", last_round_seconds=2.5, failures=0)
    write_status(path, status)
    assert read_status(path) == status


def test_missing_file_means_no_service(tmp_path: Path) -> None:
    assert read_status(tmp_path / FILE_NAME) is None


@pytest.mark.parametrize(
    "text",
    ["{", "[]", json.dumps({"state": "running"}), json.dumps({**BASE.__dict__, "state": "zombie"})],
)
def test_foreign_content_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / FILE_NAME
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_status(path)


def test_keys_never_reach_the_file(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    write_status(path, BASE.but(last_error=f"AuthError: bad key {KEY}", config_warning=KEY))
    text = path.read_text(encoding="utf-8")
    assert KEY not in text and "sk-***" in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    write_status(path, BASE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unknown_state_is_refused() -> None:
    with pytest.raises(ValueError):
        BASE.but(state="zombie")
