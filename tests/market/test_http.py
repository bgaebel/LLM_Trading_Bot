# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_reporting_recovery.py (adapted)
import http.client
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from llm_trading_bot.market.http import (
    HTTPFailure,
    TransportError,
    UrllibTransport,
    retry_after_seconds,
)


def headers(**values: str) -> Message:
    msg = Message()
    for name, value in values.items():
        msg[name.replace("_", "-")] = value
    return msg


def test_retry_after_header_in_seconds() -> None:
    assert retry_after_seconds(headers(Retry_After="10"), now=0) == 10


def test_bitvavo_reset_in_milliseconds() -> None:
    h = headers(**{"bitvavo-ratelimit-resetat": "1900000900000"})
    assert retry_after_seconds(h, now=1_900_000_000) == 900


def test_larger_of_both_headers_wins() -> None:
    h = headers(Retry_After="5", **{"bitvavo-ratelimit-resetat": "1900000030"})
    assert retry_after_seconds(h, now=1_900_000_000) == 30


@pytest.mark.parametrize("value", ["99999", "1e9"])
def test_delay_capped_to_one_hour(value: str) -> None:
    assert retry_after_seconds(headers(Retry_After=value), now=0) == 3600


@pytest.mark.parametrize("value", ["garbage", "nan", "-5", "0"])
def test_unusable_header_gives_no_delay(value: str) -> None:
    assert retry_after_seconds(headers(Retry_After=value), now=0) is None


def test_no_headers_gives_no_delay() -> None:
    assert retry_after_seconds(headers(), now=0) is None


def test_http_error_exposes_status_and_delay_but_not_body(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = urllib.error.HTTPError(
        "https://example.invalid", 429, "error", headers(Retry_After="10"), io.BytesIO(b"PRIVATE")
    )

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(HTTPFailure) as exc:
        UrllibTransport().get_json("https://example.invalid")
    assert exc.value.status == 429
    assert exc.value.retry_after == 10
    assert "PRIVATE" not in str(exc.value)
    assert exc.value.__cause__ is None


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, _limit: int) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_get_request_is_plain_get_and_decodes_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(request)
        return FakeResponse(json.dumps({"name": "Ümlaut"}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert UrllibTransport().get_json("https://example.invalid/x") == {"name": "Ümlaut"}
    request = seen[0]
    assert request.get_method() == "GET"
    assert request.data is None
    assert {k.lower() for k in request.headers} == {"user-agent", "accept"}


def test_truncated_body_becomes_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Truncated(FakeResponse):
        def read(self, _limit: int) -> bytes:
            raise http.client.IncompleteRead(b"par")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: Truncated(b""))
    with pytest.raises(TransportError, match="IncompleteRead"):
        UrllibTransport().get_json("https://example.invalid")


def test_http_error_without_headers_still_gives_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = urllib.error.HTTPError("https://example.invalid", 502, "bad", None, None)  # type: ignore[arg-type]

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(HTTPFailure) as exc:
        UrllibTransport().get_json("https://example.invalid")
    assert exc.value.status == 502
    assert exc.value.retry_after is None


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"\xff\xfe[]", b""])
def test_non_json_200_becomes_transport_error_on_the_lab_path(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    class WithHeaders(FakeResponse):
        headers = Message()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: WithHeaders(body))
    with pytest.raises(TransportError, match="kein JSON") as exc:
        UrllibTransport().get_json_and_headers("https://example.invalid")
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    # The bot's own path stays as it was: callers there map ValueError themselves.
    with pytest.raises(ValueError):
        UrllibTransport().get_json("https://example.invalid")


def test_deeply_nested_json_becomes_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = b"[" * 200_000 + b"]" * 200_000
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(nested))
    with pytest.raises(TransportError, match="nested"):
        UrllibTransport().get_json("https://example.invalid")
