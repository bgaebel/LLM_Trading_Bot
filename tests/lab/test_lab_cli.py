"""Spec 016 AK1/AK6: ``trading-bot lab fetch|check|spread`` with fake transports, no network."""

import calendar
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from llm_trading_bot.lab import archive as archive_mod
from llm_trading_bot.lab import spread as spread_mod
from llm_trading_bot.lab.archive import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_RATE_LIMITED,
    EXIT_USAGE,
    Archive,
    PublicApi,
    parse_utc,
    window_start,
)
from llm_trading_bot.lab.model import SEAL_EPOCH, Quarter
from llm_trading_bot.lab.paths import LAB_DIR_ENV
from llm_trading_bot.lab.spread import HOURS_PER_WEEK, METHOD, WEEK, sample_hours
from llm_trading_bot.market.bitvavo import API
from llm_trading_bot.market.http import HTTPFailure

D0 = 19_676 * 86_400
"""2023-11-15T00:00:00Z."""
NAMES = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}


def utc(*parts: int) -> int:
    return calendar.timegm((*parts, 0, 0, 0)[:6])


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeBitvavo:
    """Answers the public candles and market-trades endpoints like Bitvavo (start inclusive,
    end exclusive, newest first). Candles: one settled bar at each window's start; trades: four
    alternating taker trades with a full spread of 4 bp in every hour asked for."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.urls: list[str] = []
        self.fail: Callable[[int], Exception | None] = lambda _n: None
        self.candle_rows: Callable[[str, int, int, int], list[list[Any]]] = (
            lambda _m, _i, start, _end: [[start, "100", "101", "99", "100.5", "1.5"]]
        )

    def get_json_and_headers(self, url: str) -> tuple[object, dict[str, str]]:
        self.urls.append(url)
        assert url.startswith(API + "/")
        error = self.fail(len(self.urls))
        if error is not None:
            raise error
        parts = urlsplit(url)
        market, kind = parts.path.split("/")[-2:]
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        start, end = int(q["start"]), int(q["end"])
        headers = {"bitvavo-ratelimit-remaining": "900"}
        if kind == "candles":
            rows = self.candle_rows(market, NAMES[q["interval"]], start, end)
            return sorted(rows, key=lambda r: -int(r[0])), headers
        assert kind == "trades"
        trades = []
        for k in range(4):
            side = "buy" if k % 2 == 0 else "sell"
            price = 100.02 if side == "buy" else 99.98
            trades.append(
                {"id": f"{start}-{k}", "timestamp": start + 10_000 * k, "price": str(price)}
                | {"side": side, "amount": "0.01"}
            )
        return list(reversed(trades)), headers


def make_api(now: float) -> tuple[FakeBitvavo, PublicApi]:
    clock = Clock(now)
    fake = FakeBitvavo(clock)
    return fake, PublicApi(fake, clock=clock, sleep=lambda _s: None, min_interval=0)


def run(
    module: Any, command: str, argv: list[str], api: PublicApi | None = None
) -> tuple[int, str]:
    out = io.StringIO()
    if api is None:
        code = module.cli_main(command, argv, out)
    else:
        code = module.cli_main(command, argv, out, api=api)
    assert isinstance(code, int)
    return code, out.getvalue()


def starts(fake: FakeBitvavo) -> list[int]:
    return [int(parse_qs(urlsplit(u).query)["start"][0]) // 1000 for u in fake.urls]


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default lab folder (``--dir`` omitted) points into tmp_path."""
    folder = tmp_path / "lab"
    monkeypatch.setenv(LAB_DIR_ENV, str(folder))
    return folder


# ---------------------------------------------------------------- fetch


def test_fetch_fills_the_archive_in_the_lab_dir(lab: Path) -> None:
    fake, api = make_api(D0 + 3 * 86_400)
    code, text = run(archive_mod, "fetch", ["--start", "2023-11-15", "--end", "2023-11-16"], api)
    assert code == EXIT_OK, text
    # Two markets x four intervals, one window each.
    assert len(fake.urls) == 8
    assert f"Kerzenarchiv: {lab / 'candles.db'}" in text
    assert "Zeitraum: 2023-11-15 bis 2023-11-16" in text
    assert "Anfragen: 8" in text and "BTC-EUR 1m: bis 2023-11-16 geprüft" in text
    with Archive.open_readonly(lab / "candles.db") as archive:
        assert archive.rows("ETH-EUR", 1, D0, D0 + 86_400) == [
            [D0 * 1000, "100", "101", "99", "100.5", "1.5"]
        ]
    # The 1m days are done and skipped; the 15-, 60- and 240-day windows still reach into the
    # (fake) present and are asked again.
    again, text = run(archive_mod, "fetch", ["--start", "2023-11-15", "--end", "2023-11-16"], api)
    assert again == EXIT_OK and len(fake.urls) == 14 and "Anfragen: 6" in text
    assert "0 neu, 2 schon vorhanden, 6 noch offen" in text


def test_fetch_defaults_start_2020_07_01_and_end_now(tmp_path: Path) -> None:
    now = utc(2020, 7, 3, 12)
    fake, api = make_api(now)
    code, text = run(archive_mod, "fetch", ["--dir", str(tmp_path), "--max-requests", "1"], api)
    assert code == EXIT_OK and "Anfragen-Grenze erreicht" in text
    assert "Zeitraum: 2020-07-01 bis 2020-07-03 12:00 UTC" in text
    assert starts(fake) == [utc(2020, 7, 1)]  # BTC-EUR 1m comes first
    assert (tmp_path / "candles.db").is_file()


def test_fetch_stops_at_429_with_german_retry_message(tmp_path: Path) -> None:
    fake, api = make_api(D0 + 3 * 86_400)
    fake.fail = lambda n: HTTPFailure(429, None) if n == 2 else None
    argv = ["--dir", str(tmp_path), "--start", "2023-11-15", "--end", "2023-11-16"]
    code, text = run(archive_mod, "fetch", argv, api)
    assert code == EXIT_RATE_LIMITED
    assert "Bitvavo-Rate-Limit (HTTP 429)" in text and "Weiter frühestens ab" in text
    # During the ban nothing is sent, and the answer is the same.
    code, text = run(archive_mod, "fetch", argv, api)
    assert code == EXIT_RATE_LIMITED and "Weiter frühestens ab" in text and len(fake.urls) == 2


def test_fetch_beyond_the_seal_is_allowed_and_says_so(tmp_path: Path) -> None:
    fake, api = make_api(SEAL_EPOCH + 10 * 86_400)
    argv = ["--dir", str(tmp_path), "--start", "2025-10-02", "--end", "2025-10-03"]
    code, text = run(archive_mod, "fetch", argv, api)
    assert code == EXIT_OK and "Treue-Test" in text and "versiegelt" in text
    assert len(fake.urls) == 8


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--start", "gestern"], "kein ISO-Datum"),
        (["--start", "2023-11-16", "--end", "2023-11-15"], "Beginn muss vor dem Ende"),
        (["--max-requests", "0"], "mindestens 1"),
        (["--unknown"], "unrecognized arguments"),
    ],
)
def test_fetch_usage_errors(tmp_path: Path, argv: list[str], expected: str) -> None:
    fake, api = make_api(D0)
    code, text = run(archive_mod, "fetch", ["--dir", str(tmp_path), *argv], api)
    assert code == EXIT_USAGE and expected in text
    assert fake.urls == [] and not (tmp_path / "candles.db").exists()


def test_help_returns_zero_and_writes_to_out() -> None:
    for module, command in ((archive_mod, "fetch"), (archive_mod, "check"), (spread_mod, "spread")):
        code, text = run(module, command, ["--help"])
        assert code == EXIT_OK and f"trading-bot lab {command}" in text and "--dir" in text


def test_fetch_errors_are_reported_not_raised(tmp_path: Path) -> None:
    argv = ["--dir", str(tmp_path), "--start", "2023-11-15", "--end", "2023-11-16"]
    fake, api = make_api(D0 + 3 * 86_400)
    fake.fail = lambda _n: HTTPFailure(400)
    code, text = run(archive_mod, "fetch", argv, api)
    assert code == EXIT_ERROR and "Abruf fehlgeschlagen: HTTP 400" in text
    fake, api = make_api(D0 + 3 * 86_400)
    fake.candle_rows = lambda _m, _i, start, _e: [[start, "98", "101", "99", "100", "1"]]
    code, text = run(archive_mod, "fetch", argv, api)
    assert code == EXIT_ERROR and "Kerzenarchiv-Fehler" in text


def test_unknown_command_is_a_usage_error() -> None:
    assert run(archive_mod, "spread", [])[0] == EXIT_USAGE
    assert run(spread_mod, "fetch", [])[0] == EXIT_USAGE


def test_parse_utc_accepts_dates_and_iso_times() -> None:
    assert parse_utc("2025-10-01") == SEAL_EPOCH
    assert parse_utc("2025-10-01T00:00Z") == SEAL_EPOCH
    assert parse_utc("2025-10-01T02:00+02:00") == SEAL_EPOCH
    assert parse_utc("2025-10-01T00:00:30") == SEAL_EPOCH + 30


# ---------------------------------------------------------------- check


def test_check_without_archive_creates_nothing(tmp_path: Path) -> None:
    code, text = run(archive_mod, "check", ["--dir", str(tmp_path)])
    assert code == EXIT_ERROR and "Kerzenarchiv-Fehler" in text
    assert not (tmp_path / "candles.db").exists()


def test_check_reports_only_before_the_seal(tmp_path: Path) -> None:
    later = SEAL_EPOCH + 30 * 86_400
    row = ("100", "101", "99", "100.5", "1.5")
    with Archive(tmp_path / "candles.db") as archive:
        for ts in (SEAL_EPOCH - 900, SEAL_EPOCH):
            archive.store_window("BTC-EUR", 1, window_start(1, ts), [(ts, *row)], fetched_at=later)
        archive.store_window(
            "BTC-EUR",
            15,
            window_start(15, SEAL_EPOCH),
            [(SEAL_EPOCH - 900, *row), (SEAL_EPOCH, *row)],
            fetched_at=later,
        )
    code, text = run(archive_mod, "check", ["--dir", str(tmp_path)])
    assert code == EXIT_OK, text
    assert "Zeitraum: 2020-07-01 bis 2025-10-01" in text
    assert "BTC-EUR 15m: 1 Kerzen verglichen, 1 gleich" in text
    assert "ETH-EUR 15m: keine Überschneidung mit 1m" in text
    # Asking for more is clamped, and said.
    code, sealed = run(archive_mod, "check", ["--dir", str(tmp_path), "--end", "2026-01-01"])
    assert code == EXIT_OK and "versiegelten Zeitraum" in sealed
    assert "BTC-EUR 15m: 1 Kerzen verglichen, 1 gleich" in sealed
    code, text = run(archive_mod, "check", ["--dir", str(tmp_path), "--start", "2025-10-02"])
    assert code == EXIT_USAGE and "höchstens 2025-10-01" in text


# ---------------------------------------------------------------- spread


def test_spread_samples_2021_to_the_seal_and_prints_quarters(lab: Path) -> None:
    fake, api = make_api(utc(2026, 11, 8))
    code, text = run(spread_mod, "spread", [], api)
    assert code == EXIT_OK, text
    first = parse_utc("2021-01-01")
    expected = [h for m in ("BTC-EUR", "ETH-EUR") for h in sample_hours(m, first, SEAL_EPOCH)]
    assert starts(fake) == expected and min(expected) >= first
    assert max(expected) + 3600 <= SEAL_EPOCH
    # Two hours per week block: about 2 * 248 weeks per market.
    assert HOURS_PER_WEEK == 2
    assert len(expected) == pytest.approx(2 * 2 * (SEAL_EPOCH - first) / WEEK, abs=8)
    assert f"{HOURS_PER_WEEK} Stunden je Woche, 2021-01-01 bis 2025-10-01" in text
    labels = [q.label for q in Quarter.span("2021Q1", "2025Q3")]
    for label in labels:
        assert f"| {label} | 0,0400 % (" in text
    assert "2025Q4" not in text and "2020Q4" not in text
    with Archive.open_readonly(lab / "candles.db") as archive:
        quarters = sorted({r[0] for r in archive.db.execute("SELECT quarter FROM spreads")})
        methods = {r[0] for r in archive.db.execute("SELECT method FROM spreads")}
    assert quarters == labels and methods == {METHOD}
    again, text = run(spread_mod, "spread", [], api)
    assert again == EXIT_OK and len(fake.urls) == len(expected) and "neue Stunden: 0" in text


def test_spread_end_clamped_to_seal(tmp_path: Path) -> None:
    fake, api = make_api(utc(2026, 11, 8))
    argv = ["--dir", str(tmp_path), "--start", "2025-09-01", "--end", "2026-03-01"]
    code, text = run(spread_mod, "spread", argv, api)
    assert code == EXIT_OK and "versiegelten Zeitraum" in text
    assert fake.urls and all(s + 3600 <= SEAL_EPOCH for s in starts(fake))
    assert "| 2025Q3 | 0,0400 % (" in text and "2025Q4" not in text


def test_spread_stops_at_429_without_table(tmp_path: Path) -> None:
    fake, api = make_api(utc(2026, 11, 8))
    fake.fail = lambda n: HTTPFailure(429, None) if n == 3 else None
    code, text = run(spread_mod, "spread", ["--dir", str(tmp_path)], api)
    assert code == EXIT_RATE_LIMITED and "Weiter frühestens ab" in text
    assert "| Quartal |" not in text and len(fake.urls) == 3
    code, text = run(spread_mod, "spread", ["--dir", str(tmp_path)], api)
    assert code == EXIT_RATE_LIMITED and len(fake.urls) == 3  # the ban is shared and kept


def test_spread_budget_stops_without_table(tmp_path: Path) -> None:
    _fake, api = make_api(utc(2026, 11, 8))
    code, text = run(spread_mod, "spread", ["--dir", str(tmp_path), "--max-requests", "2"], api)
    assert code == EXIT_OK and "Anfragen-Grenze erreicht" in text and "| Quartal |" not in text
    with Archive.open_readonly(tmp_path / "candles.db") as archive:
        assert archive.db.execute("SELECT COUNT(*) FROM spreads").fetchone()[0] == 0
        assert archive.db.execute("SELECT COUNT(*) FROM spread_samples").fetchone()[0] == 2


def test_quarter_table_marks_defaults() -> None:
    q = Quarter.parse("2021Q2")
    text = spread_mod.quarter_table({("BTC-EUR", "2021Q2"): (0.00065, 26)}, q.start, q.end)
    assert "| 2021Q2 | 0,0650 % (26 Std.) | Standard 0,1500 % |" in text
    assert "„Standard“" in text
    assert spread_mod.quarter_table({}, q.end, q.end) == "Keine Quartale im Zeitraum."
