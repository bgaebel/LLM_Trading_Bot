"""Command line entry point (`uv run trading-bot ...`)."""

import argparse
import io
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from llm_trading_bot.core import parse_mode
from llm_trading_bot.llm.agents import load_config
from llm_trading_bot.llm.openai_client import OpenAIResponses, SignalModel
from llm_trading_bot.market.bitvavo import BitvavoClient, fill_tiny_gaps
from llm_trading_bot.market.models import SYMBOLS, Candle, Quote, Symbol
from llm_trading_bot.market.snapshot import (
    DATA_ERRORS,
    INTERVALS,
    MarketClient,
    MarketSnapshot,
    build_snapshots,
)
from llm_trading_bot.paper.cycle import STRATEGY_TIMEFRAME, paper_cycle
from llm_trading_bot.paper.db import SchemaError, open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.lock import WriterLock, WriterLockBusy
from llm_trading_bot.paper.report import CycleReport, LlmOutcome, MarketResult
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import ARENA_SLIPPAGE, ARENA_TAKER_FEE, cost_roundtrip
from llm_trading_bot.strategy.signals import STRATEGIES, STRATEGY_VERSION, run_strategy
from llm_trading_bot.web.app import serve
from llm_trading_bot.web.credentials import load_env_file

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_FETCH_FAILED = 2
EXIT_LOCKED = 3


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def run_market(client: MarketClient, *, out: TextIO, clock: Callable[[], float] = time.time) -> int:
    """Fetch live data once, print it and say per market whether it would be usable."""
    fetched_at = int(clock())
    print(f"Bitvavo public market data, fetched {_utc(fetched_at)}", file=out)
    bars: dict[Symbol, dict[int, tuple[Candle, ...]]] = {}
    filled: dict[Symbol, dict[int, int]] = {}
    errors: dict[Symbol, str] = {}
    for symbol in SYMBOLS:
        try:
            bars[symbol], filled[symbol] = {}, {}
            for tf in INTERVALS:
                raw = client.closed_candles(symbol, tf, now_epoch=fetched_at)
                gaps = 2 if tf == 1 else 0
                bars[symbol][tf], filled[symbol][tf] = fill_tiny_gaps(raw, tf, max_missing=gaps)
        except DATA_ERRORS as exc:
            errors[symbol] = f"candles: {type(exc).__name__}: {exc}"
    try:
        quotes: dict[Symbol, Quote] = client.quotes()
    except DATA_ERRORS as exc:
        print(f"quotes FAILED: {type(exc).__name__}: {exc}", file=out)
        return EXIT_FETCH_FAILED
    quote_at = int(clock())

    result = EXIT_OK
    for symbol in SYMBOLS:
        print(f"\n{symbol.market}", file=out)
        quote = quotes[symbol]
        print(
            f"  quote  bid {quote.bid:.2f}  ask {quote.ask:.2f}  spread {quote.spread_bps:.2f} bp",
            file=out,
        )
        if symbol in errors:
            print(f"  snapshot: INVALID - {errors[symbol]}", file=out)
            result = EXIT_INVALID
            continue
        for tf in INTERVALS:
            seq = bars[symbol][tf]
            last = (
                f"last closed {_utc(seq[-1].ts + tf * 60)}  close {seq[-1].close:.2f}"
                if seq
                else "no closed bars"
            )
            print(f"  {tf:>4}m  {len(seq):>3} bars  {last}  filled {filled[symbol][tf]}", file=out)
        snap = MarketSnapshot(symbol, bars[symbol], quote, fetched_at, quote_at, filled[symbol])
        try:
            snap.validate(now_epoch=int(clock()))
            print("  snapshot: OK", file=out)
        except DATA_ERRORS as exc:
            print(f"  snapshot: INVALID - {exc}", file=out)
            result = EXIT_INVALID
    return result


def run_signals(
    client: MarketClient, *, out: TextIO, clock: Callable[[], float] = time.time
) -> int:
    """Fetch one validated snapshot per market and print every classic strategy's signal.

    No positions exist yet, so every strategy is asked with current_weight 0.
    """
    problems: dict[Symbol, str] = {}

    def on_error(symbol: Symbol, exc: Exception) -> None:
        problems[symbol] = f"{type(exc).__name__}: {exc}"

    snaps = build_snapshots(client, clock=clock, on_error=on_error)
    print(
        f"Signals, strategy {STRATEGY_VERSION}, {_utc(int(clock()))}, "
        f"costs: fee {ARENA_TAKER_FEE:.2%} + slippage {ARENA_SLIPPAGE:.2%} per side",
        file=out,
    )
    for symbol in SYMBOLS:
        snap = snaps.get(symbol)
        if snap is None:
            print(
                f"\n{symbol.market}: no signals - {problems.get(symbol, 'no snapshot')}", file=out
            )
            continue
        cost = cost_roundtrip(snap.quote.spread_bps, ARENA_TAKER_FEE, ARENA_SLIPPAGE)
        ind = indicators(snap, cost)
        print(
            f"\n{symbol.market}  regime {ind['regime']}  ADX {ind['adx_1h']:.1f}  "
            f"roundtrip cost {cost:.3%}",
            file=out,
        )
        for name in STRATEGIES:
            sig = run_strategy(name, snap, ind, 0.0)
            print(
                f"  {name:<9} {sig.action:<4} target {sig.target_fraction:.2f}  "
                f"conf {sig.confidence:.2f}  {sig.reason}",
                file=out,
            )
    if not snaps:
        return EXIT_FETCH_FAILED
    return EXIT_OK if len(snaps) == len(SYMBOLS) else EXIT_INVALID


def _llm_line(o: LlmOutcome) -> str:
    parts = [f"{o.agent} ({o.model}): {o.status}" + (f" - {o.detail}" if o.detail else "")]
    if o.usage is not None:
        u = o.usage
        parts.append(
            f"tokens in {u.input_tokens} out {u.output_tokens} "
            f"(reasoning {u.reasoning_tokens or 0}, cached {u.cached_tokens or 0})"
        )
    if o.cost_eur is not None:
        parts.append(f"cost ~{o.cost_eur:.4f} EUR (estimate)")
    if o.regimes:
        parts.append("regime " + ", ".join(f"{s.market} {r}" for s, r in o.regimes.items()))
    return " | ".join(parts)


def _market_line(m: MarketResult) -> str:
    if m.error:
        return f"{m.agent} {m.symbol.market}: no decision - {m.error}"
    assert m.bar_ts is not None
    if m.agent in STRATEGY_TIMEFRAME:
        when = f"{STRATEGY_TIMEFRAME[m.agent]}m bar opened {_utc(m.bar_ts)}"
    else:
        when = f"attempt {m.attempt}"
    state = "new" if m.new else "already decided"
    trade = "traded" if m.traded else "no trade"
    return (
        f"{m.agent} {m.symbol.market}: {when} {state}: {m.action} target {m.target:.2f} "
        f"[{m.status}] {trade} - {m.reason}"
    )


def print_cycle(report: CycleReport, costs: Costs, *, out: TextIO) -> None:
    print(
        f"Paper cycle {_utc(report.at)}, depots {', '.join(report.agents)}, "
        f"classic strategy {STRATEGY_VERSION}, "
        f"costs: fee {costs.fee_rate:.2%} + slippage {costs.slippage:.2%} per side "
        "(virtual orders only)",
        file=out,
    )
    if report.quotes_error:
        print(f"quotes: {report.quotes_error}", file=out)
    if not report.equity:
        print("no fresh quotes of both markets - no decisions, nothing traded", file=out)
        return
    bought = report.buyhold_bought
    print(f"buy & hold: {f'bought {bought} market(s)' if bought else 'already invested'}", file=out)
    for o in report.llm:
        print(_llm_line(o), file=out)
    for m in report.markets:
        print(_market_line(m), file=out)
    for e in report.equity:
        print(
            f"equity {e.agent:<9} {e.value:>11.2f} EUR  cash {e.cash:.2f}  "
            f"BTC {e.btc:.8f}  ETH {e.eth:.8f}",
            file=out,
        )


def run_paper_cycle(
    db: Path,
    client: MarketClient,
    *,
    env: Mapping[str, str],
    out: TextIO,
    clock: Callable[[], float] = time.time,
    model_factory: Callable[[str], SignalModel] = OpenAIResponses,
    secrets: Path | None = None,
) -> int:
    """One live paper cycle on ``db``; refuses to run while another process owns the DB.

    Values saved with `trading-bot web` (``secrets.env``, default next to the DB) fill in what the
    environment does not set; the environment always wins.
    """
    try:
        env = {**load_env_file(secrets or db.parent / "secrets.env"), **env}
        config = load_config(env)
        costs = Costs.from_env(env)
    except ValueError as exc:
        print(f"paper-cycle configuration error: {exc}", file=out)
        return EXIT_INVALID
    model = model_factory(config.llm.api_key) if config.llm else None
    try:
        with WriterLock(db):
            con = open_db(db, agents=(*config.agents, BUYHOLD))
            try:
                report = paper_cycle(con, client, config, costs=costs, model=model, clock=clock)
            finally:
                con.close()
    except WriterLockBusy as exc:
        print(f"paper-cycle refused: {exc}", file=out)
        return EXIT_LOCKED
    except (SchemaError, sqlite3.DatabaseError) as exc:
        # Never repair or migrate: the history in that file must stay as it is (ADR-0012).
        print(f"paper-cycle refused, database error: {type(exc).__name__}: {exc}", file=out)
        return EXIT_INVALID
    print_cycle(report, costs, out=out)
    if not report.equity:
        return EXIT_FETCH_FAILED
    return EXIT_OK if report.complete else EXIT_INVALID


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading-bot")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("market", help="fetch live Bitvavo data once and validate it")
    commands.add_parser("signals", help="show the classic strategies' signals on live data")
    cycle = commands.add_parser("paper-cycle", help="run one paper trading cycle on live data")
    cycle.add_argument("--db", type=Path, required=True, help="path of the paper SQLite database")
    cycle.add_argument(
        "--secrets",
        type=Path,
        default=None,
        help="credentials file (default: secrets.env next to --db)",
    )
    web = commands.add_parser("web", help="local web UI: overview and credentials (127.0.0.1 only)")
    web.add_argument("--data-dir", type=Path, required=True, help="where secrets.env is kept")
    web.add_argument(
        "--db",
        type=Path,
        default=None,
        help="bot database, opened read-only (default: bot.db in --data-dir)",
    )
    web.add_argument("--port", type=int, default=8787)
    web.add_argument("--host", default="127.0.0.1", help="must stay 127.0.0.1 (ADR-0015)")
    args = parser.parse_args(argv)
    # LEARNING(#windows): Umlaute in der Konsolenausgabe kaputt unter Windows
    # The Windows console defaults to cp1252; strategy reasons contain German umlauts.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    mode = parse_mode(os.environ.get("TRADING_MODE"))
    if args.command == "market":
        return run_market(BitvavoClient(), out=sys.stdout)
    if args.command == "signals":
        return run_signals(BitvavoClient(), out=sys.stdout)
    if args.command == "paper-cycle":
        return run_paper_cycle(
            args.db, BitvavoClient(), env=os.environ, out=sys.stdout, secrets=args.secrets
        )
    if args.command == "web":
        try:
            serve(args.data_dir, port=args.port, host=args.host, out=sys.stdout, db=args.db)
        except ValueError as exc:
            print(f"web refused: {exc}", file=sys.stdout)
            return EXIT_INVALID
        except OSError as exc:
            # Typically the port is taken (another web UI, the Arena on 8787).
            print(f"web could not start on port {args.port}: {type(exc).__name__}: {exc}")
            return EXIT_INVALID
        except KeyboardInterrupt:
            return EXIT_OK
        return EXIT_OK
    print(f"llm-trading-bot: mode={mode.value}")
    return EXIT_OK
