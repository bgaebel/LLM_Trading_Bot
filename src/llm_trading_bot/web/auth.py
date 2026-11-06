"""Single-operator login for the web UI (ADR-0014): scrypt password hash, lockout, sessions."""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from llm_trading_bot.web.files import write_private

MIN_PASSWORD_LENGTH = 12
# ~16 MB and a few tens of ms per check: slow for guessing, fast enough for one operator.
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_LENGTH = 2**14, 8, 1, 32
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
SESSION_SECONDS = 12 * 3600


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_LENGTH, maxmem=64 * 2**20
    )


class PasswordStore:
    """The operator's password as salted scrypt hash; the password itself is never stored."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def is_set(self) -> bool:
        return self.path.exists()

    def set(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password needs at least {MIN_PASSWORD_LENGTH} characters")
        salt = secrets.token_bytes(16)
        record = {
            "version": 1,
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": salt.hex(),
            "hash": _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P).hex(),
        }
        write_private(self.path, json.dumps(record))

    def verify(self, password: str) -> bool:
        if not self.is_set():
            return False
        record = json.loads(self.path.read_text(encoding="utf-8"))
        expected = bytes.fromhex(record["hash"])
        actual = _scrypt(
            password, bytes.fromhex(record["salt"]), record["n"], record["r"], record["p"]
        )
        return hmac.compare_digest(actual, expected)


class LoginGuard:
    """After MAX_FAILURES wrong passwords in a row, every attempt is refused for a while."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.failures = 0
        self.locked_until = 0.0

    def seconds_locked(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            self.locked_until = self.clock() + LOCKOUT_SECONDS
            self.failures = 0

    def succeeded(self) -> None:
        self.failures = 0


@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    expires: float


class Sessions:
    """In memory only: restarting the web server logs everybody out (ADR-0014)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        session = Session(
            secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock() + SESSION_SECONDS
        )
        self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        session = self._sessions.get(token or "")
        if session is None:
            return None
        if self.clock() >= session.expires:
            del self._sessions[session.token]
            return None
        return session

    def drop(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)


def new_setup_code() -> str:
    return secrets.token_urlsafe(12)


def same(a: str, b: str) -> bool:
    """Constant-time comparison for codes and tokens."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
