"""Spec 007 AK3-AK8: overview data from a DB written by real paper cycles (fake market and GPT)."""

import sqlite3
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.llm.agents import load_config
from llm_trading_bot.llm.budget import charged_today
from llm_trading_bot.llm.due import iso
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import START_CASH, open_db, write_tx
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.service.status import ServiceStatus
from llm_trading_bot.web import overview
from llm_trading_bot.web.readonly import reading

GAP = "trend ETH-EUR: MarketDataError: ETH-EUR: gap in recent 1m bars - no decision"
SHARES = {"gpt-luna": Decimal("0.50"), "gpt-sol": Decimal("2.50")}
AGENTS = ("gpt-luna", "gpt-sol", BUYHOLD)


def fixed(at: float) -> Callable[[], float]:
    return lambda: at


def write(path: Path, *cycles_at: float) -> None:
    con = open_db(path, agents=AGENTS)
    try:
        for at in cycles_at:
            paper_cycle(
                con,
                ScenarioClient(),
                load_config({"OPENAI_API_KEY": KEY}),
                costs=Costs(),
                model=FakeModel(),
                clock=fixed(at),
            )
    finally:
        con.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "bot.db"
    write(path, NOW)
    return path


@pytest.fixture
def con(db: Path) -> Iterator[sqlite3.Connection]:
    with reading(db) as connection:
        yield connection


# ------------------------------------------------------------------ depots (AK3)


def test_depots_show_the_latest_equity_with_buyhold_last(con: sqlite3.Connection) -> None:
    rows = overview.depots(con)
    assert [d.agent for d in rows] == ["gpt-luna", "gpt-sol", BUYHOLD]
    stored = {
        r["agent"]: r
        for r in con.execute("SELECT * FROM equity WHERE ts=(SELECT MAX(ts) FROM equity)")
    }
    for d in rows:
        e = stored[d.agent]
        assert (d.value, d.cash, d.btc, d.eth, d.at) == (
            e["value"],
            e["cash"],
            e["btc"],
            e["eth"],
            e["ts"],
        )
        assert d.pnl_eur == pytest.approx(d.value - START_CASH)
        assert d.pnl_pct == pytest.approx((d.value / START_CASH - 1) * 100)
        assert not d.halted


def test_depots_compare_against_buyhold_in_percentage_points(con: sqlite3.Connection) -> None:
    rows = {d.agent: d for d in overview.depots(con)}
    bench = rows[BUYHOLD]
    assert bench.reference and bench.vs_buyhold_pp is None
    # The fake GPT buys 25 % of each market, Buy & Hold is fully invested: both paid fees.
    assert bench.pnl_eur < 0 and rows["gpt-luna"].btc > 0
    for agent in ("gpt-luna", "gpt-sol"):
        assert rows[agent].vs_buyhold_pp == pytest.approx(rows[agent].pnl_pct - bench.pnl_pct)


def test_depots_before_the_first_cycle_show_the_start_cash(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_db(path, agents=("trend",)).close()
    with reading(path) as con:
        (row,) = overview.depots(con)
    assert (row.value, row.at, row.pnl_eur, row.vs_buyhold_pp) == (START_CASH, None, 0.0, None)


def test_halted_depot_is_flagged(db: Path) -> None:
    con = open_db(db, agents=())
    with write_tx(con):
        con.execute("UPDATE portfolios SET halted=1 WHERE agent='gpt-sol'")
    con.close()
    with reading(db) as ro:
        assert {d.agent: d.halted for d in overview.depots(ro)}["gpt-sol"] is True


def test_drawdown_is_measured_against_the_depot_peak(db: Path) -> None:
    con = open_db(db, agents=())
    with write_tx(con):
        con.execute("UPDATE portfolios SET peak=peak*1.05 WHERE agent='gpt-luna'")
    con.close()
    with reading(db) as ro:
        rows = {d.agent: d for d in overview.depots(ro)}
        peaks = {r["agent"]: r["peak"] for r in ro.execute("SELECT agent, peak FROM portfolios")}
    luna = rows["gpt-luna"]
    assert luna.from_peak_pct == pytest.approx((luna.value / peaks["gpt-luna"] - 1) * 100)
    assert luna.from_peak_pct < -100 * 0.05 / 1.05  # fees had already cost a little
    # After a cycle the peak includes the latest point: never above it.
    assert rows["gpt-sol"].from_peak_pct <= 0
    assert (rows["gpt-luna"].stopped_at, rows["gpt-sol"].stopped_at) == (None, None)


def test_drawdown_before_the_first_cycle_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_db(path, agents=("trend",)).close()
    with reading(path) as con:
        (row,) = overview.depots(con)
    assert (row.from_peak_pct, row.stopped_at) == (0.0, None)


def test_risk_stop_shows_the_time_of_the_latest_forced_sale(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    con = open_db(path, agents=AGENTS)
    with write_tx(con):
        con.execute("UPDATE portfolios SET peak=20000 WHERE agent='gpt-luna'")
    con.close()
    write(path, NOW)  # the first GPT trade of luna triggers the stop
    with reading(path) as ro:
        rows = {d.agent: d for d in overview.depots(ro)}
        sold = ro.execute(
            "SELECT MAX(ts) FROM trades WHERE agent='gpt-luna' AND side='RISK_SELL'"
        ).fetchone()[0]
    luna = rows["gpt-luna"]
    assert luna.halted and luna.stopped_at == sold is not None
    assert luna.btc == 0 and luna.eth == 0
    assert luna.from_peak_pct < -10
    assert not rows["gpt-sol"].halted and rows["gpt-sol"].stopped_at is None


# ------------------------------------------------------------------ equity series (AK4)


def test_equity_series_has_one_line_per_depot(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    write(path, NOW, NOW + 300, NOW + 600)
    with reading(path) as con:
        series = overview.equity_series(con)
    assert [s.agent for s in series] == ["gpt-luna", "gpt-sol", BUYHOLD]
    assert all(len(s.points) == 3 for s in series)
    assert all(s.points[0][0] < s.points[-1][0] for s in series)


def test_equity_thinning_keeps_first_and_last_and_the_limit() -> None:
    points = [(float(t), float(t % 97)) for t in range(10_000)]
    kept = overview.thin(points, 240)
    assert len(kept) <= 240
    assert kept[0] == points[0] and kept[-1] == points[-1]
    assert [t for t, _ in kept] == sorted(t for t, _ in kept)


def test_equity_thinning_keeps_the_last_value_of_each_slice() -> None:
    points = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0)]
    # Two slices after the first point: [0, 2) keeps 2.0 at t=1, [2, 4] keeps the newest.
    assert overview.thin(points, 3) == [(0.0, 1.0), (1.0, 2.0), (4.0, 5.0)]


def _history(path: Path, rows: dict[str, list[int]]) -> None:
    """Equity rows per agent at the given epoch seconds (values vary, so order matters)."""
    open_db(path, agents=tuple(rows)).close()
    con = sqlite3.connect(path)
    try:
        with con:
            con.executemany(
                "INSERT INTO equity(ts,agent,value,cash,btc,eth) VALUES (?,?,?,0,0,0)",
                [
                    (iso(t), agent, 10_000 + (t * 7919 + n) % 977)
                    for agent, times in rows.items()
                    for n, t in enumerate(times)
                ],
            )
    finally:
        con.close()


@pytest.mark.parametrize("limit", [2, 3, 7, 240])
def test_indexed_thinning_equals_thinning_the_whole_history(tmp_path: Path, limit: int) -> None:
    start = int(NOW)
    rows = {
        "trend": [start + 60 * i for i in range(1_500)],
        # Paused for a long stretch (deselected, chosen again later): empty slices in between.
        "breakout": [start + 60 * i for i in (*range(200), *range(1_300, 1_500))],
        "meanrev": [start + 60 * i + 5 for i in range(30)],  # short: kept whole up to the limit
        BUYHOLD: [start + 37 * i for i in range(2_000)],  # odd step: slice edges fall between
    }
    path = tmp_path / "bot.db"
    _history(path, rows)
    with reading(path) as con:
        for agent in rows:
            full = [
                (overview.epoch(r["ts"]), float(r["value"]))
                for r in con.execute(
                    "SELECT ts, value FROM equity WHERE agent=? ORDER BY ts", (agent,)
                )
            ]
            assert overview._agent_series(con, agent, limit) == overview.thin(full, limit)


def test_a_long_history_is_read_with_a_few_queries_per_slice(tmp_path: Path) -> None:
    # A year of minute rounds is ~525 000 rows per depot; reading them all took seconds per page.
    start = int(NOW)
    path = tmp_path / "bot.db"
    _history(path, {a: [start + 60 * i for i in range(40_000)] for a in ("trend", BUYHOLD)})
    statements: list[str] = []
    with reading(path) as con:
        con.set_trace_callback(statements.append)
        series = overview.equity_series(con)
        con.set_trace_callback(None)
    assert [len(s.points) for s in series] == [overview.MAX_POINTS] * 2
    # Per depot: head, newest, then two indexed lookups per slice - never a full read.
    assert len(statements) <= 1 + 2 * (2 + 2 * (overview.MAX_POINTS - 1))
    reads = [s for s in statements if "FROM equity" in s]
    assert reads and all(" LIMIT " in s for s in reads)  # no unbounded read of the table


def test_short_series_are_not_thinned() -> None:
    points = [(0.0, 1.0), (5.0, 2.0)]
    assert overview.thin(points, 240) == points
    assert overview.thin([], 240) == []


# ------------------------------------------------------------------ liveness (AK5)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, overview.RUNNING),
        (15 * 60, overview.RUNNING),
        (15 * 60 + 1, overview.DELAYED),
        (60 * 60, overview.DELAYED),
        (60 * 60 + 1, overview.STOPPED),
        (None, overview.STOPPED),
    ],
)
def test_liveness_levels_at_their_boundaries(age: float | None, expected: str) -> None:
    assert overview.level(age) == expected


def test_liveness_reports_last_cycle_and_each_quote(con: sqlite3.Connection) -> None:
    pulses = overview.liveness(con, NOW + 120)
    assert [p.label for p in pulses] == ["Letzter Zyklus", "Kurs BTC-EUR", "Kurs ETH-EUR"]
    assert pulses[0].at == iso(NOW) and pulses[0].age_seconds == pytest.approx(120)
    assert all(p.level == overview.RUNNING for p in pulses)
    assert {p.level for p in overview.liveness(con, NOW + 3601)} == {overview.STOPPED}


def test_quotes_are_the_stored_book_of_each_market_in_symbol_order(con: sqlite3.Connection) -> None:
    stored = {
        r["symbol"]: r for r in con.execute("SELECT symbol, bid, ask, last_close FROM market")
    }
    quotes = overview.quotes(con)
    assert [q.market for q in quotes] == ["BTC-EUR", "ETH-EUR"]
    for q, name in zip(quotes, ("BTCEUR", "ETHEUR"), strict=True):
        assert (q.bid, q.ask, q.last_close) == tuple(stored[name][1:])
        assert q.ts == iso(NOW) and q.bid <= q.mid <= q.ask and q.spread_pct >= 0
    assert overview.load(con, NOW, SHARES).quotes == quotes


def test_quote_spread_is_relative_to_the_mid() -> None:
    q = overview.Quote("BTC-EUR", iso(NOW), 99.0, 101.0, 100.0, "bitvavo")
    assert q.mid == 100.0 and q.spread_pct == pytest.approx(2.0)
    assert overview.Quote("X", iso(NOW), 0.0, 0.0, 0.0, "x").spread_pct == 0.0


def test_liveness_of_an_empty_database_says_stopped(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_db(path, agents=("trend",)).close()
    with reading(path) as con:
        pulses = overview.liveness(con, NOW)
    assert all(p.at is None and p.level == overview.STOPPED for p in pulses)


# ------------------------------------------------------------------ decisions (AK6)


def test_decisions_show_the_newest_per_depot_and_market(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    write(path, NOW, NOW + 3600)  # next hour: GPT is due again and decides a new bar
    with reading(path) as con:
        rows = overview.latest_decisions(con)
        newest = {
            (r["agent"], r["symbol"]): r["id"]
            for r in con.execute("SELECT agent, symbol, MAX(id) AS id FROM decisions GROUP BY 1, 2")
        }
        ids = {
            (r["agent"], r["symbol"]): r["id"]
            for r in con.execute("SELECT id, agent, symbol FROM decisions")
            if r["id"] in newest.values()
        }
    assert len(rows) == len(newest) == 6  # 3 depots x 2 markets
    assert {(d.agent, d.market) for d in rows} == {
        (a, m) for a in AGENTS for m in ("BTC-EUR", "ETH-EUR")
    }
    assert ids == newest
    gpt = next(d for d in rows if d.agent == "gpt-luna")
    assert (gpt.action, gpt.target, gpt.status) == ("BUY", 0.25, "OK")
    assert gpt.reason == "Trend und Momentum stimmen überein"


# ------------------------------------------------------------------ GPT today (AK7)


def test_llm_today_matches_the_budget_lock(con: sqlite3.Connection) -> None:
    rows = {r.agent: r for r in overview.llm_today(con, NOW + 60, SHARES)}
    assert set(rows) == {"gpt-luna", "gpt-sol"}
    for agent, row in rows.items():
        assert (row.charged, row.exceeded) == charged_today(con, NOW + 60, agent)
        assert row.share == SHARES[agent]
        assert (row.requests, row.input_tokens, row.output_tokens, row.reasoning_tokens) == (
            1,
            1500,
            700,
            300,
        )
        assert (row.last_status, row.last_error) == ("OK", None)
    assert rows["gpt-sol"].charged > rows["gpt-luna"].charged > 0


def test_llm_today_starts_at_zero_on_the_next_utc_day(con: sqlite3.Connection) -> None:
    tomorrow = (NOW // 86400 + 1) * 86400 + 10
    rows = overview.llm_today(con, tomorrow, SHARES)
    assert [(r.requests, r.charged) for r in rows] == [(0, Decimal(0)), (0, Decimal(0))]
    assert all(r.last_status == "OK" for r in rows)  # the last call is still shown


def test_llm_today_shows_the_error_of_the_last_call(db: Path) -> None:
    con = open_db(db, agents=())
    with write_tx(con):
        con.execute(
            "UPDATE llm_calls SET status='ERROR', error='APITimeoutError' WHERE agent='gpt-sol'"
        )
    con.close()
    with reading(db) as ro:
        sol = {r.agent: r for r in overview.llm_today(ro, NOW + 60, SHARES)}["gpt-sol"]
    assert (sol.last_status, sol.last_error) == ("ERROR", "APITimeoutError")


# ------------------------------------------------------------------ trades (AK8)


def test_trades_newest_first_and_at_most_twenty(db: Path) -> None:
    con = open_db(db, agents=())
    with write_tx(con):
        for i in range(25):
            con.execute(
                "INSERT INTO trades(ts,agent,symbol,side,qty,fill,notional,fee,slip_rate)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    iso(NOW + 10 + i),
                    "gpt-luna",
                    "ETHEUR",
                    "SELL",
                    0.1,
                    100.0 + i,
                    10.0,
                    0.025,
                    0.0005,
                ),
            )
    con.close()
    with reading(db) as ro:
        trades = overview.recent_trades(ro)
    assert len(trades) == overview.TRADES == 20
    assert trades[0].ts == iso(NOW + 34) and trades[0].fill == 124.0
    assert [t.ts for t in trades] == sorted((t.ts for t in trades), reverse=True)
    assert trades[0].market == "ETH-EUR"


def test_load_bundles_everything(con: sqlite3.Connection) -> None:
    view = overview.load(con, NOW + 60, SHARES)
    assert len(view.depots) == 3 and len(view.series) == 3 and len(view.pulses) == 3
    assert len(view.decisions) == 6 and len(view.llm) == 2
    assert len(view.trades) == 6  # Buy & Hold 2 + two GPT depots x 2 markets


# ------------------------------------------------------------------ service pulse (Spec 008 AK8)


def _status(state: str = "running", **changes: object) -> ServiceStatus:
    base = ServiceStatus(state, 42, iso(NOW - 600), iso(NOW), 60, 5, last_round_at=iso(NOW))
    return base.but(**changes)


@pytest.mark.parametrize(
    ("state", "age", "expected"),
    [
        ("running", 0, overview.RUNNING),
        ("running", 180, overview.RUNNING),
        ("running", 181, overview.DELAYED),
        ("running", 900, overview.DELAYED),
        ("running", 901, overview.STOPPED),
        ("degraded", 0, overview.DELAYED),
        ("degraded", 901, overview.STOPPED),
        ("starting", 0, overview.DELAYED),
        ("stopping", 0, overview.STOPPED),
        ("stopped", 0, overview.STOPPED),
        ("stalled", 0, overview.STOPPED),
    ],
)
def test_service_pulse_levels_at_their_boundaries(state: str, age: float, expected: str) -> None:
    pulse = overview.service_pulse(_status(state), NOW + age)
    assert (pulse.label, pulse.level, pulse.at) == ("Dienst", expected, iso(NOW))
    assert pulse.age_seconds == pytest.approx(age)


def test_service_pulse_without_a_status_file_says_no_service() -> None:
    pulse = overview.service_pulse(None, NOW)
    assert (pulse.level, pulse.at, pulse.note) == (overview.NO_SERVICE, None, "noch nie gestartet")


def test_service_pulse_before_the_first_round_uses_the_last_update() -> None:
    pulse = overview.service_pulse(_status("starting", last_round_at=None), NOW + 10)
    assert pulse.at == iso(NOW) and pulse.note == "startet"


def test_service_pulse_names_failures_and_configuration_problems() -> None:
    one = overview.service_pulse(_status("degraded", failures=1), NOW)
    many = overview.service_pulse(_status("degraded", failures=3, config_warning="x"), NOW)
    assert one.note == "1 Runde mit Fehler"
    assert many.note == "3 Runden mit Fehler · Einstellungen ungültig"


@pytest.mark.parametrize(
    ("error", "result", "age", "level", "note"),
    [
        (
            GAP,
            "incomplete",
            60,
            overview.RUNNING,
            "ETH-EUR ohne Entscheidung: Marktdaten-Lücke bei Bitvavo",
        ),
        (
            GAP,
            "incomplete",
            181,
            overview.DELAYED,
            "ETH-EUR ohne Entscheidung: Marktdaten-Lücke bei Bitvavo",
        ),
        (
            "trend BTC-EUR: KeyError: 'close'",
            "incomplete",
            60,
            overview.DELAYED,
            "2 Runden mit Fehler",
        ),
        ("quotes: HTTPFailure: HTTP 503", "no_quotes", 60, overview.DELAYED, "2 Runden mit Fehler"),
        (f"RuntimeError: {GAP}", "error", 60, overview.DELAYED, "2 Runden mit Fehler"),
        (
            "breakout BTC-EUR: MarketDataError: BTC-EUR: 1m data stale or in the future (200s)",
            "incomplete",
            60,
            overview.RUNNING,
            "BTC-EUR ohne Entscheidung: Marktdaten von Bitvavo unbrauchbar",
        ),
        (
            "quotes: MarketDataError: spread too wide",
            "incomplete",
            60,
            overview.RUNNING,
            "Kurs von Bitvavo unbrauchbar, der Markt bleibt ohne Entscheidung",
        ),
    ],
    ids=[
        "gap",
        "gap-but-late",
        "code-error",
        "no-quotes",
        "error-quoting-a-gap",
        "stale-data",
        "quotes-fallback",
    ],
)
def test_a_round_that_only_lacked_market_data_shows_the_bot_running(
    error: str, result: str, age: float, level: str, note: str
) -> None:
    status = _status("degraded", failures=2, last_result=result, last_error=error)
    pulse = overview.service_pulse(status, NOW + age)
    assert (pulse.level, pulse.note) == (level, note)


def test_service_pulse_of_an_unreadable_file_says_stopped() -> None:
    pulse = overview.service_pulse(None, NOW, problem="Statusdatei unlesbar (ValueError)")
    assert (pulse.level, pulse.note) == (overview.STOPPED, "Statusdatei unlesbar (ValueError)")


def test_service_pulse_comes_first_in_the_overview(con: sqlite3.Connection) -> None:
    service = overview.service_pulse(_status(), NOW + 5)
    view = overview.load(con, NOW + 5, SHARES, service=service)
    assert [p.label for p in view.pulses] == [
        "Dienst",
        "Letzter Zyklus",
        "Kurs BTC-EUR",
        "Kurs ETH-EUR",
    ]


def test_risk_stop_time_ignores_sales_from_before_the_latest_release(tmp_path: Path) -> None:
    from llm_trading_bot.paper.risk import epoch, release

    path = tmp_path / "bot.db"
    con = open_db(path, agents=AGENTS)
    with write_tx(con):
        con.execute("UPDATE portfolios SET peak=20000 WHERE agent='gpt-luna'")
    con.close()
    write(path, NOW)
    con = open_db(path, agents=AGENTS)
    sold = con.execute("SELECT MAX(ts) FROM trades WHERE side='RISK_SELL'").fetchone()[0]
    assert release(con, "gpt-luna", epoch(sold) + 1, epoch(sold) + 2) == ""
    with write_tx(con):  # stopped again without a forced sale
        con.execute("UPDATE portfolios SET halted=1 WHERE agent='gpt-luna'")
    con.close()
    with reading(path) as ro:
        luna = {d.agent: d for d in overview.depots(ro)}["gpt-luna"]
    assert luna.halted and luna.stopped_at is None
