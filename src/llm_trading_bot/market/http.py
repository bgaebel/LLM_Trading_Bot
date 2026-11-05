# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (HTTPFailure, http_json GET path) (adapted)
"""Minimal GET-only JSON transport. Kept narrow so tests can swap it for a fake."""

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Protocol

MAX_RETRY_AFTER = 3600.0
# Bitvavo reports the reset time in epoch milliseconds; anything this large cannot be seconds.
_MILLISECONDS_THRESHOLD = 100_000_000_000
_MAX_BODY_BYTES = 2_000_000


class HTTPFailure(RuntimeError):
    """HTTP error status. Carries only status and retry delay, never the response body."""

    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class Transport(Protocol):
    def get_json(self, url: str) -> object: ...


def _header_float(headers: Message, name: str) -> float | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def retry_after_seconds(headers: Message, *, now: float) -> float | None:
    """Delay requested by the server via Retry-After or bitvavo-ratelimit-resetat, capped to 1 h."""
    delay = 0.0
    retry_after = _header_float(headers, "Retry-After")
    if retry_after is not None:
        delay = min(MAX_RETRY_AFTER, max(0.0, retry_after))
    reset_at = _header_float(headers, "bitvavo-ratelimit-resetat")
    if reset_at is not None:
        if reset_at > _MILLISECONDS_THRESHOLD:
            reset_at /= 1000
        delay = max(delay, min(MAX_RETRY_AFTER, max(0.0, reset_at - now)))
    return delay or None


class UrllibTransport:
    """Standard-library transport; only GET, no auth headers, no request body (ADR-0004)."""

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        user_agent: str = "llm-trading-bot/0.1",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._clock = clock

    def get_json(self, url: str) -> object:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self._user_agent, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body: bytes = response.read(_MAX_BODY_BYTES)
        except urllib.error.HTTPError as exc:
            # The body is dropped on purpose: error pages may echo request data into logs.
            delay = retry_after_seconds(exc.headers, now=self._clock())
            raise HTTPFailure(exc.code, delay) from None
        result: object = json.loads(body.decode("utf-8"))
        return result
