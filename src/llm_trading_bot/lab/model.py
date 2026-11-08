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

from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper import usdc
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.strategy.payload import ARENA_SLIPPAGE, ARENA_TAKER_FEE

SEAL_EPOCH = 1_759_276_800
"""2025-10-01T00:00:00Z (ADR-0026): data from here on is the sealed test period."""
FIRST_GRADED = "2021Q2"
"""BTC-EUR 15m candles exist only from 2021-02-20 (Spec 016 AK4)."""
MARKETS = ("BTC-EUR", "ETH-EUR")
CLASSIC = ("trend", "breakout", "meanrev", "ensemble")
DEPOTS = (*CLASSIC, BUYHOLD)
USDC_AGENTS = tuple(f"{name}{usdc.USDC_SUFFIX}" for name in CLASSIC)
"""Spec 020: the classic rules with BTC executed over BTC-USDC (``llm.agents.USDC_AGENTS``)."""
USDC_DEPOTS = (*USDC_AGENTS, BUYHOLD, usdc.BUYHOLD_USDC)
"""Depots of the ``usdc`` variant (Spec 016 AK14): the live cycle runs buy & hold whenever it
runs, so it is there too - on the same quotes as in ``base``, hence the same numbers."""
LEAD_ROWS = 500
"""Rows per market and interval that must lie before a quarter's first round (AK4)."""
ELIGIBLE_SHARE = 0.90
"""A quarter is graded if each market got at least this share of its due decisions (AK4)."""
STRATEGY_VERSION = "v0.3.1"

# Spec 016 AK14 (from Spec 020 AK8): BTC-USDC exists only from 2024-07-01, so the usdc variant
# synthesises both route quotes from the BTC-EUR quote mid (research 2026-11-08, 5 order-book
# samples). Assumptions, shown in the report:
USDC_EUR_RATE = 1.0
"""USDC-EUR mid. The rate cancels out in euro (BTC-USDC mid = BTC-EUR mid / rate, valued at
BTC-USDC mid x USDC-EUR mid); only the spreads below cost money."""
USDC_EUR_SPREAD = 0.00011
"""Full USDC-EUR spread as a fraction of mid: measured 1.11 bps."""
BTC_USDC_SPREAD = 0.0003
"""Full BTC-USDC spread: measured 1.45-4.22 bps (median 1.98); conservatively 3 bps."""

ROUTE_SPREAD = Symbol.BTCUSDC.market
"""Key of ``QuarterResult.spread`` for the full spread of the USDC route (both books)."""
_SYMBOLS = {s.market: s for s in Symbol}

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
    usdc: bool = False
    """AK14: the USDC depots (``USDC_DEPOTS``) instead of v0.3.1, BTC over the USDC route with
    the costs of ``paper.usdc``; ``fee_rate``/``slippage`` stay the costs of ETH-EUR. Added after
    the first runs: ``register.variant_identity`` leaves it out while it is False, for the config
    hash and the outcome hash alike, so the hashes of earlier runs stay valid."""

    @property
    def roundtrip(self) -> float:
        """Model cost of a buy plus a sell without spread, as a fraction (on EUR markets)."""
        return 2 * (self.fee_rate + self.slippage)

    @property
    def depots(self) -> tuple[str, ...]:
        """The depots a run of this variant has (``DEPOTS`` or ``USDC_DEPOTS``)."""
        return USDC_DEPOTS if self.usdc else DEPOTS

    @property
    def strategies(self) -> tuple[str, ...]:
        """The depots that decide on candles (without the benchmarks)."""
        return USDC_AGENTS if self.usdc else CLASSIC

    @property
    def strategy_version(self) -> str:
        return usdc.USDC_VERSION if self.usdc else STRATEGY_VERSION

    def side_cost(self, agent: str, market: str) -> float:
        """Fee plus slippage of one side of a trade of ``agent`` in ``market`` (without spread):
        the route's for the BTC of a USDC depot, the variant's otherwise."""
        if routed(agent, market):
            return usdc.ROUTE_FEE + usdc.ROUTE_SLIPPAGE
        return self.fee_rate + self.slippage

    def side_slippage(self, agent: str, market: str) -> float:
        """Slippage of one side; over the route that of both legs."""
        return usdc.ROUTE_SLIPPAGE if routed(agent, market) else self.slippage


def routed(agent: str, market: str) -> bool:
    """True if ``agent`` trades ``market`` over the USDC route: the live rule
    (``paper.usdc.routed``) - only the BTC of a ``*-usdc`` depot. Per depot, never per variant:
    plain buy & hold runs along in the usdc variant and buys BTC on BTC-EUR, as live."""
    return market in _SYMBOLS and usdc.routed(agent, _SYMBOLS[market])


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
    """A depot on one UTC day: ``value`` and ``cash`` at its last equity point of that day
    (valued at mid), ``weights`` over the whole day."""

    day: str
    value: float
    cash: float
    weights: dict[str, float]
    """Market -> time-weighted mean share of the value held in it over the day's valued rounds:
    the sum of each round's ``qty * mid / value`` divided by the depot's valued rounds of that
    day. Not the share at the last round - a strategy that holds for hours and is flat at
    midnight was invested that day, and the benchmark of the same quote (AK7) must see it."""


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
    rounds: int = 0
    """Rounds in which the market's snapshot outcome was reported (ok or a failure code): the
    denominator of the ``no_decision`` shares, which count rounds, never bars. 0 in results
    recorded before this field existed; ``metrics.quality`` then uses ``QuarterResult.rounds``."""

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
    """Assumed full spread per market as a fraction of mid (AK6): the spread a trade on that
    market's book pays. The usdc variant adds ``ROUTE_SPREAD`` (BTC-USDC plus USDC-EUR), which
    the BTC trades of its USDC depots pay instead (``trade_spread``); plain buy & hold still
    pays BTC-EUR's."""
    rounds: int = 0
    seconds: float = 0.0
    dataset_sha256: str = ""
    """Checksum of the archive rows the run read (AK8)."""

    def trade_spread(self, agent: str, market: str) -> float:
        """The full spread a trade of ``agent`` in ``market`` paid. A usdc run recorded before
        ``ROUTE_SPREAD`` existed (one day on the feature branch) held the route's spread under
        BTC-EUR itself; its USDC depots still read it from there."""
        if routed(agent, market):
            return self.spread.get(ROUTE_SPREAD, self.spread.get(market, 0.0))
        return self.spread.get(market, 0.0)

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
"""Per market: comparisons, and exactly equal snapshots with proven equal input, below which
fidelity counts as not established (AK5)."""


@dataclass
class FidelitySummary:
    """What the fidelity check against live found (AK5); the report shows it as it is.

    Per market a comparison is one rebuilt live decision. Those with a recorded round fall
    into ``exact`` (same input, nothing strict deviates), ``different`` (the recording proves
    another input) or neither (same input, but a deviation: never explained). Those without a
    recorded round are ``unrecorded``, or ``different`` if a candle field already differs.
    """

    compared: dict[str, int] = field(default_factory=dict)
    """Market -> live decisions rebuilt and compared."""
    equal_action: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons with equal action and target."""
    indicators_ok: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons whose indicators match within one rounding step (information
    only since the AK5 change of 2026-11-08: where the input differs they may differ)."""
    deviations: list[dict[str, Any]] = field(default_factory=list)
    """Each with market, utc, agent, field, live, rebuilt and cause."""
    with_observations: bool = False
    note: str = ""
    exact: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons with a recorded round whose input the recording proves equal
    (directly or over the rows live got) and without any strict deviation. Missing in a JSON
    written before (AK5 change 2026-11-08): {} - never established."""
    truncated: dict[str, int] = field(default_factory=dict)
    """Market -> of ``exact``, comparisons rebuilt from the shorter window live got."""
    different: dict[str, dict[str, int]] = field(default_factory=dict)
    """Market -> cause -> comparisons whose input is proven different."""
    unrecorded: dict[str, int] = field(default_factory=dict)
    """Market -> comparisons without a recorded round and without a differing candle field:
    their indicators cannot be checked (``ohne Aufzeichnung nicht prüfbar``)."""

    def unexplained(self, market: str) -> int:
        return sum(1 for d in self.deviations if d.get("market") == market and not d.get("cause"))

    def verdict(self, market: str) -> tuple[bool, str]:
        """(established, reason). AK5: every comparison with equal action and target, no
        deviation without a cause, and at least ``FIDELITY_MIN`` comparisons and exactly equal
        snapshots with proven equal input - never "100 %" below that. The one rule for the
        console, the JSON and the report."""
        n = self.compared.get(market, 0)
        equal = self.equal_action.get(market, 0)
        exact = self.exact.get(market, 0)
        unexplained = self.unexplained(market)
        reasons = []
        if n < FIDELITY_MIN:
            reasons.append(f"zu wenige Vergleiche: {n} < {FIDELITY_MIN}")
        if equal != n:
            reasons.append(f"Aktion oder Ziel abweichend in {n - equal} von {n} Vergleichen")
        if unexplained:
            reasons.append(f"{unexplained} Abweichungen ohne Ursache")
        if exact < FIDELITY_MIN:
            reasons.append(f"zu wenige exakt gleiche Snapshots: {exact} < {FIDELITY_MIN}")
        return not reasons, "; ".join(reasons)

    @property
    def established(self) -> dict[str, bool]:
        return {m: self.verdict(m)[0] for m in self.compared}

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "FidelitySummary":
        return cls(**json.loads(text))
