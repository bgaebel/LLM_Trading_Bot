"""Shared data of the strategy lab (Spec 016, ADR-0026): what a run produces and what metrics,
register and report read.

Plain dataclasses that survive JSON: backtest workers return them across processes and the
register keeps them. Times are epoch seconds (UTC), days are ``YYYY-MM-DD`` (UTC), money is EUR.
"""

import calendar
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.strategy.payload import ARENA_SLIPPAGE, ARENA_TAKER_FEE

SEAL_EPOCH = 1_759_276_800
"""2025-10-01T00:00:00Z (ADR-0026): data from here on is the sealed test period."""
FIRST_GRADED = "2021Q2"
"""BTC-EUR 15m candles exist only from 2021-02-20 (Spec 016 AK4)."""
MARKETS = ("BTC-EUR", "ETH-EUR")
CLASSIC = ("trend", "breakout", "meanrev", "ensemble")
DEPOTS = (*CLASSIC, BUYHOLD)
LEAD_ROWS = 500
"""Rows per market and interval that must lie before a quarter's first round (AK4)."""
ELIGIBLE_SHARE = 0.90
"""A quarter is graded if each market got at least this share of its due decisions (AK4)."""
STRATEGY_VERSION = "v0.3.1"

_QUARTER = re.compile(r"(\d{4})Q([1-4])")


@dataclass(frozen=True)
class Quarter:
    """One backtest section ``[start, end)``, e.g. 2021Q2 = 2021-04-01 .. 2021-07-01."""

    label: str
    start: int
    end: int

    @classmethod
    def parse(cls, label: str) -> "Quarter":
        match = _QUARTER.fullmatch(label)
        if not match:
            raise ValueError(f"quarter must look like 2021Q2: {label!r}")
        year, q = int(match[1]), int(match[2])
        month = 3 * (q - 1) + 1
        start = calendar.timegm((year, month, 1, 0, 0, 0))
        end_year, end_month = (year + 1, 1) if q == 4 else (year, month + 3)
        return cls(label, start, calendar.timegm((end_year, end_month, 1, 0, 0, 0)))

    @classmethod
    def span(cls, first: str, last: str) -> list["Quarter"]:
        """Every quarter from ``first`` to ``last``, both included."""
        out, q = [], cls.parse(first)
        end = cls.parse(last)
        while q.start <= end.start:
            out.append(q)
            year, n = int(q.label[:4]), int(q.label[-1])
            q = cls.parse(f"{year + 1}Q1" if n == 4 else f"{year}Q{n + 1}")
        return out

    @property
    def sealed(self) -> bool:
        """True if any part lies in the sealed period (ADR-0026)."""
        return self.end > SEAL_EPOCH


@dataclass(frozen=True)
class Variant:
    """What a run changes against the live defaults. Counted as its own trial (AK8)."""

    name: str = "base"
    fee_rate: float = ARENA_TAKER_FEE
    slippage: float = ARENA_SLIPPAGE
    gap_window: int | None = None
    """Diagnostic only (AK9): another gap tolerance in higher intervals; None = live code."""
    diagnostic: bool = False
    """Never a strategy version; reported only as a diagnosis (AK9)."""

    @property
    def roundtrip(self) -> float:
        """Model cost of a buy plus a sell without spread, as a fraction."""
        return 2 * (self.fee_rate + self.slippage)


@dataclass(frozen=True)
class Trade:
    ts: int
    agent: str
    market: str
    side: str
    """BUY or SELL."""
    qty: float
    fill: float
    notional: float
    fee: float
    context: dict[str, Any] = field(default_factory=dict)
    """Past-only facts at the decision for the market-phase cells (AK7), e.g. ``regime``
    and ``vol`` (filled by the backtest; never values from after ``ts``)."""


@dataclass(frozen=True)
class DayPoint:
    """A depot at the end of a UTC day (its last equity point of that day, valued at mid)."""

    day: str
    value: float
    cash: float
    weights: dict[str, float]
    """Market -> share of the value held in it at that point."""


@dataclass
class DepotResult:
    agent: str
    days: list[DayPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    halted_at: int | None = None
    """Drawdown stop in this quarter (ADR-0021); the depot holds cash afterwards (AK4)."""
    end_mid: float = 0.0
    """Value at the quarter's end at mid prices."""
    end_liquidated: float = 0.0
    """Value at the quarter's end if everything were sold at bid with fee and slippage."""
    actions: dict[str, int] = field(default_factory=dict)
    """Decisions by action (BUY/SELL/HOLD) and status, e.g. ``BUY/OK``."""


@dataclass
class MarketQuality:
    market: str
    due: int = 0
    """Closed bars a classic depot of this market had to decide (summed over depots)."""
    decided: int = 0
    no_decision: dict[str, int] = field(default_factory=dict)
    """Rounds without a decision for this market, by error code (gap_*, stale, ...)."""
    blocked_rounds: int = 0
    """Rounds blocked only by gaps in a higher interval (15m/1h/4h)."""

    @property
    def share(self) -> float:
        return self.decided / self.due if self.due else 0.0


@dataclass
class QuarterResult:
    quarter: Quarter
    variant: Variant
    status: str = "ok"
    """ok, no_lead (fewer than LEAD_ROWS rows before the start), incomplete (the archive's
    windows over the quarter are not all fetched yet, so no run: missing rows would look like
    exchange gaps), sealed, aborted. Only ok can be graded."""
    error: str = ""
    depots: dict[str, DepotResult] = field(default_factory=dict)
    quality: dict[str, MarketQuality] = field(default_factory=dict)
    daily_mid: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    """Market -> ``[(day_before_quarter, mid at the first round), (day_1, mid at day 1's last
    round), ...]`` for the buy & hold benchmark (AK7), one entry per UTC day in increasing
    order. The first entry is only the base of day 1, labelled with the day before the quarter
    (e.g. ``2021-03-31`` for 2021Q2), never a benchmark day itself; without it day 1 has no
    return and ``metrics`` flags it as a data issue."""
    spread: dict[str, float] = field(default_factory=dict)
    """Assumed full spread per market as a fraction of mid (AK6)."""
    rounds: int = 0
    seconds: float = 0.0
    dataset_sha256: str = ""
    """Checksum of the archive rows the run read (AK8)."""

    @property
    def eligible(self) -> dict[str, bool]:
        return {m: q.due > 0 and q.share >= ELIGIBLE_SHARE for m, q in self.quality.items()}

    @property
    def graded(self) -> bool:
        return (
            self.status == "ok"
            and bool(self.quality)
            and all(self.eligible.values())
            and self.quarter.start >= Quarter.parse(FIRST_GRADED).start
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "QuarterResult":
        data = json.loads(text)
        depots = {
            name: DepotResult(
                agent=d["agent"],
                days=[DayPoint(**p) for p in d["days"]],
                trades=[Trade(**t) for t in d["trades"]],
                halted_at=d["halted_at"],
                end_mid=d["end_mid"],
                end_liquidated=d["end_liquidated"],
                actions=d["actions"],
            )
            for name, d in data["depots"].items()
        }
        return cls(
            quarter=Quarter(**data["quarter"]),
            variant=Variant(**data["variant"]),
            status=data["status"],
            error=data["error"],
            depots=depots,
            quality={m: MarketQuality(**q) for m, q in data["quality"].items()},
            daily_mid={m: [(d, v) for d, v in rows] for m, rows in data["daily_mid"].items()},
            spread=data["spread"],
            rounds=data["rounds"],
            seconds=data["seconds"],
            dataset_sha256=data["dataset_sha256"],
        )


FIDELITY_MIN = 300
"""Comparisons per market below which fidelity counts as not established (AK5)."""


@dataclass
class FidelitySummary:
    """What the fidelity check against live found (AK5); the report shows it as it is."""

    compared: dict[str, int] = field(default_factory=dict)
    """Market -> live decisions rebuilt and compared."""
    equal_action: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons with equal action and target."""
    indicators_ok: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons whose indicators match within one rounding step."""
    deviations: list[dict[str, Any]] = field(default_factory=list)
    """Each with market, utc, agent, field, live, rebuilt and cause."""
    with_observations: bool = False
    note: str = ""

    @property
    def established(self) -> dict[str, bool]:
        """Never "100 %" below FIDELITY_MIN. Every comparison needs equal action and target and
        indicators within one rounding step (AK5); an unexplained deviation also fails."""
        unexplained = {d["market"] for d in self.deviations if not d.get("cause")}
        return {
            m: n >= FIDELITY_MIN
            and self.equal_action.get(m, 0) == n
            and self.indicators_ok.get(m, 0) == n
            and m not in unexplained
            for m, n in self.compared.items()
        }

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "FidelitySummary":
        return cls(**json.loads(text))
