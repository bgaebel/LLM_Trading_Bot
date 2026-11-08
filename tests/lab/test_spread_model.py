"""Spec 016 AK6: spread model - estimate from public trades, conservative quarter value,
defaults, sampling with a fake transport. Reference values are worked out by hand."""

import calendar
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from llm_trading_bot.lab.archive import Archive, ArchiveError, PublicApi
from llm_trading_bot.lab.model import SEAL_EPOCH, Quarter
from llm_trading_bot.lab.spread import (
    DEFAULT_SPREAD,
    FLOOR,
    METHOD,
    WEEK,
    PublicTrade,
    QuarterSpreads,
    SpreadModel,
    build_quarter_spreads,
    complete_quarters,
    conservative_spread,
    hour_median,
    pair_spreads,
    parse_trades,
    quarter_label,
    sample_hours,
    sample_spreads,
)
from llm_trading_bot.market.bitvavo import API
from llm_trading_bot.market.http import HTTPFailure

Q2 = Quarter.parse("2021Q2")


def utc(*parts: int) -> int:
    return calendar.timegm((*parts, 0, 0, 0)[:6])


# ---------------------------------------------------------------- estimator


def test_pairs_need_opposite_sides_within_60_s() -> None:
    trades = [
        PublicTrade(0, 100.0, "buy"),
        PublicTrade(10_000, 99.9, "sell"),  # pair: 0.1 / 99.95
        PublicTrade(20_000, 99.9, "sell"),  # same side as before: no pair
        PublicTrade(90_000, 100.1, "buy"),  # 70 s after the previous: no pair
        PublicTrade(100_000, 100.0, "sell"),  # pair: 0.1 / 100.05
    ]
    assert pair_spreads(trades) == [
        pytest.approx(0.1 / 99.95, rel=1e-12),
        pytest.approx(0.1 / 100.05, rel=1e-12),
    ]


def test_hour_median_needs_three_pairs() -> None:
    assert hour_median([0.001, 0.003, 0.002]) == 0.002
    assert hour_median([0.001, 0.002]) is None


def test_quarter_value_is_upper_quartile_with_floor() -> None:
    # Inclusive quartile of 2, 4, 6, 8 bp: position 0.75 * 3 = 2.25 -> 6 + 0.25 * 2 = 6.5 bp.
    assert conservative_spread([0.0008, 0.0002, 0.0006, 0.0004]) == pytest.approx(0.00065)
    assert conservative_spread([0.0005]) == 0.0005
    assert conservative_spread([0.00003, 0.00001]) == FLOOR
    with pytest.raises(ValueError):
        conservative_spread([])


def test_parse_trades_sorts_and_rejects_bad_rows() -> None:
    data = [
        {"id": "b", "timestamp": 2000, "price": "100.5", "side": "sell", "amount": "0.1"},
        {"id": "a", "timestamp": 1000, "price": "100.0", "side": "buy", "amount": "0.2"},
    ]
    assert parse_trades(data) == [
        PublicTrade(1000, 100.0, "buy", "a"),
        PublicTrade(2000, 100.5, "sell", "b"),
    ]
    for bad in (
        {"timestamp": 1, "price": "x", "side": "buy"},
        {"timestamp": 1, "price": "-1", "side": "buy"},
        {"timestamp": 1, "price": "1", "side": "hold"},
        {"timestamp": "1", "price": "1", "side": "buy"},
    ):
        with pytest.raises(ArchiveError):
            parse_trades([bad])
    with pytest.raises(ArchiveError):
        parse_trades({"errorCode": 205})


# ---------------------------------------------------------------- model


def test_quarter_label_bounds() -> None:
    assert quarter_label(Q2.start) == "2021Q2"
    assert quarter_label(Q2.start - 1) == "2021Q1"
    assert quarter_label(Q2.end - 1) == "2021Q2"
    assert quarter_label(utc(2024, 12, 31, 23)) == "2024Q4"


def test_half_spread_per_quarter_with_documented_default() -> None:
    model: SpreadModel = QuarterSpreads({("BTC-EUR", "2021Q2"): 0.0008})
    assert model.half_spread("BTC-EUR", utc(2021, 5, 1)) == 0.0004
    assert model.half_spread("BTC-EUR", utc(2021, 8, 1)) == DEFAULT_SPREAD["BTC-EUR"] / 2
    assert model.half_spread("ETH-EUR", utc(2021, 5, 1)) == DEFAULT_SPREAD["ETH-EUR"] / 2
    assert DEFAULT_SPREAD == {"BTC-EUR": 0.0010, "ETH-EUR": 0.0015}
    spreads = QuarterSpreads({("BTC-EUR", "2021Q2"): 0.0008})
    assert spreads.source("BTC-EUR", utc(2021, 5, 1)) == "sampled"
    assert spreads.source("BTC-EUR", utc(2021, 8, 1)) == "default"
    for bad in (math.nan, -0.001, 0.5):
        with pytest.raises(ValueError):
            QuarterSpreads({("BTC-EUR", "2021Q2"): bad})


def test_sample_hours_are_stable_and_range_independent() -> None:
    start, end = Q2.start, Q2.start + 5 * WEEK
    hours = sample_hours("BTC-EUR", start, end)
    assert hours == sample_hours("BTC-EUR", start, end)
    assert hours == sorted(hours) and all(h % 3600 == 0 and start <= h < end for h in hours)
    cut = start + 2 * WEEK + 12_345
    assert hours == sample_hours("BTC-EUR", start, cut) + sample_hours("BTC-EUR", cut, end)
    # Two per week block: at most two in each, exactly two where the block lies inside.
    first_full = -(-start // WEEK) * WEEK
    assert len([h for h in hours if first_full <= h < first_full + WEEK]) == 2
    assert sample_hours("BTC-EUR", start, end, now=start + 3 * WEEK) == [
        h for h in hours if h + 3600 <= start + 3 * WEEK
    ]
    assert sample_hours("ETH-EUR", start, end) != hours  # each market has its own seed


# ---------------------------------------------------------------- sampling


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeTradesApi:
    """Answers the public market-trades endpoint from a list of trade dicts per market."""

    def __init__(self, trades: dict[str, list[dict[str, Any]]]) -> None:
        self.trades = trades
        self.urls: list[str] = []
        self.fail: Callable[[int], Exception | None] = lambda _n: None

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]:
        self.urls.append(url)
        assert url.startswith(API + "/")
        error = self.fail(len(self.urls))
        if error is not None:
            raise error
        parts = urlsplit(url)
        market, kind = parts.path.split("/")[-2:]
        assert kind == "trades"
        q = {k: int(v[0]) for k, v in parse_qs(parts.query).items()}
        rows = [t for t in self.trades.get(market, []) if q["start"] <= t["timestamp"] < q["end"]]
        return sorted(rows, key=lambda t: -int(t["timestamp"])), {
            "bitvavo-ratelimit-remaining": "900"
        }


def hour_with_spread(hour: int, spread: float, pairs: int = 3) -> list[dict[str, Any]]:
    """Alternating taker buys at the ask and sells at the bid, 10 s apart, mid 100."""
    ask, bid = 100 * (1 + spread / 2), 100 * (1 - spread / 2)
    out = []
    for k in range(pairs + 1):
        side = "buy" if k % 2 == 0 else "sell"
        price = ask if side == "buy" else bid
        out.append(
            {
                "id": f"{hour}-{k}",
                "timestamp": (hour + 10 * k) * 1000,
                "price": repr(price),
                "side": side,
                "amount": "0.01",
            }
        )
    return out


def test_sampler_builds_conservative_quarter_value(tmp_path: Path) -> None:
    start = (Q2.start // WEEK + 1) * WEEK  # two whole week blocks inside 2021Q2
    end = start + 2 * WEEK
    hours = sample_hours("BTC-EUR", start, end)
    assert len(hours) == 4
    # Hour medians 2, 4, 6, 8 bp -> upper quartile 6.5 bp (worked out above).
    trades = [t for i, h in enumerate(hours) for t in hour_with_spread(h, 0.0002 * (i + 1))]
    fake = FakeTradesApi({"BTC-EUR": trades})
    api = PublicApi(fake, clock=Clock(Q2.end + 86_400), sleep=lambda _s: None, min_interval=0)
    with Archive(tmp_path / "candles.db") as archive:
        result = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert (result.requests, result.hours_sampled, result.stopped) == (4, 4, "")
        for url in fake.urls:
            q = {k: int(v[0]) for k, v in parse_qs(urlsplit(url).query).items()}
            assert q["limit"] == 1000 and q["end"] - q["start"] == 3_600_000
            assert q["start"] // 1000 in hours
        medians = [
            r[0] for r in archive.db.execute("SELECT median FROM spread_samples ORDER BY hour")
        ]
        assert medians == [pytest.approx(0.0002 * (i + 1), rel=1e-9) for i in range(4)]

        again = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert (again.requests, again.hours_skipped) == (0, 4)

        # Two weeks of a quarter are no quarter value (wave-2 check of Spec 016).
        assert build_quarter_spreads(archive, now=123) == {}
        assert complete_quarters(archive) == set()
        assert QuarterSpreads.from_archive(archive).source("BTC-EUR", start) == "default"

        # The rest of the quarter has no trades: stored without a median, so the quarter is
        # whole and its value comes from the four hours that had pairs.
        whole = sample_spreads(archive, api, start=Q2.start, end=Q2.end, markets=["BTC-EUR"])
        assert whole.hours_skipped == 4
        assert whole.hours_sampled == len(sample_hours("BTC-EUR", Q2.start, Q2.end)) - 4
        assert complete_quarters(archive) == {("BTC-EUR", "2021Q2")}
        built = build_quarter_spreads(archive, now=123)
        assert list(built) == [("BTC-EUR", "2021Q2")]
        assert built[("BTC-EUR", "2021Q2")][0] == pytest.approx(0.00065, rel=1e-9)
        assert built[("BTC-EUR", "2021Q2")][1] == 4
        row = archive.db.execute("SELECT market, quarter, method, samples FROM spreads").fetchone()
        assert tuple(row) == ("BTC-EUR", "2021Q2", METHOD, 4)
        model = QuarterSpreads.from_archive(archive)
        assert model.half_spread("BTC-EUR", start) == pytest.approx(0.000325, rel=1e-9)
        assert model.source("BTC-EUR", start) == "sampled"
        assert model.half_spread("BTC-EUR", Q2.end) == DEFAULT_SPREAD["BTC-EUR"] / 2


def insert_sample(archive: Archive, market: str, hour: int, median: float | None) -> None:
    archive.db.execute(
        "INSERT INTO spread_samples(market, method, hour, trades, pairs, median, fetched_at) "
        "VALUES (?, ?, ?, 4, 3, ?, 1)",
        (market, METHOD, hour, median),
    )


def insert_spread_row(archive: Archive, market: str, label: str, spread: float) -> None:
    """A ``spreads`` row as older code (without the whole-quarter rule) may have left it."""
    archive.db.execute(
        "INSERT INTO spreads(market, quarter, method, spread, samples, computed_at) "
        "VALUES (?, ?, ?, ?, 1, 1)",
        (market, label, METHOD, spread),
    )


def test_partial_quarter_gives_no_value(tmp_path: Path) -> None:
    """The wave-2 probe: one quiet hour in 2022Q3 gave a "sampled" 1 bp instead of the default."""
    q = Quarter.parse("2022Q3")
    planned = sample_hours("BTC-EUR", q.start, q.end)
    with Archive(tmp_path / "candles.db") as archive:
        insert_sample(archive, "BTC-EUR", planned[0], FLOOR)
        insert_spread_row(archive, "BTC-EUR", "2022Q3", FLOOR)  # stale, never used
        model = QuarterSpreads.from_archive(archive)
        assert model.source("BTC-EUR", q.start) == "default"
        assert model.spread("BTC-EUR", q.start) == DEFAULT_SPREAD["BTC-EUR"]
        assert build_quarter_spreads(archive, now=1) == {}
        assert archive.db.execute("SELECT COUNT(*) FROM spreads").fetchone()[0] == 0
        # All planned hours but one: still partial.
        for hour in planned[1:-1]:
            insert_sample(archive, "BTC-EUR", hour, 0.0003)
        assert build_quarter_spreads(archive, now=1) == {}
        insert_sample(archive, "BTC-EUR", planned[-1], None)
        built = build_quarter_spreads(archive, now=1)
        assert list(built) == [("BTC-EUR", "2022Q3")]
        value, hours = built[("BTC-EUR", "2022Q3")]
        assert value == pytest.approx(0.0003) and hours == len(planned) - 1
        assert QuarterSpreads.from_archive(archive).source("BTC-EUR", q.start) == "sampled"


def test_budget_stop_leaves_no_quarter_value(tmp_path: Path) -> None:
    """A run cut by ``max_requests`` inside a quarter, then a finished run elsewhere: the cut
    quarter keeps the default until its own hours are all stored."""
    q3 = Quarter.parse("2022Q3")
    hours = sample_hours("BTC-EUR", q3.start, q3.end)
    trades = [t for h in hours for t in hour_with_spread(h, 0.0004)]
    fake = FakeTradesApi({"BTC-EUR": trades})
    api = PublicApi(fake, clock=Clock(SEAL_EPOCH), sleep=lambda _s: None, min_interval=0)
    with Archive(tmp_path / "candles.db") as archive:
        cut = sample_spreads(
            archive, api, start=q3.start, end=q3.end, markets=["BTC-EUR"], max_requests=3
        )
        assert cut.stopped == "budget" and cut.hours_sampled == 3
        other = sample_spreads(archive, api, start=Q2.start, end=Q2.end, markets=["BTC-EUR"])
        assert other.stopped == ""
        built = build_quarter_spreads(archive, now=1)
        # 2022Q3 is cut; 2021Q2 is whole but had no trades, so no pairs either.
        assert built == {}
        assert QuarterSpreads.from_archive(archive).source("BTC-EUR", q3.start) == "default"
        sample_spreads(archive, api, start=q3.start, end=q3.end, markets=["BTC-EUR"])
        built = build_quarter_spreads(archive, now=1)
        value, count = built[("BTC-EUR", "2022Q3")]
        assert value == pytest.approx(0.0004) and count == len(hours)


def test_more_hours_per_week_cover_the_default_plan(tmp_path: Path) -> None:
    q = Quarter.parse("2022Q3")
    with Archive(tmp_path / "candles.db") as archive:
        for hour in sample_hours("ETH-EUR", q.start, q.end, per_week=3):
            insert_sample(archive, "ETH-EUR", hour, 0.0005)
        assert complete_quarters(archive) == {("ETH-EUR", "2022Q3")}
        assert complete_quarters(archive, per_week=4) == set()


def test_hours_without_enough_pairs_fall_back_to_default(tmp_path: Path) -> None:
    start, end = Q2.start, Q2.end
    hours = sample_hours("BTC-EUR", start, end)
    trades = [t for h in hours for t in hour_with_spread(h, 0.0004, pairs=2)]
    api = PublicApi(
        FakeTradesApi({"BTC-EUR": trades}),
        clock=Clock(Q2.end),
        sleep=lambda _s: None,
        min_interval=0,
    )
    with Archive(tmp_path / "candles.db") as archive:
        sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        pairs = [r[0] for r in archive.db.execute("SELECT pairs FROM spread_samples")]
        assert pairs == [2] * len(hours)
        assert complete_quarters(archive) == {("BTC-EUR", "2021Q2")}  # whole, but no median
        assert build_quarter_spreads(archive) == {}
        model = QuarterSpreads.from_archive(archive)
        assert model.source("BTC-EUR", start) == "default"


def test_sampler_stops_at_429_and_resumes(tmp_path: Path) -> None:
    start = (Q2.start // WEEK + 1) * WEEK
    end = start + 2 * WEEK
    fake = FakeTradesApi({})
    fake.fail = lambda n: HTTPFailure(429, None) if n == 2 else None
    clock = Clock(Q2.end)
    api = PublicApi(fake, clock=clock, sleep=lambda _s: None, min_interval=0)
    with Archive(tmp_path / "candles.db") as archive:
        stopped = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert (stopped.stopped, stopped.retry_at, stopped.hours_sampled) == (
            "rate_limited",
            Q2.end + 900,
            1,
        )
        assert archive.retry_at() == Q2.end + 900
        clock.now = Q2.end + 899
        waiting = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert (waiting.stopped, waiting.requests) == ("rate_limited", 0)
        clock.now = Q2.end + 900
        done = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert (done.stopped, done.hours_skipped, done.hours_sampled) == ("", 1, 3)


# ---------------------------------------------------------------- sealed period (ADR-0026)


def test_sampler_never_requests_sealed_hours(tmp_path: Path) -> None:
    start, end = Quarter.parse("2025Q3").start, SEAL_EPOCH + 5 * WEEK
    before = sample_hours("BTC-EUR", start, SEAL_EPOCH)
    assert sample_hours("BTC-EUR", SEAL_EPOCH, end)  # there would be hours to sample
    trades = [t for h in before for t in hour_with_spread(h, 0.0004)]
    fake = FakeTradesApi({"BTC-EUR": trades})
    api = PublicApi(fake, clock=Clock(end + WEEK), sleep=lambda _s: None, min_interval=0)
    with Archive(tmp_path / "candles.db") as archive:
        result = sample_spreads(archive, api, start=start, end=end, markets=["BTC-EUR"])
        assert result.clamped and result.hours_sampled == len(before) == len(fake.urls)
        starts = [int(parse_qs(urlsplit(u).query)["start"][0]) // 1000 for u in fake.urls]
        assert starts == before and max(starts) + 3600 <= SEAL_EPOCH
        built = build_quarter_spreads(archive, now=1)
        assert list(built) == [("BTC-EUR", "2025Q3")]
        inside = sample_spreads(archive, api, start=start, end=SEAL_EPOCH, markets=["BTC-EUR"])
        assert not inside.clamped and inside.requests == 0


def test_sealed_quarters_never_computed_or_used(tmp_path: Path) -> None:
    q3, q4 = Quarter.parse("2025Q3"), Quarter.parse("2025Q4")
    planned = sample_hours("BTC-EUR", q3.start, q3.end)
    with Archive(tmp_path / "candles.db") as archive:
        # Rows as older code might have left them: a whole 2025Q3, and samples and a value
        # inside the seal - even a "whole" sealed quarter.
        for hour in planned:
            insert_sample(archive, "BTC-EUR", hour, 0.0004)
        for hour in sample_hours("BTC-EUR", q4.start, q4.end):
            insert_sample(archive, "BTC-EUR", hour, 0.0009)
        insert_spread_row(archive, "BTC-EUR", "2025Q4", 0.0009)
        assert complete_quarters(archive) == {("BTC-EUR", "2025Q3")}
        assert QuarterSpreads.from_archive(archive).source("BTC-EUR", SEAL_EPOCH) == "default"
        built = build_quarter_spreads(archive, now=1)
        assert list(built) == [("BTC-EUR", "2025Q3")]
        value, hours = built[("BTC-EUR", "2025Q3")]
        assert value == pytest.approx(0.0004) and hours == len(planned)
        quarters = [r[0] for r in archive.db.execute("SELECT quarter FROM spreads")]
        assert quarters == ["2025Q3"]  # the stale sealed row is gone
        insert_spread_row(archive, "BTC-EUR", "2025Q4", 0.0009)
        model = QuarterSpreads.from_archive(archive)
        assert model.source("BTC-EUR", SEAL_EPOCH) == "default"
        assert model.spread("BTC-EUR", SEAL_EPOCH - 1) == pytest.approx(0.0004)
