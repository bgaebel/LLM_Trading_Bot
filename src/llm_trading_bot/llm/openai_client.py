# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - llm_signal (gpt branch)
# Origin: LLM-Trading-Arena@7563724 apps/arena/provenance.py (adapted) - capture_usage
"""OpenAI Responses API adapter (ADR-0011). Only this module talks to OpenAI.

Errors are reported by type name only: provider messages can echo parts of the API key.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import openai
from openai.types.responses import Response, ResponseTextConfigParam
from openai.types.shared_params import Reasoning

from llm_trading_bot.llm.budget import Usage

TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2
SCHEMA_NAME = "trade_signals"
# The SDK's base error class: timeouts, connection problems and HTTP errors all derive from it.
API_ERRORS = (openai.APIError,)
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelReply:
    status: str
    """Provider status: completed, incomplete, failed, ..."""
    text: str | None
    refusal: str | None
    incomplete_reason: str | None
    response_id: str | None
    model: str | None
    usage: Usage | None

    def problem(self) -> str:
        """Why this reply must not be traded on; empty if it may be parsed."""
        if self.refusal:
            return "refusal"
        if self.status != "completed":
            return f"status {self.status}" + (
                f" ({self.incomplete_reason})" if self.incomplete_reason else ""
            )
        if not self.text:
            return "no output text"
        return ""


class SignalModel(Protocol):
    def ask(
        self,
        *,
        model: str,
        instructions: str,
        message: str,
        schema: Mapping[str, Any],
        effort: str,
        max_output_tokens: int,
    ) -> ModelReply: ...


def _usage(response: Response) -> Usage | None:
    usage = response.usage
    if usage is None:
        return None
    reasoning = (
        usage.output_tokens_details.reasoning_tokens if usage.output_tokens_details else None
    )
    cached = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else None
    return Usage(usage.input_tokens, usage.output_tokens, reasoning, cached)


def reply_from(response: Response) -> ModelReply:
    texts: list[str] = []
    refusals: list[str] = []
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            if part.type == "output_text":
                texts.append(part.text)
            elif part.type == "refusal":
                refusals.append(part.refusal)
    details = response.incomplete_details
    return ModelReply(
        status=str(response.status),
        text="".join(texts) or None,
        refusal=" ".join(refusals) or None,
        incomplete_reason=details.reason if details else None,
        response_id=response.id,
        model=response.model,
        usage=_usage(response),
    )


class OpenAIResponses:
    def __init__(
        self,
        api_key: str,
        *,
        client_factory: Callable[..., openai.OpenAI] = openai.OpenAI,
        **client_options: Any,
    ) -> None:
        self._client = client_factory(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES, **client_options
        )

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
        if effort not in EFFORTS:
            raise ValueError(f"unknown reasoning effort: {effort!r}")
        reasoning: Reasoning = {"effort": cast(Effort, effort)}
        text: ResponseTextConfigParam = {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "schema": dict(schema),
                "strict": True,
            }
        }
        response = self._client.responses.create(
            model=model,
            instructions=instructions,
            input=message,
            max_output_tokens=max_output_tokens,
            reasoning=reasoning,
            text=text,
            # Nothing of the trading context is kept on the provider side.
            store=False,
        )
        return reply_from(response)
