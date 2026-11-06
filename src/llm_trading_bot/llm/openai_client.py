# Origin: LLM-Trading-Arena@7563724 apps/arena/arena.py (adapted) - llm_signal (gpt branch)
# Origin: LLM-Trading-Arena@7563724 apps/arena/provenance.py (adapted) - capture_usage
"""OpenAI Responses API adapter (ADR-0013). Only this module talks to OpenAI.

Errors are reported by type name only: provider messages can echo parts of the API key.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

import openai
from openai.types.responses import Response, ResponseTextConfigParam
from openai.types.shared_params import Reasoning

from llm_trading_bot.llm.budget import Usage

TIMEOUT_SECONDS = 60.0
# Own retries instead of the SDK's: the SDK would also retry timeouts, which may already be billed
# and would multiply the wait. Only fast, unbilled-looking failures (429, 5xx) are retried here.
MAX_RETRIES = 2
RETRY_PAUSES = (1.0, 2.0)
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
    requests: int = 1
    """HTTP requests sent for this reply (1 + retries); earlier ones may have been billed."""

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
        sleep: Callable[[float], None] = time.sleep,
        **client_options: Any,
    ) -> None:
        self._client = client_factory(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0, **client_options
        )
        self._sleep = sleep

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
        for attempt in range(MAX_RETRIES + 1):
            try:
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
            except (openai.RateLimitError, openai.InternalServerError):
                if attempt == MAX_RETRIES:
                    raise
                self._sleep(RETRY_PAUSES[attempt])
                continue
            return replace(reply_from(response), requests=attempt + 1)
        raise AssertionError("unreachable")


def check_key(
    api_key: str,
    *,
    model: str = "gpt-6-luna",
    client_factory: Callable[..., openai.OpenAI] = openai.OpenAI,
    **client_options: Any,
) -> str:
    """Free connectivity check (model lookup, no tokens billed): "" if fine, else the error type."""
    client = client_factory(api_key=api_key, timeout=15.0, max_retries=0, **client_options)
    try:
        client.models.retrieve(model)
    except API_ERRORS as exc:
        return type(exc).__name__
    return ""
