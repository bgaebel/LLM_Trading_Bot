# Origin: LLM-Trading-Arena@7563724 apps/arena/tests/test_reporting_recovery.py (adapted)
import pytest

from fakes import VALID_BOOK, FakeClock, FakeTransport, fail_with
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.http import HTTPFailure
from llm_trading_bot.market.models import Symbol


def test_429_without_headers_blocks_for_900_seconds() -> None:
    clock = FakeClock(100.0)
    transport = FakeTransport(fail_with(429))
    client = BitvavoClient(transport, monotonic=clock)
    with pytest.raises(HTTPFailure):
        client.quotes()
    clock.now = 999.0
    with pytest.raises(HTTPFailure) as exc:
        client.quotes()
    assert exc.value.status == 429
    assert exc.value.retry_after == pytest.approx(1.0)
    assert len(transport.urls) == 1, "no request may go out during the cooldown"


def test_cooldown_ends_and_requests_resume() -> None:
    clock = FakeClock(100.0)
    responses: list[object] = []
    transport = FakeTransport(lambda url: responses.pop(0) if responses else fail_with(429)(url))
    client = BitvavoClient(transport, monotonic=clock)
    with pytest.raises(HTTPFailure):
        client.quotes()
    clock.now = 1000.0
    responses.append(VALID_BOOK)
    assert len(client.quotes()) == 2
    assert len(transport.urls) == 2


@pytest.mark.parametrize(("hint", "expected"), [(10.0, 10.0), (0.2, 1.0), (99_999.0, 3600.0)])
def test_server_hint_is_used_within_bounds(hint: float, expected: float) -> None:
    clock = FakeClock(0.0)
    client = BitvavoClient(FakeTransport(fail_with(429, hint)), monotonic=clock)
    with pytest.raises(HTTPFailure):
        client.quotes()
    with pytest.raises(HTTPFailure) as exc:
        client.quotes()
    assert exc.value.retry_after == pytest.approx(expected)


def test_cooldown_is_shared_between_quotes_and_candles() -> None:
    clock = FakeClock(0.0)
    transport = FakeTransport(fail_with(429, 60.0))
    client = BitvavoClient(transport, monotonic=clock)
    with pytest.raises(HTTPFailure):
        client.quotes()
    with pytest.raises(HTTPFailure):
        client.closed_candles(Symbol.BTCEUR, 1, now_epoch=1_800_000_000)
    assert len(transport.urls) == 1


def test_other_http_errors_do_not_start_a_cooldown() -> None:
    transport = FakeTransport(fail_with(503))
    client = BitvavoClient(transport, monotonic=FakeClock(0.0))
    for _ in range(2):
        with pytest.raises(HTTPFailure):
            client.quotes()
    assert len(transport.urls) == 2


def test_error_text_contains_no_response_body() -> None:
    client = BitvavoClient(FakeTransport(fail_with(429)), monotonic=FakeClock(0.0))
    with pytest.raises(HTTPFailure) as exc:
        client.quotes()
    assert str(exc.value) == "HTTP 429"
