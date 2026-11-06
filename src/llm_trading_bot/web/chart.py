"""Decorative chart for the web UI backdrop: a made-up price series is born at the scan line ("now")
and scrolls into the past, the view rescales like a real chart (out on a breakout, slowly back in
when the range narrows), and made-up signals appear with their candle and flash once. Nothing here
is market data or a real signal of the bot.

The CSP forbids scripts and style attributes, so all motion is CSS and every timing is generated:
- the tape scrolls linearly, so a candle at tape position ``x`` is born at a known fraction of the
  loop; animations with the same period get a negative delay that puts keyframe 0 % exactly there;
- the auto-zoom is one generated keyframe list (``translateY`` + ``scaleY``) on a group around the
  tape; markers, the price tip and their text get the inverse ``scaleY`` so they never stretch;
- a clip path hides everything right of the scan line, so nothing of the "future" is visible.
"""

import random
from dataclasses import dataclass
from html import escape

STEP = 16
"""Distance between candles in backdrop units (the backdrop SVG is 1440 x 900)."""
COUNT = 300
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
UNIT = 6.0
"""Backdrop units per price unit before zooming."""
BAND_TOP, BAND_BOTTOM = 566.0, 872.0
"""Where the auto-zoom puts the visible range (inside the clipped chart band 540-895)."""
WINDOW = 52
"""Candles left of the scan line that the auto-zoom fits (the part visible on most screens)."""
GROW, SHRINK = 0.6, 0.06
"""Zoom speed per candle: out quickly when a breakout leaves the range, back in slowly."""
PAD = 0.12
MIN_SPAN = 3.0


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


def walk(seed: int, count: int) -> list[float]:
    """Sideways phases in a tight range, broken by breakouts to a new level, sometimes with a
    pullback - closed as a bridge so the loop has no jump. Seeded: every page and every test sees
    the same series."""
    rng = random.Random(seed)
    path = [0.0]
    level = 0.0
    while len(path) <= count:
        # Consolidation: mean reversion around the level keeps the range tight.
        vol = rng.uniform(0.25, 0.7)
        for _ in range(rng.randint(14, 36)):
            path.append(path[-1] - 0.22 * (path[-1] - level) + rng.gauss(0, vol))
        # Breakout, biased back towards the start so the bridge correction stays small.
        up = rng.random() < 1 / (1 + 2.718 ** (path[-1] / 12))
        direction = 1.0 if up else -1.0
        for _ in range(rng.randint(5, 14)):
            path.append(path[-1] + direction * rng.uniform(0.7, 2.4) + rng.gauss(0, 0.9))
        if rng.random() < 0.6:  # pullback after the move
            for _ in range(rng.randint(2, 6)):
                path.append(path[-1] - direction * rng.uniform(0.2, 0.9) + rng.gauss(0, 0.5))
        level = path[-1]
    path = path[: count + 1]
    gap = path[count] - path[0]
    return [v - gap * i / count for i, v in enumerate(path[:count])]


def candles() -> list[Candle]:
    closes = [60.0 + v for v in walk(11, COUNT)]
    rng = random.Random(23)
    out: list[Candle] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1]  # index -1 wraps: the first candle opens at the last close
        body = abs(close - open_)
        high = max(open_, close) + rng.uniform(0.1, 0.5) * (0.5 + body)
        low = min(open_, close) - rng.uniform(0.1, 0.5) * (0.5 + body)
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


def base_y(value: float) -> float:
    """Unzoomed coordinate; the zoom keyframes map the visible range into the band."""
    return round(-value * UNIT, 2)


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


@dataclass(frozen=True)
class Zoom:
    fraction: float
    """Loop fraction at which this frame applies (a candle is born)."""
    shift: float
    scale: float
    """Screen y = shift + scale * base y."""

    def screen(self, value: float) -> float:
        return self.shift + self.scale * base_y(value)


def zoom_frames(series: list[Candle]) -> list[Zoom]:
    """One frame per newborn candle, fitting the last WINDOW candles into the band. The range
    widens fast and narrows slowly, like a chart that rescales on a breakout. Three loops are
    simulated and the middle one kept, so the smoothing is settled and the loop wraps cleanly."""
    order = sorted(range(COUNT), key=lambda i: crossing(center(i)))
    lo = min(c.low for c in series)
    hi = max(c.high for c in series)
    frames: list[Zoom] = []
    for loop in range(3):
        for i in order:
            window = [series[(i - k) % COUNT] for k in range(WINDOW)]
            w_lo, w_hi = min(c.low for c in window), max(c.high for c in window)
            pad = max(w_hi - w_lo, MIN_SPAN) * PAD
            t_lo, t_hi = w_lo - pad, w_hi + pad
            hi += (t_hi - hi) * (GROW if t_hi > hi else SHRINK)
            lo += (t_lo - lo) * (GROW if t_lo < lo else SHRINK)
            span = max(hi - lo, MIN_SPAN)
            scale = (BAND_BOTTOM - BAND_TOP) / (span * UNIT)
            shift = BAND_TOP - scale * base_y(lo + span)
            if loop == 1:
                frames.append(Zoom(crossing(center(i)), round(shift, 2), round(scale, 5)))
    return frames


def _candles_svg(series: list[Candle]) -> str:
    parts = []
    for i, c in enumerate(series):
        kind = "up" if c.close >= c.open else "down"
        top, bottom = base_y(max(c.open, c.close)), base_y(min(c.open, c.close))
        x = i * STEP
        parts.append(
            f'<line class="wick-{kind}" x1="{x + 5}" x2="{x + 5}" y1="{base_y(c.high)}" '
            f'y2="{base_y(c.low)}"/><rect class="candle-{kind}" x="{x}" y="{top}" width="10" '
            f'height="{max(round(bottom - top, 2), 0.3)}"/>'
        )
    return "".join(parts)


def _marker(n: int, s: Signal, c: Candle) -> str:
    """The shape hangs from its anchor (low for BUY, high for SELL) and is counter-scaled around
    that anchor, so it keeps its size and distance however far the chart zooms."""
    x = center(s.index)
    if s.action == "BUY":
        anchor, sign, origin = base_y(c.low), 1, "down"
    else:
        anchor, sign, origin = base_y(c.high), -1, "up"
    tip, base, text_y = anchor + 8 * sign, anchor + 20 * sign, anchor + (36 if sign > 0 else -26)
    return (
        f'<g class="sig sig-{n} {s.action.lower()}">'
        f'<g class="unzoom-{origin}">'
        f'<line class="sig-stem" x1="{x}" x2="{x}" y1="{anchor}" y2="{tip}"/>'
        f'<polygon class="sig-mark" points="{x},{tip} {x - 7},{base} {x + 7},{base}"/>'
        f'<text class="sig-label" x="{x}" y="{text_y}" text-anchor="middle">{s.action}</text></g>'
        f'<g class="unzoom-c"><circle class="sig-ring" cx="{x}" cy="{base_y(c.close)}" r="4"/></g>'
        "</g>"
    )


def eth_points() -> list[tuple[int, float]]:
    values = walk(29, COUNT)
    lo, hi = min(values), max(values)
    values.append(values[0])  # closes the loop for the second copy
    return [(i * STEP, round(512 - (v - lo) / (hi - lo) * 80, 1)) for i, v in enumerate(values)]


def _eth_line() -> str:
    points = " ".join(f"{x},{v}" for x, v in eth_points())
    area = f"M0,560 L{points.replace(' ', ' L')} L{WIDTH},560 Z"
    return f'<path class="eth-area" d="{area}"/><polyline class="eth-line" points="{points}"/>'


def _grid(series: list[Candle]) -> str:
    """Price levels inside the zoom: they spread and bunch up as the chart rescales."""
    lo = int(min(c.low for c in series)) - 4
    hi = int(max(c.high for c in series)) + 4
    return "".join(
        f'<line class="zgrid" x1="0" x2="1440" y1="{base_y(v)}" y2="{base_y(v)}"/>'
        for v in range(lo - lo % 2, hi + 1, 2)
    )


def head_keyframes(series: list[Candle]) -> str:
    """Path of the glowing price tip on the scan line (unzoomed): the close of the candle being
    born right now."""
    stops = sorted((crossing(center(i)), base_y(c.close)) for i, c in enumerate(series))
    return _keyframes("head", [(f, f"translateY({v}px)") for f, v in stops], _wrap_value(stops))


def _wrap_value(stops: list[tuple[float, float]]) -> float:
    """Value at 0 % / 100 %: between the last stop of one loop and the first of the next."""
    first, last = stops[0], stops[-1]
    span = first[0] + 1 - last[0]
    return round(last[1] + (first[1] - last[1]) * (1 - last[0]) / span, 2)


def _keyframes(name: str, stops: list[tuple[float, str]], edge: float | str) -> str:
    edge_value = edge if isinstance(edge, str) else f"translateY({edge}px)"
    frames = [(0.0, edge_value), *stops, (1.0, edge_value)]
    body = " ".join(f"{f * 100:.3f}% {{ transform: {v}; }}" for f, v in frames)
    return f"@keyframes {name} {{ {body} }}\n"


def zoom_keyframes(frames: list[Zoom]) -> str:
    def edge(attr: str) -> float:
        return _wrap_value([(z.fraction, getattr(z, attr)) for z in frames])

    shift, scale = edge("shift"), edge("scale")
    zoom = _keyframes(
        "zoom",
        [(z.fraction, f"translateY({z.shift}px) scaleY({z.scale})") for z in frames],
        f"translateY({shift}px) scaleY({scale})",
    )
    unzoom = _keyframes(
        "unzoom",
        [(z.fraction, f"scaleY({round(1 / z.scale, 5)})") for z in frames],
        f"scaleY({round(1 / scale, 5)})",
    )
    return zoom + unzoom


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
        # Everything right of the scan line is the future: clipped away. The past clip is tall
        # because inside the zoom it is scaled with the candles; only its x edge matters.
        f'<defs><clipPath id="past"><rect x="-10" y="-100000" width="{CURSOR + 10}" '
        'height="200000"/></clipPath>'
        '<clipPath id="band"><rect x="0" y="540" width="1440" height="355"/></clipPath></defs>'
        f'<g clip-path="url(#past)"><g class="eth-tape">{eth}'
        f'<g transform="translate({WIDTH} 0)">{eth}</g></g></g>'
        '<g clip-path="url(#band)"><g class="zoom">'
        + _grid(series)
        + f'<g clip-path="url(#past)"><g class="tape">{one}'
        f'<g transform="translate({WIDTH} 0)">{one}</g></g></g>'
        f'<g class="head"><line class="head-line" x1="{CURSOR}" x2="{CURSOR + 34}" y1="0" '
        f'y2="0"/><g class="unzoom-c"><circle class="head-dot" cx="{CURSOR}" cy="0" r="3.5"/>'
        f'<circle class="head-halo" cx="{CURSOR}" cy="0" r="9"/></g></g></g></g>'
        f'<rect class="scan-glow" x="{CURSOR - 40}" y="548" width="40" height="342"/>'
        f'<line class="scan" x1="{CURSOR}" x2="{CURSOR}" y1="548" y2="890"/>'
        f'<text class="scan-label" x="{CURSOR - 12}" y="560" text-anchor="end">'
        f"SIGNAL-SCAN · DEMO</text>{logs}"
        f'<text class="demo-note" x="{CURSOR - 12}" y="884" text-anchor="end">'
        "Deko · erfundene Kurse und Signale</text>"
    )


def css() -> str:
    series = candles()
    found = signals(series)
    frames = zoom_frames(series)
    first = frames[0]
    timing = "".join(
        f".sig-{n}, .sig-{n} .sig-ring, .log-{n} {{ animation-delay: {delay(s.index)}s; }}\n"
        for n, s in enumerate(found)
    )
    flash, fade = _pct(FLASH_SECONDS), _pct(FLASH_SECONDS + 1.5)
    log, gone = _pct(LOG_SECONDS), _pct(LOG_SECONDS + 0.4)
    still = f"translateY({first.shift}px) scaleY({first.scale})"
    return (
        f"""
.tape {{ animation: tape {PERIOD}s linear infinite; }}
.eth-tape {{ animation: tape {LINE_PERIOD}s linear infinite; }}
@keyframes tape {{ to {{ transform: translateX(-{WIDTH}px); }} }}
/* Static transforms are the first frame: with reduced motion the chart still sits in its band. */
.zoom {{ transform-box: view-box; transform-origin: 0 0; transform: {still};
  animation: zoom {PERIOD}s linear infinite; }}
.unzoom-c, .unzoom-up, .unzoom-down {{ transform-box: fill-box;
  transform: scaleY({round(1 / first.scale, 5)}); animation: unzoom {PERIOD}s linear infinite; }}
.unzoom-c {{ transform-origin: 50% 50%; }}
.unzoom-down {{ transform-origin: 50% 0%; }}
.unzoom-up {{ transform-origin: 50% 100%; }}
.zoom line, .zoom rect, .zoom polygon {{ vector-effect: non-scaling-stroke; }}
.zgrid {{ stroke: rgba(150, 152, 158, 0.09); stroke-width: 1; }}
.eth-line {{ fill: none; stroke: rgba(255, 181, 71, 0.32); stroke-width: 1.4; }}
.eth-area {{ fill: rgba(255, 181, 71, 0.035); }}
.scan {{ stroke: rgba(45, 226, 230, 0.55); stroke-width: 1; stroke-dasharray: 4 4; }}
.scan-glow {{ fill: rgba(45, 226, 230, 0.06); }}
.scan-label, .demo-note {{ fill: rgba(45, 226, 230, 0.55); font: 600 10px var(--mono);
  letter-spacing: 0.16em; }}
.demo-note {{ fill: var(--dim); font-weight: 400; letter-spacing: 0.08em; }}
.head {{ animation: head {PERIOD}s linear infinite;
  transform: translateY({base_y(series[0].close)}px); }}
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
        + zoom_keyframes(frames)
        + timing
    )
