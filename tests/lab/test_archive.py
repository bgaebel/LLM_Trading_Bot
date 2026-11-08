"""Spec 016 AK1: candle archive - windows by open time, only settled bars, resume, rate limit."""

import hashlib
import json
import math
import sqlite3
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import llm_trading_bot.lab.archive as archive_mod
from llm_trading_bot.lab.archive import (
    SETTLE_SECONDS,
    Archive,
    ArchiveError,
    LabTransport,
    PublicApi,
    cross_check,
    cross_check_text,
    fetch_archive,
    window_start,
    windows,
)
from llm_trading_bot.lab.model import SEAL_EPOCH
from llm_trading_bot.market import bitvavo
from llm_trading_bot.market.bitvavo import API
from llm_trading_bot.market.http import HTTPFailure

D0 = 19_676 * 86_400
"""2023-11-15T00:00:00Z, the start of an aligned 1m window (one UTC day)."""
NAMES = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}


def bar(
    ts: int, o: str = "100", h: str = "101", lo: str = "99", c: str = "100.5", v: str = "1.5"
) -> list[Any]:
    return [ts * 1000, o, h, lo, c, v]


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeExchange:
    """Answers ``/candles`` like Bitvavo: start inclusive, end exclusive by open time, newest
    first, the forming bar included. Records every URL."""

    def __init__(self, clock: Clock, series: dict[tuple[str, int], list[list[Any]]]) -> None:
        self.clock = clock
        self.series = series
        self.urls: list[str] = []
        self.headers: dict[str, str] = {"bitvavo-ratelimit-remaining": "990"}
        self.inclusive_end = False
        self.latency = 0.0
        self.fail: Callable[[int], Exception | None] = lambda _n: None

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]:
        self.urls.append(url)
        assert url.startswith(API + "/")
        error = self.fail(len(self.urls))
        if error is not None:
            raise error
        sent = self.clock.now
        self.clock.now += self.latency
        parts = urlsplit(url)
        market, kind = parts.path.split("/")[-2:]
        assert kind == "candles"
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert q["limit"] == "1440"
        start, end = int(q["start"]), int(q["end"])
        rows = [
            r
            for r in self.series.get((market, NAMES[q["interval"]]), [])
            if start <= r[0]
            and (r[0] < end or (self.inclusive_end and r[0] == end))
            and r[0] <= sent * 1000
        ]
        return sorted(rows, key=lambda r: -int(r[0])), dict(self.headers)


def make(
    tmp_path: Path, now: float, series: dict[tuple[str, int], list[list[Any]]]
) -> tuple[Archive, FakeExchange, PublicApi, list[float]]:
    clock = Clock(now)
    exchange = FakeExchange(clock, series)
    sleeps: list[float] = []
    api = PublicApi(exchange, clock=clock, sleep=sleeps.append, min_interval=0)
    return Archive(tmp_path / "candles.db"), exchange, api, sleeps


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def stored(archive: Archive, market: str = "BTC-EUR", interval: int = 1) -> list[list[Any]]:
    return archive.rows(market, interval, 0, 2**40)


# ---------------------------------------------------------------- AK1 evidence


def test_pages_by_open_time(tmp_path: Path) -> None:
    day = 86_400
    rows = [bar(D0), bar(D0 + day - 60), bar(D0 + day), bar(D0 + day + 60)]
    archive, exchange, api, _ = make(tmp_path, D0 + 3 * day, {("BTC-EUR", 1): rows})
    # Even if Bitvavo included the row at ``end``, it belongs to the next window only.
    exchange.inclusive_end = True
    result = fetch_archive(
        archive, api, start=D0, end=D0 + 2 * day, markets=["BTC-EUR"], intervals=[1]
    )
    assert result.complete and result.requests == 2 and result.windows_done == 2
    assert [query(u) for u in exchange.urls] == [
        {"interval": "1m", "limit": "1440", "start": str(D0 * 1000), "end": str((D0 + day) * 1000)},
        {
            "interval": "1m",
            "limit": "1440",
            "start": str((D0 + day) * 1000),
            "end": str((D0 + 2 * day) * 1000),
        },
    ]
    assert result.rows_outside == 1
    assert stored(archive) == rows
    state = archive.window_state("BTC-EUR", 1, D0)
    assert state is not None and (state.status, state.rows) == ("done", 2)
    # Higher intervals page the same way: 1440 bars per window, aligned to the epoch.
    for interval in (15, 60, 240):
        span = 1440 * interval * 60
        ws = windows(interval, D0, D0 + 2 * span)
        assert all(s % span == 0 and e - s == span for s, e in ws)
        assert ws[0][0] <= D0 < ws[0][1] and window_start(interval, D0) == ws[0][0]


def test_forming_bar_never_written(tmp_path: Path) -> None:
    rows = [bar(D0 + 60 * k) for k in range(11)]  # D0 .. D0+600, the last one forming
    for settle, newest in ((0, D0 + 540), (SETTLE_SECONDS, D0 + 480)):
        folder = tmp_path / str(settle)
        archive, _, api, _ = make(folder, D0 + 630, {("BTC-EUR", 1): rows})
        result = fetch_archive(
            archive,
            api,
            start=D0,
            end=D0 + 86_400,
            markets=["BTC-EUR"],
            intervals=[1],
            settle=settle,
        )
        assert result.windows_open == 1
        assert [r[0] // 1000 for r in stored(archive)][-1] == newest
        assert all(r[0] // 1000 + 60 + settle <= D0 + 630 for r in stored(archive))
        archive.close()
    # The clock is read before the request: a bar that closes while the answer travels was
    # still forming on the server and must not be stored.
    archive, exchange, api, _ = make(tmp_path / "latency", D0 + 599, {("BTC-EUR", 1): rows})
    exchange.latency = 5
    fetch_archive(
        archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1], settle=0
    )
    assert [r[0] // 1000 for r in stored(archive)][-1] == D0 + 480


def test_open_window_completed_later(tmp_path: Path) -> None:
    full = [bar(D0 + 60 * k) for k in range(1440)]
    series = {("BTC-EUR", 1): full}
    archive, exchange, api, _ = make(tmp_path, D0 + 43_200, series)
    first = fetch_archive(
        archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    state = archive.window_state("BTC-EUR", 1, D0)
    assert first.windows_open == 1 and state is not None and state.status == "open"
    noon_rows = len(stored(archive))
    assert noon_rows == (43_200 - 60 - SETTLE_SECONDS) // 60 + 1

    exchange.clock.now = D0 + 86_400 + SETTLE_SECONDS
    second = fetch_archive(
        archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    state = archive.window_state("BTC-EUR", 1, D0)
    assert second.windows_done == 1 and second.rows_written == 1440 - noon_rows
    assert state is not None and (state.status, state.rows) == ("done", 1440)
    assert stored(archive) == full
    # Rows written at noon keep their first fetch time: completing does not rewrite them.
    stamps = {r[0] for r in archive.db.execute("SELECT DISTINCT fetched_at FROM candles")}
    assert stamps == {D0 + 43_200, D0 + 86_400 + SETTLE_SECONDS}
    # AK1: every row names where it came from (literal on purpose: a changed SOURCE must fail).
    sources = [r[0] for r in archive.db.execute("SELECT DISTINCT source FROM candles")]
    assert sources == ["bitvavo-api"]

    exchange.clock.now += 86_400
    third = fetch_archive(
        archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert third.requests == 0 and third.windows_skipped == 1


def test_resume_writes_each_window_once(tmp_path: Path) -> None:
    class Crash(Exception):
        pass

    days = 4
    rows = [bar(D0 + 86_400 * d + 60 * k) for d in range(days) for k in (0, 7, 1439)]
    archive, exchange, api, _ = make(tmp_path, D0 + days * 86_400 + 3600, {("BTC-EUR", 1): rows})
    exchange.fail = lambda n: Crash() if n == 3 else None
    with pytest.raises(Crash):
        fetch_archive(
            archive, api, start=D0, end=D0 + days * 86_400, markets=["BTC-EUR"], intervals=[1]
        )
    assert len(stored(archive)) == 6  # two windows committed before the abort

    archive.close()
    archive = Archive(tmp_path / "candles.db")
    api2 = PublicApi(exchange, clock=exchange.clock, sleep=lambda _s: None, min_interval=0)
    result = fetch_archive(
        archive, api2, start=D0, end=D0 + days * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert result.complete and result.windows_skipped == 2 and result.windows_done == 2
    starts = [int(query(u)["start"]) // 1000 for u in exchange.urls]
    assert starts == [D0, D0 + 86_400, D0 + 2 * 86_400, D0 + 2 * 86_400, D0 + 3 * 86_400]
    assert stored(archive) == rows


def test_429_stops_and_reports_retry_at(tmp_path: Path) -> None:
    rows = [bar(D0 + 86_400 * d) for d in range(3)]
    now = D0 + 4 * 86_400 + 0.5
    archive, exchange, api, _ = make(tmp_path, now, {("BTC-EUR", 1): rows})
    exchange.fail = lambda n: HTTPFailure(429, 30.0) if n == 2 else None
    result = fetch_archive(
        archive, api, start=D0, end=D0 + 3 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    # A key-less 429 bans the IP for 15 minutes, longer than the 30 s hint.
    retry_at = math.ceil(now + 900)
    assert (result.stopped, result.retry_at, result.requests) == ("rate_limited", retry_at, 2)
    assert archive.retry_at() == retry_at
    assert "Weiter frühestens ab" in result.text() and "UTC" in result.text()

    exchange.clock.now = now + 300
    waiting = fetch_archive(
        archive, api, start=D0, end=D0 + 3 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert (waiting.stopped, waiting.retry_at, waiting.requests) == ("rate_limited", retry_at, 0)

    exchange.clock.now = retry_at
    later = fetch_archive(
        archive, api, start=D0, end=D0 + 3 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert later.complete and later.windows_skipped == 1 and later.windows_done == 2
    assert stored(archive) == rows
    assert len(exchange.urls) == 4  # never retried the 429 itself


def test_rows_checked_by_live_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The live client's own functions, not copies.
    assert vars(archive_mod)["_parse_candle"] is bitvavo._parse_candle
    assert vars(archive_mod)["_open_time"] is bitvavo._open_time
    calls: list[int] = []

    def spy(row: object, step: int) -> object:
        calls.append(step)
        return bitvavo._parse_candle(row, step)

    monkeypatch.setattr(archive_mod, "_parse_candle", spy)
    good = [bar(D0), bar(D0 + 60)]
    archive, exchange, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): good})
    fetch_archive(archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1])
    assert calls == [60, 60]

    for broken in (
        bar(D0 + 86_400, o="98"),  # low above open
        [(D0 + 86_400) * 1000 + 1, "100", "101", "99", "100", "1"],  # not a whole second
        bar(D0 + 86_400 + 30),  # not aligned to the minute
        bar(D0 + 86_400, v="-1"),
    ):
        exchange.series = {("BTC-EUR", 1): [bar(D0 + 86_400 + 60), broken]}
        with pytest.raises(ArchiveError):
            fetch_archive(
                archive,
                api,
                start=D0 + 86_400,
                end=D0 + 2 * 86_400,
                markets=["BTC-EUR"],
                intervals=[1],
            )
        assert archive.window_state("BTC-EUR", 1, D0 + 86_400) is None
        assert stored(archive) == good


# ---------------------------------------------------------------- more archive behaviour


def test_conflicting_duplicate_in_answer_rejected(tmp_path: Path) -> None:
    rows = [bar(D0), bar(D0, c="100.7")]
    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): rows})
    with pytest.raises(ArchiveError, match="conflicting duplicate"):
        fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    assert stored(archive) == []


def test_equal_duplicate_is_kept_once(tmp_path: Path) -> None:
    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): [bar(D0), bar(D0)]})
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    assert stored(archive) == [bar(D0)]


def test_changed_row_on_completion_is_conflict(tmp_path: Path) -> None:
    series = {("BTC-EUR", 1): [bar(D0), bar(D0 + 60)]}
    archive, exchange, api, _ = make(tmp_path, D0 + 3600, series)
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    exchange.clock.now = D0 + 2 * 86_400
    for changed in ([bar(D0, v="1.6"), bar(D0 + 60)], [bar(D0 + 60)]):  # revised, vanished
        exchange.series = {("BTC-EUR", 1): changed}
        with pytest.raises(ArchiveError):
            fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
        state = archive.window_state("BTC-EUR", 1, D0)
        assert state is not None and state.status == "open"
        assert stored(archive) == [bar(D0), bar(D0 + 60)]


def test_window_commit_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object) -> object:
        raise RuntimeError("crash between rows and status")

    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): [bar(D0)]})
    monkeypatch.setattr(archive_mod, "WindowState", broken)
    with pytest.raises(RuntimeError):
        fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    assert stored(archive) == [] and archive.window_state("BTC-EUR", 1, D0) is None


def test_stored_candles_and_done_windows_are_immutable(tmp_path: Path) -> None:
    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): [bar(D0)]})
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    for sql in (
        "UPDATE candles SET close='1'",
        "DELETE FROM candles",
        "UPDATE windows SET status='open'",
        "DELETE FROM windows",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            archive.db.execute(sql)
    assert stored(archive) == [bar(D0)]


def test_slows_down_below_200_remaining(tmp_path: Path) -> None:
    now = D0 + 3 * 86_400
    archive, exchange, api, sleeps = make(tmp_path, now, {})
    exchange.headers = {
        "bitvavo-ratelimit-remaining": "150",
        "bitvavo-ratelimit-resetat": str(int((now + 20) * 1000)),
    }
    result = fetch_archive(
        archive, api, start=D0, end=D0 + 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert sleeps == [21.0] and result.slowdowns == 1
    exchange.headers = {"bitvavo-ratelimit-remaining": "200"}
    calm = fetch_archive(
        archive, api, start=D0 + 86_400, end=D0 + 2 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert sleeps == [21.0] and calm.slowdowns == 0


def test_paces_requests(tmp_path: Path) -> None:
    clock = Clock(D0 + 5 * 86_400)
    exchange = FakeExchange(clock, {})
    sleeps: list[float] = []
    api = PublicApi(exchange, clock=clock, sleep=sleeps.append, min_interval=0.12)
    api.candles("BTC-EUR", 1, D0, D0 + 86_400)
    api.candles("BTC-EUR", 1, D0 + 86_400, D0 + 2 * 86_400)
    assert sleeps == [pytest.approx(0.12)]


class AnyAnswer:
    """Answers every URL with an empty list and the given headers; records the URLs."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers if headers is not None else {}
        self.urls: list[str] = []

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]:
        self.urls.append(url)
        return [], dict(self.headers)


def test_paces_by_request_weight() -> None:
    # Trades weigh 5: 0.6 s after one, whatever comes next; candles weigh 1: 0.12 s.
    assert archive_mod.WEIGHTS == {"candles": 1, "trades": 5}
    assert archive_mod.MIN_INTERVAL == 0.12
    clock = Clock(D0 + 5 * 86_400)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    api = PublicApi(AnyAnswer(), clock=clock, sleep=sleep)
    api.trades("BTC-EUR", D0, D0 + 3600)
    api.trades("BTC-EUR", D0 + 3600, D0 + 7200)
    api.candles("BTC-EUR", 1, D0, D0 + 86_400)
    api.candles("BTC-EUR", 1, D0 + 86_400, D0 + 2 * 86_400)
    assert sleeps == [pytest.approx(0.6), pytest.approx(0.6), pytest.approx(0.12)]
    # Time already passed counts: no sleep when the gap is over.
    clock.now += 10
    api.trades("ETH-EUR", D0, D0 + 3600)
    assert len(sleeps) == 3
    # A full minute of trades stays at half the key-less budget of 1000 points.
    assert pytest.approx(500) == 60 / (5 * archive_mod.MIN_INTERVAL) * 5


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"bitvavo-ratelimit-remaining": "150"}, [60.0]),  # reset time missing
        # Reset at or before the local clock (a clock ahead of Bitvavo's): one minute, not 1 s.
        ({"bitvavo-ratelimit-remaining": "150", "bitvavo-ratelimit-resetat": "NOW_MS"}, [60.0]),
        (
            {"bitvavo-ratelimit-remaining": "150", "bitvavo-ratelimit-resetat": "PAST_MS"},
            [60.0],
        ),
        ({"bitvavo-ratelimit-remaining": "150", "bitvavo-ratelimit-resetat": "PAST_S"}, [60.0]),
        ({"bitvavo-ratelimit-remaining": "150", "bitvavo-ratelimit-resetat": "x"}, [60.0]),
        # A reset in the future: until then plus a second, at most one budget minute.
        ({"bitvavo-ratelimit-remaining": "0", "bitvavo-ratelimit-resetat": "SOON_MS"}, [3.0]),
        ({"bitvavo-ratelimit-remaining": "150", "bitvavo-ratelimit-resetat": "FAR_MS"}, [61.0]),
        # Plenty left, or nothing to react to: weight pacing alone.
        ({"bitvavo-ratelimit-remaining": "200", "bitvavo-ratelimit-resetat": "FAR_MS"}, []),
        ({}, []),
    ],
)
def test_low_remaining_waits_for_reset(headers: dict[str, str], expected: list[float]) -> None:
    now = D0 + 5 * 86_400
    values = {
        "NOW_MS": str(now * 1000),
        "PAST_MS": str((now - 40) * 1000),
        "PAST_S": str(now - 40),
        "SOON_MS": str((now + 2) * 1000),
        "FAR_MS": str((now + 600) * 1000),
    }
    resolved = {k: values.get(v, v) for k, v in headers.items()}
    sleeps: list[float] = []
    api = PublicApi(AnyAnswer(resolved), clock=Clock(now), sleep=sleeps.append, min_interval=0)
    api.trades("BTC-EUR", D0, D0 + 3600)
    assert sleeps == [pytest.approx(s) for s in expected]
    assert api.slowdowns == len(expected)


def test_transient_errors_retried_but_client_errors_not(tmp_path: Path) -> None:
    archive, exchange, api, sleeps = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): [bar(D0)]})
    exchange.fail = lambda n: HTTPFailure(502) if n == 1 else None
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    assert stored(archive) == [bar(D0)] and sleeps == [4.0]
    exchange.fail = lambda _n: HTTPFailure(400)
    with pytest.raises(HTTPFailure):
        fetch_archive(
            archive, api, start=D0 + 86_400, end=D0 + 86_401, markets=["BTC-EUR"], intervals=[1]
        )


def test_budget_stops_and_resumes(tmp_path: Path) -> None:
    archive, _, api, _ = make(tmp_path, D0 + 5 * 86_400, {})
    first = fetch_archive(
        archive,
        api,
        start=D0,
        end=D0 + 3 * 86_400,
        markets=["BTC-EUR"],
        intervals=[1],
        max_requests=2,
    )
    assert (first.stopped, first.requests) == ("budget", 2)
    assert "Anfragen-Grenze" in first.text()
    rest = fetch_archive(
        archive, api, start=D0, end=D0 + 3 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert rest.complete and rest.requests == 1 and rest.windows_skipped == 2


def test_windows_before_first_data_skipped_without_request(tmp_path: Path) -> None:
    first_eth = archive_mod.EARLIEST[("ETH-EUR", 1)]
    archive, _, api, _ = make(tmp_path, first_eth + 10 * 86_400, {})
    result = fetch_archive(
        archive,
        api,
        start=first_eth - 5 * 86_400,
        end=first_eth + 2 * 86_400,
        markets=["ETH-EUR"],
        intervals=[1],
    )
    assert result.windows_before_data == 5 and result.requests == 2
    # An empty window is complete with 0 rows ("gaps stay gaps").
    state = archive.window_state("ETH-EUR", 1, first_eth)
    assert state is not None and (state.status, state.rows) == ("done", 0)


def test_future_windows_not_requested(tmp_path: Path) -> None:
    archive, _, api, _ = make(tmp_path, D0 + 30, {})
    result = fetch_archive(
        archive, api, start=D0 - 86_400, end=D0 + 5 * 86_400, markets=["BTC-EUR"], intervals=[1]
    )
    assert result.requests == 1 and result.windows_open == 1


def test_public_api_only_builds_public_paths() -> None:
    api = PublicApi(FakeExchange(Clock(D0), {}), clock=Clock(D0), sleep=lambda _s: None)
    with pytest.raises(ValueError):
        api.candles("BTC-EUR/../x", 1, D0, D0 + 86_400)
    with pytest.raises(ValueError):
        api._request("/ticker/book", weight=1)
    with pytest.raises(ValueError):
        api.trades("BTC-EUR", D0, D0 + 86_400)  # Bitvavo wants less than 24 h


def test_lab_transport_is_the_live_get_with_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        headers = Message()
        headers["Bitvavo-Ratelimit-Remaining"] = "998"

        def read(self, _limit: int) -> bytes:
            return b"[]"

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Response:
        seen.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    data, headers = LabTransport().get_json_and_headers(API + "/BTC-EUR/candles?interval=1m")
    assert data == [] and headers == {"bitvavo-ratelimit-remaining": "998"}
    request = seen[0]
    assert request.get_method() == "GET" and request.data is None
    assert {k.lower() for k in request.headers} == {"user-agent", "accept"}


# ---------------------------------------------------------------- storage and reading


def test_schema_version_refused(tmp_path: Path) -> None:
    path = tmp_path / "candles.db"
    Archive(path).close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    con.commit()
    con.close()
    with pytest.raises(ArchiveError, match="schema 2"):
        Archive(path)
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE x(a)")
    con.close()
    with pytest.raises(ArchiveError, match="not a candle archive"):
        Archive(other)


def test_reader_is_read_only(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError):
        Archive.open_readonly(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()
    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("BTC-EUR", 1): [bar(D0)]})
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["BTC-EUR"], intervals=[1])
    archive.close()
    with Archive.open_readonly(tmp_path / "candles.db") as reader:
        assert stored(reader) == [bar(D0)]
        with pytest.raises(sqlite3.OperationalError):
            reader.db.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
        with pytest.raises(ArchiveError):
            fetch_archive(reader, api, start=D0, end=D0 + 1)


def test_read_api_rows_count_checksum(tmp_path: Path) -> None:
    rows = [bar(D0 + 60 * k, h="120", c=str(100 + k)) for k in (0, 1, 2, 5, 9)]
    archive, _, api, _ = make(tmp_path, D0 + 2 * 86_400, {("ETH-EUR", 1): rows})
    fetch_archive(archive, api, start=D0, end=D0 + 1, markets=["ETH-EUR"], intervals=[1])
    assert archive.rows("ETH-EUR", 1, D0 + 60, D0 + 300) == rows[1:3]  # end exclusive
    assert archive.last_rows("ETH-EUR", 1, D0 + 540, 2) == rows[2:4]
    assert archive.count_before("ETH-EUR", 1, D0 + 300) == 3
    assert archive.count_before("ETH-EUR", 1, D0) == 0
    assert archive.count_before("BTC-EUR", 1, D0 + 86_400) == 0
    # sha256 over a header line and one compact JSON row per line (documented recipe).
    digest = hashlib.sha256(b'["ETH-EUR",1,%d,%d]\n' % (D0, D0 + 600))
    for r in rows:
        digest.update(json.dumps(r, separators=(",", ":")).encode() + b"\n")
    assert archive.checksum("ETH-EUR", 1, D0, D0 + 600) == digest.hexdigest()
    assert archive.checksum("ETH-EUR", 1, D0, D0 + 540) != digest.hexdigest()
    assert rows[0] == [D0 * 1000, "100", "120", "99", "100", "1.5"]  # values as sent


def test_cross_check_counts(tmp_path: Path) -> None:
    archive = Archive(tmp_path / "candles.db")
    later = D0 + 400 * 86_400
    minutes = [
        # slot D0: equal to its 15m bar
        bar(D0, o="10", h="12", lo="9", c="11", v="1"),
        bar(D0 + 60, o="11", h="13", lo="10", c="12", v="2.5"),
        # slot D0+900: volume differs
        bar(D0 + 900, o="12", h="12", lo="12", c="12", v="1"),
        # slot D0+2700: 1m only
        bar(D0 + 2700),
    ]
    higher = [
        bar(D0, o="10", h="13.0", lo="9", c="12", v="3.50"),
        bar(D0 + 900, o="12", h="12", lo="12", c="12", v="2"),
        bar(D0 + 1800),  # 15m only
        bar(D0 + 86_400),  # its 1m day is not loaded: not compared
    ]

    def stored_rows(rows: list[list[Any]]) -> list[tuple[int, str, str, str, str, str]]:
        return [(int(r[0]) // 1000, r[1], r[2], r[3], r[4], r[5]) for r in rows]

    archive.store_window("BTC-EUR", 1, D0, stored_rows(minutes), fetched_at=later)
    archive.store_window("BTC-EUR", 15, window_start(15, D0), stored_rows(higher), fetched_at=later)
    [check] = cross_check(archive, start=0, end=later, markets=["BTC-EUR"], intervals=[15])
    assert (check.equal, check.different, check.missing_1m, check.missing_higher) == (1, 1, 1, 1)
    assert check.fields == {"volume": 1}
    assert check.examples == [D0 + 900, D0 + 1800, D0 + 2700]
    text = cross_check_text([check])
    assert "4 Kerzen verglichen, 1 gleich, 1 verschieden" in text
    # Restricting the range restricts the comparison.
    [part] = cross_check(archive, start=D0, end=D0 + 900, markets=["BTC-EUR"], intervals=[15])
    assert (part.equal, part.compared) == (1, 1)


def store_around_seal(archive: Archive) -> None:
    """Equal 1m and 15m bars just before and right at the seal, all windows done."""
    later = SEAL_EPOCH + 30 * 86_400
    for ts in (SEAL_EPOCH - 900, SEAL_EPOCH):
        row = (ts, "100", "101", "99", "100.5", "1.5")
        archive.store_window("BTC-EUR", 1, window_start(1, ts), [row], fetched_at=later)
    archive.store_window(
        "BTC-EUR",
        15,
        window_start(15, SEAL_EPOCH),
        [
            (SEAL_EPOCH - 900, "100", "101", "99", "100.5", "1.5"),
            (SEAL_EPOCH, "100", "101", "99", "100.5", "1.5"),
        ],
        fetched_at=later,
    )


def test_cross_check_clamped_before_seal(tmp_path: Path) -> None:
    assert window_start(15, SEAL_EPOCH - 900) == window_start(15, SEAL_EPOCH)  # one 15m window
    with Archive(tmp_path / "candles.db") as archive:
        store_around_seal(archive)
        far = SEAL_EPOCH + 60 * 86_400
        [check] = cross_check(archive, start=0, end=far, markets=["BTC-EUR"], intervals=[15])
        assert (check.equal, check.compared) == (1, 1)  # only the bar before the seal
        [opened] = cross_check(
            archive, start=0, end=far, markets=["BTC-EUR"], intervals=[15], include_sealed=True
        )
        assert (opened.equal, opened.compared) == (2, 2)


def test_fetch_may_archive_beyond_the_seal(tmp_path: Path) -> None:
    # The fidelity check rebuilds live decisions from 2026: their candles must be fetchable.
    rows = [bar(SEAL_EPOCH + 86_400)]
    archive, _, api, _ = make(tmp_path, SEAL_EPOCH + 3 * 86_400, {("BTC-EUR", 1): rows})
    result = fetch_archive(
        archive,
        api,
        start=SEAL_EPOCH + 86_400,
        end=SEAL_EPOCH + 2 * 86_400,
        markets=["BTC-EUR"],
        intervals=[1],
    )
    assert result.complete and stored(archive) == rows


@pytest.mark.net
def test_net_one_window_from_bitvavo(tmp_path: Path) -> None:
    """One real request (weight 1): the 4h window holding Bitvavo's first BTC-EUR candles."""
    first = archive_mod.EARLIEST[("BTC-EUR", 240)]
    with Archive(tmp_path / "candles.db") as archive:
        result = fetch_archive(
            archive, PublicApi(), start=first, end=first + 1, markets=["BTC-EUR"], intervals=[240]
        )
        assert result.complete and result.requests == 1, result.text()
        rows = archive.rows("BTC-EUR", 240, 0, 2**40)
        assert rows and int(rows[0][0]) // 1000 >= first
        state = archive.window_state("BTC-EUR", 240, window_start(240, first))
        assert state is not None and state.status == "done" and state.rows == len(rows)
