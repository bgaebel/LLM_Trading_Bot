"""Base report of the strategy lab (Spec 016 AK10; readable parts of AK6, AK7 and AK9).

One document model renders to Markdown and to a standalone HTML page, so both files always say
the same. The text is German and written for a reader without statistics training: every number
that has a band shows it, every pooled number shows its case count, and the sample-size label
(``kein Urteil`` / ``indikativ`` / ``belastbar``) comes from ``metrics.verdict``.

What the report deliberately never does:

- show anything from the sealed period (ADR-0026): results whose quarter reaches 2025-10-01 are
  dropped before any number is computed, and only their count is mentioned;
- overwrite: each report gets its own directory under ``<lab>/reports/<run id>/``, created
  exclusively, and every file inside is opened with ``x``;
- write into the register: ``runs.db`` is opened with ``mode=ro`` and an empty file is refused,
  never initialised;
- load anything external: the HTML has inline CSS, no scripts, no links off the page, so it
  works offline and can be attached anywhere.
"""

import argparse
import dataclasses
import html
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from llm_trading_bot.lab import metrics
from llm_trading_bot.lab.metrics import (
    Band,
    BreakEven,
    DepotMetrics,
    LabMetrics,
    QualityRow,
    Uncertainty,
)
from llm_trading_bot.lab.model import (
    DEPOTS,
    ELIGIBLE_SHARE,
    FIDELITY_MIN,
    FIRST_GRADED,
    LEAD_ROWS,
    MARKETS,
    STRATEGY_VERSION,
    FidelitySummary,
    Quarter,
    QuarterResult,
    Variant,
)
from llm_trading_bot.lab.paths import CANDLES_FILE, CHAIN_FILE, REPORTS_DIR, RUNS_FILE, lab_dir
from llm_trading_bot.lab.register import (
    BUSY_TIMEOUT_MS,
    ChainCheck,
    CodeState,
    Register,
    RegisterError,
    RunRow,
    check_register_schema,
    code_state,
)
from llm_trading_bot.lab.spread import DEFAULT_SPREAD, FLOOR, QuarterSpreads
from llm_trading_bot.lab.spread import METHOD as SPREAD_METHOD
from llm_trading_bot.paper.db import SchemaError
from llm_trading_bot.paper.execution import BUYHOLD

BASE_VARIANT = "base"
COST_GRID = (0.003, 0.006, 0.009, 0.012)
"""Round-trip costs of the cost grid (AK6); also the k values of the analytic break-even."""
MARKDOWN_FILE = "report.md"
HTML_FILE = "report.html"
FIDELITY_FILE = "fidelity.json"
MAX_DEVIATIONS = 100
"""Deviation rows shown; the attached fidelity.json keeps all of them."""
TITLE = f"Basisbericht {STRATEGY_VERSION} - Strategie-Labor"
NOT_SHOWN = "-"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

EXIT_OK = 0
EXIT_REFUSED = 1


class ReportError(Exception):
    """The report cannot be written safely; nothing was overwritten."""


class ReportExists(ReportError):
    """The report directory or a file in it already exists."""


# --- number formatting (German) ------------------------------------------------------------


def num(x: float | None, digits: int = 2, *, sign: bool = False) -> str:
    """``1.234,57``: German grouping and decimal comma."""
    if x is None:
        return NOT_SHOWN
    if not math.isfinite(x):
        return "n/a"
    text = f"{x:{'+' if sign else ''},.{digits}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def pct(x: float | None, digits: int = 2, *, sign: bool = True) -> str:
    """A fraction as percent: 0.0123 -> ``+1,23 %``."""
    if x is None:
        return NOT_SHOWN
    return f"{num(x * 100, digits, sign=sign)} %"


def eur(x: float | None) -> str:
    return NOT_SHOWN if x is None else f"{num(x, 2)} €"


def count(n: int) -> str:
    return num(float(n), 0)


def band_pct(b: Band | None) -> str:
    if b is None:
        return NOT_SHOWN
    return f"{pct(b.estimate)} [{pct(b.low)} bis {pct(b.high)}]"


def band_num(b: Band | None) -> str:
    if b is None:
        return NOT_SHOWN
    lo, hi = num(b.low, 2, sign=True), num(b.high, 2, sign=True)
    return f"{num(b.estimate, 2, sign=True)} [{lo} bis {hi}]"


def chance(b: Band | None) -> str:
    return NOT_SHOWN if b is None else metrics.chance_text(b)


def day_of(epoch: int | None) -> str:
    return NOT_SHOWN if epoch is None else time.strftime("%Y-%m-%d", time.gmtime(epoch))


def stamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))


def depot_name(agent: str) -> str:
    return "Buy & Hold" if agent == BUYHOLD else agent


def round_trip_label(round_trips: int) -> str:
    """The AK10 label: under 100 round trips ``kein Urteil``, 100-300 ``indikativ``."""
    return metrics.verdict(round_trips)


def _ordered(agents: Iterable[str]) -> list[str]:
    """Strategies in the bot's order, buy & hold last; unknown names after them."""
    names = set(agents)
    return [a for a in DEPOTS if a in names] + sorted(names - set(DEPOTS))


# --- document model ------------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str = ""


@dataclass(frozen=True)
class Para:
    text: str
    kind: str = ""
    """``""``, ``note`` (muted) or ``warn`` (stands out)."""


@dataclass(frozen=True)
class Bullets:
    items: list[str]


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[list[str]]
    marked: frozenset[int] = frozenset()
    """Row indices to highlight in HTML (the Markdown text carries the same hint in a column)."""


Block = Heading | Para | Bullets | Table


@dataclass
class Doc:
    title: str
    meta: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)

    def section(self, text: str, anchor: str) -> None:
        self.blocks.append(Heading(2, text, anchor))

    def sub(self, text: str) -> None:
        self.blocks.append(Heading(3, text))

    def p(self, text: str, kind: str = "") -> None:
        self.blocks.append(Para(text, kind))

    def ul(self, items: Sequence[str]) -> None:
        if items:
            self.blocks.append(Bullets(list(items)))

    def table(
        self, headers: Sequence[str], rows: Sequence[Sequence[str]], marked: Iterable[int] = ()
    ) -> None:
        if rows:
            self.blocks.append(Table(list(headers), [list(r) for r in rows], frozenset(marked)))
        else:
            self.p("Keine Zeilen.", "note")


def _md_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_text(text: str) -> str:
    # A Markdown paragraph or list item is one line here; a stray newline would end it.
    return " ".join(text.split("\n"))


def render_markdown(doc: Doc) -> str:
    out = [f"# {doc.title}", ""]
    out.extend(f"- {_md_text(m)}" for m in doc.meta)
    out.append("")
    for b in doc.blocks:
        if isinstance(b, Heading):
            out += ["#" * b.level + " " + _md_text(b.text), ""]
        elif isinstance(b, Para):
            prefix = "> " if b.kind == "warn" else ""
            out += [prefix + _md_text(b.text), ""]
        elif isinstance(b, Bullets):
            out += [f"- {_md_text(item)}" for item in b.items] + [""]
        else:
            out.append("| " + " | ".join(_md_cell(h) for h in b.headers) + " |")
            out.append("|" + "|".join("---" for _ in b.headers) + "|")
            out += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in b.rows]
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


_CSS = """
:root{color-scheme:light dark;--bg:#fbfbf9;--fg:#1d1f21;--muted:#5d6166;--line:#d9dbd6;
--head:#eef0ea;--mark:#fff3cd;--warn-bg:#fdf1e3;--warn-line:#c7771b;--accent:#2d5f8b}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--fg:#e6e6e3;--muted:#a3a7ad;
--line:#363a40;--head:#22262b;--mark:#3d3418;--warn-bg:#33281a;--warn-line:#e0a050;
--accent:#8cb8e0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6em;margin:0 0 .4em}h2{font-size:1.25em;margin:2em 0 .6em;
padding-bottom:.2em;border-bottom:1px solid var(--line)}h3{font-size:1.05em;margin:1.4em 0 .4em}
p{margin:.5em 0}.note{color:var(--muted);font-size:.93em}
.warn{background:var(--warn-bg);border-left:4px solid var(--warn-line);padding:.5em .8em}
ul.meta{list-style:none;padding:0;color:var(--muted);font-size:.93em}
nav ul{columns:2;padding-left:1.2em}a{color:var(--accent)}
.scroll{overflow-x:auto;margin:.6em 0 1em}
table{border-collapse:collapse;font-size:.88em;min-width:50%}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{background:var(--head);font-weight:600}tr.mark td{background:var(--mark)}
td{font-variant-numeric:tabular-nums}
@media (max-width:640px){nav ul{columns:1}}
"""


def render_html(doc: Doc) -> str:
    e = html.escape
    out = [
        "<!doctype html>",
        '<html lang="de">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(doc.title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        f"<h1>{e(doc.title)}</h1>",
        '<ul class="meta">' + "".join(f"<li>{e(m)}</li>" for m in doc.meta) + "</ul>",
    ]
    toc = [b for b in doc.blocks if isinstance(b, Heading) and b.level == 2 and b.anchor]
    if toc:
        items = "".join(f'<li><a href="#{e(h.anchor)}">{e(h.text)}</a></li>' for h in toc)
        out.append(f'<nav aria-label="Inhalt"><ul>{items}</ul></nav>')
    for b in doc.blocks:
        if isinstance(b, Heading):
            ident = f' id="{e(b.anchor)}"' if b.anchor else ""
            out.append(f"<h{b.level}{ident}>{e(b.text)}</h{b.level}>")
        elif isinstance(b, Para):
            cls = f' class="{b.kind}"' if b.kind else ""
            out.append(f"<p{cls}>{e(b.text)}</p>")
        elif isinstance(b, Bullets):
            out.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in b.items) + "</ul>")
        else:
            head = "".join(f'<th scope="col">{e(h)}</th>' for h in b.headers)
            rows = "".join(
                ('<tr class="mark">' if i in b.marked else "<tr>")
                + "".join(f"<td>{e(c)}</td>" for c in row)
                + "</tr>"
                for i, row in enumerate(b.rows)
            )
            out.append(
                f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{rows}</tbody></table></div>"
            )
    out += ["</main>", "</body>", "</html>"]
    return "\n".join(out) + "\n"


# --- inputs besides the results ------------------------------------------------------------


@dataclass(frozen=True)
class SpreadSource:
    """Where a quarter's assumed spread came from (spread model, AK6)."""

    source: str
    """``sampled`` or ``default`` (``QuarterSpreads.source``)."""
    value: float
    """The spread this source gives today; a run only "used" it if its stored spread matches."""
    hours: int | None = None
    """Hours with a median behind a sampled value."""


_SOURCE_TEXT = {
    "sampled": "Stichprobe öffentlicher Trades",
    "default": "vorsichtiger Standardwert",
}
CHANGED_SOURCE = "Quelle geändert seit dem Lauf"
NO_ARCHIVE = "unbekannt (Kerzenarchiv nicht gelesen)"


def _source_matches(src: SpreadSource, used: float) -> bool:
    # The run stores the spread it used; the archive can gain or recompute samples afterwards,
    # and labelling today's source next to yesterday's value would misstate where it came from.
    return math.isclose(src.value, used, rel_tol=1e-9, abs_tol=1e-12)


@dataclass(frozen=True)
class UsedRun:
    """A register run the report is built from, with when it was last produced."""

    row: RunRow
    last_recorded: str
    """Time of the latest recording of this outcome, merged repeats included."""


# --- the report ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Variants:
    base: str | None
    grid: list[str]
    diagnostic: list[str]
    specs: dict[str, Variant]


def _classify(results: Mapping[str, Sequence[QuarterResult]], base: str) -> _Variants:
    specs: dict[str, Variant] = {}
    for name, rows in results.items():
        if rows:
            specs[name] = rows[0].variant
    diagnostic = sorted(n for n, rows in results.items() if any(r.variant.diagnostic for r in rows))
    grid = sorted(
        (n for n in specs if n != base and n not in diagnostic),
        key=lambda n: (specs[n].roundtrip, n),
    )
    return _Variants(base if base in specs else None, grid, diagnostic, specs)


def _visible(results: Sequence[QuarterResult]) -> tuple[list[QuarterResult], int]:
    """Results without the sealed period, in time order, and how many were dropped."""
    shown = [r for r in results if not r.quarter.sealed]
    return sorted(shown, key=lambda r: r.quarter.start), len(results) - len(shown)


def _graded_rows(m: LabMetrics, agent: str) -> list[DepotMetrics]:
    return [d for d in m.depots if d.agent == agent and d.graded]


def _quarter_start(label: str) -> int:
    return Quarter.parse(label).start


def _chained(values: Iterable[float]) -> float:
    return math.prod(1 + v for v in values) - 1


def _not_graded_reason(r: QuarterResult) -> str:
    if r.status == "no_lead":
        return f"zu wenig Datenvorlauf (unter {LEAD_ROWS} Zeilen je Markt und Intervall)"
    if r.status == "aborted":
        return f"abgebrochen: {r.error or 'unbekannter Fehler'}"
    if r.status == "sealed":
        return "versiegelter Zeitraum"
    if r.status == "incomplete":
        detail = f" ({r.error})" if r.error else ""
        return (
            "Kerzenarchiv für dieses Quartal noch unvollständig (Abruf nicht fertig); nicht "
            f"gerechnet, weil fehlende Zeilen wie Börsenlücken aussähen{detail}"
        )
    if r.status != "ok":
        return f"Status {r.status}: {r.error}".rstrip(": ")
    if r.quarter.start < Quarter.parse(FIRST_GRADED).start:
        return "vor Q2 2021 (BTC-EUR hat erst ab 2021-02-20 15m-Kerzen)"
    if not r.quality:
        return "keine Angaben zur Datenqualität"
    low = [
        f"{m} {pct(q.share, 1, sign=False)}"
        for m, q in sorted(r.quality.items())
        if not r.eligible[m]
    ]
    return f"zu wenige Entscheidungen (unter {pct(ELIGIBLE_SHARE, 0, sign=False)}): " + ", ".join(
        low
    )


def build_report(
    results_by_variant: Mapping[str, Sequence[QuarterResult]],
    *,
    fidelity: FidelitySummary | None,
    trial_count: int,
    created_at: float,
    commit: str,
    chain_check: ChainCheck | None = None,
    spread_sources: Mapping[tuple[str, str], SpreadSource] | None = None,
    runtime: Mapping[str, float] | None = None,
    config_count: int | None = None,
    runs: Sequence[UsedRun] = (),
    base_variant: str = BASE_VARIANT,
    draws: int = metrics.BOOTSTRAP_DRAWS,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[str, str]:
    """The base report as (Markdown, HTML).

    ``results_by_variant`` maps a variant name to its quarter results (base, the cost grid and
    diagnostic runs). ``spread_sources`` is keyed by (quarter label, market); ``runtime`` adds
    labelled durations in seconds (e.g. loading from the register); ``runs`` are the register
    rows the results came from (for provenance). ``draws`` exists for tests only - a real report
    uses the methodology's 5,000.
    """
    started = clock()
    visible: dict[str, list[QuarterResult]] = {}
    sealed = 0
    for name, rows in results_by_variant.items():
        shown, dropped = _visible(rows)
        sealed += dropped
        visible[name] = shown
    kinds = _classify(visible, base_variant)
    # Diagnostic runs are summarised only over the quarters they share with the base run.
    summaries = {
        name: metrics.summarize(rows, draws=draws)
        for name, rows in visible.items()
        if rows and name not in kinds.diagnostic
    }
    base_rows = visible.get(kinds.base, []) if kinds.base else []
    base = summaries.get(kinds.base) if kinds.base else None

    doc = Doc(TITLE)
    doc.meta = [
        f"Erstellt: {stamp(created_at)}",
        f"Code-Commit des Berichts: {commit}",
        f"Strategieversion: {STRATEGY_VERSION} (trend, breakout, meanrev, ensemble) und Buy & Hold",
        "Zeitraum: bis 2025-09-30; alles ab 2025-10-01 ist versiegelt und hier nicht enthalten",
        _chain_text(chain_check),
    ]

    _summary(doc, kinds, base, base_rows, fidelity, trial_count, sealed)
    _overall(doc, base)
    _quarters(doc, base, base_rows)
    _costs(doc, kinds, summaries, base, base_rows, spread_sources or {})
    _cells(doc, base)
    _uncertainty(doc, base)
    _quality(doc, base)
    _diagnosis(doc, kinds, visible, base_rows, draws)
    _fidelity(doc, fidelity)
    _assumptions(doc)
    _trials(doc, trial_count, config_count, runs, kinds, visible)
    compute = clock() - started
    _runtime(doc, kinds, visible, compute, runtime or {})
    return render_markdown(doc), render_html(doc)


def _chain_text(check: ChainCheck | None) -> str:
    if check is None:
        return "Prüfregister-Kette: nicht beigefügt"
    state = "geprüft, in Ordnung" if check.ok else "FEHLERHAFT: " + "; ".join(check.problems)
    return (
        f"Prüfregister-Kette ({CHAIN_FILE}, beigefügt): {count(check.lines)} Zeilen, "
        f"Kopf {check.head[:16]}, {state}"
    )


# --- sections ------------------------------------------------------------------------------


def _summary(
    doc: Doc,
    kinds: _Variants,
    base: LabMetrics | None,
    base_rows: Sequence[QuarterResult],
    fidelity: FidelitySummary | None,
    trial_count: int,
    sealed: int,
) -> None:
    doc.section("Kurzfazit", "kurzfazit")
    if base is None:
        doc.p(
            f"Im Register liegt kein Basislauf (Variante „{BASE_VARIANT}“). Ohne ihn gibt es "
            "keine Gesamtzahlen; die übrigen Abschnitte zeigen, was vorhanden ist.",
            "warn",
        )
        return
    graded = [r for r in base_rows if r.graded]
    items = [
        f"Gewertet sind {len(graded)} von {len(base_rows)} Quartalen "
        f"({_span(graded) or 'keines'}); die übrigen stehen unter „Quartale“ mit Grund."
    ]
    beats: list[str] = []
    banded: list[str] = []
    for agent in _ordered(base.round_trips):
        if agent == BUYHOLD:
            continue
        u = base.uncertainty.get(agent)
        n = base.round_trips[agent]
        if u is None:
            items.append(f"{agent}: zu wenige gewertete Tage für ein Band; {count(n)} Round-Trips.")
            continue
        banded.append(agent)
        if u.excess.low > 0:
            beats.append(agent)
        items.append(
            f"{agent}: Übertrendite gegenüber Buy & Hold gleicher Quote {band_pct(u.excess)} "
            f"pro Jahr, mit Zufall vereinbar: {chance(u.excess)}; {count(n)} Round-Trips, "
            f"Einstufung: {base.labels[agent]}."
        )
    if beats:
        items.append(
            "Band ganz über null: "
            + ", ".join(beats)
            + f". Das ist noch kein Beleg: {count(trial_count)} Versuche stehen im Register, "
            "und die beste von vielen Varianten sieht schon zufällig gut aus."
        )
    elif banded:
        strategies = [a for a in base.round_trips if a != BUYHOLD]
        tested = "" if len(banded) == len(strategies) else f" (geprüft: {', '.join(banded)})"
        items.append(
            f"Keine Strategie{tested} schlägt Buy & Hold gleicher Quote nachweisbar: Jedes Band "
            "enthält null oder liegt darunter. Das ist ein gültiges Ergebnis."
        )
    else:
        # Without a single band nothing was tested; "no strategy beats B&H" would read as a
        # tested negative result.
        items.append("Kein Urteil möglich: " + _no_band_reason(graded, base) + ".")
    items.append(_fidelity_short(fidelity))
    if sealed:
        items.append(
            f"{sealed} Ergebnis(se) aus dem versiegelten Zeitraum ab 2025-10-01 wurden nicht "
            "angesehen und fehlen hier."
        )
    doc.ul(items)
    doc.p(
        "Lesart: Eine Zahl in eckigen Klammern ist das 95-%-Band: der Bereich, in dem das "
        "Ergebnis bei anderen, ebenso plausiblen Marktverläufen liegen könnte. „Mit Zufall "
        "vereinbar: ja“ heißt, das Band enthält null: Das Ergebnis kann ebenso gut Glück oder Pech "
        "sein. Unter 100 Round-Trips (ein Kauf und der zugehörige Verkauf) gibt es kein Urteil, "
        "bis 300 ist es nur indikativ.",
        "note",
    )


def _no_band_reason(graded: Sequence[QuarterResult], base: LabMetrics) -> str:
    if not graded:
        return "Kein Quartal ist gewertet, es wurde also nichts geprüft"
    if not any(a != BUYHOLD for a in base.round_trips):
        return "Die gewerteten Quartale enthalten kein Strategie-Depot"
    return (
        "Für keine Strategie gibt es ein Unsicherheitsband (zu wenige gewertete Tage, mindestens "
        "zwei nötig), es wurde also nichts geprüft"
    )


def _span(graded: Sequence[QuarterResult]) -> str:
    if not graded:
        return ""
    first, last = graded[0].quarter.label, graded[-1].quarter.label
    return first if first == last else f"{first} bis {last}"


def _established(f: FidelitySummary) -> dict[str, bool]:
    """``FidelitySummary.established`` plus AK5's indicator condition, which it does not check:
    "belegt" next to "350 von 400" indicators would contradict itself."""
    return {m: ok and f.indicators_ok.get(m, 0) == f.compared[m] for m, ok in f.established.items()}


def _fidelity_short(f: FidelitySummary | None) -> str:
    if f is None or not f.compared:
        return "Treue zum Live-Bot: Treue nicht belegt (kein Treue-Test beigefügt)."
    est = _established(f)
    parts = [
        f"{m}: {'belegt' if est[m] else 'Treue nicht belegt'} ({count(n)} Vergleiche)"
        for m, n in sorted(f.compared.items())
    ]
    return "Treue zum Live-Bot: " + "; ".join(parts) + "."


def _overall(doc: Doc, base: LabMetrics | None) -> None:
    doc.section("Gesamt (gewertete Quartale zusammen)", "gesamt")
    if base is None:
        doc.p("Kein Basislauf vorhanden.", "note")
        return
    agents = _ordered(base.round_trips)
    if not agents:
        doc.p("Kein Quartal ist gewertet; es gibt keine Gesamtzahlen.", "warn")
        return
    if not base.uncertainty:
        doc.p("Zu wenige gewertete Tage für Unsicherheitsbänder (mindestens zwei nötig).", "warn")
    rows = []
    for a in agents:
        u = base.uncertainty.get(a)
        rows.append(
            [
                depot_name(a),
                band_pct(u.mean_return if u else None),
                band_pct(u.excess if u else None),
                chance(u.excess if u else None),
                band_num(u.sharpe if u else None),
                count(u.days) if u else NOT_SHOWN,
                count(base.round_trips[a]),
                base.labels[a],
            ]
        )
    doc.p(
        "Renditen pro Jahr (Mittel der Tagesrenditen mal 365) mit 95-%-Band. Die Übertrendite "
        "vergleicht mit Buy & Hold, das im Mittel genauso viel investiert war wie die Strategie, "
        "mit dessen Kosten; Bargeld bringt 0 %."
    )
    doc.table(
        [
            "Depot",
            "Netto p. a. [95-%-Band]",
            "Übertrendite p. a. gegenüber Buy & Hold gleicher Quote [Band]",
            "mit Zufall vereinbar",
            "Sharpe-Quote [Band]",
            "Tage",
            "Round-Trips",
            "Einstufung",
        ],
        rows,
    )
    trade_rows = []
    for a in agents:
        mine = _graded_rows(base, a)
        trade_rows.append(
            [
                depot_name(a),
                count(len(mine)),
                pct(_chained(d.net_return_mid for d in mine)),
                pct(_chained(d.net_return_liquidated for d in mine)),
                count(sum(d.trades for d in mine)),
                num(sum(d.turnover for d in mine) / len(mine) if mine else None, 1),
                eur(sum(d.fees for d in mine)),
                pct(sum(d.mean_quote for d in mine) / len(mine) if mine else None, 1, sign=False),
                pct(max((d.max_drawdown for d in mine), default=None), 1, sign=False),
                count(sum(1 for d in mine if d.halted_at is not None)),
            ]
        )
    doc.sub("Handel und Kosten")
    doc.p(
        "Verkettet: die Quartalsrenditen hintereinander gerechnet, als wäre das Ergebnis jedes "
        "Quartals ins nächste übernommen worden (jedes Quartal beginnt im Lauf aber frisch mit "
        "10.000 €). „Mitte“ bewertet den Bestand am Quartalsende zum Mittelkurs, „verkauft“ "
        "zieht den Verkauf mit allen Kosten ab."
    )
    doc.table(
        [
            "Depot",
            "Quartale",
            "verkettet (Mitte)",
            "verkettet (verkauft)",
            "Trades",
            "Umschlag je Quartal (Vielfaches des Kapitals)",
            "Gebühren",
            "mittlere Investitionsquote",
            "schlechtester Drawdown (Tagesende)",
            "Quartale mit Drawdown-Stopp",
        ],
        trade_rows,
    )


def _quarters(doc: Doc, base: LabMetrics | None, base_rows: Sequence[QuarterResult]) -> None:
    doc.section("Quartale", "quartale")
    if not base_rows:
        doc.p("Kein Basislauf vorhanden.", "note")
        return
    doc.p(
        f"Ein Quartal wird gewertet, wenn je Markt mindestens {pct(ELIGIBLE_SHARE, 0, sign=False)} "
        f"der fälligen Entscheidungen fielen und es ab {FIRST_GRADED} liegt. Jedes Quartal "
        "beginnt frisch mit 10.000 €."
    )
    rows = []
    marked = []
    for i, r in enumerate(base_rows):
        shares = []
        for m in MARKETS:
            q = r.quality.get(m)
            shares.append(f"{pct(q.share, 1, sign=False)} (von {count(q.due)})" if q else NOT_SHOWN)
        if not r.graded:
            marked.append(i)
        rows.append(
            [
                r.quarter.label,
                "gewertet" if r.graded else "nicht gewertet",
                "" if r.graded else _not_graded_reason(r),
                *shares,
                count(r.rounds),
            ]
        )
    doc.table(
        ["Quartal", "Status", "Grund", *(f"Entscheidungen {m}" for m in MARKETS), "Runden"],
        rows,
        marked,
    )
    if base is None or not base.depots:
        return
    doc.sub("Ergebnis je Quartal und Depot")
    doc.p(
        "Je Quartal nur die einfachen Zahlen ohne Band: Ein Quartal ist zu kurz für ein eigenes "
        "Urteil. Die Streuung über die Quartale steht unter „Unsicherheit“."
    )
    depot_rows: list[list[str]] = []
    depot_marked = []
    rank = {a: i for i, a in enumerate(_ordered(d.agent for d in base.depots))}
    # metrics lists depots alphabetically; the report keeps the bot's order within a quarter.
    for d in sorted(base.depots, key=lambda d: (_quarter_start(d.quarter), rank[d.agent])):
        if not d.graded:
            depot_marked.append(len(depot_rows))
        depot_rows.append(
            [
                d.quarter,
                depot_name(d.agent),
                "ja" if d.graded else "nein",
                pct(d.net_return_mid),
                pct(d.net_return_liquidated),
                pct(d.benchmark_cost),
                pct(d.excess),
                pct(d.mean_quote, 1, sign=False),
                count(d.round_trips),
                pct(d.max_drawdown, 1, sign=False),
                day_of(d.halted_at),
            ]
        )
    doc.table(
        [
            "Quartal",
            "Depot",
            "gewertet",
            "Netto (Mitte)",
            "Netto (verkauft)",
            "Buy & Hold gleicher Quote mit Kosten",
            "Übertrendite",
            "Quote",
            "Round-Trips",
            "max. Drawdown (Tagesende)",
            "Drawdown-Stopp am",
        ],
        depot_rows,
        depot_marked,
    )


def _costs(
    doc: Doc,
    kinds: _Variants,
    summaries: Mapping[str, LabMetrics],
    base: LabMetrics | None,
    base_rows: Sequence[QuarterResult],
    sources: Mapping[tuple[str, str], SpreadSource],
) -> None:
    doc.section("Kosten", "kosten")
    if base is not None and kinds.base is not None:
        spec = kinds.specs[kinds.base]
        doc.sub("Kosten-Gleichstand bei festen Entscheidungen")
        doc.p(
            "Bei welchen Kosten je Round-Trip wäre das Ergebnis null, wenn die Strategie genau "
            "dieselben Trades machte? Gerechnet aus der Tradeliste des Basislaufs: netto(k) = "
            "brutto - k mal Round-Trip-Volumen. Das Modell rechnet mit "
            f"{pct(spec.roundtrip, 2, sign=False)} je Round-Trip (Gebühr und Slippage, je Seite "
            f"{pct(spec.fee_rate, 2, sign=False)} + {pct(spec.slippage, 2, sign=False)}) plus "
            "Spread. Faustregel: Der Gleichstand sollte mindestens doppelt so hoch sein wie die "
            "Kosten (Verhältnis ≥ 2)."
        )
        rows = [_break_even_row(base.break_even[a]) for a in _ordered(base.break_even)]
        doc.table(
            [
                "Depot",
                "Quartale",
                "Round-Trip-Volumen",
                "brutto",
                "Gleichstand ohne Spread",
                "Gleichstand mit Spread",
                "Kosten + Spread",
                "Verhältnis",
                *(f"netto bei {pct(k, 1, sign=False)}" for k in COST_GRID),
            ],
            rows,
        )
    doc.sub("Kostenraster: Entscheidungen reagieren auf Kosten")
    grid = ([kinds.base] if kinds.base else []) + kinds.grid
    if not kinds.grid:
        doc.p(
            "Kein Lauf des Kostenrasters im Register; die Tabelle zeigt nur den Basislauf.", "note"
        )
    if grid and any(n in summaries for n in grid):
        doc.p(
            "Jede Zeile ist ein vollständiger neuer Lauf mit anderen Kosten: Die Strategien sehen "
            "andere Kosten und entscheiden anders. Jede Variante zählt im Register als eigener "
            "Versuch."
        )
        rows = []
        for name in grid:
            s = summaries.get(name)
            if s is None:
                continue
            for a in _ordered(s.round_trips):
                u = s.uncertainty.get(a)
                rows.append(
                    [
                        name,
                        pct(kinds.specs[name].roundtrip, 2, sign=False),
                        depot_name(a),
                        band_pct(u.mean_return if u else None),
                        band_pct(u.excess if u else None),
                        chance(u.excess if u else None),
                        count(s.round_trips[a]),
                        s.labels[a],
                    ]
                )
        doc.table(
            [
                "Variante",
                "Kosten je Round-Trip (ohne Spread)",
                "Depot",
                "Netto p. a. [Band]",
                "Übertrendite p. a. [Band]",
                "mit Zufall vereinbar",
                "Round-Trips",
                "Einstufung",
            ],
            rows,
        )
    doc.sub("Spread-Annahmen je Quartal")
    doc.p(
        "Kerzen enthalten keine Geld- und Briefkurse. Der Spread (Abstand zwischen Kauf- und "
        "Verkaufskurs) ist deshalb je Quartal angenommen: aus Stichproben öffentlicher Trades, "
        "sonst ein bewusst vorsichtiger Standardwert."
    )
    rows = []
    changed = False
    for r in base_rows:
        for m in sorted(r.spread):
            src = sources.get((r.quarter.label, m))
            if src is None:
                text, hours = NO_ARCHIVE, NOT_SHOWN
            elif not _source_matches(src, r.spread[m]):
                text, hours, changed = CHANGED_SOURCE, NOT_SHOWN, True
            else:
                text = _SOURCE_TEXT.get(src.source, src.source)
                hours = count(src.hours) if src.hours is not None else NOT_SHOWN
            rows.append([r.quarter.label, m, pct(r.spread[m], 3, sign=False), text, hours])
    doc.table(["Quartal", "Markt", "Spread", "Quelle", "Stunden in der Stichprobe"], rows)
    if changed:
        doc.p(
            f"„{CHANGED_SOURCE}“: Das Kerzenarchiv liefert für dieses Quartal heute einen anderen "
            "Spread als den, mit dem der Lauf gerechnet hat (zum Beispiel weil später Stichproben "
            "dazukamen). Der Lauf rechnete mit dem Wert in der Spalte „Spread“; woher er stammte, "
            "lässt sich nicht mehr sicher sagen.",
            "note",
        )


def _break_even_row(b: BreakEven) -> list[str]:
    if b.break_even is None:
        return (
            [depot_name(b.agent), count(b.quarters), eur(0.0), eur(b.gross_pnl)]
            + ["keine Trades"]
            + [NOT_SHOWN] * (3 + len(COST_GRID))
        )
    k = pct(b.break_even, 2, sign=True)
    if b.break_even < 0:
        k += " (Verlust schon ohne Kosten)"
    return [
        depot_name(b.agent),
        count(b.quarters),
        eur(b.roundtrip_volume),
        eur(b.gross_pnl),
        k,
        pct(b.break_even_total, 2, sign=True),
        pct(b.model_cost + b.spread, 2, sign=False),
        num(b.ratio, 2),
        *(eur(b.net(c)) for c in COST_GRID),
    ]


_VOL = {"low": "niedrig", "mid": "mittel", "high": "hoch", metrics.UNKNOWN: "unbekannt"}
_DIRECTION = {
    "up": "steigend",
    "down": "fallend",
    "flat": "seitwärts",
    metrics.UNKNOWN: "unbekannt",
}


def _label(names: Mapping[str, str], value: str) -> str:
    # A label metrics adds later must stay visible: "unbekannt" would merge it with real gaps.
    return names.get(value, value)


def _cells(doc: Doc, base: LabMetrics | None) -> None:
    doc.section("Marktphasen", "marktphasen")
    if base is None or not any(base.cells.values()):
        doc.p("Keine Round-Trips in gewerteten Quartalen.", "note")
        return
    doc.p(
        "Round-Trips nach der Lage beim Einstieg: Regime des Bots, Volatilitäts-Drittel (nur aus "
        "früheren Werten bestimmt) und Richtung. Band: Mittel ± 1,96 Standardfehler. Zellen mit "
        f"weniger als {metrics.FEW_TRADES} Round-Trips sind markiert und nicht belastbar."
    )
    rows: list[list[str]] = []
    marked: list[int] = []
    for a in _ordered(base.cells):
        for c in base.cells[a]:
            if c.few:
                marked.append(len(rows))
            rows.append(
                [
                    depot_name(a),
                    c.regime if c.regime != metrics.UNKNOWN else "unbekannt",
                    _label(_VOL, c.vol),
                    _label(_DIRECTION, c.direction),
                    count(c.count),
                    f"{pct(c.mean)} [{pct(c.low)} bis {pct(c.high)}]",
                    eur(c.pnl),
                    f"unter {metrics.FEW_TRADES}: nicht robust" if c.few else "",
                ]
            )
    doc.table(
        [
            "Depot",
            "Regime",
            "Volatilität",
            "Richtung",
            "Round-Trips",
            "Ø netto je Round-Trip [Band]",
            "Ergebnis",
            "Hinweis",
        ],
        rows,
        marked,
    )


def _uncertainty(doc: Doc, base: LabMetrics | None) -> None:
    doc.section("Unsicherheit", "unsicherheit")
    some: Uncertainty | None = next(iter(base.uncertainty.values()), None) if base else None
    days = count(some.days) if some else "die"
    seed = some.seed if some else metrics.BOOTSTRAP_SEED
    doc.p(
        "Die Bänder entstehen mit einem stationären Block-Bootstrap: Aus "
        f"{days} gewerteten Tagen aller Quartale werden "
        f"{count(some.draws if some else metrics.BOOTSTRAP_DRAWS)}-mal neue Verläufe gleicher "
        "Länge gezogen, in Blöcken von im Mittel "
        f"{some.mean_block if some else metrics.BOOTSTRAP_BLOCK} Tagen, damit ruhige und "
        "unruhige Wochen zusammenbleiben. Alle Depots verwenden dieselben Ziehungen. Der "
        f"Zufallsgenerator hat einen festen Startwert ({seed}), "
        "der Bericht ergibt also bei jedem Lauf dieselben Zahlen. Das 95-%-Band reicht vom "
        "2,5-%- bis zum 97,5-%-Wert der Ziehungen."
    )
    if base is None:
        return
    doc.sub("Streuung über die Quartale")
    rows = []
    for a in _ordered(base.spreads):
        s = base.spreads[a]
        if s.net_return_mid.n == 0:
            continue
        rows.append(
            [
                depot_name(a),
                count(s.net_return_mid.n),
                pct(s.net_return_mid.mean),
                pct(s.net_return_mid.median),
                pct(s.net_return_mid.stdev, sign=False),
                f"{pct(s.net_return_mid.low)} bis {pct(s.net_return_mid.high)}",
                pct(s.excess.mean),
                pct(s.excess.stdev, sign=False),
                f"{pct(s.excess.low)} bis {pct(s.excess.high)}",
            ]
        )
    doc.table(
        [
            "Depot",
            "Quartale",
            "Netto: Mittel",
            "Median",
            "Std.-Abw.",
            "Spanne",
            "Übertrendite: Mittel",
            "Std.-Abw.",
            "Spanne",
        ],
        rows,
    )


def _codes(row: QualityRow) -> str:
    if not row.no_decision:
        return "keine"
    return "; ".join(
        f"{code}: {count(n)} ({pct(row.no_decision_share[code], 1, sign=False)})"
        for code, n in row.no_decision.items()
    )


def _quality_rows(rows: Sequence[QualityRow]) -> list[list[str]]:
    return [
        [
            "alle" if q.quarter == "all" else q.quarter,
            q.market,
            count(q.due),
            count(q.decided),
            pct(q.share, 1, sign=False),
            _codes(q),
            num(q.blocked_hours, 1),
            NOT_SHOWN if q.eligible is None else ("ja" if q.eligible else "nein"),
        ]
        for q in rows
    ]


_QUALITY_HEADERS = [
    "Quartal",
    "Markt",
    "fällige Entscheidungen",
    "getroffen",
    "Anteil",
    "ohne Entscheidung nach Code (Anteil)",
    "gesperrt durch Lücken in 15m, 1h, 4h (Stunden)",
    "zugelassen",
]


def _quality(doc: Doc, base: LabMetrics | None) -> None:
    doc.section("Datenqualität", "datenqualitaet")
    if base is None or not base.quality:
        doc.p("Keine Angaben.", "note")
        return
    doc.p(
        "Runden ohne Entscheidung je Markt, nach Fehlercode, und Stunden, in denen nur eine "
        "Lücke in einem höheren Intervall den Markt sperrte (wie live: der Bot handelt nicht auf "
        "lückenhaften Daten). Fällige Entscheidungen sind über die vier Strategien summiert."
    )
    marked = [i for i, q in enumerate(base.quality) if q.eligible is False]
    doc.table(_QUALITY_HEADERS, _quality_rows(base.quality), marked)


def _labels(labels: Iterable[str]) -> str:
    return ", ".join(sorted(labels, key=_quarter_start)) or "keines"


def _diagnosis(
    doc: Doc,
    kinds: _Variants,
    visible: Mapping[str, Sequence[QuarterResult]],
    base_rows: Sequence[QuarterResult],
    draws: int,
) -> None:
    doc.section("Diagnose: Was kosten die Sperrzeiten?", "diagnose")
    if not kinds.diagnostic:
        doc.p("Kein Diagnose-Lauf im Register.", "note")
        return
    for name in kinds.diagnostic:
        spec = kinds.specs[name]
        diag_rows = visible.get(name, [])
        doc.sub(f"Variante {name}")
        tolerance = (
            f"Lücken-Toleranz in höheren Intervallen: {spec.gap_window}"
            if spec.gap_window is not None
            else "Lücken-Toleranz: wie live"
        )
        doc.p(
            f"Diagnose, keine Strategieversion. {tolerance}. Der Lauf zeigt nur, wie viel die "
            "Sperrzeiten durch Datenlücken kosten oder bringen; der Live-Bot rechnet weiter mit "
            "seinem Code.",
            "warn",
        )
        if not diag_rows:
            doc.p("Keine Ergebnisse.", "note")
            continue
        _diagnosis_variant(doc, diag_rows, base_rows, draws)


def _diagnosis_variant(
    doc: Doc,
    diag_rows: Sequence[QuarterResult],
    base_rows: Sequence[QuarterResult],
    draws: int,
) -> None:
    # A wider gap tolerance lifts the decision share, so the diagnosis can grade quarters the
    # base run left out. Pooling each run over its own quarters would credit that different
    # period to the blocked times; only quarters graded in both runs are compared.
    diag_graded = {r.quarter.label for r in diag_rows if r.graded}
    base_graded = {r.quarter.label for r in base_rows if r.graded}
    if base_rows:
        common = diag_graded & base_graded
        text = (
            "Verglichen werden nur Quartale, die in beiden Läufen gewertet sind: "
            f"{_labels(common)}."
        )
        if only_diag := diag_graded - base_graded:
            text += (
                f" Nur in der Diagnose gewertet und deshalb nicht verglichen: {_labels(only_diag)}"
                " (dort fielen mit der anderen Lücken-Toleranz genug Entscheidungen)."
            )
        if only_base := base_graded - diag_graded:
            text += f" Nur im Basislauf gewertet, nicht verglichen: {_labels(only_base)}."
        doc.p(text)
    else:
        common = diag_graded
        doc.p("Kein Basislauf vorhanden: nur die Zahlen der Diagnose, ohne Vergleich.", "note")
    if not common:
        doc.p(
            "Kein Quartal ist in beiden Läufen gewertet; ein Vergleich der Renditen entfällt."
            if base_rows
            else "Kein Quartal der Diagnose ist gewertet; es gibt keine Renditen."
        )
    else:
        s = metrics.summarize([r for r in diag_rows if r.quarter.label in common], draws=draws)
        compared_base = [r for r in base_rows if r.quarter.label in common]
        b = metrics.summarize(compared_base, draws=draws) if compared_base else None
        rows = []
        for a in _ordered(s.round_trips):
            u = s.uncertainty.get(a)
            ub = b.uncertainty.get(a) if b else None
            diff = u.mean_return.estimate - ub.mean_return.estimate if u and ub else None
            rows.append(
                [
                    depot_name(a),
                    pct(ub.mean_return.estimate) if ub else NOT_SHOWN,
                    band_pct(u.mean_return if u else None),
                    pct(diff),
                    count(b.round_trips.get(a, 0)) if b else NOT_SHOWN,
                    count(s.round_trips[a]),
                ]
            )
        doc.table(
            [
                "Depot",
                "Netto p. a. Basis",
                "Netto p. a. Diagnose [Band]",
                "Unterschied",
                "Round-Trips Basis",
                "Round-Trips Diagnose",
            ],
            rows,
        )
    base_labels = {r.quarter.label for r in base_rows}
    shared = {r.quarter.label for r in diag_rows}
    if base_rows:
        shared &= base_labels
        doc.p(
            "Gesperrte Stunden und Entscheidungen über die Quartale, die in beiden Läufen "
            f"vorliegen (gewertet oder nicht): {_labels(shared)}."
        )
    totals = _quality_totals([r for r in diag_rows if r.quarter.label in shared])
    base_totals = _quality_totals([r for r in base_rows if r.quarter.label in shared])
    qrows = [
        [
            m,
            num(base_totals[m].blocked_hours, 1) if m in base_totals else NOT_SHOWN,
            num(totals[m].blocked_hours, 1),
            pct(base_totals[m].share, 1, sign=False) if m in base_totals else NOT_SHOWN,
            pct(totals[m].share, 1, sign=False),
        ]
        for m in sorted(totals)
    ]
    doc.table(
        [
            "Markt",
            "gesperrte Stunden Basis",
            "gesperrte Stunden Diagnose",
            "Entscheidungen Basis",
            "Entscheidungen Diagnose",
        ],
        qrows,
    )


def _quality_totals(rows: Sequence[QuarterResult]) -> dict[str, QualityRow]:
    return {q.market: q for q in metrics.quality(rows) if q.quarter == "all"}


def _fidelity(doc: Doc, f: FidelitySummary | None) -> None:
    doc.section("Treue zum Live-Bot", "treue")
    doc.p(
        "Der Treue-Test baut aufgezeichnete Live-Entscheidungen mit dem Labor nach. Erst ab "
        f"{FIDELITY_MIN} Vergleichen je Markt mit gleicher Aktion, gleichen Indikatoren "
        "(± 1 Stelle) und ohne ungeklärte Abweichung gilt die Treue als belegt."
    )
    if f is None:
        doc.p("Kein Treue-Test beigefügt: Treue nicht belegt.", "warn")
        return
    est = _established(f)
    rows = []
    for m, n in sorted(f.compared.items()):
        if est[m]:
            verdict = "belegt"
        elif n < FIDELITY_MIN:
            verdict = f"Treue nicht belegt (unter {FIDELITY_MIN} Vergleiche)"
        elif f.established[m]:
            verdict = "Treue nicht belegt (Indikatoren weichen ab)"
        else:
            verdict = "Treue nicht belegt (Abweichungen)"
        rows.append(
            [
                m,
                count(n),
                f"{count(f.equal_action.get(m, 0))} von {count(n)}",
                f"{count(f.indicators_ok.get(m, 0))} von {count(n)}",
                verdict,
            ]
        )
    if not rows:
        doc.p("Der Treue-Test enthält keine Vergleiche: Treue nicht belegt.", "warn")
    doc.table(
        [
            "Markt",
            "Vergleiche",
            "Aktion und Ziel gleich",
            "Indikatoren gleich (± 1 Stelle)",
            "Ergebnis",
        ],
        rows,
    )
    doc.p(
        "Mit Beobachtungen (Spec 015) geprüft: " + ("ja" if f.with_observations else "nein") + "."
    )
    if f.note:
        doc.p(f"Hinweis des Treue-Tests: {f.note}", "note")
    if f.deviations:
        doc.sub(f"Abweichungen ({count(len(f.deviations))})")
        shown = f.deviations[:MAX_DEVIATIONS]
        doc.table(
            ["Markt", "Zeit (UTC)", "Depot", "Feld", "live", "nachgebaut", "Ursache"],
            [
                [
                    str(d.get("market", "")),
                    str(d.get("utc", "")),
                    str(d.get("agent", "")),
                    str(d.get("field", "")),
                    str(d.get("live", "")),
                    str(d.get("rebuilt", "")),
                    str(d.get("cause") or "ungeklärt"),
                ]
                for d in shown
            ],
            [i for i, d in enumerate(shown) if not d.get("cause")],
        )
        if len(f.deviations) > MAX_DEVIATIONS:
            doc.p(
                f"Weitere {count(len(f.deviations) - MAX_DEVIATIONS)} Abweichungen stehen in "
                f"{FIDELITY_FILE}.",
                "note",
            )


def _assumptions(doc: Doc) -> None:
    defaults = ", ".join(f"{m} {pct(v, 2, sign=False)}" for m, v in sorted(DEFAULT_SPREAD.items()))
    doc.section("Annahmen", "annahmen")
    doc.ul(
        [
            "Spread: Kerzen haben keine Geld- und Briefkurse. Je Quartal gilt ein angenommener "
            "Spread aus Stichproben öffentlicher Trades (wenige zufällig gewählte Stunden je "
            "Woche, oberes Viertel der Stundenwerte, mindestens "
            f"{pct(FLOOR, 2, sign=False)}), sonst ein vorsichtiger Standardwert ({defaults}). "
            "Die Stichproben stammen aus dem ganzen Quartal, auch aus der Zeit nach einer "
            "Entscheidung.",
            "Kurs: Mittelkurs ist der Schlusskurs der letzten abgeschlossenen Minutenkerze vor "
            "der Rundenzeit; Kauf- und Verkaufskurs liegen einen halben Spread darüber und "
            "darunter. Fehlt eine Minute, gilt die letzte davor.",
            "Laufende Kerze: Sie zählt bei der Fensterlänge von 500 Zeilen mit, wenn das Archiv "
            "eine Zeile für sie hat, und wird danach wie live verworfen; eine Näherung an die "
            "Antwort, die der Live-Bot bekommt.",
            "Drawdown: Tageswerte und maximaler Drawdown beruhen auf dem Stand am Tagesende "
            "(letzte Runde des UTC-Tages, zum Mittelkurs); Einbrüche innerhalb eines Tages fehlen "
            "darin. Der Drawdown-Stopp (10 %) prüft wie live in jeder Runde und beendet das Depot "
            "nur im laufenden Quartal; danach hält es Bargeld.",
            "Quartale: Jedes Quartal beginnt frisch mit 10.000 € je Depot. Am Quartalsende steht "
            "das Ergebnis zweimal im Bericht: zum Mittelkurs bewertet und verkauft mit allen "
            "Kosten.",
            "Buy & Hold gleicher Quote: je Markt die mittlere Investitionsquote der Strategie im "
            "Quartal, täglich auf diese Quote zurückgesetzt. Mit Kosten: Einstieg am ersten Tag "
            "und tägliches Umschichten mit Gebühr, Slippage und halbem Spread; der Ausstieg wird "
            "nicht berechnet, weil auch die Strategie zum Mittelkurs bewertet wird.",
            "Brutto ist netto plus die gezahlten Gebühren und die Slippage; der Spread bleibt im "
            "Brutto enthalten.",
            "Versiegelt: Daten ab 2025-10-01 werden für diesen Bericht weder gerechnet noch "
            "gezeigt; sie bleiben der unberührte Prüfzeitraum für spätere Kandidaten.",
            "GPT-Depots fehlen: GPT wird nur live bewertet, nie auf historischen Kursen.",
            "Zulassung: gewertet ab Q2 2021 und nur bei mindestens 90 % fälliger Entscheidungen je "
            "Markt; Daten werden nie aufgefüllt (Lücken bleiben Lücken).",
            "Läufe ohne git-Prüfung gelten im Register als „unsauberer Arbeitsbaum“.",
        ]
    )


def _trials(
    doc: Doc,
    trial_count: int,
    config_count: int | None,
    runs: Sequence[UsedRun],
    kinds: _Variants,
    visible: Mapping[str, Sequence[QuarterResult]],
) -> None:
    doc.section("Zahl der Versuche", "versuche")
    text = (
        f"Im Prüfregister stehen {count(trial_count)} Versuche (je Konfiguration, Quartal und "
        "Variante; gleiche Wiederholungen zusammengefasst, Diagnose eingeschlossen)"
    )
    if config_count is not None:
        text += f" aus {count(config_count)} Konfigurationen"
    doc.p(text + ".")
    doc.p(
        "Warum das zählt: Wer viele Varianten prüft, findet eine, die zufällig gut aussieht. "
        "Bei 50 Versuchen über vier Jahre erreicht die beste Variante schon durch Zufall eine "
        "Sharpe-Quote um 1,1. Die Bänder in diesem Bericht gelten je Depot einzeln und sind dafür "
        "nicht korrigiert; die Hürden für neue Strategieversionen (Deflated Sharpe Ratio, "
        "Holm/BHY) legt Spec 018 mit dieser Zahl fest."
    )
    used = [u for u in runs if not Quarter.parse(u.row.quarter).sealed]
    if used:
        commits: dict[str, int] = {}
        for u in used:
            commits[u.row.commit[:12]] = commits.get(u.row.commit[:12], 0) + 1
        clean = sum(1 for u in used if u.row.clean)
        doc.p(
            f"Dem Bericht liegen {count(len(used))} Läufe zugrunde: je Quartal und Variante der "
            "zuletzt aufgezeichnete, gleiche Wiederholungen eingeschlossen. Code-Commits: "
            + ", ".join(f"{c} ({count(n)})" for c, n in sorted(commits.items()))
            + f"; mit sauberem Arbeitsbaum: {count(clean)} von {count(len(used))}."
        )
        doc.table(
            [
                "Variante",
                "Quartal",
                "Lauf",
                "Code-Commit",
                "zuletzt aufgezeichnet",
                "Ausführungen",
                "sauberer Arbeitsbaum",
            ],
            [
                [
                    u.row.variant,
                    u.row.quarter,
                    count(u.row.run_id),
                    u.row.commit[:12],
                    u.last_recorded,
                    count(u.row.repeats),
                    "ja" if u.row.clean else "nein",
                ]
                for u in sorted(used, key=lambda u: (u.row.variant, _quarter_start(u.row.quarter)))
            ],
        )
    rows = [
        [
            name,
            "Diagnose" if name in kinds.diagnostic else "Basis" if name == kinds.base else "Kosten",
            count(len(rows)),
            count(sum(1 for r in rows if r.graded)),
        ]
        for name, rows in sorted(visible.items())
    ]
    doc.table(["Variante", "Art", "Quartale", "davon gewertet"], rows)


def _runtime(
    doc: Doc,
    kinds: _Variants,
    visible: Mapping[str, Sequence[QuarterResult]],
    compute: float,
    extra: Mapping[str, float],
) -> None:
    doc.section("Laufzeit", "laufzeit")
    rows = [
        [name, count(len(rs)), count(sum(r.rounds for r in rs)), num(sum(r.seconds for r in rs), 1)]
        for name, rs in sorted(visible.items())
    ]
    doc.p(
        "Rechenzeit der Läufe als Summe über die Quartale; die Quartale liefen parallel, die "
        "Wartezeit war also kürzer."
    )
    doc.table(["Variante", "Quartale", "Runden", "Rechenzeit (s)"], rows)
    items = [f"Kennzahlen und Bericht: {num(compute, 1)} s"]
    items += [f"{label}: {num(seconds, 1)} s" for label, seconds in extra.items()]
    doc.ul(items)


# --- writing -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportFiles:
    directory: Path
    files: list[Path]


def run_id_for(created_at: float, commit: str) -> str:
    """``20261108T120000Z-1a2b3c4``: sortable, and the commit tells which code wrote it."""
    short = re.sub(r"[^0-9a-z]", "", commit.lower())[:7] or "unknown"
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created_at)) + f"-{short}"


def _write_new(path: Path, text: str) -> Path:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError as exc:
        raise ReportExists(f"{path} exists; reports are never overwritten") from exc
    return path


def write_report(
    base: Path,
    run_id: str,
    markdown: str,
    html_text: str,
    *,
    register: Register | None = None,
    fidelity_json: str | None = None,
) -> ReportFiles:
    """Write ``<base>/reports/<run_id>/`` with report.md, report.html, a verified copy of the
    register chain and the fidelity JSON. The directory is created exclusively: an existing
    one (or any existing file) raises ReportExists and nothing in it is touched."""
    if not _RUN_ID.fullmatch(run_id):
        raise ReportError(f"invalid run id {run_id!r}")
    parent = base / REPORTS_DIR
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / run_id
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise ReportExists(f"{target} exists; reports are never overwritten") from exc
    files = [
        _write_new(target / MARKDOWN_FILE, markdown),
        _write_new(target / HTML_FILE, html_text),
    ]
    if register is not None:
        dest = target / CHAIN_FILE
        try:
            register.copy_chain(dest)
        except FileExistsError as exc:
            raise ReportExists(f"{dest} exists; reports are never overwritten") from exc
        files.append(dest)
    if fidelity_json is not None:
        files.append(_write_new(target / FIDELITY_FILE, fidelity_json))
    return ReportFiles(target, files)


# --- loading from the register -------------------------------------------------------------


def newest_results(register: Register) -> tuple[dict[str, list[QuarterResult]], list[UsedRun]]:
    """Per quarter and variant the run recorded last, grouped by variant name.

    "Last" goes by the chain's events, not the run id: an identical rerun is merged into its
    older run as a repeat, so the highest id can be an outcome that was superseded later (for
    example a test on a discarded branch, then the same quarter again on main). Sealed quarters
    are skipped before their result is even loaded."""
    last: dict[int, tuple[int, str]] = {}
    for seq, run_id, created in register.con.execute(
        "SELECT seq, run_id, created_at FROM events ORDER BY seq"
    ):
        last[int(run_id)] = (int(seq), str(created))
    newest: dict[tuple[str, str], tuple[tuple[int, int], RunRow]] = {}
    for row in register.list_runs():
        order = (last.get(row.run_id, (0, ""))[0], row.run_id)
        key = (row.quarter, row.variant)
        if key not in newest or order > newest[key][0]:
            newest[key] = (order, row)
    out: dict[str, list[QuarterResult]] = {}
    used: list[UsedRun] = []
    for (label, variant), (_, row) in sorted(newest.items()):
        if Quarter.parse(label).sealed:
            continue
        out.setdefault(variant, []).append(register.result(row.run_id))
        recorded = last.get(row.run_id, (0, row.created_at))[1]
        used.append(UsedRun(row, recorded))
    return out, used


def spread_sources_from_archive(path: Path) -> dict[tuple[str, str], SpreadSource]:
    """Source of each stored quarter spread, read-only; empty without an archive."""
    from llm_trading_bot.lab.archive import Archive, ArchiveError

    if not path.is_file():
        return {}
    try:
        with Archive.open_readonly(path) as archive:
            rows = archive.db.execute(
                "SELECT market, quarter, spread, samples FROM spreads WHERE method=?",
                (SPREAD_METHOD,),
            ).fetchall()
    except ArchiveError:
        return {}
    return {
        (str(q), str(m)): SpreadSource("sampled", float(s), int(n))
        for m, q, s, n in rows
        # The backtest never uses a sealed quarter's row either (ADR-0026).
        if not Quarter.parse(str(q)).sealed
    }


def _with_defaults(
    sampled: Mapping[tuple[str, str], SpreadSource],
    results: Mapping[str, Sequence[QuarterResult]],
    known: bool,
) -> dict[tuple[str, str], SpreadSource]:
    """Every quarter and market without a sample used the default (if the archive was read)."""
    out = dict(sampled)
    if not known:
        return out
    # The same fallback the backtest takes, so a default is only claimed where it was used.
    model = QuarterSpreads({})
    for rows in results.values():
        for r in rows:
            for m in r.spread:
                default = model.spread(m, r.quarter.start)
                out.setdefault((r.quarter.label, m), SpreadSource("default", default))
    return out


class FidelityUnreadable(ReportError):
    """The fidelity JSON is not a fidelity summary; the message is German, for the CLI."""


_INT_MAPS = ("compared", "equal_action", "indicators_ok")


def load_fidelity(text: str) -> FidelitySummary:
    """A FidelitySummary from JSON, checked field by field.

    ``FidelitySummary.from_json`` trusts its input; a hand-edited or truncated file would crash
    the report later (e.g. ``established`` on a deviation without ``market``) instead of being
    refused here with a reason."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FidelityUnreadable(f"kein gültiges JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise FidelityUnreadable("erwartet ein JSON-Objekt")
    known = {f.name for f in dataclasses.fields(FidelitySummary)}
    if unknown := sorted(set(data) - known):
        raise FidelityUnreadable("unbekannte Felder: " + ", ".join(map(str, unknown)))
    for name in _INT_MAPS:
        value = data.get(name, {})
        if not isinstance(value, dict) or not all(
            isinstance(m, str) and type(n) is int and n >= 0 for m, n in value.items()
        ):
            raise FidelityUnreadable(f"„{name}“ muss je Markt eine ganze Zahl ab 0 sein")
    deviations = data.get("deviations", [])
    if not isinstance(deviations, list) or not all(
        isinstance(d, dict) and isinstance(d.get("market"), str) for d in deviations
    ):
        raise FidelityUnreadable("„deviations“ muss eine Liste mit je einem Feld „market“ sein")
    if not isinstance(data.get("with_observations", False), bool):
        raise FidelityUnreadable("„with_observations“ muss true oder false sein")
    if not isinstance(data.get("note", ""), str):
        raise FidelityUnreadable("„note“ muss ein Text sein")
    return FidelitySummary(**data)


def open_register_readonly(
    db_path: Path, chain_path: Path, *, code: CodeState | None = None
) -> Register:
    """The register for reading only (ADR-0016's rule: one writer, readers with ``mode=ro``).

    ``Register.open`` creates the schema in a blank file; a report must never do that, nor
    write anything else into ``runs.db``. An empty file is refused instead of initialised."""
    if not db_path.is_file():
        raise ReportError(f"kein Prüfregister in {db_path.parent}")
    if db_path.stat().st_size == 0:
        raise ReportError(f"das Prüfregister {db_path} ist leer")
    con = sqlite3.connect(
        db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20, isolation_level=None
    )
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=1")
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        check_register_schema(con)
    except BaseException:
        con.close()
        raise
    return Register(con, chain_path, code or code_state())


def cli_main(command: str, argv: Sequence[str], out: TextIO) -> int:
    """``trading-bot lab report [--dir DIR] [--fidelity FILE]``."""
    if command != "report":
        print(f"unbekannter Befehl: {command}", file=out)
        return EXIT_REFUSED
    parser = argparse.ArgumentParser(prog="trading-bot lab report")
    parser.add_argument("--dir", type=Path, default=None, help="Labor-Ordner (Standard: lab_dir())")
    parser.add_argument("--fidelity", type=Path, default=None, help="JSON des Treue-Tests")
    args = parser.parse_args(list(argv))
    base: Path = args.dir if args.dir is not None else lab_dir()
    runs_db = base / RUNS_FILE
    if not runs_db.is_file():
        print(f"Kein Prüfregister in {base}: zuerst trading-bot lab backtest.", file=out)
        return EXIT_REFUSED
    if runs_db.stat().st_size == 0:
        print(f"Das Prüfregister {runs_db} ist leer: zuerst trading-bot lab backtest.", file=out)
        return EXIT_REFUSED
    fidelity: FidelitySummary | None = None
    fidelity_json: str | None = None
    if args.fidelity is not None:
        try:
            fidelity_json = args.fidelity.read_text(encoding="utf-8")
            fidelity = load_fidelity(fidelity_json)
        except (OSError, ValueError, FidelityUnreadable) as exc:
            print(f"Treue-Test nicht lesbar ({args.fidelity}): {exc}", file=out)
            return EXIT_REFUSED
    loading = time.perf_counter()
    try:
        with open_register_readonly(runs_db, base / CHAIN_FILE) as register:
            check = register.verify()
            if not check.ok:
                print("Prüfregister-Kette fehlerhaft, kein Bericht:", file=out)
                for problem in check.problems:
                    print(f"  {problem}", file=out)
                return EXIT_REFUSED
            results, used = newest_results(register)
            if not results:
                print(
                    "Das Prüfregister enthält keine Läufe außerhalb des versiegelten Zeitraums.",
                    file=out,
                )
                return EXIT_REFUSED
            candles = base / CANDLES_FILE
            sources = _with_defaults(
                spread_sources_from_archive(candles), results, candles.is_file()
            )
            loaded = time.perf_counter() - loading
            created = time.time()
            commit = register.code.commit
            markdown, html_text = build_report(
                results,
                fidelity=fidelity,
                trial_count=register.trial_count(),
                config_count=register.config_count(),
                created_at=created,
                commit=commit,
                chain_check=check,
                spread_sources=sources,
                runtime={"Laden aus dem Register": loaded},
                runs=used,
            )
            written = write_report(
                base,
                run_id_for(created, commit),
                markdown,
                html_text,
                register=register,
                fidelity_json=fidelity_json,
            )
    except (RegisterError, ReportError, ValueError) as exc:
        print(f"Kein Bericht: {exc}", file=out)
        return EXIT_REFUSED
    except (SchemaError, sqlite3.Error) as exc:
        print(f"Kein Bericht: Das Prüfregister {runs_db} ist nicht lesbar ({exc}).", file=out)
        return EXIT_REFUSED
    print(f"Bericht geschrieben: {written.directory}", file=out)
    for path in written.files:
        print(f"  {path}", file=out)
    return EXIT_OK
