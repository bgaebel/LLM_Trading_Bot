"""Spec 016 AK5: the fidelity check rebuilds every live snapshot and lists each deviation.

The "live" databases are written by the unchanged ``paper_cycle`` (classic depots, a GPT depot
with a scripted model, buy & hold) over a synthetic market whose client hides a forming bar
younger than 5 s, as Bitvavo does until the bar's first trade. The rebuild reads the same rows
from an archive, where every bar has a row - the approximation the check must explain.
"""

import hashlib
import io
import json
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClock, FakeTransport
from lab_captures import captures, merged
from llm_fakes import KEY, FakeModel
from llm_trading_bot.lab import fidelity, model
from llm_trading_bot.lab.archive import Archive, StoredRow, window_start
from llm_trading_bot.lab.fidelity import (
    FORMING,
    NOT_COVERED,
    ROUGH,
    UNREADABLE,
    check,
    cli_main,
    open_readonly,
    recorded_snapshots,
    summary_text,
    write_report,
)
from llm_trading_bot.lab.history import (
    CandleRow,
    CandleSource,
    ConstantSpreads,
    HistoricalClient,
    MemorySource,
)
from llm_trading_bot.lab.model import FIDELITY_MIN, MARKETS, FidelitySummary
from llm_trading_bot.lab.paths import CANDLES_FILE, REPORTS_DIR
from llm_trading_bot.llm.agents import BotConfig, load_config
from llm_trading_bot.market.bitvavo import CANDLE_LIMIT, BitvavoClient
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.market.snapshot import INTERVALS, MarketClient
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import ObservationBuffer, ObservationWriter
from llm_trading_bot.paper.observations_schema import create
from llm_trading_bot.paper.risk import Controls

P = 1_793_577_600
"""2026-11-02T00:00:00Z, aligned to every interval; the first round is P + 5 s."""
ROUNDS = [P + k * 900 + 5 for k in range(8)]
"""Two hours of rounds every 15 minutes, two of them at an hour mark."""
END = P + 3 * 3600
LEAD = 650
"""Rows per interval before P: more than one window of 500."""
VOL = {1: 0.0005, 15: 0.003, 60: 0.03, 240: 0.03}
PRICE = {"BTC-EUR": 60_000.0, "ETH-EUR": 3_000.0}
AGENTS = "trend,breakout,meanrev,ensemble,gpt-luna"
BTC = Symbol.BTCEUR


def candles(market: str, interval: int) -> list[CandleRow]:
    """A seeded random walk (randrange only: equal on every platform). Every 37th minute has
    no trade, so the live gap filling of 1m bars is part of the rebuild."""
    rng = random.Random(f"{market}/{interval}")
    step = interval * 60
    price = PRICE[market]
    out: list[CandleRow] = []
    for ts in range(P - LEAD * step, END, step):
        close = price * (1 + VOL[interval] * rng.randrange(-1000, 1001) / 1000)
        high = max(price, close) * (1 + rng.randrange(0, 50) / 100_000)
        low = min(price, close) * (1 - rng.randrange(0, 50) / 100_000)
        volume = (1 + rng.randrange(1000)) / 100
        if not (interval == 1 and (ts // 60) % 37 == 0):
            values = (price, high, low, close, volume)
            out.append([ts * 1000, *(f"{v:.2f}" for v in values)])
        price = float(f"{close:.2f}")
    return out


def source() -> MemorySource:
    src = MemorySource()
    for market in PRICE:
        for interval in INTERVALS:
            src.add(market, interval, candles(market, interval))
    return src


def live_hint(_symbol: Symbol, interval: int, now: int) -> bool:
    """Live: a bar younger than 5 s had no trade yet, so Bitvavo did not send it."""
    return now % (interval * 60) > 5


@dataclass
class Live:
    bot: Path
    obs: Path


def record(
    folder: Path, config: BotConfig, clients: list[tuple[int, MarketClient]], gpt: bool
) -> Live:
    """The service's round: paper_cycle with an observation buffer, written after the round."""
    live = Live(folder / "bot.db", folder / "observations.db")
    con = open_db(live.bot, agents=(*config.agents, BUYHOLD))
    writer = ObservationWriter(live.obs)
    try:
        for at, client in clients:
            buffer = ObservationBuffer()
            report = paper_cycle(
                con,
                client,
                config,
                costs=Costs(),
                model=FakeModel() if gpt else None,
                clock=FakeClock(at),
                controls=Controls(),
                observations=buffer,
            )
            assert not report.quotes_error
            writer.write(buffer)
            assert writer.failed == 0, writer.last_error
    finally:
        con.close()
    return live


def run_live(folder: Path) -> Live:
    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": AGENTS})
    src = source()
    clients: list[tuple[int, MarketClient]] = [
        (
            at,
            HistoricalClient(
                src,
                ConstantSpreads(0.0002),
                FakeClock(at),
                start=P - LEAD * 240 * 60,
                end=END,
                forming_hint=live_hint,
            ),
        )
        for at in ROUNDS
    ]
    return record(folder, config, clients, gpt=True)


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Live:
    return run_live(tmp_path_factory.mktemp("live"))


@pytest.fixture
def copy(live: Live, tmp_path: Path) -> Live:
    """A private copy a test may change; the module's live files stay as they are."""
    out = Live(tmp_path / "bot.db", tmp_path / "observations.db")
    out.bot.write_bytes(live.bot.read_bytes())
    out.obs.write_bytes(live.obs.read_bytes())
    return out


@contextmanager
def opened(live: Live, obs: bool) -> Iterator[tuple[sqlite3.Connection, sqlite3.Connection | None]]:
    bot = open_readonly(live.bot)
    o = open_readonly(live.obs) if obs else None
    try:
        yield bot, o
    finally:
        bot.close()
        if o is not None:
            o.close()


def checked(live: Live, *, obs: bool, src: CandleSource | None = None) -> FidelitySummary:
    with opened(live, obs) as (bot, o):
        return check(bot, src or source(), obs=o)


def failing(s: FidelitySummary) -> list[dict[str, Any]]:
    return [d for d in s.deviations if d["kind"] not in ("rough", "skipped")]


def edit(path: Path, sql: str, *args: object) -> None:
    con = sqlite3.connect(path)
    try:
        assert con.execute(sql, args).rowcount >= 1, sql
        con.commit()
    finally:
        con.close()


def classic_row(path: Path, *, agent: str, round_at: int) -> tuple[int, dict[str, Any]]:
    """A BTC decision of ``agent`` in the round at ``round_at``: (id, inputs)."""
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT id, inputs FROM decisions WHERE agent=? AND symbol='BTCEUR'", (agent,)
        ).fetchall()
    finally:
        con.close()
    for row_id, text in rows:
        inputs = json.loads(text)
        if fidelity._epoch(inputs["utc"]) == round_at:
            return int(row_id), inputs
    raise AssertionError(f"no {agent} decision at {round_at}")


@pytest.fixture
def few(monkeypatch: pytest.MonkeyPatch) -> None:
    """A minimum the short synthetic run can reach, to see the other conditions."""
    monkeypatch.setattr(model, "FIDELITY_MIN", 5)
    monkeypatch.setattr(fidelity, "FIDELITY_MIN", 5)


# ------------------------------------------------------------------ the forming hint


def test_forming_hint_rebuilds_the_live_window() -> None:
    src = source()
    now = P + 3600 + 5  # the 1h bar opened 5 s ago and has a row in the archive

    def client(hint: bool | None) -> HistoricalClient:
        return HistoricalClient(
            src,
            ConstantSpreads(),
            FakeClock(now),
            start=P - LEAD * 240 * 60,
            end=END,
            forming_hint=lambda _s, _i, _n: hint,
        )

    default = HistoricalClient(
        src, ConstantSpreads(), FakeClock(now), start=P - LEAD * 14400, end=END
    )
    approx = default.closed_candles(BTC, 60, now_epoch=now)
    assert client(None).closed_candles(BTC, 60, now_epoch=now) == approx
    assert client(True).closed_candles(BTC, 60, now_epoch=now) == approx
    assert approx.forming and len(approx) == CANDLE_LIMIT - 1
    exact = client(False).closed_candles(BTC, 60, now_epoch=now)
    # Live without the forming bar: 500 closed rows, one older bar at the start.
    assert not exact.forming and exact.received == len(exact) == CANDLE_LIMIT
    assert exact.first_open == approx.first_open - 3600  # type: ignore[operator]
    assert exact[1:] == approx[:]
    assert exact.last_open == approx[-1].ts
    # Between bar edges the forming bar always had trades: the hint changes nothing there.
    assert client(None).closed_candles(BTC, 15, now_epoch=now + 300) == client(True).closed_candles(
        BTC, 15, now_epoch=now + 300
    )


# ------------------------------------------------------------------ the check


def test_every_recorded_snapshot_is_compared(live: Live) -> None:
    with opened(live, False) as (bot, _):
        recs, unreadable = recorded_snapshots(bot)
        classic = bot.execute(
            "SELECT COUNT(*) FROM decisions WHERE agent IN "
            "('trend','breakout','meanrev','ensemble')"
        ).fetchone()[0]
        calls = bot.execute("SELECT context FROM llm_calls").fetchall()
    gpt_markets = sum(len(json.loads(c[0])["markets"]) for c in calls)
    assert not unreadable
    assert calls and classic >= 2 * len(ROUNDS) * 3
    assert {r.source for r in recs} == {"decision", "llm_call"}
    assert len(recs) == classic + gpt_markets
    # GPT snapshots carry the cadence's trigger signals; classic ones their action and target.
    assert all(r.triggers for r in recs if r.source == "llm_call")
    assert all(r.action and r.bar_ts for r in recs if r.source == "decision")
    s = checked(live, obs=True)
    assert sum(s.compared.values()) == len(recs)


def test_too_few_is_not_established(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    s = checked(live, obs=True)
    assert s.with_observations
    assert failing(s) == []
    for market in MARKETS:
        assert 0 < s.compared[market] < FIDELITY_MIN
        assert s.equal_action[market] == s.indicators_ok[market] == s.compared[market]
    # Every comparison equal, and still not established below the minimum - never "100 %".
    assert s.established == {m: False for m in MARKETS}
    text = summary_text(s)
    assert text.count(f"Treue nicht belegt (unter {FIDELITY_MIN} Vergleichen)") == 2
    assert "100 %" not in text and "%" not in text.split("Ungeklärte")[0]
    assert "Versiegelung" in s.note and "keine Renditen" in s.note
    # The same run reaches it once the minimum is met.
    monkeypatch.setattr(model, "FIDELITY_MIN", 5)
    assert s.established == {m: True for m in MARKETS}


def test_empty_copy_is_not_established() -> None:
    con = open_db(Path(":memory:"), agents=("trend",))
    s = check(con, MemorySource())
    assert s.compared == {m: 0 for m in MARKETS}
    assert s.established == {m: False for m in MARKETS}


def test_forming_bar_explained_without_observations(live: Live, few: None) -> None:
    s = checked(live, obs=False)
    assert not s.with_observations
    found = failing(s)
    # The archive counts the young 1h bar that live did not get: only ema72_1h feels the window
    # start one bar later, and only in the rounds at an hour mark.
    assert found, "the synthetic run must show the approximation"
    assert {d["cause"] for d in found} == {FORMING}
    assert {d["field"] for d in found} == {"indicators.ema72_1h"}
    assert all(d["utc"].endswith(":00:05+00:00") for d in found)
    assert all("60m" in d["detail"] for d in found)
    assert s.equal_action == s.compared == s.indicators_ok
    assert s.established == {m: True for m in MARKETS}
    assert "laufende Kerze" in s.note
    # With the recorded forming flags the same snapshots are rebuilt exactly.
    assert failing(checked(live, obs=True)) == []


def test_mid_bar_deviation_not_explained_as_forming(copy: Live, few: None) -> None:
    """15 minutes into the 1h bar the archive's 1m rows show trades: live had that bar, so a
    window one 1h bar further back is a real deviation (a window-start bug), not the
    approximation - even though dropping the forming bar would make it disappear."""
    row_id, inputs = classic_row(copy.bot, agent="breakout", round_at=ROUNDS[1])
    with opened(copy, False) as (bot, _):
        recs, _ = recorded_snapshots(bot)
    (rec,) = [
        r
        for r in recs
        if (r.agent, r.symbol, r.epoch, r.source) == ("breakout", BTC, ROUNDS[1], "decision")
    ]
    rebuilder = fidelity.Rebuilder(source(), [rec.epoch])
    shifted = rebuilder.rebuild(rec, {60: False}).indicators
    assert shifted is not None
    stored = inputs["indicators"]["ema72_1h"]
    wrong = round(float(shifted["ema72_1h"]), 5)
    assert fidelity._steps(wrong, stored, 5) > 1, "the shifted window must be a real deviation"
    inputs["indicators"]["ema72_1h"] = wrong
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    s = checked(copy, obs=False)
    (d,) = [d for d in failing(s) if not d["cause"]]
    assert (d["utc"], d["agent"], d["field"]) == (inputs["utc"], "breakout", "indicators.ema72_1h")
    assert s.indicators_ok["BTC-EUR"] == s.compared["BTC-EUR"] - 1
    assert s.established["BTC-EUR"] is False
    # The hour-mark rounds still get the approximation: no 1m bar of the new hour had closed.
    explained = {d["utc"] for d in failing(s) if d["cause"] == FORMING}
    assert explained and all(u.endswith(":00:05+00:00") for u in explained)


def test_traded_counts_only_closed_minutes_with_volume() -> None:
    """Live lacks the forming bar only while it has no trade: a closed 1m bar with volume in
    it rules that out; a zero-volume row or the 1m bar still forming does not."""
    src = MemorySource()
    zero = [P * 1000, "1", "1", "1", "1", "0"]
    some = [(P + 60) * 1000, "1", "1", "1", "1", "0.5"]
    src.add("BTC-EUR", 1, [zero, some])
    rebuilder = fidelity.Rebuilder(src, [P + 600])
    assert not rebuilder.traded(BTC, 60, P + 119)  # P+60 is still forming at P+119
    assert rebuilder.traded(BTC, 60, P + 120)
    assert not rebuilder.traded(BTC, 60, P + 3600 + 5)  # a new hour: nothing closed in it
    assert not rebuilder.traded(BTC, 1, P + 120)  # the forming 1m bar itself: never known
    real = fidelity.Rebuilder(source(), [ROUNDS[1]])
    assert real.traded(BTC, 60, ROUNDS[1]) and not real.traded(BTC, 60, ROUNDS[4])


def test_stale_quote_compared_with_fail_closed_hold(copy: Live, few: None) -> None:
    """record_decision stores a STALE_QUOTE decision as HOLD with target 0; anything else is a
    regression of the fail-closed path and must not count as equal."""
    row_id, _ = classic_row(copy.bot, agent="meanrev", round_at=ROUNDS[2])
    edit(
        copy.bot,
        "UPDATE decisions SET status='STALE_QUOTE', action='HOLD', target=0 WHERE id=?",
        row_id,
    )
    s = checked(copy, obs=True)
    assert failing(s) == []
    assert s.equal_action == s.compared
    edit(copy.bot, "UPDATE decisions SET action='SELL', target=0.77 WHERE id=?", row_id)
    s = checked(copy, obs=True)
    got = {(d["field"], d["live"], d["rebuilt"], d["cause"]) for d in failing(s)}
    assert got == {("action", "SELL", "HOLD", ""), ("target", 0.77, 0.0, "")}
    assert s.equal_action["BTC-EUR"] == s.compared["BTC-EUR"] - 1
    assert s.established["BTC-EUR"] is False


def test_stale_quote_expects_hold_whatever_the_signal(copy: Live, few: None) -> None:
    """The fail-closed HOLD/0 is what the check expects even where the rebuilt signal says BUY:
    a STALE_QUOTE row that kept the raw signal is a regression, the stored HOLD/0 is not."""
    row_id, _ = classic_row(copy.bot, agent="breakout", round_at=ROUNDS[3])
    con = sqlite3.connect(copy.bot)
    try:
        signal = con.execute(
            "SELECT action, target FROM decisions WHERE id=?", (row_id,)
        ).fetchone()
    finally:
        con.close()
    assert signal == ("BUY", 0.45), "the row must carry a signal other than HOLD/0"
    edit(
        copy.bot,
        "UPDATE decisions SET status='STALE_QUOTE', action='HOLD', target=0 WHERE id=?",
        row_id,
    )
    s = checked(copy, obs=True)
    assert failing(s) == []
    assert s.equal_action == s.compared
    # The raw signal stored under STALE_QUOTE: listed, although the rebuild gives the same BUY.
    edit(copy.bot, "UPDATE decisions SET action='BUY', target=0.45 WHERE id=?", row_id)
    s = checked(copy, obs=True)
    got = {(d["agent"], d["field"], d["live"], d["rebuilt"], d["cause"]) for d in failing(s)}
    assert got == {("breakout", "action", "BUY", "HOLD", ""), ("breakout", "target", 0.45, 0.0, "")}
    assert s.equal_action["BTC-EUR"] == s.compared["BTC-EUR"] - 1
    assert s.established["BTC-EUR"] is False


def test_classic_decision_without_utc_is_listed(copy: Live) -> None:
    """Only buy & hold has no snapshot behind its decision; a classic row without ``utc`` is
    listed as unreadable, never dropped silently."""
    before = checked(copy, obs=False)
    assert not [d for d in before.deviations if d["kind"] == "skipped"]
    row_id, inputs = classic_row(copy.bot, agent="breakout", round_at=ROUNDS[3])
    del inputs["utc"]
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    edit(
        copy.bot,
        "UPDATE decisions SET inputs='kein json'"
        " WHERE id=(SELECT MIN(id) FROM decisions WHERE agent=?)",
        "trend",
    )
    s = checked(copy, obs=False)
    skipped = [d for d in s.deviations if d["kind"] == "skipped"]
    assert sorted((d["agent"], d["cause"]) for d in skipped) == [
        ("breakout", UNREADABLE),
        ("trend", UNREADABLE),
    ]
    assert "utc" in next(d for d in skipped if d["agent"] == "breakout")["rebuilt"]
    assert sum(s.compared.values()) == sum(before.compared.values()) - 2
    assert "2 Snapshots übersprungen" in s.note and "2 davon mit nicht lesbaren Eingaben" in s.note
    assert "Übersprungen: 2" in summary_text(s)


def test_decision_neither_classic_nor_gpt_is_listed(copy: Live) -> None:
    """A decision row of an agent the check does not know as classic and without GPT
    provenance (``_llm``) has no snapshot to find: it is listed as unreadable, never dropped."""
    before = checked(copy, obs=False)
    assert not [d for d in before.deviations if d["kind"] == "skipped"]
    con = sqlite3.connect(copy.bot)
    try:
        row_id, text = con.execute(
            "SELECT id, inputs FROM decisions WHERE agent='gpt-luna' ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    inputs = json.loads(text)
    del inputs["_llm"]
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    s = checked(copy, obs=False)
    skipped = [d for d in s.deviations if d["kind"] == "skipped"]
    assert [(d["agent"], d["cause"]) for d in skipped] == [("gpt-luna", UNREADABLE)]
    assert "_llm" in skipped[0]["rebuilt"]
    # Its call row still carries the snapshot: nothing else is lost, nothing else fails.
    assert s.compared == before.compared
    assert "1 Snapshots übersprungen" in s.note
    assert "Übersprungen: 1" in summary_text(s)


def test_rounding_step_allowed(copy: Live, few: None) -> None:
    row_id, inputs = classic_row(copy.bot, agent="breakout", round_at=ROUNDS[1])
    stored = inputs["indicators"]["sma20_15m"]
    inputs["indicators"]["sma20_15m"] = stored + 1e-5
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    s = checked(copy, obs=True)
    assert failing(s) == []
    assert s.indicators_ok == s.compared

    inputs["indicators"]["sma20_15m"] = stored + 2e-5
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    s = checked(copy, obs=True)
    (d,) = failing(s)
    assert d["field"] == "indicators.sma20_15m" and d["cause"] == ""
    assert d["live"] == stored + 2e-5 and d["rebuilt"] == stored
    assert (d["market"], d["agent"], d["kind"]) == ("BTC-EUR", "breakout", "indicator")
    assert s.indicators_ok["BTC-EUR"] == s.compared["BTC-EUR"] - 1
    assert s.equal_action == s.compared  # the action itself is still equal
    assert s.established == {"BTC-EUR": False, "ETH-EUR": True}


def test_action_mismatch_listed(copy: Live, few: None) -> None:
    row_id, inputs = classic_row(copy.bot, agent="trend", round_at=ROUNDS[0])
    con = sqlite3.connect(copy.bot)
    action, target = con.execute(
        "SELECT action, target FROM decisions WHERE id=?", (row_id,)
    ).fetchone()
    con.close()
    other = "SELL" if action != "SELL" else "BUY"
    edit(copy.bot, "UPDATE decisions SET action=? WHERE id=?", other, row_id)
    for obs in (True, False):
        s = checked(copy, obs=obs)
        # Without observations the young 1h bar of this hour-mark round is explained as well.
        (d,) = [d for d in failing(s) if not d["cause"]]
        assert d == {
            "market": "BTC-EUR",
            "utc": inputs["utc"],
            "agent": "trend",
            "field": "action",
            "live": other,
            "rebuilt": action,
            "cause": "",
            "kind": "action",
        }
        assert s.equal_action["BTC-EUR"] == s.compared["BTC-EUR"] - 1
        assert s.established["BTC-EUR"] is False
        text = summary_text(s)
        assert "Ungeklärte Abweichungen: 1" in text
        assert f"BTC-EUR {inputs['utc']} trend action: live '{other}', Nachbau '{action}'" in text

    edit(copy.bot, "UPDATE decisions SET action=?, target=? WHERE id=?", action, 0.33, row_id)
    (d,) = failing(checked(copy, obs=True))
    assert (d["field"], d["live"], d["rebuilt"]) == ("target", 0.33, target)


def test_reference_and_trigger_signals_compared(copy: Live, few: None) -> None:
    con = sqlite3.connect(copy.bot)
    call_id, context, triggers = con.execute(
        "SELECT id, context, trigger_signals FROM llm_calls ORDER BY id LIMIT 1"
    ).fetchone()
    con.close()
    flip = {"BUY": "SELL", "SELL": "HOLD", "HOLD": "BUY"}
    trig = json.loads(triggers)
    trig["BTC-EUR"]["trend"] = flip[trig["BTC-EUR"]["trend"]]
    ctx = json.loads(context)
    ref = ctx["markets"]["ETH-EUR"]["reference_signals"]
    ref["breakout"] = flip[ref["breakout"]]
    edit(
        copy.bot,
        "UPDATE llm_calls SET trigger_signals=?, context=? WHERE id=?",
        json.dumps(trig),
        json.dumps(ctx),
        call_id,
    )
    s = checked(copy, obs=True)
    got = {(d["market"], d["agent"], d["field"], d["cause"]) for d in failing(s)}
    assert got == {
        ("BTC-EUR", "gpt-luna", "trigger_signals.trend", ""),
        ("ETH-EUR", "gpt-luna", "reference_signals.breakout", ""),
    }
    assert s.established == {m: False for m in MARKETS}


def test_windows_and_unrounded_indicators_with_observations(copy: Live, few: None) -> None:
    con = sqlite3.connect(copy.obs)
    (window_id,) = con.execute(
        "SELECT MAX(id) FROM windows WHERE symbol='ETHEUR' AND interval=240"
    ).fetchone()
    meta_id, ind = con.execute(
        "SELECT id, indicators FROM decision_meta WHERE symbol='BTCEUR' AND agent='meanrev'"
        " ORDER BY id LIMIT 1"
    ).fetchone()
    con.close()
    edit(copy.obs, "UPDATE windows SET rows_sha256=? WHERE id=?", "0" * 64, window_id)
    raw = json.loads(ind)
    raw["ema24_1h"] *= 1 + 1e-9
    edit(copy.obs, "UPDATE decision_meta SET indicators=? WHERE id=?", json.dumps(raw), meta_id)
    s = checked(copy, obs=True)
    fields = {(d["market"], d["field"], d["cause"]) for d in failing(s)}
    assert fields == {
        ("ETH-EUR", "window.240m.rows_sha256", ""),
        ("BTC-EUR", "indicators_raw.ema24_1h", ""),
    }
    # The rounded payload still matches: only the observations tell these apart.
    assert s.indicators_ok == s.equal_action == s.compared
    assert s.established == {m: False for m in MARKETS}
    # Without observations nothing of this is checked.
    assert {d["cause"] for d in failing(checked(copy, obs=False))} == {FORMING}


def test_percent_fields_only_rough(copy: Live, few: None) -> None:
    row_id, inputs = classic_row(copy.bot, agent="ensemble", round_at=ROUNDS[2])
    inputs["returns_pct"]["60"] += 0.01
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(inputs), row_id)
    s = checked(copy, obs=True)
    assert failing(s) == []
    (d,) = [d for d in s.deviations if d["kind"] == "rough"]
    assert (d["field"], d["cause"], d["agent"]) == ("returns_pct.60", ROUGH, "ensemble")
    assert s.established == {m: True for m in MARKETS}
    assert "Nur grob prüfbar (Prozentfelder): 1" in summary_text(s)


def test_unreadable_inputs_are_listed_not_compared(copy: Live) -> None:
    row_id, inputs = classic_row(copy.bot, agent="trend", round_at=ROUNDS[0])
    other_id, other = classic_row(copy.bot, agent="meanrev", round_at=ROUNDS[1])
    before = checked(copy, obs=False)
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", '{"utc": "kaputt"}', row_id)
    del other["quote_bid"]
    edit(copy.bot, "UPDATE decisions SET inputs=? WHERE id=?", json.dumps(other), other_id)
    s = checked(copy, obs=False)
    skipped = [d for d in s.deviations if d["kind"] == "skipped"]
    assert [(d["agent"], d["cause"]) for d in skipped] == [
        ("trend", UNREADABLE),
        ("meanrev", UNREADABLE),
    ]
    assert "quote_bid" in skipped[1]["rebuilt"]
    assert s.compared["BTC-EUR"] == before.compared["BTC-EUR"] - 2
    assert "2 Snapshots übersprungen" in s.note
    assert inputs["utc"] != "kaputt"


def test_round_without_observations_is_checked_as_without(copy: Live, few: None) -> None:
    """A round whose observations were lost: its snapshots fall back to the approximation."""
    con = sqlite3.connect(copy.obs)
    (round_id,) = con.execute(
        "SELECT id FROM rounds WHERE ts<=? ORDER BY id DESC LIMIT 1", (fidelity._iso(ROUNDS[4]),)
    ).fetchone()
    con.close()
    edit(copy.obs, "DELETE FROM snapshots WHERE round_id=?", round_id)
    s = checked(copy, obs=True)
    found = failing(s)
    # Round 4 is an hour mark: without its forming flags the young 1h bar shows again.
    assert found and {d["cause"] for d in found} == {FORMING}
    assert {fidelity._epoch(d["utc"]) for d in found} == {ROUNDS[4]}
    assert "ohne aufgezeichnete Runde" in s.note
    assert s.established == {m: True for m in MARKETS}


# ------------------------------------------------------------------ archive, files, CLI


def build_archive(path: Path, *, cut_1m: int | None = None) -> None:
    """``candles.db`` from the synthetic rows; ``cut_1m``: the 1m window of the live period
    was last fetched then (rows up to that time only, status open)."""
    late = END + 30 * 86_400
    with Archive(path) as archive:
        for market in PRICE:
            for interval in INTERVALS:
                step = interval * 60
                by_window: dict[int, list[StoredRow]] = {}
                for r in candles(market, interval):
                    ts = int(str(r[0])) // 1000
                    stored: StoredRow = (ts, *(str(v) for v in r[1:6]))  # type: ignore[assignment]
                    by_window.setdefault(window_start(interval, ts), []).append(stored)
                for start, rows in sorted(by_window.items()):
                    fetched = late
                    if cut_1m is not None and interval == 1 and start == window_start(1, P):
                        fetched = cut_1m
                        rows = [r for r in rows if r[0] + step + 60 <= cut_1m]
                    archive.store_window(market, interval, start, rows, fetched_at=fetched)


def test_archive_not_covering_is_skipped(live: Live, tmp_path: Path) -> None:
    path = tmp_path / CANDLES_FILE
    build_archive(path, cut_1m=ROUNDS[4])
    s = fidelity.run(live.bot, path, obs_db=live.obs)
    skipped = [d for d in s.deviations if d["kind"] == "skipped"]
    assert skipped and {d["cause"] for d in skipped} == {NOT_COVERED}
    assert {fidelity._epoch(d["utc"]) for d in skipped} == set(ROUNDS[4:])
    full = checked(live, obs=True)
    assert 0 < sum(s.compared.values()) < sum(full.compared.values())
    assert sum(s.compared.values()) + len(skipped) == sum(full.compared.values())
    assert failing(s) == []
    assert "übersprungen" in s.note


D = P
"""The day of the coverage probe (window start of 1m)."""
DAY = 86_400


def coverage_archive(path: Path, *, earlier: str, today: int | None = None) -> Archive:
    """BTC-EUR rows of every interval up to D + 1 day, all windows complete, except the 1m
    window of the day before D: ``earlier`` = "done", "open" (a fetch at D-1 12:00 left it
    with rows up to 11:58) or "missing" (never fetched); ``today``: 1m window D was last
    fetched then (open, the rows settled by then)."""
    late = D + 30 * DAY
    archive = Archive(path)
    for interval in INTERVALS:
        step = interval * 60
        first = D - 2 * DAY if interval == 1 else D - 600 * step
        by_window: dict[int, list[StoredRow]] = {}
        for ts in range(first, D + DAY, step):
            by_window.setdefault(window_start(interval, ts), []).append(
                (ts, "1.00", "1.00", "1.00", "1.00", "1.00")
            )
        for start, rows in sorted(by_window.items()):
            fetched = late
            if interval == 1 and start == D and today is not None:
                fetched = today
                rows = [r for r in rows if r[0] + step + 60 <= fetched]
            if interval == 1 and start == D - DAY:
                if earlier == "missing":
                    continue
                if earlier == "open":
                    fetched = D - DAY + 12 * 3600
                    rows = [r for r in rows if r[0] + step + 60 <= fetched]
            archive.store_window("BTC-EUR", interval, start, rows, fetched_at=fetched)
    return archive


@pytest.mark.parametrize("earlier", ["open", "missing"])
def test_coverage_checks_earlier_windows_the_lead_reaches(tmp_path: Path, earlier: str) -> None:
    """Review probe: ``lab fetch`` on D-1 at 12:00 left 1m window D-1 open, a later
    ``lab fetch --start D`` never fetched it again. The 500 rows of a snapshot at D 00:05
    reach back into that gap: not covered, rather than deviations blamed on the live code."""
    with coverage_archive(tmp_path / CANDLES_FILE, earlier=earlier) as archive:
        state = archive.window_state("BTC-EUR", 1, D)
        assert state is not None and state.status == "done"
        assert archive.count_before("BTC-EUR", 1, D + 301) >= CANDLE_LIMIT
        assert fidelity.archive_coverage(archive)("BTC-EUR", D + 300) is False
        # Once the 500 rows lie inside window D, the gap no longer matters.
        assert fidelity.archive_coverage(archive)("BTC-EUR", D + 600 * 60) is True


def test_coverage_with_complete_windows(tmp_path: Path) -> None:
    with coverage_archive(tmp_path / CANDLES_FILE, earlier="done") as archive:
        covered = fidelity.archive_coverage(archive)
        assert covered("BTC-EUR", D + 300) is True
        assert covered("ETH-EUR", D + 300) is False  # no rows at all


def test_coverage_of_the_forming_window(tmp_path: Path) -> None:
    """The window of the forming bar may be open if it was fetched after that bar settled."""
    with coverage_archive(tmp_path / CANDLES_FILE, earlier="done", today=D + 420) as archive:
        state = archive.window_state("BTC-EUR", 1, D)
        assert state is not None and state.status == "open"
        covered = fidelity.archive_coverage(archive)
        assert covered("BTC-EUR", D + 300) is True  # bar 00:05 closed 00:06, settled 00:07
        assert covered("BTC-EUR", D + 360) is False  # bar 00:06 was still forming then


def old_windows(con: sqlite3.Connection, symbol: Symbol, epoch: int) -> object:
    """The per-snapshot SQL the lookup replaced: the reference for its answers."""
    r = con.execute(
        "SELECT id FROM rounds WHERE ts<=? AND ts>=? ORDER BY id DESC LIMIT 1",
        (fidelity._iso(epoch), fidelity._iso(epoch - 30)),
    ).fetchone()
    if r is None:
        return None
    ok = con.execute(
        "SELECT 1 FROM snapshots WHERE round_id=? AND symbol=? AND status='ok'",
        (r[0], symbol.name),
    ).fetchone()
    if ok is None:
        return None
    out = {}
    for interval in INTERVALS:
        w = con.execute(
            "SELECT rows, first_open, last_open, forming, rows_sha256 FROM windows"
            " WHERE symbol=? AND interval=? AND round_id<=? ORDER BY id DESC LIMIT 1",
            (symbol.name, interval, r[0]),
        ).fetchone()
        if w is None:
            return None
        out[interval] = fidelity.ObservedWindow(int(w[0]), w[1], w[2], bool(w[3]), str(w[4]))
    return out


def synthetic_obs(path: Path, rounds: int) -> None:
    """Rounds every minute (some with fractional seconds, ids not in time order), snapshots
    mostly ok, windows written when they change, with ids not in round order either."""
    create(path)
    rng = random.Random(f"obs/{rounds}")
    con = sqlite3.connect(path)
    ids = list(range(1, rounds + 1))
    for k in range(0, rounds - 1, 7):
        ids[k], ids[k + 1] = ids[k + 1], ids[k]
    window_ids = iter(rng.sample(range(1, 10 * rounds), 9 * rounds))
    with con:
        for k, rid in enumerate(ids):
            at = P + k * 60 + rng.randrange(0, 4)
            ts = fidelity._iso(at) if k % 3 else fidelity._utc(at).replace("+", ".25+")
            con.execute("INSERT INTO rounds(id, ts, release) VALUES (?, ?, 'r')", (rid, ts))
            for symbol in (Symbol.BTCEUR, Symbol.ETHEUR):
                status = "ok" if rng.randrange(10) else "error"
                con.execute(
                    "INSERT INTO snapshots(round_id, ts, symbol, status, decided)"
                    " VALUES (?, ?, ?, ?, 1)",
                    (rid, ts, symbol.name, status),
                )
                for interval in INTERVALS:
                    if k and rng.randrange(interval) and k % 50:
                        continue
                    con.execute(
                        "INSERT INTO windows(id, round_id, ts, symbol, interval, rows,"
                        " first_open, last_open, forming, rows_sha256)"
                        " VALUES (?, ?, ?, ?, ?, 500, ?, ?, ?, ?)",
                        (
                            next(window_ids),
                            rid,
                            ts,
                            symbol.name,
                            interval,
                            at - 500 * interval * 60,
                            at,
                            rng.randrange(2),
                            f"{rid}/{interval}",
                        ),
                    )
            con.execute(
                "INSERT INTO decision_meta(ts, agent, symbol, bar_ts, strategy,"
                " strategy_version, release, indicators) VALUES (?, 'trend', 'BTCEUR', ?,"
                " 'trend', '1', 'r', '{\"rsi\": 1.0}')",
                (ts, at),
            )
    con.close()


def test_observation_lookup_answers_as_the_sql(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    synthetic_obs(path, 600)
    con = open_readonly(path)
    try:
        obs = fidelity.Observations(con)
        seen = 0
        for epoch in range(P - 40, P + 600 * 60 + 40, 13):
            for symbol in (Symbol.BTCEUR, Symbol.ETHEUR):
                want = old_windows(con, symbol, epoch)
                assert obs.windows(symbol, epoch) == want, (symbol, epoch)
                seen += want is not None
        assert seen > 1000
    finally:
        con.close()


def test_observation_lookups_go_through_an_index(tmp_path: Path) -> None:
    """Review finding: one full read of rounds and windows per snapshot made months of
    observations take hours. After the one load at the start, every statement per snapshot
    is an indexed search (no table scan, no sort) and there are at most six of them."""
    path = tmp_path / "observations.db"
    synthetic_obs(path, 300)
    con = open_readonly(path)
    try:
        obs = fidelity.Observations(con)
        traced: list[str] = []
        con.set_trace_callback(traced.append)
        calls = 0
        for k in range(300):
            calls += 1
            obs.windows(Symbol.BTCEUR, P + k * 60 + 5)
            obs.indicators(Symbol.BTCEUR, ("trend", P + k * 60, None))
        con.set_trace_callback(None)
        assert 0 < len(traced) <= 6 * calls
        for sql in set(traced):
            plan = " | ".join(str(r[3]) for r in con.execute("EXPLAIN QUERY PLAN " + sql))
            assert "SCAN" not in plan and "TEMP B-TREE" not in plan, (sql, plan)
    finally:
        con.close()


def digest(path: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.parent.iterdir())
    }


def test_cli_writes_report_never_overwrites(live: Live, tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    build_archive(lab / CANDLES_FILE)
    before = digest(live.bot)
    args = ["--bot", str(live.bot), "--obs", str(live.obs), "--dir", str(lab)]
    out = io.StringIO()
    assert cli_main("fidelity", args, out) == fidelity.EXIT_NOT_ESTABLISHED
    assert cli_main("fidelity", args, io.StringIO()) == fidelity.EXIT_NOT_ESTABLISHED
    # The second report of the same second gets "-2": first by length, then by name.
    reports = sorted((lab / REPORTS_DIR).glob("fidelity-*.json"), key=lambda p: (len(p.name), p))
    assert len(reports) == 2
    first = FidelitySummary.from_json(reports[0].read_text(encoding="utf-8"))
    assert first.compared == checked(live, obs=True).compared
    assert first.with_observations
    text = out.getvalue()
    assert "Treue nicht belegt" in text and f"Bericht: {reports[0]}" in text
    # Only read: the copies and their folder are exactly as before (no -wal/-shm left).
    assert digest(live.bot) == before


def test_cli_established_exit_code(live: Live, tmp_path: Path, few: None) -> None:
    build_archive(tmp_path / CANDLES_FILE)
    args = ["--bot", str(live.bot), "--obs", str(live.obs), "--dir", str(tmp_path)]
    out = io.StringIO()
    assert cli_main("fidelity", args, out) == fidelity.EXIT_ESTABLISHED
    assert out.getvalue().count("Treue belegt") == 2


def test_write_report_never_overwrites(tmp_path: Path) -> None:
    s = FidelitySummary(compared={"BTC-EUR": 1})
    a = write_report(s, tmp_path, now=P)
    a.write_text("geändert", encoding="utf-8")
    b = write_report(s, tmp_path, now=P)
    assert a.name == "fidelity-20261102T000000Z.json"
    assert b.name == "fidelity-20261102T000000Z-2.json"
    assert a.read_text(encoding="utf-8") == "geändert"
    assert FidelitySummary.from_json(b.read_text(encoding="utf-8")) == s


def test_missing_input_is_an_error(tmp_path: Path, live: Live) -> None:
    out = io.StringIO()
    args = ["--bot", str(tmp_path / "fehlt.db"), "--dir", str(tmp_path)]
    assert cli_main("fidelity", args, out) == fidelity.EXIT_ERROR
    assert "Treue-Test nicht möglich" in out.getvalue()
    assert not (tmp_path / "fehlt.db").exists()
    assert not (tmp_path / REPORTS_DIR).exists()
    # The bot copy is fine, but there is no archive: refused, nothing created.
    args = ["--bot", str(live.bot), "--dir", str(tmp_path)]
    assert cli_main("fidelity", args, io.StringIO()) == fidelity.EXIT_ERROR
    assert not (tmp_path / CANDLES_FILE).exists()


def test_copies_are_opened_read_only(live: Live, tmp_path: Path) -> None:
    con = open_readonly(live.bot)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE x(a)")
    finally:
        con.close()
    # A copy with a -wal beside it: the rows in the WAL count (immutable would drop them).
    db = tmp_path / "w.db"
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE t(a)")
    writer.execute("INSERT INTO t VALUES (1)")
    copy = tmp_path / "copy.db"
    copy.write_bytes(db.read_bytes())
    (tmp_path / "copy.db-wal").write_bytes((tmp_path / "w.db-wal").read_bytes())
    writer.close()
    con = open_readonly(copy)
    try:
        assert [tuple(r) for r in con.execute("SELECT a FROM t")] == [(1,)]
    finally:
        con.close()


# ------------------------------------------------------------------ real Bitvavo responses


def test_real_captures_rebuilt_exactly_with_observations(tmp_path: Path) -> None:
    """Frozen real responses (Spec 016 AK3): live through the real BitvavoClient, the rebuild
    from the same rows. With the recorded forming flags nothing differs; without them every
    difference is the forming bar (wave-1 finding: ema72_1h at a round after an hour mark)."""
    clients: list[tuple[int, MarketClient]] = [
        (c.at, BitvavoClient(FakeTransport(c.handler()))) for c in captures()
    ]
    live = record(
        tmp_path, BotConfig(("trend", "breakout", "meanrev", "ensemble"), None), clients, False
    )
    src = merged(captures())
    exact = checked(live, obs=True, src=src)
    assert sum(exact.compared.values()) >= 8
    assert failing(exact) == []
    assert exact.equal_action == exact.indicators_ok == exact.compared
    approx = checked(live, obs=False, src=src)
    found = failing(approx)
    assert found and {d["cause"] for d in found} == {FORMING}
    assert {d["field"] for d in found} == {"indicators.ema72_1h"}
    # At the hour mark BTC-EUR had no trade yet in the new 1h bar: 8 rounding steps.
    assert all(d["utc"].endswith(":00:05+00:00") for d in found)
    assert {round((d["rebuilt"] - d["live"]) * 1e5) for d in found} == {8}
    assert approx.equal_action == approx.compared
