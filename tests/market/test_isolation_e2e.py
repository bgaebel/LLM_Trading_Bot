# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_v031.py (adapted) - partial snapshot test
"""Real transport path: a broken BTC response must not cost ETH its snapshot (CLAUDE.md rule)."""

import http.client
import json
import re
import urllib.request
from typing import Any

import pytest

from fakes import VALID_BOOK
from llm_trading_bot.market.bitvavo import TIMEFRAMES, BitvavoClient
from llm_trading_bot.market.http import UrllibTransport
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import build_snapshots

NOW = 1_800_000_030
MINUTES = {name: tf for tf, name in TIMEFRAMES.items()}


class Response:
    def __init__(self, payload: bytes | Exception) -> None:
        self.payload = payload

    def read(self, _limit: int) -> bytes:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def candles_json(url: str) -> bytes:
    match = re.search(r"interval=(\w+)", url)
    assert match
    step = MINUTES[match.group(1)] * 60
    last = (NOW // step - 1) * step
    rows = [[(last - i * step) * 1000, "100", "101", "99", "100", "1"] for i in range(200)]
    return json.dumps(rows).encode("utf-8")


def serve(btc_failure: Exception | bytes):  # type: ignore[no-untyped-def]
    def urlopen(request: urllib.request.Request, timeout: float) -> Response:
        url = request.full_url
        if url.endswith("/ticker/book"):
            return Response(json.dumps(VALID_BOOK).encode("utf-8"))
        if "/BTC-EUR/" in url:
            return Response(btc_failure)
        return Response(candles_json(url))

    return urlopen


@pytest.mark.parametrize(
    "btc_failure",
    [
        http.client.IncompleteRead(b"partial"),
        http.client.BadStatusLine("garbage"),
        TimeoutError("read timed out"),
        b"[[Infinity, 1, 1, 1, 1, 1]]",  # json.loads accepts Infinity -> OverflowError in int()
        b"<html>not json</html>",
        b"\xff\xfe",  # not UTF-8
    ],
)
def test_broken_btc_response_keeps_eth_snapshot(
    monkeypatch: pytest.MonkeyPatch, btc_failure: Any
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", serve(btc_failure))
    problems: list[Symbol] = []
    snaps = build_snapshots(
        BitvavoClient(UrllibTransport()),
        clock=lambda: NOW,
        on_error=lambda symbol, _exc: problems.append(symbol),
    )
    assert set(snaps) == {Symbol.ETHEUR}
    assert problems == [Symbol.BTCEUR]
