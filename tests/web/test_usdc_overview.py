"""Spec 020 AK7: the overview marks the USDC depots, shows their trades as BTC-USDC and the two
new quotes in the ticker; settings offer the depots. Without USDC depots nothing changes."""

import re
import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import ScenarioClient, UsdcScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import USDC_AGENTS, USDC_FIELD, load_config
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web import overview
from llm_trading_bot.web.credentials import AGENT_CHOICES, SecretsFile
from llm_trading_bot.web.overview_page import overview_page, ticker
from llm_trading_bot.web.pages import settings_page
from llm_trading_bot.web.readonly import reading

SHARES: dict[str, Decimal] = {}


def write(path: Path, usdc: str, client: ScenarioClient, at: float = NOW) -> None:
    """One round of trend and the USDC depots ``usdc`` (BOT_USDC_AGENTS) at ``at``."""
    # Written before Spec 021/022: with the euro buy & hold depot and the euro depot trend
    # (both off by default since).
    config = load_config(
        {"BOT_AGENTS": "trend", USDC_FIELD: usdc, "BOT_BUYHOLD": "1", "BOT_EURO_DEPOTS": "1"}
    )
    con = open_db(path, agents=config.depots)
    try:
        paper_cycle(con, client, config, costs=Costs(), clock=lambda: at)
    finally:
        con.close()


@pytest.fixture
def view(tmp_path: Path) -> Iterator[overview.Overview]:
    path = tmp_path / "bot.db"
    write(path, "trend-usdc", UsdcScenarioClient())
    with reading(path) as con:
        yield overview.load(con, NOW + 5, SHARES)


def test_depots_list_the_usdc_depots_and_their_benchmark(view: overview.Overview) -> None:
    rows = {d.agent: d for d in view.depots}
    assert [d.agent for d in view.depots] == ["trend", "trend-usdc", BUYHOLD, BUYHOLD_USDC]
    assert rows["trend-usdc"].usdc and not rows["trend-usdc"].reference
    assert rows[BUYHOLD_USDC].usdc and rows[BUYHOLD_USDC].reference
    assert not rows["trend"].usdc and not rows[BUYHOLD].usdc
    # Spec 021 (ADR-0031): Buy & Hold USDC is the reference now; Buy & Hold, while it runs,
    # is compared with it like any other depot.
    assert rows[BUYHOLD_USDC].vs_buyhold_pp is None and rows[BUYHOLD].vs_buyhold_pp is not None


def test_page_marks_usdc_depots_and_shows_btc_usdc_trades(view: overview.Overview) -> None:
    html = overview_page(view, csrf="x", now=NOW + 5)
    for agent in ("trend-usdc", BUYHOLD_USDC):
        assert re.search(
            rf"<b>{agent}</b>( <span class=\"badge\">Referenz</span>)? "
            r'<span class="badge usdc">USDC</span>',
            html,
        ), agent
    assert "<b>trend</b></td>" in html  # no badge for the v0.3.1 depot
    markets = {(t.agent, t.market) for t in view.trades}
    assert ("trend-usdc", "BTC-USDC") in markets and (BUYHOLD_USDC, "BTC-USDC") in markets
    assert ("trend-usdc", "ETH-EUR") in markets and ("trend", "BTC-EUR") in markets
    assert "<td>BTC-USDC</td>" in html


def test_ticker_and_pulses_show_the_usdc_quotes(view: overview.Overview) -> None:
    items = dict(ticker(view))
    assert items["BTC-USDC"].endswith(" USDC") and items["USDC-EUR"] == "0.89755 EUR"
    assert items["USDC-EUR Spread"] == "0.011 %"
    assert "BTC-USDC Kerzenschluss" not in items and "BTC Kerzenschluss" in items
    assert items["Buy & Hold USDC"].startswith("9") and "Buy & Hold" in items
    labels = [p.label for p in view.pulses]
    assert labels[-4:] == ["Kurs BTC-EUR", "Kurs ETH-EUR", "Kurs BTC-USDC", "Kurs USDC-EUR"]


def test_public_view_shows_the_same_depots_read_only(view: overview.Overview) -> None:
    html = overview_page(view, csrf=None, now=NOW + 5, public=True)
    assert '<span class="badge usdc">USDC</span>' in html and "<td>BTC-USDC</td>" in html
    assert "<form" not in html.split("</header>", 1)[-1]


def test_without_usdc_depots_the_overview_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    # The book holds the USDC markets, but no USDC depot asked for them.
    write(path, "", UsdcScenarioClient())
    with reading(path) as con:
        view = overview.load(con, NOW + 5, SHARES)
    assert [p.label for p in view.pulses][-2:] == ["Kurs BTC-EUR", "Kurs ETH-EUR"]
    assert [q.market for q in view.quotes] == ["BTC-EUR", "ETH-EUR"]
    assert "USDC" not in overview_page(view, csrf="x", now=NOW + 5)


def test_settings_offer_the_usdc_depots(tmp_path: Path) -> None:
    assert AGENT_CHOICES[-4:] == USDC_AGENTS
    html = settings_page({"BOT_AGENTS": "gpt-luna", USDC_FIELD: "trend-usdc"}, csrf="x")
    for agent in USDC_AGENTS:
        tile = re.search(rf'value="{agent}"(?: checked)?>.*?</label>', html)
        assert tile and '<span class="badge usdc">USDC</span>' in tile.group(0), agent
    assert 'value="trend-usdc" checked>' in html and 'value="meanrev-usdc">' in html
    # Spec 022: gpt-luna in BOT_AGENTS ticks gpt-luna-usdc; only the six USDC depots are offered.
    assert 'value="gpt-luna-usdc" checked>' in html and "2 / 6" in html
    secrets = SecretsFile(tmp_path / "secrets.env")
    stored = secrets.update({}, agents=["ensemble-usdc", "trend-usdc"])
    assert (stored["BOT_AGENTS"], stored[USDC_FIELD]) == (
        "trend,ensemble",
        "trend-usdc,ensemble-usdc",
    )
    secrets.request_resume("trend-usdc", "2026-11-08T12:00:00+00:00")
    assert "trend-usdc@" in secrets.read()["BOT_RESUME"]
    with pytest.raises(ValueError, match="unknown depot"):
        secrets.request_resume(BUYHOLD_USDC, "2026-11-08T12:00:00+00:00")


def test_settings_tick_the_usdc_depots_while_no_choice_is_stored() -> None:
    """AK4: the bot runs them when nothing is stored, so the tiles must say so."""
    html = settings_page({"BOT_AGENTS": "gpt-luna"}, csrf="x")
    assert all(f'value="{agent}" checked>' in html for agent in USDC_AGENTS)
    off = settings_page({"BOT_AGENTS": "gpt-luna", USDC_FIELD: ""}, csrf="x")
    assert not any(f'value="{agent}" checked>' in off for agent in USDC_AGENTS)


LATER = NOW + 120
"""A second round two minutes after the first."""


def usdc_markets(con: sqlite3.Connection, now: float) -> tuple[list[str], list[str], list[str]]:
    view = overview.load(con, now, SHARES)
    pulses = [p for p in view.pulses if "USDC" in p.label]
    return (
        [p.label for p in pulses],
        [p.level for p in pulses],
        [q.market for q in view.quotes if "USDC" in q.market],
    )


def test_switched_off_usdc_quotes_leave_pulses_and_ticker(tmp_path: Path) -> None:
    """The bot stops storing BTC-USDC/USDC-EUR once no USDC depot runs; their last rows must not
    read as a market-data failure, also on the public view (ADR-0028)."""
    path = tmp_path / "bot.db"
    write(path, "trend-usdc", UsdcScenarioClient())
    write(path, "", UsdcScenarioClient(), at=LATER)
    labels = ["Kurs BTC-USDC", "Kurs USDC-EUR"]
    with reading(path) as con:
        # Shortly after: still the age rule, so a real outage stays visible for a while.
        assert usdc_markets(con, LATER + 5)[0] == labels
        late = LATER + overview.DELAYED_SECONDS + 1
        assert usdc_markets(con, late) == ([], [], [])
        view = overview.load(con, late, SHARES)
        assert "BTC-USDC" not in dict(ticker(view))
        assert [p.label for p in view.pulses][-2:] == ["Kurs BTC-EUR", "Kurs ETH-EUR"]


def test_usdc_quotes_in_use_stay_visible_when_stale(tmp_path: Path) -> None:
    """While a USDC depot still gets equity points, an old USDC quote is a real problem."""
    path = tmp_path / "bot.db"
    write(path, "trend-usdc", UsdcScenarioClient())
    with reading(path) as con:
        late = NOW + overview.DELAYED_SECONDS + 1
        labels, levels, markets = usdc_markets(con, late)
    assert labels == ["Kurs BTC-USDC", "Kurs USDC-EUR"]
    assert levels == [overview.STOPPED, overview.STOPPED]
    assert markets == ["BTC-USDC", "USDC-EUR"]
