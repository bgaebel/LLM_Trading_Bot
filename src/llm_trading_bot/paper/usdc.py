"""The USDC route of the ``*-usdc`` depots (Spec 020, ADR-0029/0030). Pure, no I/O.

Same rules as v0.3.1, other execution: these depots keep their cash in euro and trade BTC as
EUR -> USDC -> BTC (sell: BTC -> USDC -> EUR) on the books of USDC-EUR and BTC-USDC, because
BTC-USDC costs 0.05 % per side instead of 0.25 % on BTC-EUR. Signals still come from the BTC-EUR
and ETH-EUR candles (BTC-USDC is too thin for the gap rules); ETH stays on ETH-EUR with the
v0.3.1 costs. Every number of the route is defined here and nowhere else.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from llm_trading_bot.market.models import Quote, Symbol

USDC_VERSION = "v0.3.1-usdc"
"""ADR-0029: an execution variant of v0.3.1 - identical rules, its own version."""
USDC_SUFFIX = "-usdc"
BUYHOLD_USDC = "buyhold-usdc"
"""The benchmark of the USDC depots; runs whenever one of them is selected, like buy & hold."""

# Research 2026-11-08 (Bitvavo fee page read through search results, public API; Spec 020).
# Taker fees of the entry tier; not confirmed through /account/fees (needs a key, ADR-0004).
BTC_USDC_FEE = 0.0005
"""BTC-USDC, fee category B (the USDC markets)."""
BTC_USDC_SLIPPAGE = 0.0005
"""As on BTC-EUR in v0.3.1: the book is thinner, but 4,500 EUR stays far inside its depth."""
USDC_EUR_FEE = 0.0010
"""USDC-EUR, fee category C (stablecoin pairs)."""
USDC_EUR_SLIPPAGE = 0.0001
"""Measured 0.006 % for a 5,000 EUR market order; rounded up."""
ROUTE_FEE = BTC_USDC_FEE + USDC_EUR_FEE
"""Fees of one side of the route, both legs: each is charged on the same euro amount."""
ROUTE_SLIPPAGE = BTC_USDC_SLIPPAGE + USDC_EUR_SLIPPAGE
"""Slippage of one side, both legs, as stored in ``trades.slip_rate`` and shown in a context."""

ROUTE_MARKETS: tuple[Symbol, ...] = (Symbol.BTCUSDC, Symbol.USDCEUR)
"""The books that price, buy and sell the BTC of a USDC depot (BTC-EUR is not used for it)."""

Quotes = Mapping[Symbol, Quote]


def is_usdc(agent: str) -> bool:
    return agent.endswith(USDC_SUFFIX)


def strategy_of(agent: str) -> str:
    """The v0.3.1 strategy whose rules a depot follows: ``trend-usdc`` -> ``trend``."""
    return agent.removesuffix(USDC_SUFFIX)


def routed(agent: str, symbol: Symbol) -> bool:
    """True if ``agent`` trades and values ``symbol`` over the USDC route (only its BTC)."""
    return symbol is Symbol.BTCEUR and is_usdc(agent)


def route_markets(agent: str, symbol: Symbol) -> tuple[Symbol, ...]:
    """The markets whose fresh quotes ``agent`` needs to value, buy or sell ``symbol``."""
    return ROUTE_MARKETS if routed(agent, symbol) else (symbol,)


def trade_market(agent: str, symbol: Symbol) -> Symbol:
    """The market a trade is stored under: BTC-USDC for the routed BTC (the web shows it)."""
    return Symbol.BTCUSDC if routed(agent, symbol) else symbol


@dataclass(frozen=True)
class Fill:
    """One side of the route in euro, as a trade row stores it."""

    qty: float
    """BTC bought or sold."""
    fill: float
    """Effective price in EUR per BTC over both legs, slippage included."""
    notional: float
    """EUR spent (buy) or received before fees (sell)."""
    fee: float
    """Fees of both legs in EUR."""


def btc_mid(qs: Quotes) -> float:
    """EUR per BTC for valuation: BTC-USDC mid times USDC-EUR mid (ADR-0030)."""
    return qs[Symbol.BTCUSDC].mid * qs[Symbol.USDCEUR].mid


def buy(spend: float, qs: Quotes) -> Fill:
    """Buy BTC for ``spend`` EUR: EUR -> USDC at the USDC-EUR ask, USDC -> BTC at the BTC-USDC ask.

    - Leg 1: ``eur_per_usdc = ask(USDC-EUR) * (1 + USDC_EUR_SLIPPAGE)``,
      ``usdc = spend / eur_per_usdc``, fee ``spend * USDC_EUR_FEE``.
    - Leg 2: ``usdc_per_btc = ask(BTC-USDC) * (1 + BTC_USDC_SLIPPAGE)``,
      ``qty = usdc / usdc_per_btc``, fee ``usdc * BTC_USDC_FEE`` in USDC, converted at the price
      the USDC were bought for (``* eur_per_usdc``).

    As in v0.3.1 the fees are paid on top of ``spend`` (cash falls by ``spend + fee``) and no
    USDC is kept: the whole amount goes on into BTC.
    """
    eur_per_usdc = qs[Symbol.USDCEUR].ask * (1 + USDC_EUR_SLIPPAGE)
    usdc = spend / eur_per_usdc
    usdc_per_btc = qs[Symbol.BTCUSDC].ask * (1 + BTC_USDC_SLIPPAGE)
    qty = usdc / usdc_per_btc
    fee = spend * USDC_EUR_FEE + usdc * BTC_USDC_FEE * eur_per_usdc
    return Fill(qty, eur_per_usdc * usdc_per_btc, spend, fee)


def sell(qty: float, qs: Quotes) -> Fill:
    """Sell ``qty`` BTC: BTC -> USDC at the BTC-USDC bid, USDC -> EUR at the USDC-EUR bid.

    - Leg 1: ``usdc_per_btc = bid(BTC-USDC) * (1 - BTC_USDC_SLIPPAGE)``,
      ``usdc = qty * usdc_per_btc``, fee ``usdc * BTC_USDC_FEE`` in USDC.
    - Leg 2: ``eur_per_usdc = bid(USDC-EUR) * (1 - USDC_EUR_SLIPPAGE)``,
      ``notional = usdc * eur_per_usdc``, fee ``notional * USDC_EUR_FEE``; the leg-1 fee is
      converted at the same price.

    As in v0.3.1 the fees are taken from the proceeds (cash rises by ``notional - fee``).
    """
    usdc_per_btc = qs[Symbol.BTCUSDC].bid * (1 - BTC_USDC_SLIPPAGE)
    usdc = qty * usdc_per_btc
    eur_per_usdc = qs[Symbol.USDCEUR].bid * (1 - USDC_EUR_SLIPPAGE)
    notional = usdc * eur_per_usdc
    fee = usdc * BTC_USDC_FEE * eur_per_usdc + notional * USDC_EUR_FEE
    return Fill(qty, usdc_per_btc * eur_per_usdc, notional, fee)


def roundtrip_cost(qs: Quotes) -> float:
    """Buy plus sell over the route as a fraction, the input of the v0.3.1 rules (AK3).

    ``2 * (0.05 % + 0.05 % + 0.10 % + 0.01 %) = 0.42 %`` plus the spreads of both books, in place
    of v0.3.1's ``2 * (fee + slippage) + spread(BTC-EUR)``.
    """
    fees = BTC_USDC_FEE + BTC_USDC_SLIPPAGE + USDC_EUR_FEE + USDC_EUR_SLIPPAGE
    return 2 * fees + qs[Symbol.BTCUSDC].spread_bps / 10000 + qs[Symbol.USDCEUR].spread_bps / 10000
