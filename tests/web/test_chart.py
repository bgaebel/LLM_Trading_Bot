"""The decorative chart: born at the scan line, seamless loop, signals flash when they are born."""

import itertools
import re

import pytest

from llm_trading_bot.web import chart


def test_candle_tape_loops_without_a_jump() -> None:
    series = chart.candles()
    assert len(series) == chart.COUNT
    assert series[0].open == pytest.approx(series[-1].close)
    assert all(c.low < min(c.open, c.close) and c.high > max(c.open, c.close) for c in series)


def test_walk_is_a_bridge_inside_its_band() -> None:
    values = chart.walk(11, chart.COUNT, *chart.BAND)
    assert min(values) == pytest.approx(chart.BAND[0])
    assert max(values) == pytest.approx(chart.BAND[1])
    # Not a smooth wave: direction changes often and step sizes vary a lot.
    steps = [b - a for a, b in itertools.pairwise(values)]
    turns = sum(1 for a, b in itertools.pairwise(steps) if a * b < 0)
    assert turns > chart.COUNT // 4
    assert max(abs(s) for s in steps) > 4 * sorted(abs(s) for s in steps)[len(steps) // 2]


def test_candles_and_markers_stay_inside_the_chart_band() -> None:
    ys = [chart.y(v) for c in chart.candles() for v in (c.high, c.low)]
    assert min(ys) > 548 and max(ys) < 890
    pattern = r'class="sig-label" x="[0-9]+" y="([0-9.]+)"'
    labels = [float(v) for v in re.findall(pattern, chart.svg())]
    assert labels and min(labels) > 560 and max(labels) < 900


def test_eth_line_ends_where_it_starts() -> None:
    points = chart.eth_points()
    assert points[0][1] == points[-1][1] and points[-1][0] == chart.WIDTH


def test_nothing_right_of_the_scan_line_is_drawn() -> None:
    svg = chart.svg()
    clip = re.search(r'<clipPath id="past"><rect x="(-?[0-9]+)" y="0" width="([0-9]+)"', svg)
    assert clip and int(clip.group(1)) + int(clip.group(2)) == chart.CURSOR
    clipped = svg[svg.index('<g clip-path="url(#past)">') :]
    # Both tapes sit inside the clipped group, which closes only after them.
    assert clipped.index('class="eth-tape"') < clipped.index('class="tape"')
    assert clipped.index('class="tape"') < clipped.index('class="scan-glow"')
    assert svg.index('class="scan-glow"') > svg.index('<g clip-path="url(#past)">')


def test_signals_are_regular_and_never_overlap() -> None:
    found = chart.signals(chart.candles())
    assert len(found) >= 10 and {s.action for s in found} == {"BUY", "SELL"}
    starts = sorted(chart.crossing(chart.center(s.index)) * chart.PERIOD for s in found)
    gaps = [b - a for a, b in zip(starts, [*starts[1:], starts[0] + chart.PERIOD], strict=True)]
    # A log line shows for LOG_SECONDS; the next signal must come later, but not much later.
    assert min(gaps) > chart.LOG_SECONDS + 0.4 and max(gaps) < 35


def test_each_signal_flashes_when_its_candle_is_born() -> None:
    for s in chart.signals(chart.candles()):
        start = chart.delay(s.index)
        assert -chart.PERIOD < start <= 0
        # Keyframe 0 % of the flash comes after (-start) seconds into a cycle, i.e. at this time:
        t = (chart.PERIOD + start) % chart.PERIOD
        shift = chart.WIDTH * t / chart.PERIOD
        positions = [chart.center(s.index) + copy * chart.WIDTH - shift for copy in (0, 1)]
        assert min(abs(p - chart.CURSOR) for p in positions) < 0.01 * chart.STEP


def test_price_tip_follows_the_candle_being_born() -> None:
    series = chart.candles()
    frames = re.findall(r"([0-9.]+)% \{ transform: translateY\(([0-9.]+)px\); \}", chart.css())
    stops = {round(float(p), 3): float(v) for p, v in frames}
    assert stops[0.0] == stops[100.0]
    for i, c in enumerate(series):
        assert stops[round(chart.crossing(chart.center(i)) * 100, 3)] == chart.y(c.close)


def test_every_signal_has_marker_log_line_and_timing_class() -> None:
    found = chart.signals(chart.candles())
    svg, css = chart.svg(), chart.css()
    for n in range(len(found)):
        assert svg.count(f'class="sig sig-{n} ') == 2  # both copies of the tape
        assert svg.count(f'class="log log-{n} ') == 1
        assert f".sig-{n}, .sig-{n} .sig-ring, .log-{n} {{ animation-delay:" in css


def test_chart_says_it_is_decoration() -> None:
    assert "DEMO" in chart.svg() and "erfundene Kurse und Signale" in chart.svg()
    assert "style=" not in chart.svg()
