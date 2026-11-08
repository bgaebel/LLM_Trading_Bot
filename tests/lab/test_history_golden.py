"""Spec 016 AK3: the historical client equals the live client on frozen real Bitvavo responses.

The real ``BitvavoClient`` reads the captured bodies through a fake transport; the
``HistoricalClient`` gets the same rows from memory. Both must give the same closed candles, and
the unchanged ``paper_cycle`` must write the same decisions, trades and equity with either.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path

import pytest

from fakes import FakeClock, FakeTransport
from lab_captures import MARKETS, Capture, captures, merged
from llm_trading_bot.lab.history import ConstantSpreads, HistoricalClient, MemorySource
from llm_trading_bot.llm.agents import BotConfig
from llm_trading_bot.market.bitvavo import TIMEFRAMES, BitvavoClient
from llm_trading_bot.market.models import QUOTE_MARKETS, SYMBOLS, ClosedCandles, QuoteBook
from llm_trading_bot.market.snapshot import MarketClient
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.risk import Controls
from llm_trading_bot.paper.usdc import BUYHOLD_USDC

CLASSIC = ("trend", "breakout", "meanrev", "ensemble")
USDC = tuple(f"{name}-usdc" for name in CLASSIC)
OFFSET = 5
"""Captures are taken at k*60 + 5 s, like the service's rounds."""


def live(capture: Capture) -> BitvavoClient:
    return BitvavoClient(FakeTransport(capture.handler()))


def frozen_book(capture: Capture) -> QuoteBook:
    return live(capture).quotes()


def fixed(book: QuoteBook) -> Callable[[], QuoteBook]:
    return lambda: book


def history(source: MemorySource, clock: FakeClock, end: int) -> HistoricalClient:
    return HistoricalClient(source, ConstantSpreads(), clock, start=0, end=end)


def observed(candles: ClosedCandles) -> tuple[object, ...]:
    return (
        tuple(candles),
        candles.received,
        candles.first_open,
        candles.last_open,
        candles.forming,
    )


def test_fixture_is_real_and_shaped_like_the_live_request() -> None:
    items = captures()
    assert len(items) >= 3
    ats = [c.at for c in items]
    assert all(at % 60 == OFFSET for at in ats)
    # t0, t0 + 60 s and a crossing of a 15-minute (and hour) boundary.
    assert any(b - a == 60 for a, b in pairwise(ats))
    assert any(a // 900 != b // 900 for a, b in pairwise(ats))
    for capture in items:
        book = frozen_book(capture)
        # Since Spec 020 the live client also reads the quote-only markets of the USDC route
        # from the same /ticker/book response; the frozen bodies hold them, unchanged.
        assert {s.market for s in book if s in SYMBOLS} == set(MARKETS)
        assert all(s in book for s in QUOTE_MARKETS), book.errors
        assert not book.errors
        for market in MARKETS:
            for interval in TIMEFRAMES:
                rows = capture.rows(market, interval)
                opens = [int(str(r[0])) // 1000 for r in rows]
                assert len(rows) == 500, (capture.at, market, interval)
                assert opens == sorted(opens, reverse=True)  # newest first, as Bitvavo sends
                assert all(isinstance(v, str) for r in rows for v in r[1:])
                assert opens[0] <= capture.at


def test_closed_bars_were_not_revised_between_captures() -> None:
    """Finding: a bar that had closed at an earlier capture is unchanged in every later one
    (only forming bars changed), so the archive's later rows equal what live saw. A later
    capture still wins in the merged source if Bitvavo ever revises a bar."""
    items = captures()
    compared = 0
    for early, late in pairwise(items):
        for market in MARKETS:
            for interval in TIMEFRAMES:
                step = interval * 60
                a = {int(str(r[0])) // 1000: r for r in early.rows(market, interval)}
                b = {int(str(r[0])) // 1000: r for r in late.rows(market, interval)}
                for ts in sorted(a.keys() & b.keys()):
                    if ts + step <= early.at:
                        assert a[ts] == b[ts], (early.at, market, interval, ts)
                        compared += 1
    assert compared > 1000


@pytest.mark.parametrize("capture", captures(), ids=lambda c: str(c.at))
def test_closed_candles_equal_live_client(capture: Capture) -> None:
    clock = FakeClock(capture.at)
    hist = history(capture.source(), clock, capture.at + 1)
    real = live(capture)
    forming_seen = 0
    # At the round time and at the exact minute edge before it (open + step == now there).
    for now in (capture.at, capture.at - OFFSET):
        # The candle markets only: live never requests candles of BTC-USDC or USDC-EUR
        # (Spec 020), and the frozen captures hold none.
        for symbol in SYMBOLS:
            for interval in TIMEFRAMES:
                want = real.closed_candles(symbol, interval, now_epoch=now)
                got = hist.closed_candles(symbol, interval, now_epoch=now)
                assert observed(got) == observed(want), (now, symbol, interval)
                forming_seen += want.forming
    # The forming bar was in the responses, so the 499-bar window really was exercised.
    assert forming_seen > 0


def _digest(con: sqlite3.Connection) -> dict[str, str]:
    out = {}
    for table, cols in (
        ("decisions", "ts,agent,symbol,bar_ts,model,action,target,confidence,reason,inputs,status"),
        ("trades", "ts,agent,symbol,side,qty,fill,notional,fee,slip_rate,decision_id"),
        ("equity", "ts,agent,value,cash,btc,eth"),
        ("portfolios", "agent,cash,btc,eth,peak,halted"),
    ):
        rows = [tuple(r) for r in con.execute(f"SELECT {cols} FROM {table} ORDER BY 1,2,3")]
        out[table] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return out


def _run(
    clients: list[tuple[int, MarketClient]], usdc: tuple[str, ...] = ()
) -> tuple[dict[str, str], sqlite3.Connection]:
    # Explicit: without USDC depots unless asked for, never the BOT_USDC_AGENTS default.
    config = BotConfig(CLASSIC, None, usdc)
    con = open_db(Path(":memory:"), agents=config.depots)
    for at, client in clients:
        report = paper_cycle(
            con,
            client,
            config,
            costs=Costs(),
            clock=FakeClock(at),
            controls=Controls(),
        )
        assert report.at == at
    return _digest(con), con


def _hist_run(source: str, usdc: tuple[str, ...] = ()) -> tuple[dict[str, str], sqlite3.Connection]:
    items = captures()
    end = items[-1].at + 1
    shared = merged(items)
    clients: list[tuple[int, MarketClient]] = []
    for capture in items:
        src = capture.source() if source == "per-capture" else shared
        hist = HistoricalClient(
            src,
            ConstantSpreads(),
            FakeClock(capture.at),
            start=0,
            end=end,
            quotes=fixed(frozen_book(capture)),
        )
        clients.append((capture.at, hist))
    return _run(clients, usdc)


def _live_run(usdc: tuple[str, ...] = ()) -> tuple[dict[str, str], sqlite3.Connection]:
    return _run([(c.at, live(c)) for c in captures()], usdc)


def test_paper_cycle_checksums_equal() -> None:
    """The unchanged paper_cycle over all captures, once per client, quotes from the frozen
    book; the historical client sees exactly the rows of each response."""
    want, con_live = _live_run()
    got, con_hist = _hist_run("per-capture")
    assert got == want
    # Not vacuous: decisions on several bars, buy & hold traded, every depot valued.
    decided = con_live.execute("SELECT COUNT(*), COUNT(DISTINCT bar_ts) FROM decisions").fetchone()
    assert decided[0] >= 8 and decided[1] >= 2
    assert con_live.execute("SELECT COUNT(*) FROM trades WHERE agent=?", (BUYHOLD,)).fetchone()[0]
    points = con_live.execute("SELECT COUNT(DISTINCT agent) FROM equity").fetchone()[0]
    assert points == len(CLASSIC) + 1
    # Without USDC depots the bot stores exactly the v0.3.1 markets, as before Spec 020.
    stored = {r[0] for r in con_live.execute("SELECT symbol FROM market")}
    assert stored == {s.name for s in SYMBOLS}
    con_live.close()
    con_hist.close()


def test_paper_cycle_checksums_equal_with_usdc_depots() -> None:
    """Spec 020 in the lab: the USDC depots decide on the same candles and trade on the frozen
    BTC-USDC and USDC-EUR quotes; the historical client gives the same rows as live."""
    want, con_live = _live_run(USDC)
    got, con_hist = _hist_run("per-capture", USDC)
    assert got == want
    decided = con_live.execute(
        "SELECT COUNT(*) FROM decisions WHERE agent LIKE '%-usdc' AND agent != ?", (BUYHOLD_USDC,)
    ).fetchone()[0]
    assert decided >= 8
    bought = con_live.execute(
        "SELECT symbol FROM trades WHERE agent=? ORDER BY symbol", (BUYHOLD_USDC,)
    ).fetchall()
    assert [r[0] for r in bought] == ["BTCUSDC", "ETHEUR"]
    points = con_live.execute("SELECT COUNT(DISTINCT agent) FROM equity").fetchone()[0]
    assert points == 2 * len(CLASSIC) + 2
    con_live.close()
    con_hist.close()


ROUNDING_STEP = 1e-5
"""Live stores indicators rounded to 5 decimals (payload.py)."""
RELATIVE = 1e-8
"""A one-bar shift of the window start moves EMA/ADX by about 1e-9 relative (research and the
finding below); for price-level values like ema72_1h at 68355 that is several rounding steps."""


def _decisions(con: sqlite3.Connection) -> list[tuple[object, ...]]:
    cols = "ts,agent,symbol,bar_ts,model,action,target,confidence,reason,status,inputs"
    return [tuple(r) for r in con.execute(f"SELECT {cols} FROM decisions ORDER BY 1,2,3,4")]


def test_merged_source_differs_only_by_the_forming_bar_approximation() -> None:
    """One source over the whole run, as the backtest has it (a later capture wins).

    Finding (2026-11-08 10:00:05 UTC): BTC-EUR had no trade in the first 5 s of the new
    15m/1h bar, so live got 500 closed bars; the archive has the bar's row and the client
    counts it, so 499. Trades, equity and every action are still identical; stored indicators
    move by about 1e-9 relative - for ema72_1h at 68355 that is 0.00008, eight rounding steps.
    """
    want, con_live = _live_run()
    got, con_hist = _hist_run("merged")
    for table in ("trades", "equity", "portfolios"):
        assert got[table] == want[table], table
    live_rows, hist_rows = _decisions(con_live), _decisions(con_hist)
    assert len(live_rows) == len(hist_rows)
    moved = 0
    for a, b in zip(live_rows, hist_rows, strict=True):
        assert a[:-1] == b[:-1]
        ia, ib = json.loads(str(a[-1])), json.loads(str(b[-1]))
        ind_a, ind_b = ia.pop("indicators", {}), ib.pop("indicators", {})
        assert ia == ib
        assert ind_a.keys() == ind_b.keys()
        for key, value in ind_a.items():
            if isinstance(value, float):
                limit = max(ROUNDING_STEP * 1.5, RELATIVE * abs(value))
                assert abs(value - ind_b[key]) <= limit, (a[:4], key)
                moved += value != ind_b[key]
            else:
                assert value == ind_b[key], (a[:4], key)
    # The approximation was really hit by the captured data (see the finding above).
    assert moved > 0
    con_live.close()
    con_hist.close()
