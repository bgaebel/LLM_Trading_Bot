"""Rendering of the decorated pages: odd stored values must not break the page or the gauge."""

import re
from collections.abc import Callable

import pytest

from llm_trading_bot.web import pages


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
