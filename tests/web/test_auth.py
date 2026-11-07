"""Spec 006 AK2/AK3: password hash, lockout and sessions."""

import json
import os
import sys
from pathlib import Path

import pytest

from llm_trading_bot.web.auth import (
    DEVICE_SECONDS,
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    MAX_LOCKOUT_SECONDS,
    SESSION_SECONDS,
    DeviceKeys,
    LoginGuard,
    PasswordStore,
    PublicGuard,
    Sessions,
    client_key,
    new_setup_code,
    password_problem,
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


@pytest.mark.parametrize("password", ["", "kurz", "x" * 7, "Ab3-defghij"])
def test_short_passwords_are_refused(tmp_path: Path, password: str) -> None:
    """ADR-0024: every new password needs 12 characters (the public rule)."""
    store = PasswordStore(tmp_path / "web-auth.json")
    with pytest.raises(ValueError, match="12"):
        store.set(password)
    assert not store.is_set()


def test_twelve_characters_are_enough(tmp_path: Path) -> None:
    store = PasswordStore(tmp_path / "web-auth.json")
    store.set("Ab3-defghijk")
    assert store.verify("Ab3-defghijk")


@pytest.mark.parametrize(
    "password",
    [
        "password1234",
        "PASSWORT1234",
        "123456789012",
        "aaaaaaaaaaaa",
        "abababababab",
        "qwertz123456",
    ],
)
def test_password_policy_refuses_common_or_trivial_ones(tmp_path: Path, password: str) -> None:
    assert password_problem(password, public=True) == "zu leicht zu erraten"
    with pytest.raises(ValueError, match="erraten"):
        PasswordStore(tmp_path / "web-auth.json").set(password)


def test_password_policy_through_the_tunnel_keeps_eight() -> None:
    assert password_problem("abcdefgh", public=False) == ""
    assert password_problem("abcdefg", public=False) == "mindestens 8 Zeichen"
    assert password_problem("abcdefgh", public=True) == "mindestens 12 Zeichen"
    assert password_problem("Gr\u00fcne-Wiese-17", public=True) == ""


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


# ------------------------------------------------------------------ ADR-0024: the public way


def lock(guard: LoginGuard) -> None:
    for _ in range(MAX_FAILURES):
        guard.failed()


def test_backoff_doubles_up_to_an_hour_and_resets_on_success() -> None:
    clock = Clock()
    guard = LoginGuard(clock, doubling=True)
    seen = []
    for _ in range(8):
        lock(guard)
        seen.append(guard.seconds_locked())
        clock.now += guard.seconds_locked()
        assert guard.seconds_locked() == 0
    assert seen == [60, 120, 240, 480, 960, 1920, 3600, 3600]
    assert seen[-1] == MAX_LOCKOUT_SECONDS
    guard.succeeded()
    lock(guard)
    assert guard.seconds_locked() == LOCKOUT_SECONDS


def test_backoff_without_doubling_stays_at_sixty() -> None:
    clock = Clock()
    guard = LoginGuard(clock)
    for _ in range(3):
        lock(guard)
        assert guard.seconds_locked() == LOCKOUT_SECONDS
        clock.now += LOCKOUT_SECONDS


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff::1", "2001:db8:1:2::/64"),
        ("not an address", "unknown"),
        ("", "unknown"),
    ],
)
def test_client_key_groups_an_ipv6_household(address: str, key: str) -> None:
    assert client_key(address) == key


def test_public_guard_locks_one_client_not_another() -> None:
    guard = PublicGuard(Clock())
    for _ in range(MAX_FAILURES):
        guard.failed("ip:a", device=False)
    assert guard.seconds_locked("ip:a", device=False) == LOCKOUT_SECONDS
    assert guard.seconds_locked("ip:b", device=False) == 0


def test_public_guard_global_ceiling_spares_known_devices() -> None:
    clock = Clock()
    guard = PublicGuard(clock)
    for i in range(100):
        guard.failed(f"ip:{i}", device=False)  # 100 clients, one guess each
    assert guard.seconds_locked("ip:new", device=False) > 0
    assert guard.seconds_locked("device:owner", device=True) == 0
    clock.now += 3600
    assert guard.seconds_locked("ip:new", device=False) == 0


def test_public_guard_forgets_a_client_after_success() -> None:
    guard = PublicGuard(Clock())
    for _ in range(MAX_FAILURES):
        guard.failed("ip:a", device=False)
    guard.succeeded("ip:a")
    assert guard.seconds_locked("ip:a", device=True) == 0


def test_device_cookie_is_signed_and_expires(tmp_path: Path) -> None:
    now = [1_800_000_000.0]
    keys = DeviceKeys(tmp_path / "web-device.key", wall_clock=lambda: now[0])
    token = keys.issue()
    device = keys.verify(token)
    assert device and token.startswith(device + ".")
    other = DeviceKeys(tmp_path / "other.key", wall_clock=lambda: now[0])
    assert other.verify(token) is None  # another key
    head, expiry, sig = token.split(".")
    assert keys.verify(f"{head}.{int(expiry) + 1}.{sig}") is None  # tampered expiry
    assert keys.verify(f"x{head}.{expiry}.{sig}") is None
    for bad in (None, "", "a.b", "a.b.c.d", f"{head}.soon.{sig}"):
        assert keys.verify(bad) is None
    now[0] += DEVICE_SECONDS
    assert keys.verify(token) is None  # expired


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_device_key_is_private(tmp_path: Path) -> None:
    keys = DeviceKeys(tmp_path / "web-device.key")
    keys.issue()
    assert os.stat(tmp_path / "web-device.key").st_mode & 0o777 == 0o600


def test_sessions_drop_others_keeps_the_current_one() -> None:
    sessions = Sessions(Clock())
    a, b = sessions.create(), sessions.create()
    sessions.drop_others(a.token)
    assert sessions.get(a.token) == a and sessions.get(b.token) is None


def test_device_cookie_garbage_is_no_device(tmp_path: Path) -> None:
    keys = DeviceKeys(tmp_path / "web-device.key", wall_clock=lambda: 1_800_000_000.0)
    head, expiry, sig = keys.issue().split(".")
    # Non-ASCII bytes in a cookie arrive as latin-1 characters; huge numbers must not be parsed.
    for bad in (
        f"\xe4{head}.{expiry}.{sig}",
        f"{head}.{expiry}.\xe4{sig}",
        f"{head}.{'9' * 4000}.x",
    ):
        assert keys.verify(bad) is None


def test_device_key_rotation_voids_every_cookie(tmp_path: Path) -> None:
    keys = DeviceKeys(tmp_path / "web-device.key", wall_clock=lambda: 1_800_000_000.0)
    old = keys.issue()
    keys.rotate()
    assert keys.verify(old) is None
    assert keys.verify(keys.issue()) is not None


def test_public_guard_full_table_keeps_escalated_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_trading_bot.web import auth

    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 3)
    guard = PublicGuard(Clock())
    for key in ("ip:a", "ip:b", "ip:c"):
        for _ in range(MAX_FAILURES):
            guard.failed(key, device=False)
    # A flood of new addresses neither forgets the locked ones nor grows the table without end.
    for i in range(50):
        guard.failed(f"ip:new{i}", device=False)
    assert all(guard.seconds_locked(k, device=True) > 0 for k in ("ip:a", "ip:b", "ip:c"))
    assert len(guard.guards) <= 4
    assert guard.seconds_locked("ip:another", device=True) > 0  # newcomers share the overflow
