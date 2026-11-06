"""Rendering of the decorated pages: odd stored values must not break the page or the gauge."""

import re
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from llm_trading_bot.web import pages
from llm_trading_bot.web.overview import Overview


def _widths(page: str) -> list[float]:
    gauge = page[page.index('<svg class="gauge"') :]
    gauge = gauge[: gauge.index("</svg>")]
    return [
        float(w)
        for w in re.findall(
            r'<rect (?:class="(?:luna|sol)"|fill="url\(#hatch\)")[^>]*width="([0-9.]+)"', gauge
        )
    ]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {
            "LLM_DAILY_BUDGET_EUR": "1",
            "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "2",
            "LLM_DAILY_BUDGET_EUR_GPT_SOL": "3",
        },
        {
            "LLM_DAILY_BUDGET_EUR": "abc",
            "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "NaN",
            "LLM_DAILY_BUDGET_EUR_GPT_SOL": "-1",
        },
        {
            "LLM_DAILY_BUDGET_EUR": "0",
            "LLM_DAILY_BUDGET_EUR_GPT_LUNA": "0",
            "LLM_DAILY_BUDGET_EUR_GPT_SOL": "0",
        },
    ],
    ids=["defaults", "shares-above-total", "garbage", "zero"],
)
def test_budget_gauge_stays_inside_its_bar(values: dict[str, str]) -> None:
    page = pages.settings_page(values, csrf="t")
    widths = _widths(page)
    assert len(widths) == 3 and all(w >= 0 for w in widths) and sum(widths) <= 600.5


def test_default_gauge_shows_the_adr_0012_split() -> None:
    luna, sol, free = _widths(pages.settings_page({}, csrf="t"))
    assert (luna + 1, sol, free) == (100.0, 500.0, 0.0)


@pytest.mark.parametrize("render", [pages.login_page, pages.setup_page])
def test_public_pages_carry_no_inline_style_or_script(render: Callable[..., str]) -> None:
    page = render(csrf="t")
    assert "style=" not in page and "<script" not in page and "<style" not in page


def _cost_rows(page: str) -> dict[str, list[str]]:
    table = page[page.index('<table class="costs"') : page.index("</table>")]
    rows = re.findall(r"<tr><th>([^<]+)</th>((?:<td>[^<]*</td>)+)</tr>", table)
    return {label: re.findall(r"<td>([^<]*)</td>", cells) for label, cells in rows}


def test_reasoning_panel_shows_what_tokens_cost_per_model() -> None:
    rows = _cost_rows(pages.settings_page({}, csrf="t"))
    assert rows["je 1.000 Reasoning-Tokens"] == ["0.0005 €", "0.0100 €"]
    assert rows["Reasoning-Tokens gemessen bei low"] == ["89", "0"]
    assert rows["gemessen bei low je Anfrage"] == ["0.00034 €", "0.00572 €"]
    # Worst case: measured input plus the whole output allowance at the output price.
    assert rows["Obergrenze je Anfrage (8.000 Output-Tokens)"] == ["0.0042 €", "0.0831 €"]
    assert rows["bei low, 96 Anfragen/Tag (Maximum)"] == ["0.03 €", "0.55 €"]
    # 96 worst-case requests would exceed sol's 2.50 EUR share: the budget is the real limit.
    assert rows["Obergrenze pro Tag (96 Anfragen)"] == ["0.40 €", "2.50 € (Budget)"]


def test_unmeasured_effort_is_marked_as_not_measured() -> None:
    page = pages.settings_page({"LLM_EFFORT": "high"}, csrf="t")
    assert "„high“ ist nicht gemessen" in page
    assert "Gemessen ist nur diese Stufe." in pages.settings_page({}, csrf="t")


def test_a_smaller_budget_caps_the_daily_worst_case() -> None:
    page = pages.settings_page({"LLM_DAILY_BUDGET_EUR_GPT_LUNA": "0.10"}, csrf="t")
    assert _cost_rows(page)["Obergrenze pro Tag (96 Anfragen)"][0] == "0.10 € (Budget)"


# ------------------------------------------------------------------ overview (Spec 007)


def _view(**changes: object) -> Overview:
    from llm_trading_bot.web.overview import (
        DecisionRow,
        DepotRow,
        LlmToday,
        Overview,
        Pulse,
        Series,
        TradeRow,
    )

    base = Overview(
        depots=[
            DepotRow(
                "gpt-luna",
                10_100.0,
                7_000.0,
                0.01,
                0.5,
                "2026-11-06T12:00:00+00:00",
                False,
                100.0,
                1.0,
                1.5,
            ),
            DepotRow(
                "buyhold",
                9_950.0,
                900.0,
                0.06,
                2.0,
                "2026-11-06T12:00:00+00:00",
                False,
                -50.0,
                -0.5,
                None,
            ),
        ],
        series=[
            Series("gpt-luna", ((1_000.0, 10_000.0), (2_000.0, 10_100.0))),
            Series("buyhold", ((1_000.0, 10_000.0), (2_000.0, 9_950.0))),
        ],
        pulses=[Pulse("Letzter Zyklus", "2026-11-06T12:00:00+00:00", 30.0)],
        decisions=[
            DecisionRow(
                "gpt-luna",
                "BTC-EUR",
                "2026-11-06T12:00:00+00:00",
                "BUY",
                0.25,
                0.6,
                "OK",
                "<script>alert(1)</script> & mehr",
            ),
        ],
        llm=[
            LlmToday(
                "gpt-luna",
                2,
                Decimal("0.0007"),
                Decimal("0.50"),
                False,
                3000,
                700,
                180,
                "OK",
                None,
                "2026-11-06T12:00:00+00:00",
            ),
        ],
        trades=[
            TradeRow(
                "2026-11-06T12:00:00+00:00",
                "gpt-luna",
                "BTC-EUR",
                "BUY",
                0.01,
                60_000.0,
                600.0,
                1.5,
            ),
        ],
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_reason_html_is_escaped() -> None:
    from llm_trading_bot.web.overview_page import overview_page

    page = overview_page(_view(), csrf="t")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; mehr" in page
    assert "style=" not in page and "<script" not in page


def test_overview_refreshes_but_settings_do_not() -> None:
    from llm_trading_bot.web.overview_page import REFRESH_SECONDS, overview_page

    page = overview_page(_view(), csrf="t")
    assert f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' in page
    assert "http-equiv" not in pages.settings_page({}, csrf="t")
    assert "http-equiv" not in pages.login_page(csrf="t")


def test_navigation_marks_the_active_tab_and_only_after_login() -> None:
    from llm_trading_bot.web.overview_page import overview_page

    home = overview_page(_view(), csrf="t")
    settings = pages.settings_page({}, csrf="t")
    assert '<a href="/" class="active" aria-current="page">Übersicht</a>' in home
    assert '<a href="/settings">Einstellungen</a>' in home
    assert '<a href="/settings" class="active" aria-current="page">Einstellungen</a>' in settings
    assert '<a class="brand" href="/">' in home
    assert 'class="tabs"' not in pages.login_page(csrf="t")


def test_equity_chart_draws_one_line_per_depot_and_the_start_line() -> None:
    from llm_trading_bot.web.overview_page import equity_chart

    svg = equity_chart(_view().series)
    assert svg.count('<polyline class="eq-line') == 2
    assert 'class="eq-line bench"' in svg and 'class="eq-line c0"' in svg
    assert 'class="eq-start"' in svg and "echte Paper-Daten" in svg
    # Every point lies inside the plot area.
    for x, y in re.findall(r"([0-9.]+),([0-9.]+)", " ".join(re.findall(r'points="([^"]+)"', svg))):
        assert 64 <= float(x) <= 788 and 16 <= float(y) <= 222


def test_equity_chart_needs_two_moments() -> None:
    from llm_trading_bot.web.overview import Series
    from llm_trading_bot.web.overview_page import equity_chart

    assert "Noch kein Verlauf" in equity_chart([])
    assert "Noch kein Verlauf" in equity_chart([Series("a", ((5.0, 10_000.0),))])


def test_overview_without_data_explains_why() -> None:
    from llm_trading_bot.web.overview_page import overview_page

    page = overview_page(None, csrf="t", problem="Noch keine Daten <b>")
    assert "Noch keine Daten &lt;b&gt;" in page and "ADR-0016" in page


def test_overview_shows_comparison_and_halt_flags() -> None:
    from llm_trading_bot.web.overview_page import overview_page

    page = overview_page(_view(), csrf="t")
    assert "+1.50 pp" in page and "Referenz" in page
    assert "+100.00 € (+1.00 %)" in page
    assert "0.0007 € von 0.50 €" in page


def test_overview_reloads_several_times_per_service_round() -> None:
    # At 60 s the reload ran in lockstep with the 60-s rounds: every load showed the same age.
    from llm_trading_bot.service.schedule import PERIOD
    from llm_trading_bot.web.overview_page import REFRESH_SECONDS

    assert 0 < REFRESH_SECONDS <= PERIOD / 4


def test_readouts_take_the_last_cycle_by_label_not_from_the_service_pulse() -> None:
    from llm_trading_bot.web.overview import Pulse
    from llm_trading_bot.web.overview_page import overview_page

    service = Pulse("Dienst", "2026-11-06T11:00:00+00:00", 3_600.0, "steht", "beendet")
    cycle = Pulse("Letzter Zyklus", "2026-11-06T12:00:00+00:00", 30.0)
    page = overview_page(_view(pulses=[service, cycle]), csrf="t")
    # Readouts are <small>label</small><b>value</b>; the pulse rows use <span>.
    assert "<small>Letzter Zyklus</small><b>vor 30 s</b>" in page
    # The bot readout follows the service, which knows more than the last equity point.
    assert '<small>Bot</small><b class="off">steht</b>' in page


def test_best_depot_is_chosen_among_the_depots_of_the_newest_round() -> None:
    from llm_trading_bot.web.overview import DepotRow
    from llm_trading_bot.web.overview_page import overview_page

    old = "2026-11-01T12:00:00+00:00"  # deselected since: last value is days old
    stale = DepotRow("trend", 12_000.0, 12_000.0, 0, 0, old, False, 2_000.0, 20.0, 20.5)
    base = _view()
    page = overview_page(replace(base, depots=[stale, *base.depots]), csrf="t")
    assert "<small>Bestes Depot</small><b>gpt-luna +1.00 %</b>" in page


def test_pulse_times_show_seconds() -> None:
    from llm_trading_bot.web.overview_page import overview_page

    page = overview_page(_view(), csrf="t")
    assert "2026-11-06 12:00:00 UTC · vor 30 s" in page


def _track(page: str) -> str:
    start = page.index('<div class="ticker-track')
    return page[start : page.index("</div>", start)]


def test_overview_ticker_shows_quotes_and_depots_not_fixed_hints() -> None:
    from llm_trading_bot.web.overview import Quote
    from llm_trading_bot.web.overview_page import NBSP as N
    from llm_trading_bot.web.overview_page import overview_page

    btc = Quote("BTC-EUR", "2026-11-06T12:00:05+00:00", 60_000.0, 60_050.0, 60_010.0, "bitvavo")
    track = _track(overview_page(_view(quotes=[btc]), csrf="t"))
    assert f"<b>BTC-EUR</b>60{N}025.00 €" in track
    assert f"<b>BTC Geld / Brief</b>60{N}000.00 / 60{N}050.00" in track
    assert "<b>BTC Spread</b>0.083 %" in track and f"<b>BTC Kerzenschluss</b>60{N}010.00 €" in track
    assert "<b>Kurse</b>2026-11-06 12:00:05 UTC · bitvavo" in track
    assert f"<b>gpt-luna</b>10{N}100.00 € · +1.00 %" in track
    assert f"<b>Buy &amp; Hold</b>9{N}950.00 € · -0.50 %" in track
    assert "Fail closed" not in track


def test_ticker_without_data_keeps_the_fixed_hints() -> None:
    from llm_trading_bot.web.overview_page import overview_page, ticker

    assert ticker(_view(depots=[], quotes=[])) == list(pages.TICKER)
    assert "Fail closed" in _track(overview_page(None, csrf="t", problem="x"))


def test_ticker_continues_across_reloads_from_the_wall_clock() -> None:
    from llm_trading_bot.web.overview_page import overview_page
    from llm_trading_bot.web.style import CSS, TICKER_SECONDS

    assert 'class="ticker-track tp-5"' in overview_page(
        _view(), csrf="t", now=TICKER_SECONDS * 7 + 5.9
    )
    assert 'class="ticker-track"' in overview_page(_view(), csrf="t")
    for n in (0, TICKER_SECONDS - 1):
        assert f".ticker-track.tp-{n} {{ animation-delay: -{n}s; }}" in CSS
    assert f".ticker-track.tp-{TICKER_SECONDS} " not in CSS
    assert f"animation: ticker {TICKER_SECONDS}s linear infinite" in CSS


def test_only_the_reloading_overview_drops_its_entrance_animation() -> None:
    from llm_trading_bot.web.overview_page import overview_page
    from llm_trading_bot.web.style import CSS

    assert '<body class="live">' in overview_page(_view(), csrf="t")
    assert '<body class="live">' not in pages.settings_page({}, csrf="t")
    assert ".live .panel, .live .readout, .live .msg { animation: none; }" in CSS
