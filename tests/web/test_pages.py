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
