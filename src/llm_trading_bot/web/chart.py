"""Decorative chart for the web UI backdrop: a made-up price series is born at the scan line ("now")
and scrolls into the past; made-up signals appear with their candle and flash once. Nothing here is
market data or a real signal of the bot.

The CSP forbids scripts and style attributes, so all motion is CSS and every per-signal timing is
a generated class. Timing works because the tape scrolls linearly: a candle at tape position ``x``
reaches the scan line at a known fraction of the loop, and an animation with the same period gets
a negative delay that puts its keyframe 0 % exactly there. A clip path hides everything right of
the scan line, so nothing of the "future" is visible.
"""

import random
from dataclasses import dataclass
from html import escape

STEP = 16
"""Distance between candles in backdrop units (the backdrop SVG is 1440 x 900)."""
COUNT = 180
WIDTH = STEP * COUNT
"""One loop of the tape; it is drawn twice side by side so the seam never shows."""
PERIOD = float(COUNT)
"""Seconds for one loop: one new candle per second."""
CURSOR = 900
"""Fixed scan line ("now"); inside the band that stays visible on phones (the SVG is cropped)."""
MIN_GAP = 7
"""Candles between two signals, so two log lines never show at once."""
LINE_PERIOD = 1.6 * PERIOD
"""Slower second tape (ETH line) for depth."""
FLASH_SECONDS = 2.5
LOG_SECONDS = 4.0
BAND = (47.0, 76.0)
"""Value range of the candles (see ``y``)."""


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    index: int
    action: str
    market: str
    confidence: float


def walk(seed: int, count: int, low: float, high: float) -> list[float]:
    """Random walk with changing volatility that returns to its start (a bridge): the loop has no
    jump. Seeded, so every page shows the same series and the tests can pin it."""
    rng = random.Random(seed)
    steps: list[float] = []
    vol = 1.0
    for _ in range(count):
        vol = min(max(vol * rng.uniform(0.8, 1.25), 0.4), 2.6)  # calm and wild stretches
        jump = rng.gauss(0, 3.2) if rng.random() < 0.04 else 0.0
        steps.append(rng.gauss(0, vol) + jump)
    drift = sum(steps) / count
    path = [0.0]
    for step in steps[:-1]:
        path.append(path[-1] + step - drift)
    lo, hi = min(path), max(path)
    return [low + (v - lo) / (hi - lo) * (high - low) for v in path]


def candles() -> list[Candle]:
    closes = walk(11, COUNT, *BAND)
    rng = random.Random(23)
    out: list[Candle] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1]  # index -1 wraps: the first candle opens at the last close
        body = abs(close - open_)
        high = max(open_, close) + rng.uniform(0.15, 0.6) * (0.6 + body)
        low = min(open_, close) - rng.uniform(0.15, 0.6) * (0.6 + body)
        out.append(Candle(open_, high, low, close))
    return out


def signals(series: list[Candle]) -> list[Signal]:
    """Local turning points of the made-up series, labelled like the bot would label them."""
    rng = random.Random(5)
    found: list[Signal] = []
    closes = [c.close for c in series]
    for i, close in enumerate(closes):
        window = [closes[(i + d) % COUNT] for d in range(-5, 6)]
        if close == min(window):
            action = "BUY"
        elif close == max(window):
            action = "SELL"
        else:
            continue
        # Circular gap: the last signal must also be far enough from the first one.
        if found and (i - found[-1].index < MIN_GAP or found[0].index + COUNT - i < MIN_GAP):
            continue
        market = rng.choice(("BTC/EUR", "BTC/EUR", "ETH/EUR"))
        found.append(Signal(i, action, market, round(rng.uniform(0.55, 0.92), 2)))
    return found


def y(value: float) -> float:
    return round(878 - (value - 44) * 9, 1)


def center(index: int) -> int:
    return index * STEP + 5


def crossing(position: float) -> float:
    """Fraction of the loop after which tape position ``position`` (first copy) reaches the scan
    line."""
    return ((position - CURSOR) / WIDTH) % 1.0


def delay(index: int) -> float:
    """Negative animation delay that puts keyframe 0 % at the moment the candle is born."""
    return round(crossing(center(index)) * PERIOD - PERIOD, 2)


def _pct(seconds: float) -> str:
    return f"{seconds / PERIOD * 100:.3f}%"


def _candles_svg(series: list[Candle]) -> str:
    parts = []
    for i, c in enumerate(series):
        kind = "up" if c.close >= c.open else "down"
        top, bottom = y(max(c.open, c.close)), y(min(c.open, c.close))
        x = i * STEP
        parts.append(
            f'<line class="wick-{kind}" x1="{x + 5}" x2="{x + 5}" y1="{y(c.high)}" '
            f'y2="{y(c.low)}"/><rect class="candle-{kind}" x="{x}" y="{top}" width="10" '
            f'height="{max(round(bottom - top, 1), 1.5)}"/>'
        )
    return "".join(parts)


def _marker(n: int, s: Signal, c: Candle) -> str:
    x = center(s.index)
    if s.action == "BUY":
        tip, base, text_y = y(c.low) + 8, y(c.low) + 20, y(c.low) + 36
    else:
        tip, base, text_y = y(c.high) - 8, y(c.high) - 20, y(c.high) - 26
    return (
        f'<g class="sig sig-{n} {s.action.lower()}">'
        f'<line class="sig-stem" x1="{x}" x2="{x}" y1="{y(c.close)}" y2="{tip}"/>'
        f'<polygon class="sig-mark" points="{x},{tip} {x - 7},{base} {x + 7},{base}"/>'
        f'<circle class="sig-ring" cx="{x}" cy="{y(c.close)}" r="4"/>'
        f'<text class="sig-label" x="{x}" y="{text_y}" text-anchor="middle">{s.action}</text></g>'
    )


def eth_points() -> list[tuple[int, float]]:
    values = walk(29, COUNT, 0.0, 1.0)
    values.append(values[0])  # closes the loop for the second copy
    return [(i * STEP, round(512 - v * 80, 1)) for i, v in enumerate(values)]


def _eth_line() -> str:
    points = " ".join(f"{x},{v}" for x, v in eth_points())
    area = f"M0,560 L{points.replace(' ', ' L')} L{WIDTH},560 Z"
    return f'<path class="eth-area" d="{area}"/><polyline class="eth-line" points="{points}"/>'


def head_keyframes(series: list[Candle]) -> str:
    """Path of the glowing price tip on the scan line: the close of whichever candle is being
    born right now."""
    stops = sorted((crossing(center(i)), y(c.close)) for i, c in enumerate(series))
    first, last = stops[0], stops[-1]
    # Value at 0 % / 100 %: between the last stop of one loop and the first of the next.
    span = first[0] + 1 - last[0]
    edge = round(last[1] + (first[1] - last[1]) * (1 - last[0]) / span, 1)
    frames = [(0.0, edge), *stops, (1.0, edge)]
    body = " ".join(f"{f * 100:.3f}% {{ transform: translateY({v}px); }}" for f, v in frames)
    return f"@keyframes head {{ {body} }}\n"


def svg() -> str:
    series = candles()
    found = signals(series)
    one = _candles_svg(series) + "".join(
        _marker(n, s, series[s.index]) for n, s in enumerate(found)
    )
    eth = _eth_line()
    logs = "".join(
        f'<text class="log log-{n} {s.action.lower()}" x="{CURSOR - 12}" y="574" '
        'text-anchor="end">'
        f"{'▲' if s.action == 'BUY' else '▼'} {s.action} {escape(s.market)} · conf "
        f"{s.confidence:.2f}</text>"
        for n, s in enumerate(found)
    )
    return (
        # Everything right of the scan line is the future: clipped away.
        f'<defs><clipPath id="past"><rect x="-10" y="0" width="{CURSOR + 10}" height="900"/>'
        "</clipPath></defs>"
        '<g clip-path="url(#past)">'
        f'<g class="eth-tape">{eth}<g transform="translate({WIDTH} 0)">{eth}</g></g>'
        f'<g class="tape">{one}<g transform="translate({WIDTH} 0)">{one}</g></g></g>'
        f'<rect class="scan-glow" x="{CURSOR - 40}" y="548" width="40" height="342"/>'
        f'<line class="scan" x1="{CURSOR}" x2="{CURSOR}" y1="548" y2="890"/>'
        f'<g class="head"><line class="head-line" x1="{CURSOR}" x2="{CURSOR + 34}" y1="0" '
        f'y2="0"/><circle class="head-dot" cx="{CURSOR}" cy="0" r="3.5"/>'
        f'<circle class="head-halo" cx="{CURSOR}" cy="0" r="9"/></g>'
        f'<text class="scan-label" x="{CURSOR - 12}" y="560" text-anchor="end">'
        f"SIGNAL-SCAN · DEMO</text>{logs}"
        f'<text class="demo-note" x="{CURSOR - 12}" y="884" text-anchor="end">'
        "Deko · erfundene Kurse und Signale</text>"
    )


def css() -> str:
    series = candles()
    found = signals(series)
    timing = "".join(
        f".sig-{n}, .sig-{n} .sig-ring, .log-{n} {{ animation-delay: {delay(s.index)}s; }}\n"
        for n, s in enumerate(found)
    )
    flash, fade = _pct(FLASH_SECONDS), _pct(FLASH_SECONDS + 1.5)
    log, gone = _pct(LOG_SECONDS), _pct(LOG_SECONDS + 0.4)
    return (
        f"""
.tape {{ animation: tape {PERIOD}s linear infinite; }}
.eth-tape {{ animation: tape {LINE_PERIOD}s linear infinite; }}
@keyframes tape {{ to {{ transform: translateX(-{WIDTH}px); }} }}
.eth-line {{ fill: none; stroke: rgba(255, 181, 71, 0.32); stroke-width: 1.4; }}
.eth-area {{ fill: rgba(255, 181, 71, 0.035); }}
.scan {{ stroke: rgba(45, 226, 230, 0.55); stroke-width: 1; stroke-dasharray: 4 4; }}
.scan-glow {{ fill: rgba(45, 226, 230, 0.06); }}
.scan-label, .demo-note {{ fill: rgba(45, 226, 230, 0.55); font: 600 10px var(--mono);
  letter-spacing: 0.16em; }}
.demo-note {{ fill: var(--dim); font-weight: 400; letter-spacing: 0.08em; }}
.head {{ animation: head {PERIOD}s linear infinite;
  transform: translateY({y(series[0].close)}px); }}
.head-dot {{ fill: var(--cyan); }}
.head-halo {{ fill: none; stroke: var(--cyan); stroke-width: 1; opacity: 0.5;
  animation: blink 1.2s ease-in-out infinite; }}
.head-line {{ stroke: rgba(45, 226, 230, 0.5); stroke-width: 1; stroke-dasharray: 3 3; }}
.sig {{ opacity: 0.3; animation: sig-flash {PERIOD}s linear infinite; }}
.sig.buy {{ --sig: var(--lime); }} .sig.sell {{ --sig: var(--magenta); }}
.sig-mark {{ fill: var(--sig); }}
.sig-stem {{ stroke: var(--sig); stroke-width: 1; stroke-dasharray: 2 3; }}
.sig-label {{ fill: var(--sig); font: 700 10px var(--mono); letter-spacing: 0.12em; }}
.sig-ring {{ fill: none; stroke: var(--sig); stroke-width: 1.5; opacity: 0;
  animation: sig-ring {PERIOD}s linear infinite; }}
@keyframes sig-flash {{ 0%, {flash} {{ opacity: 1; }} {fade}, 100% {{ opacity: 0.3; }} }}
@keyframes sig-ring {{ 0% {{ r: 4; opacity: 1; }} {flash} {{ r: 28; opacity: 0; }}
  100% {{ r: 28; opacity: 0; }} }}
.log {{ opacity: 0; font: 600 12px var(--mono); letter-spacing: 0.08em;
  paint-order: stroke; stroke: var(--void); stroke-width: 4px; stroke-linejoin: round;
  animation: sig-log {PERIOD}s linear infinite; }}
.log.buy {{ fill: var(--lime); }} .log.sell {{ fill: var(--magenta); }}
@keyframes sig-log {{ 0%, {log} {{ opacity: 1; }} {gone}, 100% {{ opacity: 0; }} }}
"""
        + head_keyframes(series)
        + timing
    )
