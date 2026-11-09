"""Spec 022 AK1-AK3 (ADR-0032): only the USDC depots run by default - also with the server's
secrets.env from before Spec 022 -, ``BOT_EURO_DEPOTS=1`` brings back the depot set of Spec 021,
and anything unclear is refused before a database is touched."""

from pathlib import Path

import pytest

from fakes import FakeClock, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import (
    BUYHOLD_FIELD,
    CLASSIC_AGENTS,
    EURO_DEPOTS,
    EURO_FIELD,
    GPT_USDC,
    LLM_DEFAULTS,
    USDC_AGENTS,
    USDC_FIELD,
    BotConfig,
    load_config,
)
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web.credentials import FIELDS, parse

GPT = ("gpt-luna-usdc", "gpt-sol-usdc")
ALL_USDC = (*USDC_AGENTS, *GPT, BUYHOLD_USDC)


def names(config: BotConfig) -> tuple[str, ...]:
    return tuple(a.name for a in config.llm.agents) if config.llm else ()


# ------------------------------------------------------------------ AK1: the default set


def test_default_set_runs_only_usdc_depots() -> None:
    assert EURO_DEPOTS == (
        "trend",
        "breakout",
        "meanrev",
        "ensemble",
        "gpt-luna",
        "gpt-sol",
        BUYHOLD,
    )
    assert dict(GPT_USDC) == {"gpt-luna-usdc": "gpt-luna", "gpt-sol-usdc": "gpt-sol"}
    config = load_config({"OPENAI_API_KEY": KEY})
    assert (config.classic, config.usdc, names(config)) == ((), USDC_AGENTS, GPT)
    assert config.depots == (*USDC_AGENTS, *GPT, BUYHOLD_USDC)
    assert config.routed and not config.buyhold
    assert not set(config.depots) & set(EURO_DEPOTS)


SERVER = {
    "gpt only": "BOT_AGENTS=gpt-luna,gpt-sol\n",
    "everything": "BOT_AGENTS=gpt-luna,gpt-sol,trend,breakout,meanrev,ensemble\n"
    "BOT_USDC_AGENTS=trend-usdc,breakout-usdc,meanrev-usdc,ensemble-usdc\n",
    "classic and luna": "BOT_AGENTS=trend,meanrev,gpt-luna\n",
    "some usdc": "BOT_AGENTS=gpt-sol,ensemble\nBOT_USDC_AGENTS=meanrev-usdc\n",
}
"""secrets.env as the web UI of Spec 020/021 wrote it (BOT_USDC_AGENTS stored or not)."""
EXPECTED = {
    "gpt only": (USDC_AGENTS, GPT),
    "everything": (USDC_AGENTS, GPT),
    "classic and luna": (USDC_AGENTS, ("gpt-luna-usdc",)),
    "some usdc": (("meanrev-usdc",), ("gpt-sol-usdc",)),
}


@pytest.mark.parametrize("case", list(SERVER))
def test_server_secrets_from_before_022_start_only_usdc_depots(tmp_path: Path, case: str) -> None:
    """AK1: the deploy alone switches over - nothing in secrets.env has to change."""
    env = {**parse(SERVER[case] + f"OPENAI_API_KEY={KEY}\n")}
    config = load_config(env)
    usdc, gpt = EXPECTED[case]
    assert (config.classic, config.usdc, names(config)) == ((), usdc, gpt)
    assert config.depots == (*usdc, *gpt, BUYHOLD_USDC)
    # One real round on a new database: only these depots get a portfolio, a decision, a point.
    con = open_db(tmp_path / "bot.db", agents=config.depots)
    try:
        report = paper_cycle(
            con,
            UsdcScenarioClient(),
            config,
            costs=Costs(),
            model=FakeModel(),
            clock=FakeClock(NOW),
        )
        assert report.complete
        assert {e.agent for e in report.equity} == set(config.depots)
        assert {o.agent for o in report.llm} == set(gpt)
        for table in ("portfolios", "decisions", "trades", "equity", "llm_calls"):
            agents = {r[0] for r in con.execute(f"SELECT DISTINCT agent FROM {table}")}
            assert not agents & set(EURO_DEPOTS), table
    finally:
        con.close()


def test_gpt_usdc_follow_the_gpt_choice_in_bot_agents() -> None:
    base = {"OPENAI_API_KEY": KEY, USDC_FIELD: ""}
    for chosen, expected in (
        ("gpt-luna", ("gpt-luna-usdc",)),
        ("gpt-sol", ("gpt-sol-usdc",)),
        ("gpt-sol,gpt-luna", GPT),
        ("trend,gpt-sol", ("gpt-sol-usdc",)),
    ):
        config = load_config({**base, "BOT_AGENTS": chosen})
        assert names(config) == expected and config.depots == (*expected, BUYHOLD_USDC), chosen
    # Missing BOT_AGENTS: the default of every release, both GPT depots.
    assert names(load_config(base)) == GPT


# ------------------------------------------------------------------ AK2: the euro switch


def test_euro_switch_is_off_by_default_and_strict() -> None:
    base = {"OPENAI_API_KEY": KEY, "BOT_AGENTS": "trend,gpt-luna"}
    assert EURO_FIELD == "BOT_EURO_DEPOTS"
    assert load_config(base).classic == () and names(load_config(base)) == ("gpt-luna-usdc",)
    assert load_config({**base, EURO_FIELD: "0"}) == load_config(base)
    assert load_config({**base, EURO_FIELD: "1"}).classic == ("trend",)
    for wrong in ("", " 1", "1 ", "true", "on", "yes", "2", "01"):
        with pytest.raises(ValueError, match="BOT_EURO_DEPOTS must be 1 or 0"):
            load_config({**base, EURO_FIELD: wrong})
    # The web UI keeps only its own fields in secrets.env: the switch belongs in the environment.
    assert EURO_FIELD not in FIELDS and parse("BOT_EURO_DEPOTS=1\n") == {}


def test_euro_switch_restores_the_depot_set_of_021() -> None:
    """``1`` is the way back without a code change: exactly the depots of Spec 021, GPT in
    euro and no GPT USDC depot (they would share the day's total with their twins)."""
    on = {EURO_FIELD: "1", "OPENAI_API_KEY": KEY}
    config = load_config({**on, "BOT_AGENTS": "gpt-sol,trend,gpt-luna"})
    assert (config.classic, config.usdc, names(config)) == (
        ("trend",),
        USDC_AGENTS,
        ("gpt-luna", "gpt-sol"),
    )
    assert config.depots == ("trend", *USDC_AGENTS, "gpt-luna", "gpt-sol", BUYHOLD_USDC)
    plain = load_config({**on, "BOT_AGENTS": "trend", USDC_FIELD: "", BUYHOLD_FIELD: "1"})
    assert plain.depots == ("trend", BUYHOLD) and not plain.routed
    every = ",".join((*CLASSIC_AGENTS, *LLM_DEFAULTS))
    full = load_config({**on, "BOT_AGENTS": every, BUYHOLD_FIELD: "1"})
    assert set(full.depots) == {*EURO_DEPOTS, *USDC_AGENTS, BUYHOLD_USDC}


def test_config_built_by_hand_runs_exactly_its_depots() -> None:
    """The strategy lab (Spec 016) passes its depots explicitly; the switch is load_config's."""
    lab = BotConfig(CLASSIC_AGENTS, None, buyhold=True)
    assert lab.depots == (*CLASSIC_AGENTS, BUYHOLD) and not lab.routed
    assert BotConfig(CLASSIC_AGENTS, None).depots == CLASSIC_AGENTS
    usdc = BotConfig((), None, USDC_AGENTS)
    assert usdc.depots == (*USDC_AGENTS, BUYHOLD_USDC) and usdc.routed


# ------------------------------------------------------------------ AK3: errors


def test_missing_key_refuses_the_gpt_usdc_depots() -> None:
    for env in ({}, {"BOT_AGENTS": "gpt-luna"}, {"BOT_AGENTS": "trend,gpt-sol", USDC_FIELD: ""}):
        with pytest.raises(ValueError, match=r"OPENAI_API_KEY is required for the GPT depots \["):
            load_config(env)
    with pytest.raises(ValueError, match=r"\['gpt-luna-usdc'\]"):
        load_config({"BOT_AGENTS": "gpt-luna", "OPENAI_API_KEY": " "})


def test_classic_usdc_only_needs_no_key() -> None:
    config = load_config({"BOT_AGENTS": "trend"})
    assert (config.llm, config.classic) == (None, ())
    assert config.depots == (*USDC_AGENTS, BUYHOLD_USDC)
    assert load_config({"BOT_AGENTS": "ensemble", USDC_FIELD: "meanrev-usdc"}).depots == (
        "meanrev-usdc",
        BUYHOLD_USDC,
    )


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"BOT_AGENTS": ""}, "at least one"),
        ({"BOT_AGENTS": "trend", USDC_FIELD: ""}, "no depot would run"),
        ({"BOT_AGENTS": "trend,breakout", USDC_FIELD: " "}, "BOT_EURO_DEPOTS=1"),
        ({"BOT_AGENTS": "gpt-luna-usdc"}, "follow their euro twin in BOT_AGENTS"),
        ({"BOT_AGENTS": "trend", USDC_FIELD: "gpt-luna-usdc"}, "unknown depots"),
        ({"BOT_AGENTS": "trend", USDC_FIELD: "buyhold-usdc"}, "unknown depots"),
        ({"BOT_AGENTS": "trend", EURO_FIELD: "yes"}, "BOT_EURO_DEPOTS must be 1 or 0"),
    ],
)
def test_unclear_depot_choice_is_refused(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(env)
