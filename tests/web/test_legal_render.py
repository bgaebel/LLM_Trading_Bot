"""Spec 014 AK4: the mini markup of Impressum and privacy policy, and hostile input."""

import re

import pytest

from llm_trading_bot.web.legal import render


def test_render_headings_lists_and_paragraphs() -> None:
    text = "# Impressum\n\n## Kontakt\n\nZeile eins\nZeile zwei\n\n- erstens\n- zweitens\n"
    assert render(text) == (
        "<h1>Impressum</h1><h2>Kontakt</h2><p>Zeile eins<br>Zeile zwei</p>"
        "<ul><li>erstens</li><li>zweitens</li></ul>"
    )


def test_render_heading_list_and_text_in_one_block() -> None:
    """Without blank lines in between, each line still keeps its kind."""
    text = "## Cookies\nVorspann\n- a\n- b\nNachsatz"
    assert render(text) == (
        "<h2>Cookies</h2><p>Vorspann</p><ul><li>a</li><li>b</li></ul><p>Nachsatz</p>"
    )


def test_render_blank_lines_with_spaces_separate_blocks() -> None:
    assert render("eins\n   \n\n\nzwei") == "<p>eins</p><p>zwei</p>"
    assert render("") == ""
    assert render("\n\n") == ""


def test_render_marker_without_text_is_plain_text() -> None:
    assert render("#\n\n##\n\n-") == "<p>#</p><p>##</p><p>-</p>"
    assert render("#Kein Titel") == "<p>#Kein Titel</p>"
    assert render("### drei") == "<p>### drei</p>"


def test_render_drops_bom_and_reads_crlf_like_lf() -> None:
    lf = "# Titel\n\nZeile 1\nZeile 2\n\n- x\n"
    assert render("﻿" + lf.replace("\n", "\r\n")) == render(lf)
    assert "﻿" not in render("﻿Text")
    assert render("a\rb") == "<p>a<br>b</p>"


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        '<meta http-equiv="refresh" content="0;url=https://evil.example">',
        '<img src=x onerror="alert(1)">',
        "<style>body{display:none}</style>",
        '<a href="javascript:alert(1)">x</a>',
        "# <script>alert(1)</script>",
        "- <iframe src=//evil.example>",
    ],
)
def test_render_escapes_markup(hostile: str) -> None:
    html = render(hostile)
    assert "<script" not in html and "<meta" not in html and "<img" not in html
    assert "<style" not in html and "<iframe" not in html and "<a " not in html
    assert "&lt;" in html


def test_render_quote_cannot_open_an_attribute() -> None:
    html = render('a"onmouseover=x@y.de')
    assert '"onmouseover' not in html
    assert "a&quot;onmouseover=" in html
    # Only the address itself becomes the link; the attribute holds nothing else.
    assert '<a href="mailto:x@y.de">x@y.de</a>' in html
    assert re.findall(r'href="([^"]*)"', html) == ["mailto:x@y.de"]


def test_render_single_quote_is_escaped() -> None:
    assert "&#x27;" in render("it's")


def test_render_makes_only_mailto_links() -> None:
    html = render(
        "javascript:alert(1) https://evil.example/ www.evil.example data:text/html,x "
        "kontakt@example.test"
    )
    assert re.findall(r'href="([^"]*)"', html) == ["mailto:kontakt@example.test"]
    assert "javascript:alert(1)" in html  # shown as text, never as a link


def test_render_mailto_link_carries_no_parameters() -> None:
    html = render("a@b.de?subject=x&body=y kontakt@example.test?cc=evil@x.de")
    hrefs = re.findall(r'href="([^"]*)"', html)
    assert hrefs == ["mailto:a@b.de", "mailto:kontakt@example.test", "mailto:evil@x.de"]
    assert all("?" not in h and "&" not in h for h in hrefs)


def test_render_scheme_before_an_address_stays_text() -> None:
    html = render("javascript:x@y.de")
    assert html == '<p>javascript:<a href="mailto:x@y.de">x@y.de</a></p>'


def test_render_ampersand_is_escaped_once() -> None:
    html = render("Tom & Jerry <b>")
    assert html == "<p>Tom &amp; Jerry &lt;b&gt;</p>"
    assert "&amp;amp;" not in html
    # A written entity is shown as written, not interpreted.
    assert render("&lt;") == "<p>&amp;lt;</p>"


def test_render_address_with_ampersand_next_to_it() -> None:
    html = render("a&b@x.de")
    assert html == '<p>a&amp;<a href="mailto:b@x.de">b@x.de</a></p>'


def test_render_no_address_without_a_top_level_domain() -> None:
    assert "<a " not in render("user@localhost und a@b.c")
