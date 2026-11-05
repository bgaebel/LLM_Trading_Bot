# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - feature_payload
# Origin: LLM-Trading-Arena@7563724 apps/arena/engine03.py (adapted) - cost_roundtrip
"""Compact, grounded LLM context exactly as the Arena v0.3.1 agents received it (ADR-0007).

The model gets no invented news: only these quotes, completed bars and derived indicators.
"""

from datetime import UTC, datetime
from typing import Any

from llm_trading_bot.market.snapshot import INTERVALS, MarketSnapshot
from llm_trading_bot.strategy.indicators import Indicators, closes
from llm_trading_bot.strategy.signals import run_strategy

PER_ASSET_MAX = 0.45
# Cost defaults in the Arena code (arena.py fee_rate/slip_rate). The bot's own values are set
# with the paper execution (M1 Nr. 3); until then these only feed diagnostics.
ARENA_TAKER_FEE = 0.0025
ARENA_SLIPPAGE = 0.0005


def cost_roundtrip(spread_bps: float, fee_rate: float, slippage: float) -> float:
    """Buy plus sell: fee and slippage on both sides plus the quoted spread, as a fraction."""
    return 2 * (fee_rate + slippage) + spread_bps / 10000


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def feature_payload(
    s: MarketSnapshot, i: Indicators, current_weight: float, fee_rate: float, slippage: float
) -> dict[str, Any]:
    current = s.quote.mid
    return {
        "market": s.symbol.name,
        "utc": _utc(s.fetched_at),
        "closed_bars_utc": {str(tf): _utc(s.bars[tf][-1].ts) for tf in INTERVALS},
        "quote_bid": s.quote.bid,
        "quote_ask": s.quote.ask,
        "quote_spread_bps": round(s.quote.spread_bps, 2),
        "latest_completed_1m_close": s.bars[1][-1].close,
        "filled_no_trade_1m_intervals_last_60": sum(b.volume == 0 for b in s.bars[1][-60:]),
        "data_quality_note": "Missing 1m OHLCV rows may be filled (maximum 2 adjacent zero-volume "
        "minutes) with previous close; larger/recent stale gaps block signals",
        "returns_pct": {
            str(minutes): round(100 * (current / closes(s.bars[tf])[-1 - n] - 1), 3)
            for minutes, tf, n in ((15, 1, 15), (60, 15, 4), (240, 60, 4), (1440, 60, 24))
        },
        "indicators": {k: round(v, 5) if isinstance(v, float) else v for k, v in i.items()},
        "reference_signals": {
            name: run_strategy(name, s, i, current_weight).action
            for name in ("trend", "breakout", "meanrev")
        },
        "current_weight": round(current_weight, 4),
        "per_asset_max": PER_ASSET_MAX,
        "allowed_weights": [0, 0.25, 0.45],
        "taker_fee_per_side_pct": round(100 * fee_rate, 3),
        "slippage_per_side_pct": round(100 * slippage, 3),
        "scope": "Only these quotes and completed OHLCV bars; no external news or order book "
        "in this version",
    }
