"""Deterministic synthetic market scenarios for the strategy golden tests (ADR-0007).

Shared by the fixture generator (tools/golden) and the tests, so fixtures only store scenario
parameters plus expected outputs, not thousands of bars. Uses only ``random.Random(seed)``,
whose sequence is stable across Python versions.
"""

import random
from collections.abc import Mapping
from statistics import mean, pstdev
from typing import Any, NotRequired, TypedDict

COUNTS = {1: 60, 15: 120, 60: 120, 240: 60}
NOW = 1_800_000_030  # 1_800_000_000 is aligned to every interval

Row = tuple[int, float, float, float, float, float]


class Params(TypedDict):
    name: str
    seed: int
    start: float
    drift: dict[str, float]  # per interval (as str key), relative change per bar
    vol: dict[str, float]
    volume: float
    special: str  # "", "spike", "dip", "dump", "zerovol"
    spread_bps: float
    symbol: NotRequired[str]  # default "BTCEUR"
    dip_k: NotRequired[float]  # depth of the "dip" in standard deviations, default 3


def build_bars(p: Params) -> dict[int, list[Row]]:
    rng = random.Random(p["seed"])
    out: dict[int, list[Row]] = {}
    for tf, count in COUNTS.items():
        step = tf * 60
        last = (NOW // step - 1) * step
        price = p["start"]
        drift = p["drift"][str(tf)]
        vol = p["vol"][str(tf)]
        rows: list[Row] = []
        for i in range(count):
            o = price
            c = o * (1 + drift + vol * rng.gauss(0, 1))
            h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
            v = p["volume"] * (1 + 0.3 * rng.random())
            rows.append((last - (count - 1 - i) * step, o, h, lo, c, v))
            price = c
        out[tf] = rows
    _apply_special(p, out)
    return out


def _replace_close(row: Row, close: float, volume: float | None = None) -> Row:
    ts, o, h, lo, _c, v = row
    return (ts, o, max(h, o, close), min(lo, o, close), close, v if volume is None else volume)


def _apply_special(p: Params, bars: dict[int, list[Row]]) -> None:
    b15 = bars[15]
    if p["special"] == "spike":
        # Last 15m close breaks above the 48h high of the 1h bars, with a volume surge.
        high48 = max(r[2] for r in bars[60][-49:-1])
        b15[-1] = _replace_close(b15[-1], high48 * 1.004, volume=b15[-1][5] * 3)
    elif p["special"] == "dip":
        # Oversold close on the previous 15m bar, then a small rebound on the last one.
        prior = [r[4] for r in b15[-22:-2]]
        low = mean(prior) - p.get("dip_k", 3.0) * pstdev(prior)
        b15[-2] = _replace_close(b15[-2], low)
        b15[-1] = _replace_close(b15[-1], low * 1.002)
    elif p["special"] == "zerovol":
        # No-trade minutes inside the last 60 one-minute bars (Bitvavo omits them; filled bars
        # carry volume 0). Positions 5 and 45 of 60 make the window size observable.
        b1 = bars[1]
        for idx in (5, 45):
            ts, o, _h, _lo, _c, _v = b1[idx]
            b1[idx] = (ts, o, o, o, o, 0.0)
    elif p["special"] == "dump":
        # Last 15m close falls below the 48h low.
        low48 = min(r[3] for r in bars[60][-49:-1])
        b15[-1] = _replace_close(b15[-1], low48 * 0.995)


def quote_for(p: Params, bars: dict[int, list[Row]]) -> tuple[float, float]:
    mid = bars[1][-1][4]
    half = p["spread_bps"] / 20_000
    return mid * (1 - half), mid * (1 + half)


def grid() -> list[Params]:
    """Base grid; the generator adds targeted seeds for branches the grid misses."""
    params: list[Params] = []
    drifts = {"up": 0.004, "down": -0.004, "flat": 0.0, "mild_up": 0.0015, "mild_down": -0.0015}
    for dname, d in drifts.items():
        for vname, v in {"calm": 0.002, "wild": 0.006}.items():
            for special in ("", "spike", "dip", "dump"):
                params.append(make(f"{dname}-{vname}-{special or 'plain'}-s1", 1, d, v, special))
    return params


def make(name: str, seed: int, drift: float, vol: float, special: str) -> Params:
    return Params(
        name=name,
        seed=seed,
        start=50_000.0,
        drift={"1": drift / 15, "15": drift / 4, "60": drift, "240": drift * 2},
        vol={"1": vol / 4, "15": vol / 2, "60": vol, "240": vol * 2},
        volume=12.0,
        special=special,
        spread_bps=4.0,
    )


def as_jsonable(p: Params) -> dict[str, Any]:
    return dict(p)


# --- Boundary cases (Spec 003, review): indicator dicts with one condition at its threshold.
# run_strategy reads almost only the indicator dict, so thresholds can be probed directly. The
# sampler is shared by generator and tests; each fixture entry stores only (base, seed, sha).

MODES = ("trend", "breakout_buy", "breakout_sell", "meanrev_buy", "meanrev_sell", "ensemble")
BOUNDARY_WEIGHTS = (0.02, 0.0200001, 0.45)  # current_weight > 0.02 is the only weight test


def sample_indicators(
    base: Mapping[str, Any], anchors: Mapping[str, float], seed: int, mode: str | None = None
) -> dict[str, Any]:
    """Deterministic indicator dict around the thresholds of one strategy condition.

    ``anchors``: ema34_15m, ema30_15m, prev_close_15m of the base snapshot (computed by the caller
    with its own ema, so a deviating ema shows up as a hash mismatch).
    """
    rng = random.Random(seed)
    i: dict[str, Any] = dict(base)
    p = float(base["close_1h"])
    cost = float(base["roundtrip_cost_pct"]) / 100

    def edge(target: float, spread: float) -> float:
        if rng.random() < 0.35:
            return target + rng.choice((-1e-9, 0.0, 1e-9)) * max(1.0, abs(target))
        return target + rng.uniform(-spread, spread)

    mode = mode or rng.choice(MODES)
    i["regime"] = rng.choice(("TREND", "RANGE", "TRANSITION"))
    i["adx_1h"] = rng.uniform(14, 28)
    i["plus_di_1h"], i["minus_di_1h"] = 30.0, 15.0
    # Comfortable 1h/4h uptrend unless a mode changes it.
    i["close_1h"], i["ema24_1h"], i["ema72_1h"] = p * 1.01, p * 1.005, p
    i["ema12_4h"], i["ema30_4h"] = p * 1.004, p
    i["high_48h_excl_current"], i["low_48h_excl_current"] = p * 1.01, p * 0.99
    i["volume15_mean20_prior"], i["volume15_latest"] = 10.0, 15.0
    i["atr14_1h_pct"] = cost * rng.uniform(0.15, 0.7)
    i["close_15m"] = p
    i["sma20_15m"], i["std20_15m"] = p * 1.02, p * 0.004
    i["rsi14_15m"] = rng.uniform(25, 75)

    if mode == "trend":
        knob = rng.choice(
            ("adx", "di", "close_ema24", "ema24_ema72", "ema12_ema30", "sell", "down")
        )
        i["adx_1h"] = edge(20, 1.5) if knob == "adx" else rng.uniform(19, 26)
        if knob == "di":
            i["minus_di_1h"] = edge(30.0, 2.0)
        elif knob == "close_ema24":
            i["close_1h"] = i["ema24_1h"] * (1 + edge(0.0, 0.002))
        elif knob == "ema24_ema72":
            i["ema24_1h"] = i["ema72_1h"] * (1 + edge(0.0, 0.002))
            i["close_1h"] = max(i["close_1h"], i["ema24_1h"] * 1.001)
        elif knob == "ema12_ema30":
            i["ema12_4h"] = i["ema30_4h"] * (1 + edge(0.0, 0.002))
        elif knob == "sell":
            # Neither up nor down; the SELL rule then depends on close_1h vs ema72 (not ema24).
            i["ema24_1h"], i["ema72_1h"] = p * 1.005, p
            i["close_1h"] = i["ema72_1h"] * (1 + edge(0.0, 0.003))
        else:  # down: both 1h and 4h EMAs falling, one of them right at equality
            i["ema24_1h"], i["ema72_1h"] = p * 0.995, p
            i["ema12_4h"], i["ema30_4h"] = p * 0.996, p
            if rng.random() < 0.5:
                i["ema24_1h"] = i["ema72_1h"] * (1 + edge(0.0, 0.002))
            else:
                i["ema12_4h"] = i["ema30_4h"] * (1 + edge(0.0, 0.002))
            i["close_1h"] = p * rng.uniform(0.98, 1.02)
    elif mode == "breakout_buy":
        knob = rng.choice(("adx", "price", "volume", "volume_zero", "atr"))
        high = i["high_48h_excl_current"]
        i["adx_1h"] = edge(19, 1.0) if knob == "adx" else rng.uniform(19.5, 30)
        i["close_15m"] = high * (1 + edge(0.001, 0.0012)) if knob == "price" else high * 1.003
        if knob == "volume":
            i["volume15_latest"] = 10.0 * edge(1.25, 0.3)
        elif knob == "volume_zero":
            i["volume15_mean20_prior"] = rng.choice((0.0, 1e-12))
        if knob == "atr":
            i["atr14_1h_pct"] = cost * edge(rng.choice((0.25, 1 / 3, 0.5)), 0.08)
        else:
            i["atr14_1h_pct"] = cost * rng.uniform(0.3, 0.7)
    elif mode == "breakout_sell":
        i["adx_1h"] = rng.uniform(10, 18.9)
        lo, hi = sorted((anchors["ema30_15m"], anchors["ema34_15m"]))
        knob = rng.choice(("between_emas", "ema34", "low48"))
        if knob == "between_emas":
            i["close_15m"] = rng.uniform(lo, hi) if hi > lo else lo
        elif knob == "ema34":
            i["close_15m"] = anchors["ema34_15m"] * (1 + edge(0.0, 0.002))
        else:
            i["low_48h_excl_current"] = anchors["ema34_15m"] * 0.98
            i["close_15m"] = i["low_48h_excl_current"] * (1 + edge(0.0, 0.002))
    elif mode in ("meanrev_buy", "meanrev_sell"):
        prev = anchors["prev_close_15m"]
        i["adx_1h"] = 12.0
        i["ema24_1h"], i["ema72_1h"] = p * 1.002, p
        i["close_15m"] = prev * 1.002
        i["rsi14_15m"] = 35.0
        i["std20_15m"] = i["close_15m"] * 0.004
        i["sma20_15m"] = i["close_15m"] * (1 + cost * 3)
        if mode == "meanrev_buy":
            knob = rng.choice(("adx", "ratio", "turned", "rsi", "distance"))
            if knob == "adx":
                i["adx_1h"] = edge(18, 1.0)
            elif knob == "ratio":
                sign = rng.choice((-1, 1))
                i["ema24_1h"] = i["ema72_1h"] * (1 + sign * edge(0.012, 0.004))
            elif knob == "turned":
                i["close_15m"] = prev * (1 + edge(0.0, 0.001))
                i["sma20_15m"] = i["close_15m"] * (1 + cost * 3)
            elif knob == "rsi":
                i["rsi14_15m"] = edge(42, 4.0)
            else:
                i["sma20_15m"] = i["close_15m"] * (1 + cost * edge(1.25, 0.4))
        else:
            knob = rng.choice(("mid", "adx", "rsi"))
            sd, mid = i["std20_15m"], i["sma20_15m"]
            i["close_15m"] = mid - (edge(0.25, 0.3) if knob == "mid" else 1.0) * sd
            i["adx_1h"] = edge(24, 2.0) if knob == "adx" else 15.0
            i["rsi14_15m"] = edge(66, 4.0) if knob == "rsi" else 50.0
    else:  # ensemble: mix of the other modes, with the ensemble's own ATR rule at its edge
        mixed = sample_indicators(base, anchors, seed + 1_000_003, rng.choice(MODES[:-1]))
        mixed["regime"] = i["regime"]
        mixed["atr14_1h_pct"] = cost * edge(rng.choice((0.25, 1 / 3)), 0.06)
        return mixed
    return i


def boundary_digest(rows: list[Any]) -> str:
    """Exact SHA-256 fingerprint of JSON-serialisable rows (e.g. signal rows or one LLM string)."""
    import hashlib
    import json

    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def llm_json(payload: Mapping[str, Any]) -> str:
    """The exact string the Arena sends to the model (arena.py llm_signal)."""
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
