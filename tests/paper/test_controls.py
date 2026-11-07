"""Spec 011 AK6: trading pause and release requests, read every round and fail closed."""

import os
import sys
from pathlib import Path

import pytest

from llm_trading_bot.paper.risk import Controls, epoch, parse_resumes, read_controls


def controls(tmp_path: Path, text: str | None, env: dict[str, str] | None = None) -> Controls:
    path = tmp_path / "secrets.env"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return read_controls(env or {}, path)


def test_pause_is_off_without_a_file_or_field(tmp_path: Path) -> None:
    for text in (None, "", "BOT_AGENTS=trend\n", "BOT_PAUSED=\n", "BOT_PAUSED=0\n"):
        c = controls(tmp_path, text)
        assert (c.paused, c.source, c.warnings) == (False, "", ()), text


def test_pause_from_the_file(tmp_path: Path) -> None:
    c = controls(tmp_path, "BOT_PAUSED=1\n")
    assert (c.paused, c.source, c.warnings) == (True, "file", ())


@pytest.mark.parametrize("value", ["yes", "true", "2", "on", "1 1"])
def test_pause_with_an_invalid_value_fails_closed(tmp_path: Path, value: str) -> None:
    c = controls(tmp_path, f"BOT_PAUSED={value}\n")
    assert (c.paused, c.source) == (True, "invalid")
    assert "is not 1 or 0: trading paused" in c.warnings[0]


def test_pause_with_an_unreadable_file_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "secrets.env").mkdir()  # a directory cannot be read as a file
    c = read_controls({}, tmp_path / "secrets.env")
    assert (c.paused, c.source) == (True, "invalid")
    assert "unreadable" in c.warnings[0]


def test_pause_with_undecodable_bytes_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "secrets.env").write_bytes(b"BOT_PAUSED=0\n\xff\xfe")
    c = read_controls({}, tmp_path / "secrets.env")
    assert (c.paused, c.source) == (True, "invalid")


def test_pause_from_the_environment_wins(tmp_path: Path) -> None:
    c = controls(tmp_path, "BOT_PAUSED=1\n", {"BOT_PAUSED": "0"})
    assert (c.paused, c.source) == (False, "environment")
    c = controls(tmp_path, "", {"BOT_PAUSED": "1"})
    assert (c.paused, c.source) == (True, "environment")
    c = controls(tmp_path, "", {"BOT_PAUSED": "maybe"})
    # Still the environment decides: the web switch cannot change it (review of Spec 011).
    assert (c.paused, c.source) == (True, "environment")
    assert "is not 1 or 0: trading paused" in c.warnings[0]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_pause_with_a_file_without_read_permission_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    path.write_text("BOT_PAUSED=0\n", encoding="utf-8")
    os.chmod(path, 0)
    if os.access(path, os.R_OK):
        pytest.skip("running as root")
    assert read_controls({}, path).paused


def test_resume_requests_are_parsed_per_depot(tmp_path: Path) -> None:
    text = (
        "BOT_RESUME=gpt-luna@2026-11-07T08:00:00+00:00, trend@2026-11-07T09:00:00+00:00,"
        "junk,nobody@,x@yesterday,naive@2026-11-07T08:00:00\n"
    )
    c = controls(tmp_path, text)
    assert c.resumes == {
        "gpt-luna": epoch("2026-11-07T08:00:00+00:00"),
        "trend": epoch("2026-11-07T09:00:00+00:00"),
    }
    assert len(c.warnings) == 4 and not c.paused
    assert parse_resumes("") == ({}, [])


def test_unreadable_file_with_the_pause_in_the_environment_says_what_follows(
    tmp_path: Path,
) -> None:
    (tmp_path / "secrets.env").write_bytes(b"\xff\xfe")
    c = read_controls({"BOT_PAUSED": "0"}, tmp_path / "secrets.env")
    assert (c.paused, c.source) == (False, "environment")
    assert c.warnings == ("secrets.env unreadable (UnicodeDecodeError): release requests ignored",)


@pytest.mark.parametrize(
    "ts", ["1969-01-01T00:00:00", "0001-01-01T00:00:00", "9999-12-31T23:59:59", "2026-11-07"]
)
def test_naive_or_odd_resume_times_are_skipped_not_raised(ts: str) -> None:
    resumes, bad = parse_resumes(f"trend@{ts},gpt-luna@2026-11-07T08:00:00+00:00")
    assert list(resumes) == ["gpt-luna"] and bad == [f"trend@{ts}"]
