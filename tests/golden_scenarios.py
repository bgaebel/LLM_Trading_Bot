"""Deterministic synthetic market scenarios for the strategy golden tests (ADR-0007).

Shared by the fixture generator (tools/golden) and the tests, so fixtures only store scenario
parameters plus expected outputs, not thousands of bars. Uses only ``random.Random(seed)``,
whose sequence is stable across Python versions.
"""

import random
from statistics import mean, pstdev
from typing import Any, TypedDict

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
    special: str  # "", "spike", "dip", "dump"
    spread_bps: float


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
        low = mean(prior) - 3 * pstdev(prior)
        b15[-2] = _replace_close(b15[-2], low)
        b15[-1] = _replace_close(b15[-1], low * 1.002)
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
