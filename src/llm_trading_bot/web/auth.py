"""Single-operator login for the web UI (ADR-0015, ADR-0024): scrypt hash, lockout, sessions.

ADR-0024 adds the public way in (HTTPS through Caddy): a lockout per client that doubles up to
an hour, a global ceiling, a signed device cookie that keeps the owner out of both, and a
stricter password rule for logins from the internet.
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from llm_trading_bot.web.files import write_private

# ADR-0015: the operator chose 8 for the tunnel; scrypt and the lockout carry the rest.
MIN_PASSWORD_LENGTH = 8
# ADR-0024: from the internet, 5 guesses per client and hour must not suffice for a short one.
PUBLIC_MIN_PASSWORD_LENGTH = 12
# ~16 MB and a few tens of ms per check: slow for guessing, fast enough for one operator.
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_LENGTH = 2**14, 8, 1, 32
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
MAX_LOCKOUT_SECONDS = 3600
GLOBAL_FAILURES_PER_HOUR = 100
SESSION_SECONDS = 12 * 3600
DEVICE_SECONDS = 90 * 86400
# ADR-0025: a client's address is kept no longer than needed to slow down guessing - 24 h after
# its last failure or the end of its lock, whichever is later. The privacy policy names this.
FORGET_SECONDS = 24 * 3600
PRUNE_SECONDS = 60
"""How often the whole table is swept; a looked-up entry is checked on every access."""
MAX_TRACKED_CLIENTS = 10_000
OVERFLOW = "overflow"

# Long but common: a dictionary attack tries these first. Lower-case, compared case-insensitively.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890123",
        "12345678901234",
        "123456123456",
        "111111111111",
        "000000000000",
        "qwertyuiopas",
        "qwertzuiopue",
        "qwertzuiopü",
        "qwertyuiop123",
        "qwertz123456",
        "qwerty123456",
        "asdfghjkl123",
        "passwort1234",
        "passwort12345",
        "password1234",
        "password12345",
        "password123!",
        "passwort123!",
        "passwordpassword",
        "passwortpasswort",
        "iloveyou1234",
        "letmein12345",
        "welcome12345",
        "willkommen123",
        "hallo1234567",
        "sommer2025!!",
        "sommer2026!!",
        "trustno1trustno1",
        "administrator",
        "administrator1",
        "bitcoin12345",
        "bitcoinbitcoin",
        "tradingbot123",
        "tradingbot2026",
        "changeme1234",
        "abc123abc123",
        "abcdefghijkl",
        "1q2w3e4r5t6y",
        "1qaz2wsx3edc",
        "zaq12wsxcde3",
        "football1234",
        "superman1234",
        "dragon123456",
        "sunshine1234",
        "princess1234",
        "monkey123456",
        "baseball1234",
        "starwars1234",
        "pokemon12345",
    }
)


def password_problem(password: str, *, public: bool) -> str:
    """Why ``password`` is not good enough ("" if it is). New passwords always use the public
    rule; for a login through the tunnel the ADR-0015 minimum still applies."""
    minimum = PUBLIC_MIN_PASSWORD_LENGTH if public else MIN_PASSWORD_LENGTH
    if len(password) < minimum:
        return f"mindestens {minimum} Zeichen"
    if public:
        folded = password.casefold()
        if folded in COMMON_PASSWORDS or len(set(folded)) <= 2 or folded.isdigit():
            return "zu leicht zu erraten"
    return ""


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
        """Every new password follows the public rule (ADR-0024)."""
        problem = password_problem(password, public=True)
        if problem:
            raise ValueError(f"password: {problem}")
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
    """After MAX_FAILURES wrong passwords in a row, every attempt is refused for a while.

    The tunnel's guard: a fixed LOCKOUT_SECONDS. ``doubling`` (the public way, ADR-0024) makes
    every further lock twice as long, up to MAX_LOCKOUT_SECONDS, until a login succeeds.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, *, doubling: bool = False):
        self.clock = clock
        self.doubling = doubling
        self.failures = 0
        self.locks = 0
        self.locked_until = 0.0
        self.last_failure = 0.0

    def seconds_locked(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def failed(self) -> None:
        self.last_failure = self.clock()
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            factor = 2**self.locks if self.doubling else 1
            self.locked_until = self.clock() + min(LOCKOUT_SECONDS * factor, MAX_LOCKOUT_SECONDS)
            self.locks += 1
            self.failures = 0

    def succeeded(self) -> None:
        self.failures = 0
        self.locks = 0
        self.locked_until = 0.0

    def idle(self) -> bool:
        """Nothing to remember any more: no failures, no lock (doubling resets only on success,
        so a client that was locked stays tracked)."""
        return self.failures == 0 and self.locks == 0

    def forgettable(self, now: float) -> bool:
        """FORGET_SECONDS have passed since the last failure and since the end of the lock; a
        running lock is never forgotten, whatever the constants say."""
        if self.locked_until > now:
            return False
        return now >= max(self.last_failure, self.locked_until) + FORGET_SECONDS


def client_key(address: str) -> str:
    """The lockout key of a client address: an IPv4 address, or the /64 of an IPv6 one (one
    household or server gets a whole /64)."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class PublicGuard:
    """Lockouts of the public way: per client key, plus a global ceiling for unknown devices.

    A device that once logged in (valid device cookie) is keyed by its device id and ignores
    the global ceiling, so an attacker can neither lock the owner out nor guess through it.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.guards: dict[str, LoginGuard] = {}
        self.recent: deque[float] = deque()
        self.pruned = clock()

    def prune(self) -> None:
        """Forget every entry whose time is up (ADR-0025)."""
        now = self.clock()
        self.pruned = now
        for k in [k for k, g in self.guards.items() if g.forgettable(now)]:
            del self.guards[k]

    def _tidy(self, key: str) -> None:
        """Lazily on access: the entry asked for is checked every time, the whole table at
        most every PRUNE_SECONDS - a flood of requests must not sweep 10,000 entries each."""
        now = self.clock()
        if now - self.pruned >= PRUNE_SECONDS:
            self.prune()
            return
        guard = self.guards.get(key)
        if guard is not None and guard.forgettable(now):
            del self.guards[key]

    def _guard(self, key: str) -> LoginGuard:
        guard = self.guards.get(key)
        if guard is None:
            if len(self.guards) >= MAX_TRACKED_CLIENTS:
                # A flood of fresh addresses must not grow memory - but a client in an escalated
                # backoff stays tracked (forgetting it would hand it fresh tries). Only clients
                # without failures and locks are forgotten; beyond that, newcomers share one key.
                for k in [k for k, g in self.guards.items() if g.idle()]:
                    del self.guards[k]
                if len(self.guards) >= MAX_TRACKED_CLIENTS:
                    key = OVERFLOW
                    guard = self.guards.get(key)
                    if guard is not None:
                        return guard
            guard = self.guards[key] = LoginGuard(self.clock, doubling=True)
        return guard

    def _global_seconds(self) -> int:
        now = self.clock()
        while self.recent and self.recent[0] <= now - 3600:
            self.recent.popleft()
        if len(self.recent) >= GLOBAL_FAILURES_PER_HOUR:
            return max(1, int(self.recent[0] + 3600 - now + 0.999))
        return 0

    def seconds_locked(self, key: str, *, device: bool) -> int:
        self._tidy(key)
        if key not in self.guards and len(self.guards) >= MAX_TRACKED_CLIENTS:
            key = OVERFLOW
        own = self.guards[key].seconds_locked() if key in self.guards else 0
        return own if device else max(own, self._global_seconds())

    def failed(self, key: str, *, device: bool) -> None:
        self._tidy(key)
        self._guard(key).failed()
        if not device:
            self.recent.append(self.clock())

    def succeeded(self, key: str) -> None:
        self.guards.pop(key, None)


class DeviceKeys:
    """Signs the device cookie: ``<id>.<expiry epoch>.<hmac>`` with a key kept in the data dir."""

    def __init__(self, path: Path, wall_clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.wall_clock = wall_clock

    def _key(self) -> bytes:
        if not self.path.exists():
            write_private(self.path, secrets.token_hex(32))
        return bytes.fromhex(self.path.read_text(encoding="utf-8").strip())

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key(), payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        """A fresh cookie; issued on every public login, so a used device never expires."""
        payload = f"{secrets.token_urlsafe(16)}.{int(self.wall_clock()) + DEVICE_SECONDS}"
        return f"{payload}.{self._sign(payload)}"

    def rotate(self) -> None:
        """A new key: every cookie issued so far is void (after a password change)."""
        write_private(self.path, secrets.token_hex(32))

    def verify(self, token: str | None) -> str | None:
        """The device id of a valid, unexpired token; None otherwise - also for any garbage a
        client sends (non-ASCII bytes arrive as latin-1 characters)."""
        if not token or not token.isascii() or token.count(".") != 2:
            return None
        device, expiry, signature = token.split(".")
        if not expiry.isdigit() or len(expiry) > 12:
            return None
        if not hmac.compare_digest(signature.encode(), self._sign(f"{device}.{expiry}").encode()):
            return None
        return device if int(expiry) > self.wall_clock() else None


@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    expires: float


class Sessions:
    """In memory only: restarting the web server logs everybody out (ADR-0015)."""

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

    def drop_others(self, token: str) -> None:
        """After a password change only the session that changed it stays logged in."""
        self._sessions = {t: s for t, s in self._sessions.items() if t == token}


def new_setup_code() -> str:
    return secrets.token_urlsafe(12)


def same(a: str, b: str) -> bool:
    """Constant-time comparison for codes and tokens."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
