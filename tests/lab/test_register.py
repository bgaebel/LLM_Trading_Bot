"""Spec 016 AK8: trial register - dedup of repeats, append-only stores, checksum chain."""

import dataclasses
import json
import math
import os
import sqlite3
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from llm_trading_bot.lab.model import (
    DayPoint,
    DepotResult,
    MarketQuality,
    Quarter,
    QuarterResult,
    Trade,
    Variant,
)
from llm_trading_bot.lab.paths import CHAIN_FILE, LAB_DIR_ENV, RUNS_FILE
from llm_trading_bot.lab.register import (
    CEILING_ENV,
    GENESIS,
    UNKNOWN_COMMIT,
    ChainError,
    CodeState,
    GitRunner,
    Register,
    RegisterError,
    canonical,
    code_state,
    config_hash,
    daily_returns,
    head_from_files,
    sha256_text,
    trial_key,
    verify_chain,
)
from llm_trading_bot.paper.db import SchemaError

COMMIT = "a" * 40
OTHER = "b" * 40
DATA = "d" * 64


def day_of(epoch: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def result(label: str = "2022Q1", variant: Variant | None = None, **kw: object) -> QuarterResult:
    q = Quarter.parse(label)
    # The quarter's first two UTC days: the register refuses day points outside the quarter.
    day1, day2 = (day_of(q.start + n * 86_400) for n in (0, 1))
    depot = DepotResult(
        "trend",
        [
            DayPoint(day1, 10_100.0, 5_000.0, {"BTC-EUR": 0.5}),
            DayPoint(day2, 9_999.0, 5_000.0, {"BTC-EUR": 0.5}),
        ],
        end_mid=9_999.0,
        end_liquidated=9_980.0,
    )
    out = QuarterResult(
        q,
        variant or Variant(),
        depots={"trend": depot},
        quality={"BTC-EUR": MarketQuality("BTC-EUR", 100, 99)},
        rounds=1440,
        seconds=3.5,
        dataset_sha256=DATA,
    )
    for name, value in kw.items():
        setattr(out, name, value)
    return out


def open_reg(tmp_path: Path, commit: str = COMMIT, clean: bool = True) -> Register:
    return Register.open(
        tmp_path / RUNS_FILE,
        tmp_path / CHAIN_FILE,
        code=CodeState(commit, clean),
        now=lambda: 1_700_000_000.0,
    )


def test_rerun_deduplicated(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        first = reg.record(result())
        # Same trial, data and commit: only the wall-clock runtime differs.
        again = reg.record(result(seconds=9.9))
        assert (first.merged, first.repeat) == (False, 1)
        assert (again.merged, again.repeat, again.run_id) == (True, 2, first.run_id)
        assert again.trial_key == first.trial_key
        runs = reg.list_runs()
        assert len(runs) == 1 and runs[0].repeats == 2
        assert reg.trial_count() == 1
        # The archive grew: another dataset, still the same trial (config x window x variant).
        other_data = reg.record(result(dataset_sha256="e" * 64))
        assert not other_data.merged and reg.trial_count() == 1 and len(reg.list_runs()) == 2
        assert reg.verify().ok and reg.verify().lines == 3
    # Another commit is another configuration, hence another trial.
    with open_reg(tmp_path, commit=OTHER) as reg:
        assert not reg.record(result()).merged
        assert reg.trial_count() == 2 and reg.config_count() == 2
        assert reg.verify().ok


def test_differing_repeat_kept_as_own_run(tmp_path: Path) -> None:
    with open_reg(tmp_path, clean=False) as reg:
        reg.record(result())
        changed = reg.record(result(rounds=1439))
        assert not changed.merged and changed.repeat == 1
        assert changed.trial_key == reg.list_runs()[0].trial_key
        # Same key, data and commit, other outcome: other code was run - one more trial.
        assert len(reg.list_runs()) == 2 and reg.trial_count() == 2
        assert reg.verify().ok


def test_differing_outcomes_never_under_counted(tmp_path: Path) -> None:
    # Dirty tree: three uncommitted strategy edits on one commit are three trials.
    with open_reg(tmp_path / "dirty", clean=False) as reg:
        for n in (1, 2, 3):
            reg.record(result("2023Q1", rounds=1000 + n))
        assert reg.trial_count() == 3 and reg.config_count() == 1
        # The identical outcome again is a repeat, not a trial.
        assert reg.record(result("2023Q1", rounds=1001)).merged
        assert reg.trial_count() == 3
        # New data on a dirty tree cannot be told from a code change: counted (cautious).
        reg.record(result("2023Q1", rounds=1001, dataset_sha256="e" * 64))
        assert reg.trial_count() == 4
    # Clean tree: a data change is the same trial, a non-deterministic rerun is not.
    with open_reg(tmp_path / "clean") as reg:
        reg.record(result())
        reg.record(result(dataset_sha256="e" * 64))
        assert reg.trial_count() == 1
        reg.record(result(rounds=1439))
        assert reg.trial_count() == 2
    # Unknown commit proves nothing, even if the tree is said to be clean.
    with open_reg(tmp_path / "unknown", commit=UNKNOWN_COMMIT) as reg:
        reg.record(result())
        reg.record(result(dataset_sha256="e" * 64))
        assert reg.trial_count() == 2
    # A dirty outcome later reproduced from a clean tree is proven: data growth merges again.
    with open_reg(tmp_path / "proven", clean=False) as reg:
        reg.record(result())
    with open_reg(tmp_path / "proven", clean=True) as reg:
        reg.record(result(dataset_sha256="e" * 64))
        assert reg.trial_count() == 2  # the dirty outcome may stem from other code
        assert reg.record(result()).merged
        assert reg.trial_count() == 1 and reg.trial_count(statuses=None) == 1
    # An abort and a later success on the same data are one trial.
    with open_reg(tmp_path / "abort") as reg:
        reg.record_aborted(Quarter.parse("2022Q1"), Variant(), "boom", dataset_sha256=DATA)
        reg.record(result())
        assert reg.trial_count(statuses=None) == 1 and reg.trial_count() == 1


def test_chain_detects_loss(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        for label in ("2021Q2", "2021Q3", "2021Q4", "2022Q1"):
            reg.record(result(label))
        good = reg.verify()
        assert good.ok and good.lines == 4 and good.head != GENESIS
        chain = tmp_path / CHAIN_FILE
        lines = chain.read_text(encoding="utf-8").splitlines(keepends=True)

        def variant_of(name: str, content: list[str]) -> Path:
            path = tmp_path / name
            path.write_text("".join(content), encoding="utf-8", newline="")
            return path

        assert verify_chain(variant_of("same.jsonl", lines)).ok
        removed = variant_of("removed.jsonl", [lines[0], lines[2], lines[3]])
        assert not verify_chain(removed).ok
        changed_line = lines[1].replace('"2021Q3"', '"2023Q3"')
        changed = variant_of("changed.jsonl", [lines[0], changed_line, *lines[2:]])
        assert any("hash mismatch" in p for p in verify_chain(changed).problems)
        reordered = variant_of("reordered.jsonl", [lines[0], lines[2], lines[1], lines[3]])
        assert not verify_chain(reordered).ok
        cut = variant_of("cut.jsonl", lines[:3])
        assert verify_chain(cut).ok  # a cut-off tail is only visible against the register
        assert not verify_chain(cut, expected_lines=4, expected_head=good.head).ok
        half = variant_of("half.jsonl", [*lines[:3], lines[3][:20]])
        assert not verify_chain(half).ok

        # The real chain loses its last line: the register notices and refuses to build on it.
        chain.write_text("".join(lines[:3]), encoding="utf-8", newline="")
        assert not reg.verify().ok
        with pytest.raises(ChainError):
            reg.record(result("2022Q2"))
        assert len(reg.list_runs()) == 4  # nothing recorded by the refused attempt
        chain.write_text("".join(lines), encoding="utf-8", newline="")
        assert reg.verify().ok
        assert reg.record(result("2022Q2")).seq == 5


def test_aborted_quarter_recorded(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        rec = reg.record_aborted(Quarter.parse("2023Q2"), Variant(), "MemoryError in worker")
        (run,) = reg.list_runs(status="aborted")
        assert run.run_id == rec.run_id and run.error == "MemoryError in worker"
        assert run.quarter == "2023Q2" and not run.graded
        assert reg.result(rec.run_id).status == "aborted"
        # Not a result anyone could look at, but never forgotten.
        assert reg.trial_count() == 0 and reg.trial_count(statuses=None) == 1
        event = json.loads((tmp_path / CHAIN_FILE).read_text(encoding="utf-8"))["event"]
        assert (event["status"], event["error"]) == ("aborted", "MemoryError in worker")
        # The same quarter later succeeds: one trial, two runs.
        reg.record(result("2023Q2"))
        assert reg.trial_count(statuses=None) == 1 and len(reg.list_runs()) == 2


def test_register_is_append_only(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        reg.record(result())
        reg.record(result())
        for sql in (
            "UPDATE runs SET status='ok'",
            "DELETE FROM runs",
            "UPDATE repeats SET clean=1",
            "DELETE FROM repeats",
            "DELETE FROM daily_returns",
            "UPDATE events SET line=''",
            "DELETE FROM events",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                reg.con.execute(sql)
        assert reg.verify().ok


def test_daily_returns_stored(tmp_path: Path) -> None:
    res = result()
    returns = daily_returns(res.depots["trend"])
    assert [day for day, _ in returns] == ["2022-01-01", "2022-01-02"]
    assert [r for _, r in returns] == pytest.approx([0.01, 9_999.0 / 10_100.0 - 1])
    # Compounded, the daily returns give the depot's change over the quarter.
    assert (1 + returns[0][1]) * (1 + returns[1][1]) == pytest.approx(9_999.0 / 10_000.0)
    with open_reg(tmp_path) as reg:
        rec = reg.record(res)
        assert reg.returns(rec.run_id) == {"trend": daily_returns(res.depots["trend"])}
        assert reg.result(rec.run_id) == res


def test_config_hash_and_trial_key() -> None:
    base = config_hash(Variant(), COMMIT)
    shuffled = ["buyhold", "trend", "meanrev", "ensemble", "breakout"]
    assert base == config_hash(Variant(), COMMIT, depots=shuffled)
    assert base != config_hash(Variant(fee_rate=0.003), COMMIT)
    assert base != config_hash(Variant(), OTHER)
    assert base != config_hash(Variant(), COMMIT, depots=["trend"])
    assert base != config_hash(Variant(), COMMIT, strategy_version="v0.4.0")
    q1, q2 = Quarter.parse("2022Q1"), Quarter.parse("2022Q2")
    assert trial_key(base, q1, Variant()) != trial_key(base, q2, Variant())


def test_diagnostic_and_filters(tmp_path: Path) -> None:
    diag = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    with open_reg(tmp_path) as reg:
        reg.record(result("2022Q1"))
        reg.record(result("2022Q2"))
        reg.record(result("2022Q1", diag))
        assert reg.trial_count() == 3
        assert reg.trial_count(include_diagnostic=False) == 2
        assert reg.config_count() == 2 and reg.config_count(include_diagnostic=False) == 1
        assert [r.variant for r in reg.list_runs(diagnostic=True)] == ["gap-tolerance"]
        assert [r.quarter for r in reg.list_runs(variant="base")] == ["2022Q1", "2022Q2"]
        assert len(reg.list_runs(quarter="2022Q1")) == 2
        assert len(reg.list_runs(commit=COMMIT, graded=True)) == 3
        assert reg.list_runs(status="sealed") == []


def test_invalid_status_refused(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg, pytest.raises(RegisterError):
        reg.record(result(status="OK; DROP"))


def assert_nothing_written(reg: Register) -> None:
    assert reg.list_runs() == [] and reg.verify().ok and reg.verify().lines == 0


def test_sealed_period_refused(tmp_path: Path) -> None:
    sealed_q = Quarter.parse("2025Q4")
    with open_reg(tmp_path) as reg:
        # A bug in the backtest would otherwise leave sealed numbers in runs.db for good.
        with pytest.raises(RegisterError, match="sealed period"):
            reg.record(result("2025Q4"))
        with pytest.raises(RegisterError, match="sealed period"):
            reg.record_aborted(sealed_q, Variant(), "boom")
        with pytest.raises(RegisterError, match="carries no numbers"):
            reg.record(result("2025Q4", status="sealed"))
        with pytest.raises(RegisterError, match="carries no numbers"):
            reg.record(
                QuarterResult(sealed_q, Variant(), "sealed", daily_mid={"BTC-EUR": [("x", 1.0)]})
            )
        # Review finding: without days and trades, sealed numbers still got in (end values,
        # stop, decisions, data quality, spreads, rounds). Each one alone is refused.
        numbers: list[dict[str, object]] = [
            {"depots": {"trend": DepotResult("trend", end_mid=10_234.5)}},
            {"depots": {"trend": DepotResult("trend", end_liquidated=10_200.0)}},
            {"depots": {"trend": DepotResult("trend", halted_at=sealed_q.start + 60)}},
            {"depots": {"trend": DepotResult("trend", actions={"BUY/OK": 7})}},
            {"quality": {"BTC-EUR": MarketQuality("BTC-EUR", 8000, 7900, {"gap_1h": 3}, 5)}},
            {"quality": {"BTC-EUR": MarketQuality("BTC-EUR")}},
            {"spread": {"BTC-EUR": 0.0012}},
            {"rounds": 8000},
        ]
        for fields in numbers:
            sealed = QuarterResult(sealed_q, Variant(), "sealed")
            for name, value in fields.items():
                setattr(sealed, name, value)
            with pytest.raises(RegisterError, match="carries no numbers"):
                reg.record(sealed)
        with pytest.raises(RegisterError, match="before the sealed period"):
            reg.record(QuarterResult(Quarter.parse("2025Q3"), Variant(), "sealed"))
        assert_nothing_written(reg)
        # The refusal itself is a number-free entry and may be recorded; its runtime may stay.
        refusal = QuarterResult(
            sealed_q,
            Variant(),
            "sealed",
            depots={"trend": DepotResult("trend")},
            daily_mid={"BTC-EUR": []},
            seconds=0.02,
        )
        rec = reg.record(refusal)
        assert (
            reg.returns(rec.run_id) == {} and reg.list_runs(status="sealed")[0].quarter == "2025Q4"
        )
        assert reg.result(rec.run_id) == refusal
        assert reg.verify().ok


def test_day_points_outside_quarter_refused(tmp_path: Path) -> None:
    q3 = Quarter.parse("2025Q3")

    def with_day(day: str) -> QuarterResult:
        res = result("2025Q3")
        res.depots["trend"].days[-1] = dataclasses.replace(res.depots["trend"].days[-1], day=day)
        return res

    with open_reg(tmp_path) as reg:
        for bad in ("2025-10-01", "2025-06-30", "2025-7-02", "2025-02-30", "2025-07-01"):
            # the exclusive end (also sealed), the day before, malformed, no date, repeated
            with pytest.raises(RegisterError):
                reg.record(with_day(bad))
        mid = result("2025Q3", daily_mid={"BTC-EUR": [("2025-07-01", 1.0), ("2025-10-01", 1.0)]})
        with pytest.raises(RegisterError, match="outside"):
            reg.record(mid)
        late = result("2025Q3")
        late.depots["trend"].trades.append(Trade(q3.end, "trend", "BTC-EUR", "BUY", 1, 1, 1, 0))
        with pytest.raises(RegisterError, match="trade"):
            reg.record(late)
        nan = with_day("2025-07-02")
        nan.depots["trend"].days[-1] = dataclasses.replace(
            nan.depots["trend"].days[-1], value=math.nan
        )
        with pytest.raises(RegisterError, match="finite"):
            reg.record(nan)
        assert_nothing_written(reg)
        # The last day of the quarter and a trade one second before its end are inside.
        ok = with_day("2025-09-30")
        ok.depots["trend"].trades.append(Trade(q3.end - 1, "trend", "BTC-EUR", "BUY", 1, 1, 1, 0))
        ok.daily_mid = {"BTC-EUR": [("2025-07-01", 1.0), ("2025-09-30", 1.0)]}
        reg.record(ok)
        assert reg.verify().ok


def test_daily_mid_checked_like_depot_days(tmp_path: Path) -> None:
    # Review finding: only format and quarter were checked; NaN, repeats and disorder got in.
    def with_mid(*rows: tuple[str, float]) -> QuarterResult:
        return result("2025Q3", daily_mid={"BTC-EUR": list(rows)})

    with open_reg(tmp_path) as reg:
        for bad in (
            [("2025-07-05", math.nan)],
            [("2025-07-05", math.inf)],
            [("2025-07-05", 0.0)],  # lab.metrics refuses a mid <= 0
            [("2025-07-05", 1.0), ("2025-07-02", 1.0)],  # out of order
            [("2025-07-02", 1.0), ("2025-07-02", 2.0)],  # repeated
            [("2025-07-01", 1.0), ("2025-06-30", 1.0)],  # base day only as the first entry
            [("2025-06-29", 1.0), ("2025-07-01", 1.0)],  # two days before the quarter
            [("2025-6-30", 1.0)],  # malformed
            [("2025-09-30", 1.0), ("2025-10-01", 1.0)],  # the exclusive end
        ):
            with pytest.raises(RegisterError, match="mid BTC-EUR"):
                reg.record(with_mid(*bad))
        assert_nothing_written(reg)
        # The convention of lab.metrics and the backtest: base of day 1 labelled the day before.
        reg.record(with_mid(("2025-06-30", 1.0), ("2025-07-01", 1.1), ("2025-09-30", 1.2)))
        # A market quoted only from mid-quarter on: its base lies inside the quarter.
        reg.record(with_mid(("2025-08-01", 1.0), ("2025-08-02", 1.1)))
        assert reg.verify().ok and len(reg.list_runs()) == 2


def test_stop_and_numbers_checked(tmp_path: Path) -> None:
    # Review finding: stop time, trade numbers, cash and weights were not checked at all.
    q3 = Quarter.parse("2025Q3")
    good = Trade(q3.start + 60, "trend", "BTC-EUR", "BUY", 0.1, 30_000.0, 3_000.0, 7.5)

    def changed(
        point: DayPoint | None = None,
        spread: dict[str, float] | None = None,
        **depot: object,
    ) -> QuarterResult:
        res = result("2025Q3", spread=spread or {})
        trend = res.depots["trend"]
        if point is not None:
            trend.days[0] = point
        for name, value in depot.items():
            setattr(trend, name, value)
        return res

    first = result("2025Q3").depots["trend"].days[0]
    bad = [
        changed(halted_at=q3.end),
        changed(halted_at=q3.start - 1),
        changed(halted_at=10**10),
        changed(trades=[dataclasses.replace(good, qty=math.nan)]),
        changed(trades=[dataclasses.replace(good, fill=math.inf)]),
        changed(trades=[dataclasses.replace(good, notional=-math.inf)]),
        changed(trades=[dataclasses.replace(good, fee=math.nan)]),
        changed(trades=[dataclasses.replace(good, context={"vol": math.nan})]),
        # Review finding: only the top level of a context was checked.
        changed(trades=[dataclasses.replace(good, context={"x": {"vol": math.nan}})]),
        changed(trades=[dataclasses.replace(good, context={"x": [1.0, math.inf]})]),
        changed(trades=[dataclasses.replace(good, context={"x": [{"y": (-math.inf,)}]})]),
        changed(end_mid=math.nan),
        changed(end_liquidated=math.inf),
        changed(point=dataclasses.replace(first, cash=math.nan)),
        changed(point=dataclasses.replace(first, weights={"BTC-EUR": math.inf})),
        changed(spread={"BTC-EUR": math.nan}),
    ]
    with open_reg(tmp_path) as reg:
        for res in bad:
            with pytest.raises(RegisterError, match=r"outside|not finite"):
                reg.record(res)
        assert_nothing_written(reg)
        context = {
            "vol": 0.01,
            "regime": "TREND",
            "direction": None,
            "levels": [1.5, {"atr": 2.0}],
        }
        reg.record(
            changed(
                spread={"BTC-EUR": 0.001},
                halted_at=q3.end - 1,
                trades=[dataclasses.replace(good, context=context)],
            )
        )
        assert reg.verify().ok


def test_runtime_must_be_finite(tmp_path: Path) -> None:
    # Review finding: seconds was never checked; to_json would write a non-standard NaN.
    sealed_q = Quarter.parse("2025Q4")
    bad = [
        result(seconds=math.nan),
        result(seconds=math.inf),
        QuarterResult(sealed_q, Variant(), "sealed", seconds=math.nan),
        QuarterResult(Quarter.parse("2022Q1"), Variant(), "aborted", error="x", seconds=-math.inf),
    ]
    with open_reg(tmp_path) as reg:
        for res in bad:
            with pytest.raises(RegisterError, match=r"seconds .* not finite"):
                reg.record(res)
        assert_nothing_written(reg)
        reg.record(result(seconds=0.0))
        assert reg.verify().ok


def test_daily_returns_after_zero(tmp_path: Path) -> None:
    def depot(*values: float) -> DepotResult:
        return DepotResult(
            "trend", [DayPoint(f"2022-01-0{i}", v, v, {}) for i, v in enumerate(values, 1)]
        )

    assert daily_returns(depot(5_000.0, 0.0, 0.0)) == [
        ("2022-01-01", -0.5),
        ("2022-01-02", -1.0),
        ("2022-01-03", 0.0),
    ]
    # Coming back from 0 has no return; 0.0 would hide the jump.
    with pytest.raises(RegisterError, match="after a value of 0"):
        daily_returns(depot(5_000.0, 0.0, 10.0))
    with pytest.raises(RegisterError):
        daily_returns(depot(1.0), start_cash=0.0)
    with open_reg(tmp_path) as reg:
        with pytest.raises(RegisterError, match="after a value of 0"):
            reg.record(result(depots={"trend": depot(5_000.0, 0.0, 10.0)}))
        assert_nothing_written(reg)


RUN_COLUMNS = (
    "created_at,trial_key,config_hash,strategy_version,depots,quarter,window_start,window_end,"
    "variant,diagnostic,code_commit,clean,dataset_sha256,status,error,graded,outcome_sha256,"
    "result_sha256,result_json"
)


def tampered(tmp_path: Path, name: str, *sql: str) -> list[str]:
    """Problems verify() reports after inserting rows by hand into a fresh register."""
    with open_reg(tmp_path / name) as reg:
        reg.record(result("2022Q1"))
        reg.record(result("2022Q1"))  # one repeat
        reg.record(result("2022Q2"))
        assert reg.verify().ok
        for statement in sql:
            reg.con.execute(statement)
        return reg.verify().problems


def has(problems: list[str], text: str) -> bool:
    return any(text in p for p in problems)


def test_verify_ties_tables_to_chain(tmp_path: Path) -> None:
    # Triggers stop UPDATE/DELETE, INSERT stays possible: every forged row must show.
    forged_run = tampered(
        tmp_path,
        "run",
        f"INSERT INTO runs({RUN_COLUMNS}) "
        f"SELECT {RUN_COLUMNS.replace('trial_key', "'fake'")} FROM runs WHERE id=1",
    )
    assert has(forged_run, "run 3: 0 run events, expected exactly 1")
    assert has(forged_run, "run 3: columns differ from its result in trial_key")
    extra_day = "INSERT INTO daily_returns VALUES (1,'trend','2022-01-03',1.0,0.5)"
    assert has(tampered(tmp_path, "day", extra_day), "run 1: daily returns differ")
    other_depot = "INSERT INTO daily_returns VALUES (2,'buyhold','2022-04-01',1.0,0.0)"
    assert has(tampered(tmp_path, "depot", other_depot), "run 2: daily returns differ")
    fake_repeat = (
        "INSERT INTO repeats(run_id,created_at,clean,seconds,result_sha256) VALUES (2,'x',1,0,'y')"
    )
    assert has(tampered(tmp_path, "repeat", fake_repeat), "run 2: 1 repeats in runs.db, 0 in")
    # Event row and run row inserted together, but no chain line: the chain is short.
    forged_event = tampered(
        tmp_path,
        "event",
        "INSERT INTO events(seq,created_at,kind,run_id,sha256,line) "
        "SELECT 4,created_at,kind,run_id,'f',line FROM events WHERE seq=3",
    )
    assert has(forged_event, "chain has 3 lines, register has 4")
    assert has(forged_event, "event 4: columns differ from its line")
    assert has(forged_event, "run 2: 2 run events, expected exactly 1")


def test_verify_checks_repeat_against_its_event(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        reg.record(result())
        reg.record(result(seconds=7.0))
        assert reg.verify().ok
    # A repeat row whose checksum differs from the chain's, written with the triggers bypassed
    # (a copy edited outside the register): verify() compares row by row, not only counts.
    con = sqlite3.connect(tmp_path / RUNS_FILE)
    con.execute("DROP TRIGGER repeats_no_update")
    con.execute("UPDATE repeats SET result_sha256='y'")
    con.execute(
        "CREATE TRIGGER repeats_no_update BEFORE UPDATE ON repeats "
        "BEGIN SELECT RAISE(ABORT,'trial register is append-only'); END"
    )
    con.commit()
    con.close()
    with open_reg(tmp_path) as reg:
        assert has(reg.verify().problems, "run 1: repeat 2 differs from event 2")


def append_event(folder: Path, reg: Register, event: dict[str, object]) -> None:
    """Append ``event`` to chain and ``events`` table the way ``record`` does, so the chain
    itself stays intact: only the table check in verify() can see what is wrong."""
    (head,) = reg.con.execute("SELECT sha256 FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    seq = 1 + reg.con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    sha = sha256_text(canonical({"seq": seq, "prev_sha256": head, "event": event}))
    line = canonical({"seq": seq, "prev_sha256": head, "event": event, "sha256": sha})
    reg.con.execute(
        "INSERT INTO events(seq,created_at,kind,run_id,sha256,line) VALUES (?,?,?,?,?,?)",
        (seq, event["created_at"], event["kind"], event["run_id"], sha, line),
    )
    with (folder / CHAIN_FILE).open("a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def forge_repeat(folder: Path, **fields: object) -> list[str]:
    """verify() problems after adding a repeat row and a valid chain line for it whose event
    is the run's own event (as ``record`` writes a repeat) with ``fields`` changed."""
    with open_reg(folder, clean=False) as reg:
        reg.record(result())
        run_event = json.loads((folder / CHAIN_FILE).read_text(encoding="utf-8"))["event"]
        event = {
            **run_event,
            "kind": "repeat",
            "repeat": 2,
            "created_at": "x",
            "clean": True,
            "result_sha256": "y",
            **fields,
        }
        reg.con.execute(
            "INSERT INTO repeats(run_id,created_at,clean,seconds,result_sha256) "
            "VALUES (1,'x',1,0,'y')"
        )
        append_event(folder, reg, event)
        return reg.verify().problems


def test_verify_checks_every_field_of_a_repeat(tmp_path: Path) -> None:
    # Review finding: a repeat event naming another commit, outcome, trial, dataset or status
    # than its run still verified - only checksum, counter, clean flag and time were compared.
    assert forge_repeat(tmp_path / "control") == []  # the forgery itself is built correctly
    for name, value in {
        "commit": OTHER,
        "outcome_sha256": "e" * 64,
        "trial_key": "f" * 64,
        "dataset_sha256": "e" * 64,
        "config_hash": "f" * 64,
        "status": "aborted",
        "graded": False,
        "diagnostic": 0,  # an int where the chain holds a JSON boolean
        "quarter": "2022Q2",
        "proven": True,  # a field the run event does not have
    }.items():
        problems = forge_repeat(tmp_path / name, **{name: value})
        assert problems == [f"run 1: repeat 2 differs from event 2 in {name}"], name


def test_copy_chain_never_overwrites(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        reg.record(result())
        dest = tmp_path / "report" / "register.jsonl"
        assert reg.copy_chain(dest).ok
        assert dest.read_bytes() == (tmp_path / CHAIN_FILE).read_bytes()
        with pytest.raises(FileExistsError):
            reg.copy_chain(dest)


def test_other_schema_version_refused(tmp_path: Path) -> None:
    open_reg(tmp_path).close()
    con = sqlite3.connect(tmp_path / RUNS_FILE)
    con.execute("PRAGMA user_version=2")
    con.close()
    with pytest.raises(SchemaError):
        open_reg(tmp_path)


def test_default_paths_in_lab_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LAB_DIR_ENV, str(tmp_path / "lab"))
    with Register.open(code=CodeState(COMMIT, True)) as reg:
        reg.record(result())
    assert (tmp_path / "lab" / RUNS_FILE).is_file() and (tmp_path / "lab" / CHAIN_FILE).is_file()


def fake_git(head: str | Exception, status: str | Exception) -> "FakeGit":
    return FakeGit(head, status)


class FakeGit:
    def __init__(self, head: str | Exception, status: str | Exception) -> None:
        self.answers = {"rev-parse": head, "status": status}

    def __call__(self, args: Sequence[str]) -> str:
        answer = self.answers[args[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_code_state_from_git() -> None:
    failed = subprocess.CalledProcessError(128, ["git"])
    assert code_state(fake_git(COMMIT + "\n", "")) == CodeState(COMMIT, True)
    assert code_state(fake_git(COMMIT + "\n", " M src/x.py\n")) == CodeState(COMMIT, False)
    assert code_state(fake_git(COMMIT + "\n", "?? new.py\n")) == CodeState(COMMIT, False)
    assert code_state(fake_git(COMMIT, failed)) == CodeState(COMMIT, False)
    assert code_state(fake_git(failed, "")) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(fake_git(FileNotFoundError("git"), "")) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(fake_git("fatal: not a commit", "")) == CodeState(UNKNOWN_COMMIT, False)


def real_git(repo: Path) -> GitRunner:
    """A runner that starts git, as a caller outside src may supply (src never starts processes).

    Without the caller's GIT_* variables: a git hook sets GIT_DIR/GIT_WORK_TREE, which would point
    git at the hook's repository instead of ``repo``. GIT_CEILING_DIRECTORIES is the exception, as
    tests set it on purpose."""

    def run(args: Sequence[str]) -> str:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        if CEILING_ENV in os.environ:
            env[CEILING_ENV] = os.environ[CEILING_ENV]
        done = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        )
        return done.stdout

    return run


def test_outside_git_checkout_is_unknown_and_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Independent of the environment: a checkout above the folder (here built on purpose, like a
    # dotfiles repo in the home) is ruled out by a ceiling, and GIT_DIR/GIT_WORK_TREE from a git
    # hook are neither passed to git nor read by head_from_files.
    outer = tmp_path / "outer"
    git_dir(outer, "ref: refs/heads/main", {"refs/heads/main": COMMIT})
    plain = outer / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))
    monkeypatch.setenv(CEILING_ENV, str(outer))
    # A real git in a folder that is no checkout (or no git installed at all).
    assert code_state(real_git(plain)) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(repo=plain) == CodeState(UNKNOWN_COMMIT, False)
    # Without the ceiling the outer checkout is found: the ceiling, not luck, made it unknown.
    monkeypatch.delenv(CEILING_ENV)
    assert code_state(repo=plain) == CodeState(COMMIT, False)


def git_dir(root: Path, head: str, refs: dict[str, str], packed: str = "") -> Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    for name, sha in refs.items():
        (git / name).write_text(sha + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return git


def test_head_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Like a dotfiles repo in the home above the temp folder: without the ceiling at the test's
    # own folder, "nowhere" below would find that checkout (review finding: machine-dependent).
    home = tmp_path / "home"
    git_dir(home, "c" * 40, {})
    tmp_path = home / "tmp"
    tmp_path.mkdir()
    monkeypatch.setenv(CEILING_ENV, str(tmp_path))
    loose = tmp_path / "loose"
    git_dir(loose, "ref: refs/heads/main", {"refs/heads/main": COMMIT})
    (loose / "src" / "pkg").mkdir(parents=True)
    assert head_from_files(loose / "src" / "pkg") == COMMIT
    # Without a runner the commit is known, cleanliness is not.
    assert code_state(repo=loose / "src") == CodeState(COMMIT, False)

    packed = tmp_path / "packed"
    git_dir(packed, "ref: refs/heads/main", {}, f"# pack-refs\n{OTHER} refs/heads/main\n")
    assert head_from_files(packed) == OTHER

    detached = tmp_path / "detached"
    git_dir(detached, COMMIT, {})
    assert head_from_files(detached) == COMMIT

    # A linked worktree: .git is a file, the branch ref lives in the common git dir.
    main = git_dir(tmp_path / "main", "ref: refs/heads/main", {"refs/heads/wt": OTHER})
    wt_git = main / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/wt\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    assert head_from_files(tmp_path / "wt") == OTHER

    broken = tmp_path / "broken"
    git_dir(broken, "ref: refs/heads/gone", {})
    assert head_from_files(broken) == UNKNOWN_COMMIT
    assert head_from_files(tmp_path / "nowhere") == UNKNOWN_COMMIT
    monkeypatch.delenv(CEILING_ENV)
    assert head_from_files(tmp_path / "nowhere") == "c" * 40  # the ceiling made it unknown


def test_head_from_files_matches_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CEILING_ENV, raising=False)
    repo = Path(__file__).resolve().parent
    try:
        expected = real_git(repo)(["rev-parse", "HEAD"]).strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git checkout or no git")
    assert head_from_files(repo) == expected


def test_commit_and_clean_flag_recorded(tmp_path: Path) -> None:
    with open_reg(tmp_path, commit=OTHER, clean=False) as reg:
        reg.record(result())
        (run,) = reg.list_runs()
        assert (run.commit, run.clean) == (OTHER, False)
