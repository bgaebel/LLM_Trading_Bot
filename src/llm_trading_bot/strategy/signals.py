# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - signal, run_strategy
"""Arena v0.3.1 classic strategies. They only produce signals; nothing here can place an order.

ADR-0007: logic, parameters and reason texts are identical to the Arena (golden fixtures).
The German reason texts are part of that contract and stay as they are.
"""

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Literal

from llm_trading_bot.market.snapshot import MarketSnapshot
from llm_trading_bot.strategy.indicators import Indicators, closes, ema

STRATEGY_VERSION = "v0.3.1"
STRATEGIES: tuple[str, ...] = ("trend", "breakout", "meanrev", "ensemble", "buyhold")
ALLOWED_TARGETS = (0.0, 0.25, 0.45)

Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class Signal:
    action: Action
    target_fraction: float
    confidence: float
    reason: str
    strategy: str
    version: str = STRATEGY_VERSION

    def validate(self) -> None:
        if self.action not in ("BUY", "SELL", "HOLD"):
            raise ValueError(f"invalid action: {self.action!r}")
        if self.target_fraction not in ALLOWED_TARGETS:
            raise ValueError(f"invalid target_fraction: {self.target_fraction!r}")
        if not (math.isfinite(self.confidence) and 0 <= self.confidence <= 1):
            raise ValueError(f"invalid confidence: {self.confidence!r}")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")
        if not self.strategy or not self.version:
            raise ValueError("strategy and version are required")


def _signal(
    strategy: str,
    action: Action,
    reason: str,
    confidence: float = 0.7,
    target: float | None = None,
) -> Signal:
    fraction = (0.45 if action == "BUY" else 0.0) if target is None else target
    sig = Signal(action, fraction, confidence, reason, strategy)
    sig.validate()
    return sig


def run_strategy(
    name: str, s: MarketSnapshot, i: Indicators, current_weight: float = 0.0
) -> Signal:
    # These fixed parameters are experimental starting points, not optimized coefficients.
    a = i["adx_1h"]
    up = i["close_1h"] > i["ema24_1h"] > i["ema72_1h"] and i["ema12_4h"] > i["ema30_4h"]
    down = i["ema24_1h"] < i["ema72_1h"] and i["ema12_4h"] < i["ema30_4h"]
    cost = i["roundtrip_cost_pct"] / 100
    if name == "trend":
        if up and a >= 20 and i["plus_di_1h"] > i["minus_di_1h"]:
            return _signal(
                name, "BUY", f"1h/4h-Aufwärtstrend; ADX {a:.1f}; nur abgeschlossene Kerzen"
            )
        if down or (current_weight > 0.02 and i["close_1h"] < i["ema72_1h"]):
            return _signal(name, "SELL", "1h/4h-Abwärtstrend oder 1h-Kurs unter EMA72")
        return _signal(name, "HOLD", "Keine eindeutige 1h/4h-Trendbestätigung")
    if name == "breakout":
        last = i["close_15m"]
        high = i["high_48h_excl_current"]
        vol_ok = (
            i["volume15_mean20_prior"] > 0
            and i["volume15_latest"] / i["volume15_mean20_prior"] >= 1.25
        )
        # ATR is NOT a forecast. Only used as a heuristic no-trade check relative to costs.
        movement_sufficient = i["atr14_1h_pct"] * 4 > cost
        if last > high * 1.001 and a >= 19 and vol_ok and movement_sufficient:
            return _signal(
                name, "BUY", f"15m-Ausbruch über 48h-Hoch; ADX {a:.1f}; Volumenbestätigung"
            )
        if current_weight > 0.02 and (
            last < ema(closes(s.bars[15]), 34) or last < i["low_48h_excl_current"]
        ):
            return _signal(name, "SELL", "Ausbruch verloren; 15m-Kurs unter EMA34 / 48h-Tief")
        return _signal(name, "HOLD", "Kein bestätigter Breakout (48h-Hoch/Volumen/Volatilität)")
    if name == "meanrev":
        last = i["close_15m"]
        sd = i["std20_15m"]
        mid = i["sma20_15m"]
        # Entry only AFTER first rebound from an oversold close, never buy blindly while falling.
        prior = closes(s.bars[15])[-21:-1]
        prev_mid = mean(prior)
        prev_sd = pstdev(prior)
        oversold_prev = prev_sd > 0 and s.bars[15][-2].close < prev_mid - 2 * prev_sd
        turned = last > s.bars[15][-2].close
        distance_to_mean = (mid - last) / last if last > 0 else 0
        range_ok = a < 18 and abs(i["ema24_1h"] / i["ema72_1h"] - 1) < 0.012
        if current_weight > 0.02 and (last > mid - 0.25 * sd or a > 24 or i["rsi14_15m"] > 66):
            return _signal(name, "SELL", "Rückkehr zum Mittelwert / Seitwärtsregime beendet")
        if (
            range_ok
            and oversold_prev
            and turned
            and i["rsi14_15m"] < 42
            and distance_to_mean > cost * 1.25
        ):
            return _signal(
                name,
                "BUY",
                f"15m-Erholung nach Übertreibung, ADX {a:.1f}; "
                f"Distanz zum Mittelwert {distance_to_mean * 100:.1f}%",
            )
        return _signal(name, "HOLD", "Keine gebührenrobuste Rückkehr-zum-Mittelwert-Konstellation")
    if name == "ensemble":
        tr = run_strategy("trend", s, i, current_weight)
        br = run_strategy("breakout", s, i, current_weight)
        mr = run_strategy("meanrev", s, i, current_weight)
        weights = (
            {"trend": 0.60, "breakout": 0.30, "meanrev": 0.10}
            if i["regime"] == "TREND"
            else {"trend": 0.20, "breakout": 0.15, "meanrev": 0.65}
            if i["regime"] == "RANGE"
            else {"trend": 0.45, "breakout": 0.35, "meanrev": 0.20}
        )
        votes = {"trend": tr.action, "breakout": br.action, "meanrev": mr.action}
        score = sum(weights[k] * {"BUY": 1, "SELL": -1, "HOLD": 0}[v] for k, v in votes.items())
        reason = (
            f"{i['regime']} ADX {a:.1f} | Score {score:+.2f} | "
            f"Trend={tr.action}, Breakout={br.action}, MR={mr.action}"
        )
        if score >= 0.55 and i["atr14_1h_pct"] * 4 > cost:
            return _signal(name, "BUY", reason, confidence=min(1.0, abs(score)))
        if score <= -0.40 and current_weight > 0.02:
            return _signal(name, "SELL", reason, confidence=min(1.0, abs(score)))
        return _signal(name, "HOLD", reason, confidence=min(1.0, abs(score)))
    if name == "buyhold":
        return _signal(name, "BUY", "Passiver Vergleich; einmalig kaufen und halten")
    raise ValueError("unknown strategy: " + name)
