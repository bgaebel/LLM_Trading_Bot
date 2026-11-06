"""Command line entry point (`uv run trading-bot ...`)."""

import argparse
import io
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TextIO

from llm_trading_bot.core import parse_mode
from llm_trading_bot.market.bitvavo import BitvavoClient, fill_tiny_gaps
from llm_trading_bot.market.models import SYMBOLS, Candle, Quote, Symbol
from llm_trading_bot.market.snapshot import (
    DATA_ERRORS,
    INTERVALS,
    MarketClient,
    MarketSnapshot,
    build_snapshots,
)
from llm_trading_bot.strategy.indicators import indicators
from llm_trading_bot.strategy.payload import ARENA_SLIPPAGE, ARENA_TAKER_FEE, cost_roundtrip
from llm_trading_bot.strategy.signals import STRATEGIES, STRATEGY_VERSION, run_strategy

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_FETCH_FAILED = 2


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading-bot")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("market", help="fetch live Bitvavo data once and validate it")
    commands.add_parser("signals", help="show the classic strategies' signals on live data")
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
    print(f"llm-trading-bot: mode={mode.value}")
    return EXIT_OK
