"""Spec 022 AK7 (ADR-0032): switched-off depots leave the overview within five minutes - from
every list, the GPT costs included -, while a quote outage longer than that hides no depot."""

import sqlite3
from decimal import Decimal
from pathlib import Path

from fakes import FakeClock, UsdcScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import USDC_FIELD, BotConfig, load_config
from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web import overview
from llm_trading_bot.web.app import Access, Response, WebApp
from llm_trading_bot.web.overview_page import overview_page, ticker
from llm_trading_bot.web.readonly import reading

WINDOW = overview.ACTIVE_SECONDS
EURO = {"OPENAI_API_KEY": KEY, "BOT_AGENTS": "trend,gpt-luna", "BOT_EURO_DEPOTS": "1"}
"""Spec 021's depot set (the server before the deploy), with one classic and one USDC depot."""
OURS = {"OPENAI_API_KEY": KEY, "BOT_AGENTS": "trend,gpt-luna"}
"""The same secrets.env after the deploy of Spec 022."""
SHARES = {"gpt-luna": Decimal("0.50"), "gpt-luna-usdc": Decimal("0.50")}


def rounds(path: Path, config: BotConfig, *steps: int) -> None:
    con = open_db(path, agents=config.depots)
    try:
        clock = FakeClock(NOW)
        for step in steps:
            clock.now = NOW + step
            paper_cycle(
                con, UsdcScenarioClient(), config, costs=Costs(), model=FakeModel(), clock=clock
            )
    finally:
        con.close()


def deployed(path: Path, *steps: int) -> None:
    """Rounds of Spec 021 until NOW, then the release of Spec 022 (default: for a window and a
    second)."""
    env = {USDC_FIELD: "trend-usdc", "BOT_BUYHOLD": "1"}
    rounds(path, load_config({**EURO, **env}), 0)
    rounds(path, load_config({**OURS, **env, "BOT_BUYHOLD": "0"}), *(steps or (60, WINDOW + 1)))


def view_at(path: Path, at: float) -> overview.Overview:
    with reading(path) as con:
        return overview.load(con, at, SHARES)


EURO_SET = {"trend", "gpt-luna", BUYHOLD}
USDC_SET = {"trend-usdc", "gpt-luna-usdc", BUYHOLD_USDC}


def test_switched_off_euro_depots_leave_within_five_minutes(tmp_path: Path) -> None:
    assert WINDOW == 300
    path = tmp_path / "bot.db"
    deployed(path, 60)
    # A minute after the deploy the euro depots are still shown (their last round is recent).
    assert {d.agent for d in view_at(path, NOW + 65).depots} == EURO_SET | USDC_SET
    deployed(path, WINDOW + 1)
    view = view_at(path, NOW + WINDOW + 5)
    assert {d.agent for d in view.depots} == USDC_SET
    assert {s.agent for s in view.series} == USDC_SET
    assert {d.agent for d in view.decisions} <= USDC_SET and view.decisions
    assert {t.agent for t in view.trades} <= USDC_SET and view.trades
    assert view.reference is not None and view.reference.agent == BUYHOLD_USDC
    items = dict(ticker(view))
    assert "Buy & Hold USDC" in items and "Buy & Hold" not in items
    html = overview_page(view, csrf="x", now=NOW + WINDOW + 5)
    for agent in EURO_SET:
        assert f"<b>{agent}</b>" not in html, agent
    # Still in the database (until the purge, Spec 022 AK8), just not shown.
    with reading(path) as con:
        rows = {d.agent: d.active for d in overview.depots(con)}
    assert {a for a, active in rows.items() if not active} == EURO_SET


def test_a_depot_valued_in_the_last_window_is_still_shown(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    rounds(path, load_config({**EURO, USDC_FIELD: "trend-usdc"}), 0)
    rounds(path, load_config({**OURS, USDC_FIELD: "trend-usdc"}), WINDOW)
    # Exactly the window behind the newest round: still shown; a second more and it is gone.
    assert {d.agent for d in view_at(path, NOW + WINDOW + 5).depots} == {"trend", "gpt-luna"} | (
        USDC_SET
    )
    rounds(path, load_config({**OURS, USDC_FIELD: "trend-usdc"}), WINDOW + 1)
    assert {d.agent for d in view_at(path, NOW + WINDOW + 5).depots} == USDC_SET


def test_gpt_costs_list_only_active_depots(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    deployed(path)
    with reading(path) as con:
        everything = {r.agent for r in overview.llm_today(con, NOW + WINDOW + 5, SHARES)}
    assert everything == {"gpt-luna", "gpt-luna-usdc"}  # both asked GPT today
    view = view_at(path, NOW + WINDOW + 5)
    assert [r.agent for r in view.llm] == ["gpt-luna-usdc"]
    assert sum(r.share for r in view.llm) == Decimal("0.50")  # the budget shown at the top


def test_overview_takes_the_usdc_shares_from_the_euro_twin_fields(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "secrets.env").write_text("LLM_DAILY_BUDGET_EUR_GPT_LUNA=0.20\n", encoding="utf-8")
    open_db(data / "bot.db", agents=("gpt-luna-usdc", "gpt-sol-usdc", BUYHOLD_USDC)).close()
    app = WebApp(data, public_host="bot.example.test", wall_clock=lambda: NOW)
    app.passwords.set("ein-langes-Passwort-2026")
    app.setup_code = None
    response = app.handle("GET", "/", {}, {}, Access(public=True, client="203.0.113.10"))
    assert isinstance(response, Response) and response.status == 200
    assert "0.0000 € von 0.20 €" in response.body  # gpt-luna-usdc: the field of gpt-luna
    assert "0.0000 € von 2.50 €" in response.body  # gpt-sol-usdc: ADR-0012 default
    assert "<b>gpt-luna-usdc</b>" in response.body


def test_a_quote_outage_longer_than_the_window_hides_no_depot(tmp_path: Path) -> None:
    """A GPT USDC depot holding BTC: without BTC-USDC quotes it gets no point, however long the
    outage lasts, and stays shown; once the quote is back and it still gets none, it leaves."""
    path = tmp_path / "bot.db"
    open_db(path, agents=("gpt-luna-usdc", "trend-usdc")).close()
    late = 10 * WINDOW
    con = sqlite3.connect(path)
    try:
        with con:
            con.executemany(
                "INSERT INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,1e4,1e4,0,0)",
                [
                    (iso(NOW), "gpt-luna-usdc"),
                    (iso(NOW), "trend-usdc"),
                    (iso(NOW + late), "trend-usdc"),  # holds nothing, valued every round
                ],
            )
            con.execute("UPDATE portfolios SET btc=0.01 WHERE agent='gpt-luna-usdc'")
            con.executemany(
                "INSERT INTO market(symbol,ts,bar_ts,bid,ask,last_close,source)"
                " VALUES (?,?,0,1,1,1,'TEST')",
                [
                    (Symbol.BTCUSDC.name, iso(NOW)),
                    (Symbol.USDCEUR.name, iso(NOW + late)),
                    (Symbol.ETHEUR.name, iso(NOW + late)),
                ],
            )
    finally:
        con.close()
    assert {d.agent for d in view_at(path, NOW + late + 5).depots} == {
        "gpt-luna-usdc",
        "trend-usdc",
    }
    con = sqlite3.connect(path)
    with con:
        con.execute(
            "UPDATE market SET ts=? WHERE symbol=?", (iso(NOW + WINDOW + 1), Symbol.BTCUSDC.name)
        )
    con.close()
    assert {d.agent for d in view_at(path, NOW + late + 5).depots} == {"trend-usdc"}
