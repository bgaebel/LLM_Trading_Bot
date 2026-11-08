"""Spec 020 AK7: the overview marks the USDC depots, shows their trades as BTC-USDC and the two
new quotes in the ticker; settings offer the depots. Without USDC depots nothing changes."""

import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import ScenarioClient, UsdcScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import USDC_AGENTS, load_config
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


def write(path: Path, agents: str, client: ScenarioClient) -> None:
    config = load_config({"BOT_AGENTS": agents})
    con = open_db(path, agents=config.depots)
    try:
        paper_cycle(con, client, config, costs=Costs(), clock=lambda: NOW)
    finally:
        con.close()


@pytest.fixture
def view(tmp_path: Path) -> Iterator[overview.Overview]:
    path = tmp_path / "bot.db"
    write(path, "trend,trend-usdc", UsdcScenarioClient())
    with reading(path) as con:
        yield overview.load(con, NOW + 5, SHARES)


def test_depots_list_the_usdc_depots_and_their_benchmark(view: overview.Overview) -> None:
    rows = {d.agent: d for d in view.depots}
    assert [d.agent for d in view.depots] == ["trend", "trend-usdc", BUYHOLD, BUYHOLD_USDC]
    assert rows["trend-usdc"].usdc and not rows["trend-usdc"].reference
    assert rows[BUYHOLD_USDC].usdc and rows[BUYHOLD_USDC].reference
    assert not rows["trend"].usdc and not rows[BUYHOLD].usdc
    # The comparison stays against buy & hold: the USDC benchmark shows what the route brings.
    assert rows[BUYHOLD_USDC].vs_buyhold_pp is not None and rows[BUYHOLD].vs_buyhold_pp is None


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
    write(path, "trend", UsdcScenarioClient())
    with reading(path) as con:
        view = overview.load(con, NOW + 5, SHARES)
    assert [p.label for p in view.pulses][-2:] == ["Kurs BTC-EUR", "Kurs ETH-EUR"]
    assert [q.market for q in view.quotes] == ["BTC-EUR", "ETH-EUR"]
    assert "USDC" not in overview_page(view, csrf="x", now=NOW + 5)


def test_settings_offer_the_usdc_depots(tmp_path: Path) -> None:
    assert AGENT_CHOICES[-4:] == USDC_AGENTS
    html = settings_page({"BOT_AGENTS": "gpt-luna,trend-usdc"}, csrf="x")
    for agent in USDC_AGENTS:
        tile = re.search(rf'value="{agent}"(?: checked)?>.*?</label>', html)
        assert tile and '<span class="badge usdc">USDC</span>' in tile.group(0), agent
    assert 'value="trend-usdc" checked>' in html and 'value="meanrev-usdc">' in html
    assert "2 / 10" in html
    secrets = SecretsFile(tmp_path / "secrets.env")
    stored = secrets.update({}, agents=["ensemble-usdc", "trend", "trend-usdc"])
    assert stored["BOT_AGENTS"] == "trend,trend-usdc,ensemble-usdc"
    secrets.request_resume("trend-usdc", "2026-11-08T12:00:00+00:00")
    assert "trend-usdc@" in secrets.read()["BOT_RESUME"]
    with pytest.raises(ValueError, match="unknown depot"):
        secrets.request_resume(BUYHOLD_USDC, "2026-11-08T12:00:00+00:00")
