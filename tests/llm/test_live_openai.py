"""Spec 005 risk check against the real OpenAI API: does strict mode accept the schema (number enum,
market keys with a hyphen) and does the cheapest model answer validly?

Runs only with `uv run pytest -m net` and an OPENAI_API_KEY in the shell (costs well under 0.01 $).
"""

import os

import pytest

from golden import golden, scenarios, snapshot_for
from llm_trading_bot.llm.contract import SYSTEM, parse_answer, response_schema, user_message
from llm_trading_bot.llm.openai_client import OpenAIResponses
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import feature_payload

pytestmark = pytest.mark.net


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_cheapest_model_answers_the_v1_schema() -> None:
    meta = golden()["meta"]
    payloads = {}
    for symbol, case in ((Symbol.BTCEUR, scenarios()[0]), (Symbol.ETHEUR, scenarios()[48])):
        snap = snapshot_for(case["params"])
        ind = indicators(snap, case["roundtrip_cost_frac"])
        payloads[symbol] = feature_payload(snap, ind, 0.0, meta["fee_rate"], meta["slippage"])
    markets = [Symbol.BTCEUR, Symbol.ETHEUR]
    reply = OpenAIResponses(os.environ["OPENAI_API_KEY"]).ask(
        model="gpt-6-luna",
        instructions=SYSTEM,
        message=user_message(payloads),
        schema=response_schema(markets),
        effort="low",
        max_output_tokens=8000,
    )
    assert reply.problem() == "", reply
    assert reply.text is not None
    answers = parse_answer(reply.text, markets, strategy="gpt-luna")
    assert set(answers) == set(markets)
    assert reply.usage is not None and reply.usage.input_tokens > 0
