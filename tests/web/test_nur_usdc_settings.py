"""Spec 022 AK6 (ADR-0032): the settings offer only the USDC depots; saving keeps BOT_AGENTS
readable for a release from before Spec 022 (rollback) and never empty."""

import re
from pathlib import Path

import pytest

from llm_trading_bot.llm.agents import (
    CLASSIC_AGENTS,
    EURO_FIELD,
    LLM_DEFAULTS,
    USDC_AGENTS,
    USDC_FIELD,
    load_config,
)
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web.credentials import AGENT_CHOICES, STOPPABLE, SecretsFile
from llm_trading_bot.web.pages import TICKER, settings_page

KEY = "sk-test-not-a-real-key-0000abcd"
GPT = ("gpt-luna-usdc", "gpt-sol-usdc")
PRE_022 = (*CLASSIC_AGENTS, *LLM_DEFAULTS)
"""What load_config of every release before Spec 022 accepts in BOT_AGENTS."""


def tiles(html: str) -> dict[str, bool]:
    return {
        m.group(1): bool(m.group(2))
        for m in re.finditer(r'name="agents" value="([^"]+)"( checked)?>', html)
    }


def test_settings_offer_only_usdc_depots() -> None:
    assert (*GPT, *USDC_AGENTS) == AGENT_CHOICES
    html = settings_page({}, csrf="x")
    # Nothing stored: the bot's defaults, every USDC depot runs.
    assert tiles(html) == dict.fromkeys(AGENT_CHOICES, True) and "6 / 6" in html
    stored = settings_page({"BOT_AGENTS": "gpt-sol,trend", USDC_FIELD: "meanrev-usdc"}, csrf="x")
    assert {a for a, on in tiles(stored).items() if on} == {"gpt-sol-usdc", "meanrev-usdc"}
    assert "2 / 6" in stored
    for euro in (*PRE_022, BUYHOLD, BUYHOLD_USDC):
        assert f'value="{euro}"' not in html, euro
    assert "Die Euro-Depots sind abgeschaltet (ADR-0032)" in html
    assert "gpt-luna-usdc € / Tag" in html and "gpt-sol-usdc € / Tag" in html
    assert ("gpt-luna-usdc", "Haupt-Trader") in TICKER and (
        "gpt-luna",
        "Haupt-Trader",
    ) not in TICKER


@pytest.mark.parametrize(
    ("chosen", "bot_agents", "usdc"),
    [
        ([*GPT, *USDC_AGENTS], "gpt-luna,gpt-sol,trend,breakout,meanrev,ensemble", USDC_AGENTS),
        (["gpt-sol-usdc"], "gpt-sol", ()),
        (["meanrev-usdc", "gpt-luna-usdc"], "gpt-luna,meanrev", ("meanrev-usdc",)),
        (["ensemble-usdc"], "ensemble", ("ensemble-usdc",)),
    ],
)
def test_saving_keeps_bot_agents_rollback_safe(
    tmp_path: Path, chosen: list[str], bot_agents: str, usdc: tuple[str, ...]
) -> None:
    secrets = SecretsFile(tmp_path / "secrets.env")
    # A choice stored by the settings of Spec 021 is replaced, not merged.
    secrets.path.write_text("BOT_AGENTS=trend,breakout,gpt-luna\n", encoding="utf-8")
    stored = secrets.update({"OPENAI_API_KEY": KEY}, agents=chosen)
    assert (stored["BOT_AGENTS"], stored[USDC_FIELD]) == (bot_agents, ",".join(usdc))
    # The euro twins of the choice: names every release before 022 knows, never empty.
    names = stored["BOT_AGENTS"].split(",")
    assert names and set(names) <= set(PRE_022)
    # This release runs exactly the chosen depots (and their benchmark).
    config = load_config(stored)
    assert set(config.agents) == set(chosen) and config.depots[-1] == BUYHOLD_USDC
    # The way back (BOT_EURO_DEPOTS=1, like a release before 022) runs their euro twins.
    back = load_config({**stored, EURO_FIELD: "1"})
    gpt = {a.name for a in back.llm.agents} if back.llm else set()
    assert set(back.classic) | gpt == set(names)


def test_empty_choice_is_refused(tmp_path: Path) -> None:
    secrets = SecretsFile(tmp_path / "secrets.env")
    with pytest.raises(ValueError, match="at least one"):
        secrets.update({"OPENAI_API_KEY": KEY}, agents=[])
    for euro in ("trend", "gpt-luna"):
        with pytest.raises(ValueError, match="unknown depots"):
            secrets.update({"OPENAI_API_KEY": KEY}, agents=[euro])
    assert not secrets.path.exists()  # nothing written


def test_gpt_choice_without_key_is_refused(tmp_path: Path) -> None:
    secrets = SecretsFile(tmp_path / "secrets.env")
    with pytest.raises(ValueError, match=r"OPENAI_API_KEY is required .*gpt-luna-usdc"):
        secrets.update({}, agents=["gpt-luna-usdc", "trend-usdc"])
    assert not secrets.path.exists()
    assert secrets.update({}, agents=["trend-usdc"])["BOT_AGENTS"] == "trend"


def test_release_is_accepted_for_every_known_depot(tmp_path: Path) -> None:
    """Spec 011 AK5: the settings offer the release of every stopped depot - also of a euro
    depot stopped before Spec 022, until the purge removes it."""
    secrets = SecretsFile(tmp_path / "secrets.env")
    assert set(STOPPABLE) == {*PRE_022, *AGENT_CHOICES}
    for agent in STOPPABLE:
        secrets.request_resume(agent, "2026-11-09T12:00:00+00:00")
    assert len(secrets.read()["BOT_RESUME"].split(",")) == len(STOPPABLE)
    for benchmark in (BUYHOLD, BUYHOLD_USDC, "gpt-terra-usdc"):
        with pytest.raises(ValueError, match="unknown depot"):
            secrets.request_resume(benchmark, "2026-11-09T12:00:00+00:00")
