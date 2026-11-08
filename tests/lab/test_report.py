"""Spec 016 AK10: the base report (Markdown + standalone HTML), never overwritten."""

import html
import io
import re
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from llm_trading_bot.lab import metrics, report
from llm_trading_bot.lab.archive import Archive
from llm_trading_bot.lab.model import (
    MARKETS,
    DayPoint,
    DepotResult,
    FidelitySummary,
    MarketQuality,
    Quarter,
    QuarterResult,
    Trade,
    Variant,
)
from llm_trading_bot.lab.paths import CHAIN_FILE, REPORTS_DIR, RUNS_FILE
from llm_trading_bot.lab.register import CodeState, Register
from llm_trading_bot.lab.report import (
    ReportExists,
    SpreadSource,
    build_report,
    cli_main,
    newest_results,
    round_trip_label,
    write_report,
)
from llm_trading_bot.lab.spread import DEFAULT_SPREAD, FLOOR, sample_hours
from llm_trading_bot.lab.spread import METHOD as SPREAD_METHOD

BTC = "BTC-EUR"
DAYS = 10
DRAWS = 200
"""Fewer bootstrap draws than the methodology's 5,000: tests check the wiring, not the bands."""
CREATED = 1_794_000_000.0
CODE = CodeState("c" * 40, True)


def _day(epoch: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def _depot(agent: str, q: Quarter, trips: int, drift: float) -> DepotResult:
    trades: list[Trade] = []
    for i in range(trips):
        ts = q.start + 3600 + i * 120
        ctx = {"regime": "TREND", "vol": float(i % 7), "direction": 1.0}
        trades.append(Trade(ts, agent, BTC, "BUY", 0.001, 30_000.0, 30.0, 0.075, ctx))
        trades.append(Trade(ts + 60, agent, BTC, "SELL", 0.001, 30_300.0, 30.3, 0.07575))
    value = 10_000.0
    points = []
    for i in range(DAYS):
        value *= 1 + drift * (1 if i % 3 else -2)
        points.append(DayPoint(_day(q.start + i * 86_400), value, value * 0.7, {BTC: 0.3}))
    return DepotResult(agent, points, trades, None, value, value * 0.998, {"HOLD/OK": DAYS})


def result(
    label: str,
    trips: Mapping[str, int],
    *,
    variant: Variant | None = None,
    share: float = 1.0,
    status: str = "ok",
    error: str = "",
    drift: float = 0.0005,
    base_mid: bool = True,
    fitted: bool = False,
) -> QuarterResult:
    q = Quarter.parse(label)
    if fitted:
        # Cut to the DAYS the depots carry, so the series covers the quarter: no data issue.
        q = Quarter(q.label, q.start, q.start + DAYS * 86_400)
    base = [(_day(q.start - 86_400), 100.0)] if base_mid else []
    mids = {
        m: base
        + [(_day(q.start + i * 86_400), 100.0 * (1 + 0.01 * (i % 4 - 1.5))) for i in range(DAYS)]
        for m in MARKETS
    }
    due = 1000
    decided = round(due * share)
    quality = {
        m: MarketQuality(m, due, decided, {"gap_1h": due - decided} if decided < due else {}, 120)
        for m in MARKETS
    }
    depots = (
        {a: _depot(a, q, n, drift * (k + 1)) for k, (a, n) in enumerate(trips.items())}
        if status == "ok"
        else {}
    )
    return QuarterResult(
        q,
        variant or Variant(),
        status,
        error,
        depots,
        quality if status == "ok" else {},
        mids if status == "ok" else {},
        {BTC: 0.001, "ETH-EUR": 0.0015} if status == "ok" else {},
        rounds=DAYS * 1440,
        seconds=12.5,
        dataset_sha256="ab" * 32,
    )


SMALL = {"trend": 3, "breakout": 2, "meanrev": 1, "ensemble": 4, "buyhold": 0}


def recordable(label: str, trips: Mapping[str, int], **kwargs: Any) -> QuarterResult:
    """A result the register accepts: its day check refuses the benchmark's base entry labelled
    the day before the quarter, and these tests only need the recording, not day 1's benchmark."""
    return result(label, trips, base_mid=False, **kwargs)


def build(
    results: Mapping[str, list[QuarterResult]],
    *,
    fidelity: FidelitySummary | None = None,
    trial_count: int = 7,
    config_count: int | None = None,
    spread_sources: Mapping[tuple[str, str], SpreadSource] | None = None,
) -> tuple[str, str]:
    return build_report(
        results,
        fidelity=fidelity,
        trial_count=trial_count,
        config_count=config_count,
        spread_sources=spread_sources,
        created_at=CREATED,
        commit="c" * 40,
        draws=DRAWS,
    )


def table_after(markdown: str, heading: str) -> list[list[str]]:
    """Rows of the first Markdown table after ``heading`` (without header and rule)."""
    lines = markdown.split("\n")
    start = lines.index(heading)
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("#") and rows:
            break
        if line.startswith("|---"):
            continue
        if line.startswith("| "):
            rows.append([c.strip() for c in line.strip("|").split(" | ")])
        elif rows:
            break
    return rows[1:]


def section(markdown: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    assert match, title
    return match.group(1)


# --- AK10 named tests ----------------------------------------------------------------------


def test_labels_by_round_trips() -> None:
    assert [round_trip_label(n) for n in (0, 99, 100, 300, 301)] == [
        "kein Urteil",
        "kein Urteil",
        "indikativ",
        "indikativ",
        "belastbar",
    ]
    trips = {"trend": 99, "breakout": 100, "meanrev": 300, "ensemble": 301, "buyhold": 0}
    markdown, html_text = build({"base": [result("2022Q1", trips)]})
    rows = table_after(markdown, "## Gesamt (gewertete Quartale zusammen)")
    by_depot = {r[0]: r for r in rows}
    assert {d: (r[-2], r[-1]) for d, r in by_depot.items()} == {
        "trend": ("99", "kein Urteil"),
        "breakout": ("100", "indikativ"),
        "meanrev": ("300", "indikativ"),
        "ensemble": ("301", "belastbar"),
        "Buy & Hold": ("0", "kein Urteil"),
    }
    # Every pooled number carries its band and the number of days behind it.
    trend = by_depot["trend"]
    assert all(" bis " in cell and "[" in cell for cell in (trend[1], trend[2], trend[4]))
    assert trend[3] in ("ja", "nein") and trend[5] == str(DAYS)
    summary = section(markdown, "Kurzfazit")
    assert "trend: Übertrendite" in summary and "Einstufung: kein Urteil" in summary
    assert "ensemble:" in summary and "Einstufung: belastbar" in summary
    assert "belastbar" in html_text and "indikativ" in html_text


def test_never_overwrites(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL))
        first = write_report(
            tmp_path, "run-1", "# eins\n", "<p>eins</p>\n", register=reg, fidelity_json="{}"
        )
        assert first.directory == tmp_path / REPORTS_DIR / "run-1"
        assert sorted(p.name for p in first.files) == [
            "fidelity.json",
            CHAIN_FILE,
            "report.html",
            "report.md",
        ]
        assert (first.directory / CHAIN_FILE).read_bytes() == (tmp_path / CHAIN_FILE).read_bytes()
        reg.record(recordable("2022Q2", SMALL))

        # The same run id again: refused, nothing in the old report changes.
        with pytest.raises(ReportExists):
            write_report(tmp_path, "run-1", "# zwei\n", "<p>zwei</p>\n", register=reg)
        assert (first.directory / "report.md").read_text(encoding="utf-8") == "# eins\n"
        assert (first.directory / "report.html").read_text(encoding="utf-8") == "<p>eins</p>\n"
        assert (first.directory / CHAIN_FILE).read_text(encoding="utf-8").count("\n") == 1

        # An existing empty directory is not reused either.
        (tmp_path / REPORTS_DIR / "run-2").mkdir()
        with pytest.raises(ReportExists):
            write_report(tmp_path, "run-2", "# zwei\n", "<p>zwei</p>\n")
        assert list((tmp_path / REPORTS_DIR / "run-2").iterdir()) == []

    for bad in ("../x", "a/b", "", ".hidden"):
        with pytest.raises(report.ReportError):
            write_report(tmp_path, bad, "x", "x")


# --- content -------------------------------------------------------------------------------


def test_sealed_period_never_shown() -> None:
    sealed = result("2025Q4", SMALL, drift=0.0123)
    markdown, html_text = build({"base": [result("2025Q3", SMALL), sealed]})
    for text in (markdown, html_text):
        assert "2025Q3" in text
        # The sealed quarter's days start on 2025-10-01 (named only in the seal notice).
        assert "2025Q4" not in text and "2025-10-02" not in text
    assert "1 Ergebnis(se) aus dem versiegelten Zeitraum" in section(markdown, "Kurzfazit")


def test_quarters_list_not_graded_with_reason() -> None:
    rows = [
        result("2021Q1", SMALL),
        result("2021Q2", SMALL, share=0.85),
        result("2021Q3", SMALL, status="no_lead"),
        result("2021Q4", SMALL, status="aborted", error="boom"),
        result("2022Q1", SMALL),
    ]
    markdown, _ = build({"base": rows})
    quarters = {r[0]: r for r in table_after(markdown, "## Quartale")}
    assert quarters["2022Q1"][1] == "gewertet" and quarters["2022Q1"][2] == ""
    assert quarters["2021Q1"][1] == "nicht gewertet" and "vor Q2 2021" in quarters["2021Q1"][2]
    assert "zu wenige Entscheidungen" in quarters["2021Q2"][2]
    assert "85,0 %" in quarters["2021Q2"][2] and quarters["2021Q2"][3] == "85,0 % (von 1.000)"
    assert "Datenvorlauf" in quarters["2021Q3"][2]
    assert quarters["2021Q4"][2] == "abgebrochen: boom"
    assert "Gewertet sind 1 von 5 Quartalen (2022Q1)" in section(markdown, "Kurzfazit")
    quality = section(markdown, "Datenqualität")
    assert "gap_1h: 150 (15,0 %)" in quality and "2,0" in quality  # 120 blocked rounds = 2 h


def test_html_is_standalone_and_escaped() -> None:
    rows = [result("2021Q4", SMALL, status="aborted", error="<script>alert(1)</script>")]
    rows.append(result("2022Q1", SMALL))
    markdown, html_text = build({"base": rows})
    assert html_text.startswith("<!doctype html>")
    lowered = html_text.lower()
    for forbidden in ("<script", "http://", "https://", "<link", " src=", "@import", "url("):
        assert forbidden not in lowered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
    assert "prefers-color-scheme:dark" in html_text and "color-scheme:light dark" in html_text
    # Both renderings carry the same sections.
    for title in (
        "Kurzfazit",
        "Gesamt (gewertete Quartale zusammen)",
        "Quartale",
        "Kosten",
        "Marktphasen",
        "Unsicherheit",
        "Datenqualität",
        "Diagnose: Was kosten die Sperrzeiten?",
        "Treue zum Live-Bot",
        "Annahmen",
        "Zahl der Versuche",
        "Laufzeit",
    ):
        assert f"## {title}\n" in markdown
        assert f">{title}</h2>" in html_text


def test_fidelity_shown_as_it_is() -> None:
    base = {"base": [result("2022Q1", SMALL)]}
    markdown, _ = build(base)
    assert "Treue nicht belegt" in section(markdown, "Treue zum Live-Bot")
    assert "Treue nicht belegt (kein Treue-Test beigefügt)" in section(markdown, "Kurzfazit")

    few = FidelitySummary(
        compared={BTC: 120, "ETH-EUR": 400},
        equal_action={BTC: 120, "ETH-EUR": 400},
        indicators_ok={BTC: 119, "ETH-EUR": 400},
        deviations=[
            {"market": BTC, "utc": "x", "agent": "trend", "field": "adx", "live": 1, "rebuilt": 2}
        ],
    )
    markdown, _ = build(base, fidelity=few)
    treue = section(markdown, "Treue zum Live-Bot")
    rows = {r[0]: r for r in table_after(markdown, "## Treue zum Live-Bot")}
    assert rows[BTC][1:] == [
        "120",
        "120 von 120",
        "119 von 120",
        "Treue nicht belegt (unter 300 Vergleiche)",
    ]
    assert rows["ETH-EUR"][-1] == "belegt"
    assert "100 %" not in treue and "ungeklärt" in treue


def test_costs_grid_and_diagnosis() -> None:
    cheap = Variant(name="cost-0.3", fee_rate=0.001, slippage=0.0005)
    gap = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    results = {
        "base": [result("2022Q1", SMALL)],
        "cost-0.3": [result("2022Q1", SMALL, variant=cheap, drift=0.0007)],
        "gap-tolerance": [result("2022Q1", SMALL, variant=gap, drift=0.0006)],
    }
    sources = {
        ("2022Q1", BTC): SpreadSource("sampled", 0.001, 26),
        ("2022Q1", "ETH-EUR"): SpreadSource("default", 0.0015),
    }
    markdown, _ = build(results, spread_sources=sources, trial_count=3, config_count=3)
    costs = section(markdown, "Kosten")
    assert "Entscheidungen reagieren auf Kosten" in costs
    grid = table_after(markdown, "### Kostenraster: Entscheidungen reagieren auf Kosten")
    assert [r[0] for r in grid] == ["base"] * 5 + ["cost-0.3"] * 5
    assert {r[1] for r in grid} == {"0,60 %", "0,30 %"}
    assert all("[" in r[3] and "[" in r[4] for r in grid)
    spreads = table_after(markdown, "### Spread-Annahmen je Quartal")
    assert spreads == [
        ["2022Q1", BTC, "0,100 %", "Stichprobe öffentlicher Trades", "26"],
        ["2022Q1", "ETH-EUR", "0,150 %", "vorsichtiger Standardwert", "-"],
    ]
    even = {
        r[0]: r for r in table_after(markdown, "### Kosten-Gleichstand bei festen Entscheidungen")
    }
    assert even["trend"][1] == "1" and even["Buy & Hold"][4] == "keine Trades"
    diagnosis = section(markdown, "Diagnose: Was kosten die Sperrzeiten?")
    assert "Diagnose, keine Strategieversion" in diagnosis and "gap-tolerance" in diagnosis
    assert "Lücken-Toleranz in höheren Intervallen: 3" in diagnosis
    # The diagnosis never shows up as a cost variant, the cost grid never as a diagnosis.
    assert "gap-tolerance" not in costs and "cost-0.3" not in diagnosis
    trials = section(markdown, "Zahl der Versuche")
    assert "3 Versuche" in trials and "aus 3 Konfigurationen" in trials


def test_without_base_run_still_reports() -> None:
    cheap = Variant(name="cost-0.3", fee_rate=0.001, slippage=0.0005)
    markdown, html_text = build({"cost-0.3": [result("2022Q1", SMALL, variant=cheap)]})
    assert "kein Basislauf" in section(markdown, "Kurzfazit")
    assert "</html>" in html_text


def test_number_format_is_german() -> None:
    assert report.num(1234.567) == "1.234,57"
    assert report.pct(-0.0123) == "-1,23 %" and report.pct(0.5, 0, sign=False) == "50 %"
    assert report.eur(None) == "-" and report.num(float("nan")) == "n/a"
    assert report.run_id_for(0, "ABCDEF1234") == "19700101T000000Z-abcdef1"


# --- register and CLI ----------------------------------------------------------------------


def test_newest_result_per_quarter_and_variant(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL, drift=0.001))
        reg.record(recordable("2022Q1", SMALL, drift=0.002))
        reg.record(recordable("2022Q2", SMALL))
        # A sealed quarter is recorded without any numbers (register rule, ADR-0026).
        reg.record(QuarterResult(Quarter.parse("2025Q4"), Variant(), "sealed"))
        results, used = newest_results(reg)
    assert list(results) == ["base"]
    assert [r.quarter.label for r in results["base"]] == ["2022Q1", "2022Q2"]
    # trend's first day falls by twice its drift: 0.2 % for the older run, 0.4 % for the newer.
    assert results["base"][0].depots["trend"].days[0].value == pytest.approx(9_960)
    assert [u.row.run_id for u in used] == [2, 3]


def test_cli_writes_report_from_register(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL))
        reg.record(recordable("2022Q2", SMALL))
    fidelity = tmp_path / "fid.json"
    fidelity.write_text(FidelitySummary(compared={BTC: 5}, equal_action={BTC: 5}).to_json())
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path), "--fidelity", str(fidelity)], out) == 0
    (directory,) = (tmp_path / REPORTS_DIR).iterdir()
    names = sorted(p.name for p in directory.iterdir())
    assert names == ["fidelity.json", CHAIN_FILE, "report.html", "report.md"]
    assert str(directory) in out.getvalue() and "Bericht geschrieben" in out.getvalue()
    markdown = (directory / "report.md").read_text(encoding="utf-8")
    assert "Gewertet sind 2 von 2 Quartalen (2022Q1 bis 2022Q2)" in markdown
    assert "2 Versuche" in markdown and "geprüft, in Ordnung" in markdown
    assert "unbekannt (Kerzenarchiv nicht gelesen)" in markdown
    assert "Treue nicht belegt (unter 300 Vergleiche)" in markdown
    assert (directory / "fidelity.json").read_text(encoding="utf-8") == fidelity.read_text()


def test_cli_refuses_without_register(tmp_path: Path) -> None:
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 1
    assert "Kein Prüfregister" in out.getvalue()
    assert not (tmp_path / RUNS_FILE).exists() and not (tmp_path / REPORTS_DIR).exists()


def test_cli_refuses_broken_chain(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL))
    chain = tmp_path / CHAIN_FILE
    chain.write_text(chain.read_text(encoding="utf-8").replace('"graded":true', '"graded":false'))
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 1
    assert "Kette fehlerhaft" in out.getvalue()
    assert not (tmp_path / REPORTS_DIR).exists()


# --- review findings (Spec 016 report check) -----------------------------------------------


def test_summary_without_any_band_gives_no_verdict() -> None:
    # Nothing graded: nothing was tested, so no "no strategy beats buy & hold" either.
    markdown, _ = build({"base": [result("2021Q1", SMALL), result("2021Q2", SMALL, share=0.5)]})
    summary = section(markdown, "Kurzfazit")
    assert "Gewertet sind 0 von 2 Quartalen (keines)" in summary
    assert "Keine Strategie" not in summary
    assert "Kein Urteil möglich: Kein Quartal ist gewertet, es wurde also nichts geprüft." in (
        summary
    )
    # With bands the tested negative result stays.
    markdown, _ = build({"base": [result("2022Q1", SMALL)]})
    summary = section(markdown, "Kurzfazit")
    assert "Kein Urteil möglich" not in summary
    assert "Keine Strategie schlägt Buy & Hold gleicher Quote nachweisbar" in summary


def test_diagnosis_compares_only_quarters_graded_in_both() -> None:
    gap = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    trips = {"trend": 3, "buyhold": 0}
    results = {
        # The base run leaves 2022Q1 out (85 %); the wider gap tolerance grades it.
        "base": [result("2022Q1", trips, share=0.85), result("2022Q2", trips)],
        "gap-tolerance": [
            result("2022Q1", trips, variant=gap, drift=0.0006),
            result("2022Q2", trips, variant=gap, drift=0.0006),
        ],
    }
    markdown, _ = build(results)
    diagnosis = section(markdown, "Diagnose: Was kosten die Sperrzeiten?")
    assert "nur Quartale, die in beiden Läufen gewertet sind: 2022Q2." in diagnosis
    assert "Nur in der Diagnose gewertet und deshalb nicht verglichen: 2022Q1" in diagnosis
    rows = {r[0]: r for r in table_after(markdown, "### Variante gap-tolerance")}
    # Same period on both sides: 3 round trips each, not 3 against 6.
    assert rows["trend"][4:] == ["3", "3"]
    assert "Quartale, die in beiden Läufen vorliegen (gewertet oder nicht): 2022Q1, 2022Q2" in (
        diagnosis
    )


def test_diagnosis_without_common_graded_quarter_compares_nothing() -> None:
    gap = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    results = {
        "base": [result("2022Q1", SMALL, share=0.85)],
        "gap-tolerance": [result("2022Q1", SMALL, variant=gap)],
    }
    markdown, _ = build(results)
    diagnosis = section(markdown, "Diagnose: Was kosten die Sperrzeiten?")
    assert "Kein Quartal ist in beiden Läufen gewertet" in diagnosis
    assert "Netto p. a. Basis" not in diagnosis


def test_newest_is_the_last_recording_including_merged_repeats(tmp_path: Path) -> None:
    main, branch = CodeState("a" * 40, True), CodeState("b" * 40, True)
    for code, drift in ((main, 0.001), (branch, 0.002), (main, 0.001)):
        with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=code) as reg:
            recorded = reg.record(recordable("2022Q1", SMALL, drift=drift))
    # The third execution repeats the first and is merged into run 1.
    assert (recorded.run_id, recorded.merged, recorded.repeat) == (1, True, 2)
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=main) as reg:
        results, used = newest_results(reg)
    assert [(u.row.run_id, u.row.commit) for u in used] == [(1, "a" * 40)]
    assert results["base"][0].depots["trend"].days[0].value == pytest.approx(9_980)
    markdown, _ = build_report(
        results,
        fidelity=None,
        trial_count=2,
        created_at=CREATED,
        commit="c" * 40,
        runs=used,
        draws=DRAWS,
    )
    trials = section(markdown, "Zahl der Versuche")
    assert "a" * 12 in trials and "b" * 12 not in trials
    row = table_after(markdown, "## Zahl der Versuche")[0]
    assert row[:4] == ["base", "2022Q1", "1", "a" * 12] and row[5:] == ["2", "ja"]


@pytest.mark.parametrize(
    "payload",
    [
        '{"compared": {"BTC-EUR": 5}, "deviations": [{"utc": "x", "cause": ""}]}',
        '{"compared": ["BTC-EUR"]}',
        '{"compared": {"BTC-EUR": "5"}}',
        '["not", "an", "object"]',
        '{"compared": {}, "surprise": 1}',
        '{"with_observations": "ja"}',
        "{not json",
    ],
)
def test_cli_refuses_malformed_fidelity(tmp_path: Path, payload: str) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL))
    fidelity = tmp_path / "fid.json"
    fidelity.write_text(payload, encoding="utf-8")
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path), "--fidelity", str(fidelity)], out) == 1
    assert "Treue-Test nicht lesbar" in out.getvalue()
    assert not (tmp_path / REPORTS_DIR).exists()


def test_cli_never_writes_runs_db(tmp_path: Path) -> None:
    runs = tmp_path / RUNS_FILE
    runs.touch()
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 1
    assert "ist leer" in out.getvalue()
    assert runs.stat().st_size == 0 and not (tmp_path / REPORTS_DIR).exists()

    # Another SQLite file is neither initialised nor changed; the user gets a German reason.
    runs.unlink()
    con = sqlite3.connect(runs)
    con.execute("CREATE TABLE other(x)")
    con.commit()
    con.close()
    before = runs.read_bytes()
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 1
    assert "nicht lesbar" in out.getvalue() and runs.read_bytes() == before

    runs.write_bytes(b"not a database at all")
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 1
    assert "nicht lesbar" in out.getvalue() and runs.read_bytes() == b"not a database at all"


def test_cli_leaves_the_register_byte_identical(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE) as reg:
        reg.record(recordable("2022Q1", SMALL))
    before = (tmp_path / RUNS_FILE).read_bytes(), (tmp_path / CHAIN_FILE).read_bytes()
    out = io.StringIO()
    assert cli_main("report", ["--dir", str(tmp_path)], out) == 0, out.getvalue()
    assert ((tmp_path / RUNS_FILE).read_bytes(), (tmp_path / CHAIN_FILE).read_bytes()) == before


def test_open_register_readonly_refuses_writes(tmp_path: Path) -> None:
    with Register.open(tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE, code=CODE):
        pass
    db, chain = tmp_path / RUNS_FILE, tmp_path / CHAIN_FILE
    with (
        report.open_register_readonly(db, chain, code=CODE) as reg,
        pytest.raises(sqlite3.OperationalError),
    ):
        reg.con.execute("CREATE TABLE sneaky(x)")
    with pytest.raises(report.ReportError):
        report.open_register_readonly(tmp_path / "missing.db", chain, code=CODE)


def test_spread_source_only_named_if_the_run_used_it() -> None:
    sources = {
        # Samples added after the run: today's value is not what the run used (0,100 %).
        ("2022Q1", BTC): SpreadSource("sampled", 0.0008, 40),
        ("2022Q1", "ETH-EUR"): SpreadSource("default", 0.0015),
    }
    markdown, _ = build({"base": [result("2022Q1", SMALL)]}, spread_sources=sources)
    assert table_after(markdown, "### Spread-Annahmen je Quartal") == [
        ["2022Q1", BTC, "0,100 %", "Quelle geändert seit dem Lauf", "-"],
        ["2022Q1", "ETH-EUR", "0,150 %", "vorsichtiger Standardwert", "-"],
    ]
    assert "Der Lauf rechnete mit dem Wert in der Spalte" in section(markdown, "Kosten")


def test_spread_sources_from_archive_carry_the_value(tmp_path: Path) -> None:
    path = tmp_path / "candles.db"
    q1 = Quarter.parse("2022Q1")
    with Archive(path) as archive:
        _insert_samples(archive, sample_hours(BTC, q1.start, q1.end))
        _insert_spread_rows(
            archive,
            [(BTC, "2022Q1", 0.001, 26), (BTC, "2025Q4", 0.002, 9)],
        )
    sampled = report.spread_sources_from_archive(path)
    # The sealed quarter's row is never read into the report (ADR-0026).
    assert sampled == {("2022Q1", BTC): SpreadSource("sampled", 0.001, 26)}
    eth = result("2022Q1", SMALL)
    eth.spread["ETH-EUR"] = DEFAULT_SPREAD["ETH-EUR"]
    sources = report._with_defaults(sampled, {"base": [eth]}, True)
    assert sources[("2022Q1", "ETH-EUR")] == SpreadSource("default", DEFAULT_SPREAD["ETH-EUR"])
    markdown, _ = build({"base": [eth]}, spread_sources=sources)
    rows = table_after(markdown, "### Spread-Annahmen je Quartal")
    assert [r[3] for r in rows] == ["Stichprobe öffentlicher Trades", "vorsichtiger Standardwert"]


def test_incomplete_quarter_listed_with_reason() -> None:
    rows = [result("2022Q1", SMALL, status="incomplete", error="BTC-EUR 1m window open")]
    rows.append(result("2022Q2", SMALL))
    markdown, _ = build({"base": rows})
    quarters = {r[0]: r for r in table_after(markdown, "## Quartale")}
    assert quarters["2022Q1"][1] == "nicht gewertet"
    assert "Kerzenarchiv für dieses Quartal noch unvollständig" in quarters["2022Q1"][2]
    assert "BTC-EUR 1m window open" in quarters["2022Q1"][2]
    assert "Status incomplete" not in markdown


def test_cell_labels_unknown_to_the_report_shown_raw() -> None:
    assert report._label(report._DIRECTION, "up") == "steigend"
    assert report._label(report._DIRECTION, "n/a") == "unbekannt"
    assert report._label(report._DIRECTION, "sideways-ish") == "sideways-ish"
    assert report._label(report._VOL, "extreme") == "extreme"


def test_fidelity_needs_matching_indicators() -> None:
    fid = FidelitySummary(
        compared={BTC: 400}, equal_action={BTC: 400}, indicators_ok={BTC: 350}, deviations=[]
    )
    markdown, _ = build({"base": [result("2022Q1", SMALL)]}, fidelity=fid)
    (row,) = table_after(markdown, "## Treue zum Live-Bot")
    assert row[3:] == ["350 von 400", "Treue nicht belegt (Indikatoren weichen ab)"]
    assert "Treue nicht belegt (400 Vergleiche)" in section(markdown, "Kurzfazit")


# --- wave-3 review: data issues reach the reader, spread rows go through the spread model ---


def _insert_samples(archive: Archive, hours: list[int], median: float = 0.001) -> None:
    archive.db.executemany(
        "INSERT INTO spread_samples(market, method, hour, trades, pairs, median, fetched_at) "
        "VALUES (?, ?, ?, 4, 3, ?, 1)",
        [(BTC, SPREAD_METHOD, h, median) for h in hours],
    )


def _insert_spread_rows(archive: Archive, rows: list[tuple[str, str, float, int]]) -> None:
    archive.db.executemany(
        "INSERT INTO spreads(market, quarter, method, spread, samples, computed_at) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        [(m, q, SPREAD_METHOD, s, n) for m, q, s, n in rows],
    )


OVERALL = "## Gesamt (gewertete Quartale zusammen)"


def test_report_lists_every_data_issue() -> None:
    # Review note: metrics flags every day left out of benchmark and bands, but the report
    # showed none of them, so the bands silently rested on fewer days.
    rows = [result("2022Q1", SMALL), result("2022Q2", SMALL)]
    cheap = Variant(name="cost-0.3", fee_rate=0.001, slippage=0.0005)
    grid = [result("2022Q1", SMALL, variant=cheap)]
    gap = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    diag = [result("2022Q1", SMALL, variant=gap)]
    markdown, html_text = build({"base": rows, "cost-0.3": grid, "gap-tolerance": diag})
    quality = section(markdown, "Datenqualität")
    base_issues = metrics.summarize(rows, draws=10).data_issues
    grid_issues = metrics.summarize(grid, draws=10).data_issues
    # Each depot carries 10 days of a 90- or 91-day quarter.
    assert len(base_issues) == 10 and len(grid_issues) == 5
    for issue in [*base_issues, *grid_issues]:
        assert f"- {issue.text}" in quality
        assert html.escape(issue.text) in html_text
    assert "Variante base: 10 Hinweis(e)." in quality
    assert "Variante cost-0.3: 5 Hinweis(e)." in quality
    assert "Lücken der Diagnose-Läufe stehen bei der jeweiligen Diagnose" in quality
    diagnosis = section(markdown, "Diagnose: Was kosten die Sperrzeiten?")
    assert "Lücken in den verglichenen Quartalen der Diagnose" in diagnosis
    for issue in metrics.summarize(diag, draws=10).data_issues:
        assert f"- {issue.text}" in diagnosis
    # A series that covers its quarter gives no issue, and the report says so.
    clean, _ = build({"base": [result("2022Q1", SMALL, fitted=True)]})
    assert "Keine: Jeder Tag der Quartale hat einen Depotwert" in section(clean, "Datenqualität")


def test_partial_benchmark_is_marked_wherever_it_is_shown() -> None:
    # Review note: benchmark_complete, excluded_days and QuarterSpread.incomplete never reached
    # the reader, so an excess over 10 of 90 days read like one over the whole quarter.
    cheap = Variant(name="cost-0.3", fee_rate=0.001, slippage=0.0005)
    results = {
        "base": [result("2022Q1", SMALL)],
        "cost-0.3": [result("2022Q1", SMALL, variant=cheap)],
    }
    markdown, html_text = build(results)
    marker = "(Benchmark nur über 10 von 90 Tagen)"
    per_quarter = table_after(markdown, "### Ergebnis je Quartal und Depot")
    assert len(per_quarter) == 5
    assert all(r[5].endswith(marker) and r[6].endswith(marker) for r in per_quarter)
    assert "„Benchmark nur über n von m Tagen“" in section(markdown, "Quartale")
    overall = {r[0]: r for r in table_after(markdown, OVERALL)}
    assert overall["trend"][5:7] == ["10", "80"]
    band = "(Band nur über 10 von 90 Tagen, Datenlücken siehe „Datenqualität“)"
    assert f"trend {band}" in section(markdown, OVERALL[3:])
    summary = section(markdown, "Kurzfazit")
    assert re.search(rf"trend: .* mit Zufall vereinbar: (ja|nein) {re.escape(band)};", summary)
    grid = table_after(markdown, "### Kostenraster: Entscheidungen reagieren auf Kosten")
    assert all(r[4].endswith(band) for r in grid)
    spreads = table_after(markdown, "### Streuung über die Quartale")
    assert spreads and all(r[-1] == "2022Q1" for r in spreads)
    assert '<tr class="mark"><td>trend</td>' in html_text
    # A complete series carries no marker anywhere.
    clean, _ = build({"base": [result("2022Q1", SMALL, fitted=True)]})
    assert "nur über" not in clean
    assert {r[6] for r in table_after(clean, OVERALL)} == {"0"}
    assert all(r[-1] == "keine" for r in table_after(clean, "### Streuung über die Quartale"))


def test_spread_source_ignores_the_row_of_a_partly_sampled_quarter(tmp_path: Path) -> None:
    # Review note (archive CHECK): a 1 bp row an older sampler left after one sampled hour of
    # 2022Q3 was reported as "sampled", while the backtest's spread model calls it a default.
    path = tmp_path / "candles.db"
    q3 = Quarter.parse("2022Q3")
    planned = sample_hours(BTC, q3.start, q3.end)
    with Archive(path) as archive:
        _insert_samples(archive, planned[:1], FLOOR)
        _insert_spread_rows(archive, [(BTC, "2022Q3", FLOOR, 1)])
    before = path.read_bytes()
    assert report.spread_sources_from_archive(path) == {}
    assert path.read_bytes() == before  # read-only
    run = result("2022Q3", SMALL)
    run.spread[BTC] = DEFAULT_SPREAD[BTC]  # what the backtest takes for that quarter
    sources = report._with_defaults(report.spread_sources_from_archive(path), {"base": [run]}, True)
    assert sources[("2022Q3", BTC)] == SpreadSource("default", DEFAULT_SPREAD[BTC])
    markdown, _ = build({"base": [run]}, spread_sources=sources)
    (btc, _eth) = table_after(markdown, "### Spread-Annahmen je Quartal")
    assert btc[3] == "vorsichtiger Standardwert"
    # Once every planned hour is stored, the same row is the quarter's sample.
    with Archive(path) as archive:
        _insert_samples(archive, planned[1:])
    assert report.spread_sources_from_archive(path) == {
        ("2022Q3", BTC): SpreadSource("sampled", FLOOR, 1)
    }
