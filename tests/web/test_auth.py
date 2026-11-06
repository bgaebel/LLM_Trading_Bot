"""Spec 006 AK2/AK3: password hash, lockout and sessions."""

import json
import os
import sys
from pathlib import Path

import pytest

from llm_trading_bot.web.auth import (
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    SESSION_SECONDS,
    LoginGuard,
    PasswordStore,
    Sessions,
    new_setup_code,
    same,
)

PASSWORD = "richtig-langes-Passwort-42"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_password_is_stored_only_as_salted_hash(tmp_path: Path) -> None:
    store = PasswordStore(tmp_path / "web-auth.json")
    assert not store.is_set()
    store.set(PASSWORD)
    text = (tmp_path / "web-auth.json").read_text(encoding="utf-8")
    assert PASSWORD not in text
    record = json.loads(text)
    assert set(record) == {"version", "n", "r", "p", "salt", "hash"}
    assert store.verify(PASSWORD) and not store.verify(PASSWORD + "x") and not store.verify("")


def test_same_password_gets_a_different_salt(tmp_path: Path) -> None:
    a, b = PasswordStore(tmp_path / "a.json"), PasswordStore(tmp_path / "b.json")
    a.set(PASSWORD)
    b.set(PASSWORD)
    assert json.loads(a.path.read_text())["hash"] != json.loads(b.path.read_text())["hash"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_password_file_is_private(tmp_path: Path) -> None:
    store = PasswordStore(tmp_path / "web-auth.json")
    store.set(PASSWORD)
    assert os.stat(store.path).st_mode & 0o777 == 0o600


@pytest.mark.parametrize("password", ["", "kurz", "x" * 11])
def test_short_passwords_are_refused(tmp_path: Path, password: str) -> None:
    store = PasswordStore(tmp_path / "web-auth.json")
    with pytest.raises(ValueError, match="12"):
        store.set(password)
    assert not store.is_set()


def test_twelve_characters_are_enough(tmp_path: Path) -> None:
    store = PasswordStore(tmp_path / "web-auth.json")
    store.set("x" * 12)
    assert store.verify("x" * 12)


def test_unset_store_verifies_nothing(tmp_path: Path) -> None:
    assert not PasswordStore(tmp_path / "missing.json").verify(PASSWORD)


def test_lockout_after_five_failures_for_sixty_seconds() -> None:
    clock = Clock()
    guard = LoginGuard(clock)
    for _ in range(MAX_FAILURES - 1):
        guard.failed()
    assert guard.seconds_locked() == 0
    guard.failed()
    assert guard.seconds_locked() == LOCKOUT_SECONDS
    clock.now += LOCKOUT_SECONDS - 1
    assert guard.seconds_locked() == 1
    clock.now += 1
    assert guard.seconds_locked() == 0


def test_success_resets_the_failure_count() -> None:
    guard = LoginGuard(Clock())
    for _ in range(MAX_FAILURES - 1):
        guard.failed()
    guard.succeeded()
    guard.failed()
    assert guard.seconds_locked() == 0


def test_sessions_expire_after_twelve_hours_and_can_be_dropped() -> None:
    clock = Clock()
    sessions = Sessions(clock)
    session = sessions.create()
    assert len(session.token) >= 40 and session.csrf != session.token
    assert sessions.get(session.token) == session
    clock.now += SESSION_SECONDS - 1
    assert sessions.get(session.token) == session
    clock.now += 1
    assert sessions.get(session.token) is None
    other = sessions.create()
    sessions.drop(other.token)
    assert sessions.get(other.token) is None
    assert sessions.get(None) is None and sessions.get("") is None


def test_setup_codes_are_random_and_compared_safely() -> None:
    a, b = new_setup_code(), new_setup_code()
    assert a != b and len(a) >= 16
    assert same(a, a) and not same(a, b) and not same(a, "")
