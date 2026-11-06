# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - SYSTEM, SIGNAL_SCHEMA,
#   validate_signal, llm_signal (user message)
# Origin: LLM-Trading-Arena@7563724 apps/arena/provenance.py (adapted) - strict_response
"""What the model is told and what it may answer, identical to Arena v0.3.1 (ADR-0007).

Changing the prompt or the schema is a new strategy version (CLAUDE.md), never an edit in place.
"""

import json
import math
from collections.abc import Mapping
from typing import Any, cast

from llm_trading_bot.strategy.signals import Action, Signal

AGENT = "gpt"
REASON_MAX = 550

SYSTEM = (
    "You are an experimental crypto paper-trading signal analyst, NOT a broker, order executor, "
    "risk engine or guaranteed forecaster. Use ONLY the supplied multi-timeframe CLOSED candle "
    "indicators and quotes; do not claim to know news, funding, other exchange order books or "
    "unseen future prices. Choose BUY, SELL or HOLD and a target allocation of 0, 0.25, or 0.45. "
    "Trades have explicitly stated taker fees, spread and slippage; avoiding overtrading is "
    "valuable. BUY must use 0.25 or 0.45, SELL must use 0.0; HOLD retains existing weight. "
    "Explain the specific evidence and uncertainty briefly. "
    "Return ONLY the prescribed JSON schema. "
    "Your signal is advisory; a deterministic sandbox risk engine controls every virtual order."
)

SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "target_fraction": {"type": "number", "enum": [0, 0.25, 0.45]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["action", "target_fraction", "confidence", "reason"],
    "additionalProperties": False,
}


class InvalidSignal(ValueError):
    """The model's answer is not a valid signal; nothing may be traded on it."""


def user_message(payload: Mapping[str, Any]) -> str:
    """The exact text sent as user input (compact JSON, umlauts unescaped)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def parse_signal(text: str, *, strategy: str = AGENT) -> Signal:
    """Strict check of the raw answer (Arena strict_response + validate_signal)."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InvalidSignal(f"answer is not JSON: {exc}") from None
    if not isinstance(data, dict) or set(data) != set(SIGNAL_SCHEMA["required"]):
        raise InvalidSignal("unexpected signal fields")
    action = data["action"]
    if action not in ("BUY", "SELL", "HOLD"):
        raise InvalidSignal("invalid action")
    target = data["target_fraction"]
    if not _number(target) or target not in (0, 0.25, 0.45):
        raise InvalidSignal("invalid target_fraction")
    confidence = data["confidence"]
    if not _number(confidence) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise InvalidSignal("invalid confidence")
    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidSignal("reason missing")
    if (action == "SELL" and target != 0) or (action == "BUY" and target not in (0.25, 0.45)):
        raise InvalidSignal("inconsistent action and target")
    return Signal(
        cast(Action, action),
        float(target),
        float(confidence),
        reason.strip()[:REASON_MAX],
        strategy,
    )
