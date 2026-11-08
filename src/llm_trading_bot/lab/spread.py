"""Spread model of the strategy lab (Spec 016 AK6): the assumed bid/ask spread per market and
quarter, because candles carry no bid/ask and Bitvavo keeps no order-book history.

Before the bot's own recording (Spec 015) the spread is estimated from public trades:
- **Sample:** a few hours per week (default 2), chosen by a seeded ``random.Random`` per market
  and week, so the same hours come out on every platform and a stopped run resumes where it was.
  One ``/{market}/trades`` request per hour (weight 5, at most 1000 trades).
- **Hour estimate:** adjacent trades (by time) with opposite taker sides at most 60 s apart
  roughly span the spread: a taker buy lifts the ask, a taker sell hits the bid. Each such pair
  gives ``|p_a - p_b| / mid``; the hour's value is the median of at least ``MIN_PAIRS`` pairs.
  Price moves between the two trades inflate a pair, so this leans high - acceptable for a cost.
- **Quarter value:** the upper quartile of the hour medians (``statistics.quantiles``, inclusive
  method), at least ``FLOOR``. Not the median: decisions also fall into thin hours, and an
  understated cost flatters every strategy. Not the maximum: a single hour with a fast market
  would dominate. The floor of 1 bp is several ticks of today's book (about 0.15 bp for BTC),
  because an estimate of zero would make fills free of spread.
- **Without samples:** ``DEFAULT_SPREAD``, deliberately wide (see there).

``half_spread`` is what the historical client adds to and subtracts from the mid (AK2).

**Sealed period** (ADR-0026, ``SEAL_EPOCH`` = 2025-10-01 = start of 2025Q4): the sampler never
requests an hour from there on and ``build_quarter_spreads`` never computes or stores a quarter
from 2025Q4 on, whatever range a caller passes. A spread for those quarters would be a number
from the test period; after the seal the recorded median of Spec 015 takes over anyway.

The quarter value uses samples from the whole quarter, also from after a given decision: an
assumption of the cost model (the report lists it), not a signal any strategy sees.

CLI ``trading-bot lab spread`` (German output): see ``cli_main``.
"""

import itertools
import math
import random
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TextIO

from llm_trading_bot.lab.archive import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_RATE_LIMITED,
    EXIT_USAGE,
    Archive,
    ArchiveError,
    LabArgumentParser,
    PublicApi,
    RateLimited,
    add_dir_option,
    archive_path,
    format_utc,
    german_retry_message,
    parse_cli,
    parse_utc,
    sealed_note,
)
from llm_trading_bot.lab.model import MARKETS, SEAL_EPOCH, Quarter
from llm_trading_bot.market.http import HTTPFailure, TransportError

METHOD = "trades-adjacent-opposite-60s-q75-v1"
"""Stored with every value; another estimator gets another name, never the same rows."""
PAIR_SECONDS = 60
MIN_PAIRS = 3
"""Fewer pairs in an hour say too little; such an hour gives no estimate."""
FLOOR = 0.0001
"""1 bp of the mid, the smallest full spread the model ever assumes."""
HOURS_PER_WEEK = 2
WEEK = 7 * 86_400
DEFAULT_SPREAD: dict[str, float] = {
    # Full spread as a fraction of mid when a quarter has no sample. Research 2026-11-07: 2020
    # pairs gave about 0.033 % (5 pairs, an indication), today's book is one tick (< 0.002 %).
    # About three times the 2020 indication for BTC and more for the thinner ETH book.
    "BTC-EUR": 0.0010,
    "ETH-EUR": 0.0015,
}
FALLBACK_SPREAD = 0.0020
"""For a market without its own default (not expected; the lab trades BTC-EUR and ETH-EUR)."""


class SpreadModel(Protocol):
    def half_spread(self, market: str, epoch: int) -> float:
        """Half the assumed bid/ask spread at ``epoch`` as a fraction of the mid."""
        ...


def quarter_label(epoch: int) -> str:
    """``2021Q2`` for any moment of April to June 2021 (UTC)."""
    t = time.gmtime(epoch)
    return f"{t.tm_year}Q{(t.tm_mon - 1) // 3 + 1}"


class QuarterSpreads:
    """Full spread per market and quarter label; ``half_spread`` is half of it."""

    def __init__(
        self,
        spreads: Mapping[tuple[str, str], float],
        default: Mapping[str, float] = DEFAULT_SPREAD,
    ) -> None:
        for key, value in spreads.items():
            if not math.isfinite(value) or not 0 <= value < 0.1:
                raise ValueError(f"implausible spread for {key}: {value}")
        self._spreads = dict(spreads)
        self._default = dict(default)

    @classmethod
    def from_archive(cls, archive: Archive, *, method: str = METHOD) -> "QuarterSpreads":
        rows = archive.db.execute(
            "SELECT market, quarter, spread FROM spreads WHERE method=?", (method,)
        )
        # A sealed quarter's row can only come from older code; it is never used.
        return cls(
            {(str(m), str(q)): float(s) for m, q, s in rows if not Quarter.parse(str(q)).sealed}
        )

    def spread(self, market: str, epoch: int) -> float:
        value = self._spreads.get((market, quarter_label(epoch)))
        if value is not None:
            return value
        return self._default.get(market, FALLBACK_SPREAD)

    def half_spread(self, market: str, epoch: int) -> float:
        return self.spread(market, epoch) / 2

    def source(self, market: str, epoch: int) -> str:
        """``sampled`` or ``default`` - for the report's list of assumptions."""
        return "sampled" if (market, quarter_label(epoch)) in self._spreads else "default"


# ---------------------------------------------------------------- estimator


@dataclass(frozen=True)
class PublicTrade:
    ts_ms: int
    price: float
    side: str
    """Taker side: ``buy`` or ``sell``."""
    id: str = ""


def parse_trades(data: object) -> list[PublicTrade]:
    """Trades of one response, ascending by time (then id). Raises ArchiveError."""
    if not isinstance(data, list):
        raise ArchiveError("trades: unexpected response format")
    out = []
    for item in data:
        if not isinstance(item, dict):
            raise ArchiveError("trades: unexpected row format")
        ts, side = item.get("timestamp"), item.get("side")
        if isinstance(ts, bool) or not isinstance(ts, int) or side not in ("buy", "sell"):
            raise ArchiveError("trades: missing timestamp or side")
        try:
            price = float(item["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveError("trades: unreadable price") from exc
        if not math.isfinite(price) or price <= 0:
            raise ArchiveError("trades: invalid price")
        out.append(PublicTrade(ts, price, str(side), str(item.get("id", ""))))
    out.sort(key=lambda t: (t.ts_ms, t.id))
    return out


def pair_spreads(trades: Sequence[PublicTrade]) -> list[float]:
    """``|p_a - p_b| / mid`` for each adjacent pair with opposite sides within 60 s."""
    out = []
    for a, b in itertools.pairwise(trades):
        if a.side != b.side and 0 <= b.ts_ms - a.ts_ms <= PAIR_SECONDS * 1000:
            out.append(abs(a.price - b.price) / ((a.price + b.price) / 2))
    return out


def hour_median(pairs: Sequence[float]) -> float | None:
    return statistics.median(pairs) if len(pairs) >= MIN_PAIRS else None


def conservative_spread(medians: Sequence[float]) -> float:
    """Upper quartile of the hour medians, at least FLOOR."""
    if not medians:
        raise ValueError("no hour medians")
    if len(medians) == 1:
        q = medians[0]
    else:
        q = statistics.quantiles(medians, n=4, method="inclusive")[2]
    return max(FLOOR, q)


def sample_hours(
    market: str, start: int, end: int, *, per_week: int = HOURS_PER_WEEK, now: int | None = None
) -> list[int]:
    """Sample hour starts (epoch s) in ``[start, end)``, ascending, already over by ``now``.

    Weeks are fixed blocks since the epoch, each with its own seed, so any range yields the
    same hours for the same weeks. Only ``randrange`` is used: it is integer-only and gives the
    same values on every platform (LEARNINGS #test).
    """
    hours: list[int] = []
    week = start // WEEK
    while week * WEEK < end:
        rng = random.Random(f"{METHOD}:{market}:{week}")
        picked: list[int] = []
        while len(picked) < min(per_week, 168):
            h = rng.randrange(168)
            if h not in picked:
                picked.append(h)
        for h in sorted(picked):
            epoch = week * WEEK + h * 3600
            if start <= epoch < end and (now is None or epoch + 3600 <= now):
                hours.append(epoch)
        week += 1
    return hours


# ---------------------------------------------------------------- sampling and storage


@dataclass
class SampleResult:
    requests: int = 0
    hours_sampled: int = 0
    hours_skipped: int = 0
    """Already stored by an earlier run."""
    stopped: str = ""
    """``""``, ``rate_limited`` or ``budget``."""
    retry_at: int | None = None
    clamped: bool = False
    """The requested end reached into the sealed period and was cut to ``SEAL_EPOCH``."""


def sample_spreads(
    archive: Archive,
    api: PublicApi,
    *,
    start: int,
    end: int,
    markets: Iterable[str] = MARKETS,
    per_week: int = HOURS_PER_WEEK,
    max_requests: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> SampleResult:
    """Fetch and store the hour estimates that are still missing; resumable like the archive.

    Shares the archive's 429 memory: after a ban nothing is sent before ``retry_at``. ``end`` is
    clamped to ``SEAL_EPOCH`` (module docstring); there is no switch to lift that here.
    """
    if archive.readonly:
        raise ArchiveError("the archive was opened read-only")
    result = SampleResult(clamped=end > SEAL_EPOCH)
    end = min(end, SEAL_EPOCH)
    now = api.now()
    banned = archive.retry_at()
    if banned is not None and banned > now:
        result.stopped, result.retry_at = "rate_limited", banned
        return result
    first = api.requests
    try:
        for market in markets:
            have = {
                int(r[0])
                for r in archive.db.execute(
                    "SELECT hour FROM spread_samples WHERE market=? AND method=?",
                    (market, METHOD),
                )
            }
            for hour in sample_hours(market, start, end, per_week=per_week, now=now):
                if hour in have:
                    result.hours_skipped += 1
                    continue
                if max_requests is not None and api.requests - first >= max_requests:
                    result.stopped = "budget"
                    return result
                data, fetched_at = api.trades(market, hour, hour + 3600)
                trades = [
                    t for t in parse_trades(data) if hour * 1000 <= t.ts_ms < (hour + 3600) * 1000
                ]
                pairs = pair_spreads(trades)
                archive.db.execute(
                    "INSERT INTO spread_samples(market, method, hour, trades, pairs, median, "
                    "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (market, METHOD, hour, len(trades), len(pairs), hour_median(pairs), fetched_at),
                )
                result.hours_sampled += 1
            if progress is not None:
                progress(f"{market}: Spread-Stichproben bis {quarter_label(end - 1)} geprüft")
    except RateLimited as exc:
        archive.set_meta("retry_at", str(exc.retry_at))
        result.stopped, result.retry_at = "rate_limited", exc.retry_at
    finally:
        result.requests = api.requests - first
    return result


def build_quarter_spreads(
    archive: Archive, *, now: int | None = None, method: str = METHOD
) -> dict[tuple[str, str], tuple[float, int]]:
    """Aggregate the stored hour medians into ``spreads`` (one row per market and quarter).

    Returns ``{(market, quarter): (spread, hours with a median)}``. A quarter's row is
    recomputed from all its samples each time: the samples are the record, the value follows.
    Samples from the sealed period (only possible in an archive written by older code) are
    ignored, so no quarter from 2025Q4 on is ever computed or stored.
    """
    by_quarter: dict[tuple[str, str], list[float]] = {}
    for market, hour, median in archive.db.execute(
        "SELECT market, hour, median FROM spread_samples "
        "WHERE method=? AND median IS NOT NULL AND hour + 3600 <= ?",
        (method, SEAL_EPOCH),
    ):
        by_quarter.setdefault((str(market), quarter_label(int(hour))), []).append(float(median))
    stamp = math.floor(time.time()) if now is None else now
    out = {key: (conservative_spread(v), len(v)) for key, v in sorted(by_quarter.items())}
    con = archive.db
    con.execute("BEGIN IMMEDIATE")
    try:
        con.executemany(
            "INSERT INTO spreads(market, quarter, method, spread, samples, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(market, quarter, method) DO UPDATE SET "
            "spread=excluded.spread, samples=excluded.samples, computed_at=excluded.computed_at",
            [(m, q, method, s, n, stamp) for (m, q), (s, n) in out.items()],
        )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return out


# ---------------------------------------------------------------- CLI

SPREAD_START = "2021-01-01"
"""Default start: one quarter before the first graded one (2021Q2)."""


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.4f}".replace(".", ",") + " %"


def quarter_table(
    built: Mapping[tuple[str, str], tuple[float, int]],
    start: int,
    end: int,
    *,
    markets: Sequence[str] = MARKETS,
    default: Mapping[str, float] = DEFAULT_SPREAD,
) -> str:
    """German table of the assumed full spread per quarter in ``[start, end)`` and market;
    quarters without enough samples show the default and say so."""
    if start >= end:
        return "Keine Quartale im Zeitraum."
    quarters = Quarter.span(quarter_label(start), quarter_label(end - 1))
    header = "| Quartal | " + " | ".join(markets) + " |"
    lines = [
        "Angenommener Spread (voller Abstand Bid/Ask in % der Mitte, oberes Quartil der "
        "Stunden-Mediane):",
        header,
        "|" + "---|" * (len(markets) + 1),
    ]
    for q in quarters:
        cells = []
        for market in markets:
            value = built.get((market, q.label))
            if value is None:
                fallback = default.get(market, FALLBACK_SPREAD)
                cells.append(f"Standard {_percent(fallback)}")
            else:
                cells.append(f"{_percent(value[0])} ({value[1]} Std.)")
        lines.append(f"| {q.label} | " + " | ".join(cells) + " |")
    lines.append(
        "„Standard“: zu wenige Stunden mit mindestens "
        f"{MIN_PAIRS} Gegenpaaren; der Wert ist eine vorsichtige Annahme."
    )
    return "\n".join(lines)


def _spread_parser(out: TextIO) -> LabArgumentParser:
    parser = LabArgumentParser(
        out=out,
        prog="trading-bot lab spread",
        description=f"Schätzt den Spread je Quartal aus {HOURS_PER_WEEK} Stunden öffentlicher "
        "Bitvavo-Trades je Woche (fortsetzbar), nur vor dem versiegelten Zeitraum ab 2025-10-01.",
    )
    add_dir_option(parser)
    parser.add_argument(
        "--start",
        type=parse_utc,
        default=parse_utc(SPREAD_START),
        help=f"Beginn, ISO-Datum (Standard: {SPREAD_START})",
    )
    parser.add_argument(
        "--end",
        type=parse_utc,
        default=SEAL_EPOCH,
        help="Ende, ISO-Datum (Standard und Obergrenze: 2025-10-01)",
    )
    parser.add_argument(
        "--max-requests", type=int, default=None, help="höchstens so viele Anfragen in diesem Lauf"
    )
    return parser


def cli_main(
    command: str, argv: Sequence[str], out: TextIO, *, api: PublicApi | None = None
) -> int:
    """``trading-bot lab spread`` (Spec 016 AK6): sample, then print the quarter table.

    ``api`` is injectable for tests. The table is built only once every hour of the range is
    sampled; a run stopped by ``--max-requests`` (exit 0) or a 429 (exit 3) is resumed by
    calling again.
    """
    if command != "spread":
        print(f"Unbekannter Labor-Befehl für das Spread-Modell: {command}", file=out)
        return EXIT_USAGE
    args = parse_cli(_spread_parser(out), argv)
    if isinstance(args, int):
        return args
    if args.max_requests is not None and args.max_requests < 1:
        print("--max-requests muss mindestens 1 sein.", file=out)
        return EXIT_USAGE
    start: int = args.start
    requested: int = args.end
    end = min(requested, SEAL_EPOCH)
    if requested > SEAL_EPOCH:
        print(sealed_note(requested), file=out)
    if start >= end:
        print("Der Beginn muss vor dem Ende (höchstens 2025-10-01) liegen.", file=out)
        return EXIT_USAGE
    api = api if api is not None else PublicApi()
    path = archive_path(args.dir)
    print(f"Kerzenarchiv: {path}", file=out)
    print(
        f"Spread-Stichproben: {HOURS_PER_WEEK} Stunden je Woche, "
        f"{format_utc(start)} bis {format_utc(end)} (Methode {METHOD})",
        file=out,
    )

    def progress(line: str) -> None:
        print(line, file=out, flush=True)

    try:
        with Archive(path) as archive:
            result = sample_spreads(
                archive,
                api,
                start=start,
                end=end,
                max_requests=args.max_requests,
                progress=progress,
            )
            print(
                f"Anfragen: {result.requests}, neue Stunden: {result.hours_sampled}, "
                f"schon vorhanden: {result.hours_skipped}",
                file=out,
            )
            if result.stopped == "rate_limited" and result.retry_at is not None:
                print(german_retry_message(result.retry_at, api.now()), file=out)
                return EXIT_RATE_LIMITED
            if result.stopped == "budget":
                print("Anfragen-Grenze erreicht; ein weiterer Aufruf setzt fort.", file=out)
                return EXIT_OK
            built = build_quarter_spreads(archive, now=api.now())
    except RateLimited as exc:  # sample_spreads handles 429s itself; kept for any other path
        print(german_retry_message(exc.retry_at, api.now()), file=out)
        return EXIT_RATE_LIMITED
    except ArchiveError as exc:
        print(f"Kerzenarchiv-Fehler: {exc}", file=out)
        return EXIT_ERROR
    except (HTTPFailure, TransportError, OSError) as exc:
        print(f"Abruf fehlgeschlagen: {exc}. Ein weiterer Aufruf setzt fort.", file=out)
        return EXIT_ERROR
    print(quarter_table(built, start, end), file=out)
    return EXIT_OK
