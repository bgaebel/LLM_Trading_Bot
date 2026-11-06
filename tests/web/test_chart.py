"""The decorative chart: born at the scan line, breakouts with auto-zoom, seamless loop, signals
flash when they are born."""

import itertools
import re

import pytest

from llm_trading_bot.web import chart


def _order() -> list[int]:
    return sorted(range(chart.COUNT), key=lambda i: chart.crossing(chart.center(i)))


def test_candle_tape_loops_without_a_jump() -> None:
    series = chart.candles()
    assert len(series) == chart.COUNT
    assert series[0].open == pytest.approx(series[-1].close)
    assert all(c.low < min(c.open, c.close) and c.high > max(c.open, c.close) for c in series)


def test_walk_has_tight_ranges_and_breakouts() -> None:
    values = chart.walk(11, chart.COUNT)
    steps = [b - a for a, b in itertools.pairwise(values)]
    # Breakouts: some 10-candle stretches move far more than the typical one.
    moves = sorted(abs(values[i + 10] - values[i]) for i in range(len(values) - 10))
    assert moves[-1] > 6 * moves[len(moves) // 2]
    # Still a jagged chart, not a smooth wave.
    turns = sum(1 for a, b in itertools.pairwise(steps) if a * b < 0)
    assert turns > chart.COUNT // 4


def test_auto_zoom_rescales_out_and_back_in() -> None:
    scales = [z.scale for z in chart.zoom_frames(chart.candles())]
    assert max(scales) / min(scales) > 2.5
    # Not a one-off: the view zooms in and out several times per loop.
    directions = [b > a for a, b in itertools.pairwise(scales) if abs(b - a) > 0.01]
    changes = sum(1 for a, b in itertools.pairwise(directions) if a != b)
    assert changes >= 4


def test_visible_candles_always_fit_the_band() -> None:
    series = chart.candles()
    frames = chart.zoom_frames(series)
    for frame, born in zip(frames, _order(), strict=True):
        window = [series[(born - k) % chart.COUNT] for k in range(chart.WINDOW)]
        assert min(frame.screen(c.high) for c in window) >= 540
        assert max(frame.screen(c.low) for c in window) <= 895


def test_zoom_widens_fast_and_narrows_slowly() -> None:
    assert chart.GROW > 5 * chart.SHRINK


def test_markers_and_price_tip_keep_their_size_while_zooming() -> None:
    svg, css = chart.svg(), chart.css()
    assert svg.count('<g class="unzoom-down">') + svg.count('<g class="unzoom-up">') == 2 * len(
        chart.signals(chart.candles())
    )
    assert '<g class="head">' in svg and 'class="unzoom-c"><circle class="head-dot"' in svg
    # The inverse keyframes undo the zoom at every frame.
    zoom = dict(
        re.findall(r"([0-9.]+)% \{ transform: translateY\([-0-9.]+px\) scaleY\(([0-9.]+)\)", css)
    )
    unzoom = dict(re.findall(r"([0-9.]+)% \{ transform: scaleY\(([0-9.]+)\); \}", css))
    assert zoom.keys() == unzoom.keys() and len(zoom) >= chart.COUNT
    for key, scale in zoom.items():
        assert float(scale) * float(unzoom[key]) == pytest.approx(1, abs=1e-4)


def test_eth_line_ends_where_it_starts() -> None:
    points = chart.eth_points()
    assert points[0][1] == points[-1][1] and points[-1][0] == chart.WIDTH


def test_nothing_right_of_the_scan_line_is_drawn() -> None:
    svg = chart.svg()
    clip = re.search(r'<clipPath id="past"><rect x="(-?[0-9]+)" y="[-0-9]+" width="([0-9]+)"', svg)
    assert clip and int(clip.group(1)) + int(clip.group(2)) == chart.CURSOR
    # Both tapes sit inside a group clipped to the past.
    assert re.search(r'<g clip-path="url\(#past\)"><g class="eth-tape">', svg)
    assert re.search(r'<g clip-path="url\(#past\)"><g class="tape">', svg)


def test_signals_are_regular_and_never_overlap() -> None:
    found = chart.signals(chart.candles())
    assert len(found) >= 15 and {s.action for s in found} == {"BUY", "SELL"}
    starts = sorted(chart.crossing(chart.center(s.index)) * chart.PERIOD for s in found)
    gaps = [b - a for a, b in zip(starts, [*starts[1:], starts[0] + chart.PERIOD], strict=True)]
    # A log line shows for LOG_SECONDS; the next signal comes later - and within a minute.
    assert min(gaps) > chart.LOG_SECONDS + 0.4 and max(gaps) < 60


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
    block = re.search(r"@keyframes head \{(.*?)\}\n", chart.css())
    assert block
    frames = re.findall(r"([0-9.]+)% \{ transform: translateY\(([-0-9.]+)px\); \}", block.group(1))
    stops = {round(float(p), 3): float(v) for p, v in frames}
    assert stops[0.0] == stops[100.0]
    for i, c in enumerate(series):
        assert stops[round(chart.crossing(chart.center(i)) * 100, 3)] == chart.base_y(c.close)


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
