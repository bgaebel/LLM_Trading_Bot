# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - SYSTEM, SIGNAL_SCHEMA,
#   validate_signal
# Origin: LLM-Trading-Arena@7563724 apps/arena/provenance.py (adapted) - strict_response
"""Prompt v1: one request for both markets, one strictly checked signal per market (ADR-0013).

The wording follows the Arena prompt; the per-market checks are the Arena's. Changing the system
prompt or the schema is a new PROMPT_VERSION (CLAUDE.md) - the hash test enforces that.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from llm_trading_bot.market.models import SYMBOLS, Symbol
from llm_trading_bot.strategy.signals import Action, Signal

PROMPT_VERSION = "llm-v1"
REASON_MAX = 550
REGIMES = ("UPTREND", "DOWNTREND", "SIDEWAYS")
FIELDS = ("action", "target_fraction", "confidence", "market_regime", "reason")

SYSTEM = (
    "You are an experimental crypto paper-trading signal analyst, NOT a broker, order executor, "
    "risk engine or guaranteed forecaster. You receive the state of up to two EUR spot markets "
    "(BTC-EUR, ETH-EUR) in one message. Use ONLY the supplied multi-timeframe CLOSED candle "
    "indicators, quotes, current portfolio weight and reference signals of classic strategies; "
    "do not claim to know news, funding, other exchange order books or unseen future prices. "
    "For EACH supplied market choose BUY, SELL or HOLD and a target allocation of 0, 0.25 or "
    "0.45 of equity, classify the market regime as UPTREND, DOWNTREND or SIDEWAYS and state your "
    "confidence from 0 to 1. Trades have explicitly stated taker fees, spread and slippage; "
    "avoiding overtrading is valuable. BUY must use 0.25 or 0.45, SELL must use 0.0; HOLD "
    "retains the existing weight. Explain the specific evidence and uncertainty briefly per "
    "market. Return ONLY the prescribed JSON schema. Your signals are advisory; a deterministic "
    "sandbox risk engine controls every virtual order."
)

_ENTRY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "target_fraction": {"type": "number", "enum": [0, 0.25, 0.45]},
        "confidence": {"type": "number"},
        "market_regime": {"type": "string", "enum": list(REGIMES)},
        "reason": {"type": "string"},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}


class InvalidSignal(ValueError):
    """The model's answer is not valid; nothing may be traded on any part of it."""


@dataclass(frozen=True)
class MarketAnswer:
    signal: Signal
    regime: str


def ordered(markets: Sequence[Symbol]) -> list[Symbol]:
    return [s for s in SYMBOLS if s in markets]


def response_schema(markets: Sequence[Symbol]) -> dict[str, Any]:
    names = [s.market for s in ordered(markets)]
    if not names:
        raise ValueError("at least one market is required")
    return {
        "type": "object",
        "properties": {name: _ENTRY for name in names},
        "required": names,
        "additionalProperties": False,
    }


def prompt_sha256() -> str:
    """Fingerprint of everything fixed about the request; stored with every attempt."""
    fixed = {"version": PROMPT_VERSION, "instructions": SYSTEM, "schema": response_schema(SYMBOLS)}
    return hashlib.sha256(json.dumps(fixed, sort_keys=True).encode("utf-8")).hexdigest()


def user_message(payloads: Mapping[Symbol, Mapping[str, Any]]) -> str:
    """Compact JSON, umlauts unescaped; each market's content is the Arena feature_payload."""
    markets = {s.market: payloads[s] for s in ordered(list(payloads))}
    return json.dumps({"markets": markets}, ensure_ascii=False, separators=(",", ":"))


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _entry(name: str, data: object, strategy: str) -> MarketAnswer:
    # Arena strict_response + validate_signal, per market, plus the regime.
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise InvalidSignal(f"{name}: unexpected signal fields")
    action = data["action"]
    if action not in ("BUY", "SELL", "HOLD"):
        raise InvalidSignal(f"{name}: invalid action")
    target = data["target_fraction"]
    if not _number(target) or target not in (0, 0.25, 0.45):
        raise InvalidSignal(f"{name}: invalid target_fraction")
    confidence = data["confidence"]
    if not _number(confidence) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise InvalidSignal(f"{name}: invalid confidence")
    regime = data["market_regime"]
    if regime not in REGIMES:
        raise InvalidSignal(f"{name}: invalid market_regime")
    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidSignal(f"{name}: reason missing")
    if (action == "SELL" and target != 0) or (action == "BUY" and target not in (0.25, 0.45)):
        raise InvalidSignal(f"{name}: inconsistent action and target")
    signal = Signal(
        cast(Action, action),
        float(target),
        float(confidence),
        reason.strip()[:REASON_MAX],
        strategy,
        PROMPT_VERSION,
    )
    return MarketAnswer(signal, cast(str, regime))


def parse_answer(
    text: str, markets: Sequence[Symbol], *, strategy: str
) -> dict[Symbol, MarketAnswer]:
    """All requested markets, each valid - or InvalidSignal for the whole answer (fail closed)."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InvalidSignal(f"answer is not JSON: {exc}") from None
    wanted = ordered(markets)
    if not isinstance(data, dict) or set(data) != {s.market for s in wanted}:
        raise InvalidSignal("answer does not cover exactly the requested markets")
    return {s: _entry(s.market, data[s.market], strategy) for s in wanted}
