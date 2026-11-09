"""Spec 021 AK5-AK8 (ADR-0031): the overview shows only active depots, measures "vs." against
Buy & Hold USDC since its start (else Buy & Hold, else nothing) and never offers buy & hold."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import FakeClock, UsdcScenarioClient
from golden_scenarios import NOW
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.llm.due import iso
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.usdc import BUYHOLD_USDC
from llm_trading_bot.web import overview
from llm_trading_bot.web.app import Access, Response, WebApp
from llm_trading_bot.web.overview_page import overview_page, ticker, when
from llm_trading_bot.web.pages import TICKER, settings_page
from llm_trading_bot.web.readonly import reading

SHARES: dict[str, Decimal] = {}
T0 = int(NOW)
WINDOW = overview.ACTIVE_SECONDS
"""Spec 022: five minutes (an hour in Spec 021); the times below are fractions of it."""


def history(path: Path, points: dict[str, list[tuple[int, float]]]) -> None:
    """Equity points (seconds after T0, value) per depot, written as the bot stores them."""
    open_db(path, agents=tuple(points)).close()
    con = sqlite3.connect(path)
    try:
        with con:
            con.executemany(
                "INSERT INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,?,?,0,0)",
                [(iso(T0 + t), a, v, v) for a, pts in points.items() for t, v in pts],
            )
    finally:
        con.close()


def view_of(path: Path, now: float | None = None) -> overview.Overview:
    with reading(path) as con:
        return overview.load(con, now or T0 + 7_200, SHARES)


# ------------------------------------------------------------------ AK5: active depots only


ON = BotConfig(("trend",), None, ("trend-usdc",), True)
OFF = BotConfig(("trend",), None, ("trend-usdc",))


def rounds(path: Path, config: BotConfig, *steps: int) -> None:
    con = open_db(path, agents=config.depots)
    try:
        clock = FakeClock(NOW)
        for step in steps:
            clock.now = NOW + step
            paper_cycle(con, UsdcScenarioClient(), config, costs=Costs(), clock=clock)
    finally:
        con.close()


@pytest.fixture
def stopped(tmp_path: Path) -> Path:
    """Buy & hold ran (and bought) until NOW, then the release of Spec 021 took over."""
    path = tmp_path / "bot.db"
    rounds(path, ON, 0)
    rounds(path, OFF, 60, WINDOW + 1)
    return path


def test_stopped_depot_leaves_every_list(stopped: Path) -> None:
    view = view_of(stopped, NOW + WINDOW + 5)
    assert [d.agent for d in view.depots] == ["trend", "trend-usdc", BUYHOLD_USDC]
    assert BUYHOLD not in {s.agent for s in view.series}
    assert BUYHOLD not in {d.agent for d in view.decisions} and view.decisions
    assert BUYHOLD not in {t.agent for t in view.trades}
    assert {t.agent for t in view.trades} == {"trend", "trend-usdc", BUYHOLD_USDC}
    assert "Buy & Hold" not in dict(ticker(view)) and "Buy & Hold USDC" in dict(ticker(view))
    html = overview_page(view, csrf="x", now=NOW + WINDOW + 5)
    assert "<b>buyhold</b>" not in html and ">buyhold " not in html
    # Its history stays in the database, untouched.
    with reading(stopped) as con:
        assert overview.depots(con)[-2].agent == BUYHOLD  # still there, just not shown
        assert not next(d for d in overview.depots(con) if d.agent == BUYHOLD).active
        assert con.execute("SELECT COUNT(*) FROM trades WHERE agent=?", (BUYHOLD,)).fetchone()[0]
        everything = overview.latest_decisions(con)
        assert BUYHOLD in {d.agent for d in everything}


def test_a_depot_stays_shown_for_the_active_window_after_its_last_valuation(
    tmp_path: Path,
) -> None:
    assert WINDOW == 300  # Spec 022 AK7 (an hour in Spec 021)
    trend = [(t, 10_000.0) for t in range(0, WINDOW + 1, 60)]
    path = tmp_path / "bot.db"
    history(path, {"trend": trend, "breakout": [(0, 10_000.0)], BUYHOLD: [(0, 10_000.0)]})
    view = view_of(path)
    # Exactly ACTIVE_SECONDS older than the newest point: still shown (a held market without a
    # fresh quote costs a depot some points, which must not hide it).
    assert [d.agent for d in view.depots] == ["breakout", "trend", BUYHOLD]
    assert {s.agent for s in view.series} == {"breakout", "trend", BUYHOLD}
    con = sqlite3.connect(path)
    with con:
        con.execute(
            "INSERT INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,'trend',1e4,1e4,0,0)",
            (iso(T0 + WINDOW + 1),),
        )
    con.close()
    view = view_of(path)
    assert [d.agent for d in view.depots] == ["trend"]
    assert [s.agent for s in view.series] == ["trend"]


def test_a_long_quote_outage_of_one_market_hides_no_depot(tmp_path: Path) -> None:
    """Checker defect 1: without a fresh ETH quote the bot values no depot holding ETH (Spec 011
    AK8), while the others keep getting points. Measured against the newest point of the
    database alone, every depot holding ETH - the reference included - left after an hour."""
    on = BotConfig(("trend", "meanrev"), None, ("trend-usdc",), True)
    off = BotConfig(("trend", "meanrev"), None, ("trend-usdc",))
    stopped = tmp_path / "bot.db"
    rounds(stopped, on, 0)
    rounds(stopped, off, 60, WINDOW + 1)
    con = open_db(stopped, agents=off.depots)
    try:
        client = UsdcScenarioClient()
        client.bad_quotes = {Symbol.ETHEUR}
        clock = FakeClock(NOW)
        reports = []
        for step in (WINDOW + 120, 2 * WINDOW, 3 * WINDOW):
            clock.now = NOW + step
            reports.append(paper_cycle(con, client, off, costs=Costs(), clock=clock))
    finally:
        con.close()
    # The probe really is an outage: the ETH holders got no point in any of the rounds, while a
    # depot without ETH did (else the newest point of the database would not move on).
    holders = {u.agent for r in reports for u in r.unvalued if u.symbol is Symbol.ETHEUR}
    assert {BUYHOLD_USDC, "trend", "trend-usdc"} <= holders
    valued = {e.agent for r in reports for e in r.equity}
    assert valued == {"meanrev"}
    view = view_of(stopped, NOW + 3 * WINDOW + 5)
    assert [d.agent for d in view.depots] == ["meanrev", "trend", "trend-usdc", BUYHOLD_USDC]
    assert view.reference is not None and view.reference.agent == BUYHOLD_USDC
    rows = {d.agent: d for d in view.depots}
    assert rows["trend"].vs_buyhold_pp is not None
    assert BUYHOLD_USDC in {s.agent for s in view.series}
    # The switched-off depot also holds ETH, but its last point is older than the last ETH quote
    # by more than an hour: it stays hidden.
    with reading(stopped) as ro:
        assert not next(d for d in overview.depots(ro) if d.agent == BUYHOLD).active


def holdings(path: Path, held: dict[str, tuple[float, float]], quoted: dict[Symbol, int]) -> None:
    """Depot holdings (btc, eth) and the time of each market's latest stored quote."""
    con = sqlite3.connect(path)
    try:
        with con:
            con.executemany(
                "UPDATE portfolios SET btc=?, eth=? WHERE agent=?",
                [(btc, eth, a) for a, (btc, eth) in held.items()],
            )
            con.executemany(
                "INSERT INTO market(symbol,ts,bar_ts,bid,ask,last_close,source)"
                " VALUES (?,?,0,1,1,1,'TEST')",
                [(m.name, iso(T0 + t)) for m, t in quoted.items()],
            )
    finally:
        con.close()


def test_the_active_window_ends_at_the_last_quote_of_a_held_market(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    late = 2 * WINDOW
    seen = WINDOW // 6  # the last round of the depots that stopped getting points
    old = [(0, 10_000.0), (seen, 10_000.0)]
    history(
        path,
        {
            "meanrev": [(0, 10_000.0), (late, 10_000.0)],  # holds nothing, valued every round
            "trend": old,  # holds ETH: no ETH quote since seen
            "breakout": old,  # holds nothing: no excuse for missing rounds
            "ensemble": old,  # holds BTC only, which is quoted
            "trend-usdc": old,  # its BTC is priced over BTC-USDC, never quoted
            "meanrev-usdc": old,  # holds ETH, like trend
        },
    )
    holdings(
        path,
        {
            "trend": (0.0, 0.1),
            "ensemble": (0.1, 0.0),
            "trend-usdc": (0.1, 0.0),
            "meanrev-usdc": (0.0, 0.1),
        },
        {Symbol.BTCEUR: late, Symbol.ETHEUR: seen, Symbol.USDCEUR: late},
    )
    view = view_of(path, T0 + late + 5)
    assert [d.agent for d in view.depots] == ["meanrev", "meanrev-usdc", "trend", "trend-usdc"]
    # The bound is the older of the newest point and the held market's last quote, a window back.
    holdings(path, {}, {})
    con = sqlite3.connect(path)
    with con:
        con.execute("UPDATE market SET ts=? WHERE symbol='ETHEUR'", (iso(T0 + seen + 1 + WINDOW),))
    con.close()
    view = view_of(path, T0 + late + 5)
    assert [d.agent for d in view.depots] == ["meanrev", "trend-usdc"]


def test_before_the_first_round_every_depot_is_shown(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_db(path, agents=("trend", "trend-usdc", BUYHOLD_USDC)).close()
    view = view_of(path)
    assert [d.agent for d in view.depots] == ["trend", "trend-usdc", BUYHOLD_USDC]
    assert all(d.value == 10_000.0 and d.vs_buyhold_pp is None for d in view.depots)
    assert view.reference is None


def test_public_view_hides_the_stopped_depot(stopped: Path) -> None:
    data = stopped.parent
    app = WebApp(
        data,
        public_host="bot.example.test",
        key_check=lambda key: "",
        clock=lambda: 1_000.0,
        wall_clock=lambda: NOW + WINDOW + 5,
    )
    app.passwords.set("ein-langes-Passwort-2026")
    app.setup_code = None
    response = app.handle("GET", "/", {}, {}, Access(public=True, client="203.0.113.10"))
    assert isinstance(response, Response) and response.status == 200
    page = response.body
    assert "<b>trend-usdc</b>" in page and "<b>buyhold-usdc</b>" in page
    assert "<b>buyhold</b>" not in page and "Buy &amp; Hold</b>" not in page


# ------------------------------------------------------------------ AK6/AK7: the reference


def depot_rows(path: Path) -> dict[str, overview.DepotRow]:
    return {d.agent: d for d in view_of(path).depots}


def test_vs_is_measured_against_buyhold_usdc_since_its_start(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    history(
        path,
        {
            # +3 % from the reference's start (600 s) on; +4.03 % from the start cash.
            "trend": [(0, 10_000.0), (600, 10_100.0), (1_200, 10_403.0)],
            # No point at 600 s: the one right before it counts.
            "meanrev": [(0, 10_000.0), (1_200, 10_200.0)],
            # The reference: +2 % since its first point (after its purchase fees).
            BUYHOLD_USDC: [(600, 9_980.0), (1_200, 10_179.6)],
        },
    )
    rows = depot_rows(path)
    assert rows[BUYHOLD_USDC].vs_buyhold_pp is None  # the reference itself
    assert rows["trend"].vs_buyhold_pp == pytest.approx(3.0 - 2.0)
    assert rows["meanrev"].vs_buyhold_pp == pytest.approx(2.0 - 2.0)
    ref = view_of(path).reference
    assert ref == overview.Reference(BUYHOLD_USDC, iso(T0 + 600), 9_980.0)


def test_depot_younger_than_the_reference_shows_a_dash(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    history(
        path,
        {
            "trend": [(0, 10_000.0), (1_200, 10_100.0)],
            "breakout": [(900, 10_000.0), (1_200, 10_500.0)],  # started after the reference
            BUYHOLD_USDC: [(600, 9_980.0), (1_200, 9_990.0)],
        },
    )
    rows = depot_rows(path)
    assert rows["breakout"].vs_buyhold_pp is None
    assert rows["trend"].vs_buyhold_pp == pytest.approx(1.0 - (9_990 / 9_980 - 1) * 100)
    html = overview_page(view_of(path), csrf="x", now=T0 + 1_300)
    row = html.split("<td><b>breakout</b>")[1].split("</tr>")[0]
    assert "<td>–</td>" in row and " pp" not in row


def test_reference_falls_back_to_buyhold_then_to_none(tmp_path: Path) -> None:
    late = WINDOW + 900
    both = tmp_path / "both.db"
    history(
        both,
        {
            "trend": [(0, 10_000.0), (600, 10_000.0), (late, 10_300.0)],
            BUYHOLD: [(0, 9_950.0), (late, 10_049.5)],  # +1 %
            BUYHOLD_USDC: [(600, 9_980.0), (late, 10_179.6)],  # +2 %
        },
    )
    rows = depot_rows(both)
    # Both active: Buy & Hold USDC is preferred, Buy & Hold is compared like any other depot.
    assert view_of(both).reference is not None
    assert rows[BUYHOLD_USDC].vs_buyhold_pp is None
    assert rows["trend"].vs_buyhold_pp == pytest.approx(3.0 - 2.0)
    assert rows[BUYHOLD].vs_buyhold_pp == pytest.approx((10_049.5 / 9_950 - 1) * 100 - 2.0)

    fallback = tmp_path / "fallback.db"
    history(
        fallback,
        {
            "trend": [(0, 10_000.0), (late, 10_300.0)],
            BUYHOLD: [(0, 9_950.0), (late, 10_049.5)],
            BUYHOLD_USDC: [(600, 9_980.0)],  # USDC depots switched off over an hour ago
        },
    )
    view = view_of(fallback)
    assert view.reference == overview.Reference(BUYHOLD, iso(T0), 9_950.0)
    rows = {d.agent: d for d in view.depots}
    assert BUYHOLD_USDC not in rows and rows[BUYHOLD].vs_buyhold_pp is None
    assert rows["trend"].vs_buyhold_pp == pytest.approx(3.0 - 1.0)
    html = overview_page(view, csrf="x", now=T0 + late)
    assert "vs. Buy &amp; Hold <small>(seit dessen Start)</small>" in html

    none = tmp_path / "none.db"
    history(none, {"trend": [(0, 10_000.0), (late, 10_300.0)], BUYHOLD: [(0, 9_950.0)]})
    view = view_of(none)
    assert view.reference is None
    assert [(d.agent, d.vs_buyhold_pp) for d in view.depots] == [("trend", None)]
    html = overview_page(view, csrf="x", now=T0 + late)
    assert "<th>vs. Buy &amp; Hold</th>" in html and "gerade läuft keine Referenz" in html


def test_header_and_footnote_name_the_reference_and_its_start(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    history(
        path,
        {
            "trend": [(0, 10_000.0), (WINDOW // 3, 10_100.0)],
            BUYHOLD_USDC: [(WINDOW // 6, 9_980.0)],
        },
    )
    stamp = when(iso(T0 + WINDOW // 6))
    for public in (False, True):
        html = overview_page(view_of(path), csrf=None if public else "x", public=public)
        assert "<th>vs. Buy &amp; Hold USDC <small>(seit dessen Start)</small></th>" in html
        assert f"pp = Prozentpunkte gegenüber Buy &amp; Hold USDC seit dessen Start ({stamp})" in (
            html
        )


# ------------------------------------------------------------------ AK8: settings


def test_settings_never_offer_buyhold() -> None:
    html = settings_page({"BOT_AGENTS": "gpt-luna,trend"}, csrf="x")
    assert 'value="buyhold"' not in html and 'value="buyhold-usdc"' not in html
    assert "Buy &amp; Hold USDC läuft als Referenz mit" in html
    assert "Buy &amp; Hold läuft immer" not in html
    assert ("Buy & Hold USDC", "Referenz") in TICKER and ("Buy & Hold", "Referenz") not in TICKER
