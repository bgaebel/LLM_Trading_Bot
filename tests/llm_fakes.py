"""A scripted stand-in for the OpenAI client (SignalModel protocol)."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_trading_bot.llm.budget import Usage
from llm_trading_bot.llm.openai_client import ModelReply

KEY = "sk-test-not-a-real-key-0000"


def entry(action: str = "BUY", target: float = 0.25, regime: str = "UPTREND") -> dict[str, Any]:
    return {
        "action": action,
        "target_fraction": target,
        "confidence": 0.6,
        "market_regime": regime,
        "reason": "Trend und Momentum stimmen überein",
    }


def valid_reply(markets: list[str], **entries: dict[str, Any]) -> ModelReply:
    body = {m: entries.get(m.replace("-", "_"), entry()) for m in markets}
    return ModelReply(
        "completed",
        json.dumps(body, ensure_ascii=False),
        None,
        None,
        "resp_1",
        None,
        Usage(1500, 700, 300, 0),
    )


@dataclass
class FakeModel:
    """Answers per model; a reply factory gets the requested market names."""

    replies: dict[str, Callable[[list[str]], ModelReply]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def ask(
        self,
        *,
        model: str,
        instructions: str,
        message: str,
        schema: Mapping[str, Any],
        effort: str,
        max_output_tokens: int,
    ) -> ModelReply:
        markets = list(schema["required"])
        self.calls.append(
            {
                "model": model,
                "message": message,
                "markets": markets,
                "effort": effort,
                "max_output_tokens": max_output_tokens,
                "instructions": instructions,
            }
        )
        factory = self.replies.get(model, valid_reply)
        return factory(markets)
