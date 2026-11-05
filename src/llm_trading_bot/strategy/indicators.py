# Origin: LLM-Trading-Arena@7563724 apps/arena/strategies.py (adapted) - indicators
"""Arena v0.3.1 indicators on closed candles. Pure, standard library only.

ADR-0007: behaviour must stay identical to the Arena, proven by golden fixtures. Keep the order of
operations exactly as it is - even harmless-looking rewrites change the last floating-point digits.
"""

from collections.abc import Sequence
from statistics import mean, pstdev
from typing import TypedDict

from llm_trading_bot.market.models import Candle
from llm_trading_bot.market.snapshot import MarketSnapshot


class Indicators(TypedDict):
    regime: str
    adx_1h: float
    plus_di_1h: float
    minus_di_1h: float
    ema24_1h: float
    ema72_1h: float
    ema12_4h: float
    ema30_4h: float
    rsi14_15m: float
    sma20_15m: float
    std20_15m: float
    z20_15m: float
    atr14_1h_pct: float
    atr14_15m_pct: float
    close_15m: float
    close_1h: float
    close_4h: float
    high_48h_excl_current: float
    low_48h_excl_current: float
    volume15_latest: float
    volume15_mean20_prior: float
    roundtrip_cost_pct: float


def closes(bars: Sequence[Candle]) -> list[float]:
    return [c.close for c in bars]


def ema(values: Sequence[float], n: int) -> float:
    if not values or n < 1:
        raise ValueError("EMA: no data")
    weight = 2 / (n + 1)
    out = values[0]
    for v in values[1:]:
        out = weight * v + (1 - weight) * out
    return out


def rsi(values: Sequence[float], n: int = 14) -> float:
    if len(values) <= n:
        return 50.0
    d = [values[i] - values[i - 1] for i in range(len(values) - n, len(values))]
    gain = sum(max(x, 0) for x in d) / n
    loss = sum(max(-x, 0) for x in d) / n
    if gain + loss == 0:
        return 50.0
    return 100 if loss == 0 else 100 - 100 / (1 + gain / loss)


def atr(bars: Sequence[Candle], n: int = 14) -> float:
    if len(bars) < n + 1:
        return 0.0
    rs = []
    for i in range(len(bars) - n, len(bars)):
        b = bars[i]
        p = bars[i - 1].close
        rs.append(max(b.high - b.low, abs(b.high - p), abs(b.low - p)))
    return mean(rs)


def adx(bars: Sequence[Candle], n: int = 14) -> tuple[float, float, float]:
    """Wilder ADX with directional indicators on closed candles. Output 0..100."""
    if len(bars) < 2 * n + 2:
        return (0.0, 0.0, 0.0)
    tr: list[float] = []
    pos: list[float] = []
    neg: list[float] = []
    for i in range(1, len(bars)):
        cur, prev = bars[i], bars[i - 1]
        up = cur.high - prev.high
        down = prev.low - cur.low
        pos.append(up if up > down and up > 0 else 0.0)
        neg.append(down if down > up and down > 0 else 0.0)
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    tr_s = sum(tr[:n])
    pos_s = sum(pos[:n])
    neg_s = sum(neg[:n])
    dx: list[float] = []

    def compute() -> tuple[float, float, float]:
        pdi = 100 * pos_s / tr_s if tr_s else 0
        ndi = 100 * neg_s / tr_s if tr_s else 0
        return pdi, ndi, 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0

    pdi, ndi, v = compute()
    dx.append(v)
    for i in range(n, len(tr)):
        tr_s = tr_s - tr_s / n + tr[i]
        pos_s = pos_s - pos_s / n + pos[i]
        neg_s = neg_s - neg_s / n + neg[i]
        pdi, ndi, v = compute()
        dx.append(v)
    strength = mean(dx[:n])
    for value in dx[n:]:
        strength = (strength * (n - 1) + value) / n
    return (strength, pdi, ndi)


def indicators(s: MarketSnapshot, roundtrip_cost_frac: float) -> Indicators:
    v15 = closes(s.bars[15])
    v60 = closes(s.bars[60])
    v240 = closes(s.bars[240])
    sd = pstdev(v15[-20:])
    avg = mean(v15[-20:])
    a, pdi, ndi = adx(s.bars[60])
    a1 = atr(s.bars[60])
    a15 = atr(s.bars[15])
    e24, e72 = ema(v60, 24), ema(v60, 72)
    e12_4h, e30_4h = ema(v240, 12), ema(v240, 30)
    regime = "TREND" if a >= 22 else "RANGE" if a < 18 else "TRANSITION"
    return {
        "regime": regime,
        "adx_1h": a,
        "plus_di_1h": pdi,
        "minus_di_1h": ndi,
        "ema24_1h": e24,
        "ema72_1h": e72,
        "ema12_4h": e12_4h,
        "ema30_4h": e30_4h,
        "rsi14_15m": rsi(v15),
        "sma20_15m": avg,
        "std20_15m": sd,
        "z20_15m": (v15[-1] - avg) / sd if sd > 0 else 0,
        "atr14_1h_pct": a1 / v60[-1],
        "atr14_15m_pct": a15 / v15[-1],
        "close_15m": v15[-1],
        "close_1h": v60[-1],
        "close_4h": v240[-1],
        "high_48h_excl_current": max(b.high for b in s.bars[60][-49:-1]),
        "low_48h_excl_current": min(b.low for b in s.bars[60][-49:-1]),
        "volume15_latest": s.bars[15][-1].volume,
        "volume15_mean20_prior": mean(b.volume for b in s.bars[15][-21:-1]),
        "roundtrip_cost_pct": roundtrip_cost_frac * 100,
    }
