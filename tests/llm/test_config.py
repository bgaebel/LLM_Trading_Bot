"""Spec 005 AK9 / ADR-0012: depots, models, prices and shares come from the environment; anything
unclear is refused before a database is touched."""

from decimal import Decimal

import pytest

from llm_trading_bot.llm.agents import load_config
from llm_trading_bot.llm.budget import Pricing
from llm_trading_bot.llm.due import Cadence

OFF = {"BOT_USDC_AGENTS": "", "BOT_EURO_DEPOTS": "1"}
"""Spec 020: without the USDC depots, which run by default. Spec 022: with the euro depots
of BOT_AGENTS, which these tests (written before) configure and which are off by default."""
KEY = {"OPENAI_API_KEY": "sk-test-not-a-real-key-0000", **OFF}


def test_defaults_are_luna_trading_and_sol_cross_checking() -> None:
    config = load_config(KEY)
    assert config.classic == ()
    assert config.llm is not None
    luna, sol = config.llm.agents
    assert (luna.name, luna.model, luna.daily_eur) == ("gpt-luna", "gpt-6-luna", Decimal("0.50"))
    assert (sol.name, sol.model, sol.daily_eur) == ("gpt-sol", "gpt-6.1-sol", Decimal("2.50"))
    assert luna.pricing == Pricing(Decimal("0.10"), Decimal("0.50"))
    assert sol.pricing == Pricing(Decimal("2.00"), Decimal("10.00"))
    assert config.llm.total_daily_eur == Decimal(3)
    assert (config.llm.effort, config.llm.max_output_tokens) == ("low", 8000)
    assert config.llm.cadence == Cadence(3600, 600, 0.018, 4)
    assert config.agents == ("gpt-luna", "gpt-sol")


def test_key_never_appears_in_the_settings_repr() -> None:
    config = load_config(KEY)
    assert KEY["OPENAI_API_KEY"] not in repr(config)


def test_classic_only_needs_no_key() -> None:
    config = load_config({**OFF, "BOT_AGENTS": "trend, meanrev"})
    assert (config.classic, config.llm, config.agents) == (
        ("trend", "meanrev"),
        None,
        ("trend", "meanrev"),
    )


def test_mixed_depots_keep_llm_order_luna_first() -> None:
    config = load_config({**KEY, "BOT_AGENTS": "gpt-sol,trend,gpt-luna"})
    assert config.agents == ("trend", "gpt-luna", "gpt-sol")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": "  "}, "OPENAI_API_KEY"),
        ({**KEY, "BOT_AGENTS": ""}, "at least one"),
        ({**KEY, "BOT_AGENTS": "gpt-luna,gpt-luna"}, "twice"),
        ({**KEY, "BOT_AGENTS": "gpt-terra"}, "unknown depots"),
        ({**KEY, "BOT_AGENTS": "buyhold"}, "unknown depots"),
        ({**KEY, "LLM_EFFORT": "turbo"}, "LLM_EFFORT"),
        ({**KEY, "LLM_DAILY_BUDGET_EUR": "-1"}, "LLM_DAILY_BUDGET_EUR"),
        ({**KEY, "LLM_DAILY_BUDGET_EUR_GPT_SOL": "x"}, "LLM_DAILY_BUDGET_EUR_GPT_SOL"),
        ({**KEY, "LLM_PRICE_IN_EUR_GPT_LUNA": "0.1"}, "set together"),
        (
            {**KEY, "LLM_PRICE_IN_EUR_GPT_SOL": "0", "LLM_PRICE_OUT_EUR_GPT_SOL": "10"},
            "greater than 0",
        ),
    ],
)
def test_unclear_configuration_is_refused(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(env)


def test_other_model_without_prices_has_no_pricing_and_is_never_called() -> None:
    config = load_config({**KEY, "OPENAI_MODEL_GPT_SOL": "gpt-6-astra"})
    assert config.llm is not None
    sol = config.llm.agents[1]
    assert (sol.model, sol.pricing) == ("gpt-6-astra", None)


def test_other_model_with_own_prices() -> None:
    env = {
        **KEY,
        "OPENAI_MODEL_GPT_SOL": "gpt-6-astra",
        "LLM_PRICE_IN_EUR_GPT_SOL": "10",
        "LLM_PRICE_OUT_EUR_GPT_SOL": "50",
        "LLM_DAILY_BUDGET_EUR_GPT_SOL": "4",
        "LLM_EFFORT": "medium",
    }
    config = load_config(env)
    assert config.llm is not None
    sol = config.llm.agents[1]
    assert sol.pricing == Pricing(Decimal(10), Decimal(50)) and sol.daily_eur == Decimal(4)
    assert config.llm.effort == "medium"
