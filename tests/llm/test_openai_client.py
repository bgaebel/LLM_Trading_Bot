"""Spec 005 AK2/AK7: the request the real SDK sends, how replies are read, timeouts and retries.

The real `openai` client runs against an in-memory transport: no network, no key needed.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from llm_trading_bot.llm.budget import Usage
from llm_trading_bot.llm.contract import SYSTEM, response_schema
from llm_trading_bot.llm.openai_client import OpenAIResponses, check_key
from llm_trading_bot.market.models import Symbol

KEY = "sk-test-not-a-real-key-0000"


def response_json(
    *, status: str = "completed", content: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "resp_123",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6.1-sol",
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": content
                if content is not None
                else [
                    {"type": "output_text", "text": '{"a":', "annotations": []},
                    {"type": "output_text", "text": "1}", "annotations": []},
                ],
            }
        ],
        "usage": {
            "input_tokens": 1500,
            "output_tokens": 700,
            "total_tokens": 2200,
            "input_tokens_details": {"cached_tokens": 128},
            "output_tokens_details": {"reasoning_tokens": 300},
        },
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }
    body.update(extra)
    return body


class Recorder:
    def __init__(self, *answers: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return answer(request)


def ok(**kw: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _r: httpx2.Response(200, json=response_json(**kw))


def fail(status: int) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _r: httpx2.Response(
        status, headers={"retry-after-ms": "1"}, json={"error": {"message": f"{KEY} rejected"}}
    )


def model_for(recorder: Recorder) -> OpenAIResponses:
    return OpenAIResponses(KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)))


def ask(model: OpenAIResponses) -> Any:
    return model.ask(
        model="gpt-6.1-sol",
        instructions=SYSTEM,
        message='{"markets":{}}',
        schema=response_schema([Symbol.BTCEUR, Symbol.ETHEUR]),
        effort="low",
        max_output_tokens=8000,
    )


def test_request_shape() -> None:
    recorder = Recorder(ok())
    ask(model_for(recorder))
    (request,) = recorder.requests
    assert request.url.path.endswith("/responses")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-6.1-sol",
        "instructions": SYSTEM,
        "input": '{"markets":{}}',
        "max_output_tokens": 8000,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "trade_signals",
                "schema": response_schema([Symbol.BTCEUR, Symbol.ETHEUR]),
                "strict": True,
            }
        },
        "store": False,
    }


def test_client_is_bounded_to_60_seconds_and_2_retries() -> None:
    model = model_for(Recorder(ok()))
    client = model._client
    assert client.max_retries == 2
    assert client.timeout == 60.0


def test_completed_reply_joins_text_and_reads_usage() -> None:
    reply = ask(model_for(Recorder(ok())))
    assert (reply.status, reply.text, reply.response_id, reply.problem()) == (
        "completed",
        '{"a":1}',
        "resp_123",
        "",
    )
    assert reply.usage == Usage(1500, 700, 300, 128)


@pytest.mark.parametrize(
    ("kw", "problem"),
    [
        (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            "status incomplete (max_output_tokens)",
        ),
        ({"content": [{"type": "refusal", "refusal": "I can't help"}]}, "refusal"),
        ({"content": []}, "no output text"),
        ({"status": "failed"}, "status failed"),
    ],
    ids=["incomplete", "refusal", "empty", "failed"],
)
def test_replies_that_must_not_be_traded(kw: dict[str, Any], problem: str) -> None:
    assert ask(model_for(Recorder(ok(**kw)))).problem() == problem


def test_server_errors_are_retried_twice_then_succeed() -> None:
    recorder = Recorder(fail(500), fail(503), ok())
    assert ask(model_for(recorder)).problem() == ""
    assert len(recorder.requests) == 3


def test_persistent_server_error_raises_after_three_tries() -> None:
    recorder = Recorder(fail(500))
    with pytest.raises(openai.APIStatusError):
        ask(model_for(recorder))
    assert len(recorder.requests) == 3


def test_authentication_error_is_not_retried() -> None:
    recorder = Recorder(fail(401))
    with pytest.raises(openai.AuthenticationError):
        ask(model_for(recorder))
    assert len(recorder.requests) == 1


def test_timeouts_are_retried_then_raised() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    recorder = Recorder(timeout)
    with pytest.raises(openai.APITimeoutError):
        ask(model_for(recorder))
    assert len(recorder.requests) == 3


def test_key_check_only_looks_up_the_model() -> None:
    recorder = Recorder(
        lambda _r: httpx2.Response(
            200, json={"id": "gpt-6-luna", "object": "model", "created": 0, "owned_by": "openai"}
        )
    )
    problem = check_key(KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)))
    (request,) = recorder.requests
    assert problem == "" and request.method == "GET"
    assert request.url.path.endswith("/models/gpt-6-luna")


def test_key_check_reports_only_the_error_type() -> None:
    recorder = Recorder(fail(401))
    problem = check_key(KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)))
    assert problem == "AuthenticationError" and len(recorder.requests) == 1
