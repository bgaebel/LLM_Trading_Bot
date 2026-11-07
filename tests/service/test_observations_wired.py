"""Spec 015 AK3/AK5 (ADR-0027): recording never changes or blocks trading.

Every scenario runs with no recorder, with a recorder whose every call raises, and with a normal
one; decisions, trades, equity, GPT calls and round reports must be identical. Then the service
runs against an observations.db that is busy, of another version or corrupt.
"""

import io
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel, entry, valid_reply
from llm_trading_bot.cli import run_paper_cycle
from llm_trading_bot.llm.agents import load_config
from llm_trading_bot.llm.openai_client import ModelReply
from llm_trading_bot.paper import observations as obs_module
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.paper.observations import ObservationBuffer
from llm_trading_bot.paper.observations_schema import OBS_FILE, OBS_TABLES, create
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.paper.risk import Controls
from llm_trading_bot.service import run as run_module
from llm_trading_bot.service.run import EXIT_OK
from llm_trading_bot.service.status import ServiceStatus, read_status, status_path
from service_fakes import Harness, MovingClient, harness

BOT_TABLES = ("portfolios", "decisions", "trades", "market", "equity", "llm_calls")
# Measured with the real monotonic clock, so it differs between any two runs.
VOLATILE = {"latency_seconds"}
AGENTS = "trend,breakout,meanrev,ensemble,gpt-luna,gpt-sol"


class RaisingBuffer(ObservationBuffer):
    """Every attribute access raises - the worst recorder a round can be given."""

    def __getattribute__(self, name: str) -> Any:
        raise RuntimeError(f"recorder broken ({name})")


def dump(db: Path) -> dict[str, list[tuple[Any, ...]]]:
    con = sqlite3.connect(db)
    try:
        result = {}
        for table in BOT_TABLES:
            cursor = con.execute(f"SELECT * FROM {table} ORDER BY rowid")
            keep = [i for i, d in enumerate(cursor.description) if d[0] not in VOLATILE]
            result[table] = [tuple(row[i] for i in keep) for row in cursor.fetchall()]
        return result
    finally:
        con.close()


def scripted_model() -> FakeModel:
    """BUY, then SELL BTC with a HOLD on ETH, then an invalid answer, then BUY again."""
    calls = {"n": 0}

    def answer(markets: list[str]) -> ModelReply:
        calls["n"] += 1
        step = calls["n"] % 4
        if step == 3:
            reply = valid_reply(markets)
            return ModelReply("completed", "{broken", None, None, "r", None, reply.usage)
        if step == 2:
            return valid_reply(markets, BTC_EUR=entry("SELL", 0), ETH_EUR=entry("HOLD", 0))
        return valid_reply(markets, BTC_EUR=entry("BUY", 0.45), ETH_EUR=entry("BUY", 0.25))

    return FakeModel({"gpt-6-luna": answer, "gpt-6.1-sol": answer})


# Round times: a cooldown, an event window, the hourly interval, a later bar.
OFFSETS = (0, 60, 660, 3600, 3660, 7260, 10_860)
SCENARIOS = (
    ("up-calm-plain-s1", "down-wild-dump-s1"),
    ("down-calm-spike-s1", "up-wild-dip-s1"),
    ("flat-wild-plain-s1", "mild_up-calm-dump-s1"),
)


def fixed(at: float) -> Callable[[], float]:
    return lambda: at


def play(
    tmp: Path, scenario: tuple[str, str], buffer: Callable[[], ObservationBuffer] | None
) -> tuple[dict[str, list[tuple[Any, ...]]], list[CycleReport], list[ObservationBuffer]]:
    tmp.mkdir()
    config = load_config({"OPENAI_API_KEY": KEY, "BOT_AGENTS": AGENTS})
    con = open_db(tmp / "bot.db", agents=(*config.agents, BUYHOLD))
    client = MovingClient(*scenario)
    model = scripted_model()
    reports, buffers = [], []
    try:
        for offset in OFFSETS:
            observations = buffer() if buffer else None
            reports.append(
                paper_cycle(
                    con,
                    client,
                    config,
                    costs=Costs(),
                    model=model,
                    clock=fixed(NOW + offset),
                    controls=Controls(),
                    observations=observations,
                )
            )
            if observations is not None:
                buffers.append(observations)
    finally:
        con.close()
    return dump(tmp / "bot.db"), reports, buffers


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_golden_rounds_same_with_raising_recorder(
    tmp_path: Path, scenario: tuple[str, str]
) -> None:
    plain = play(tmp_path / "none", scenario, None)
    broken = play(tmp_path / "raising", scenario, RaisingBuffer)
    recorded = play(tmp_path / "normal", scenario, lambda: ObservationBuffer("f" * 40))
    assert broken[:2] == plain[:2]
    assert recorded[:2] == plain[:2]
    # The scenarios really trade, ask GPT and record, so the equality means something.
    assert plain[0]["trades"] and plain[0]["llm_calls"] and plain[0]["equity"]
    assert any(b.decisions for b in recorded[2]) and any(b.attempts for b in recorded[2])
    assert all(b.dropped == 0 for b in recorded[2])


# ------------------------------------------------------------------ the service


GPT_SECRETS = f"BOT_AGENTS=trend,breakout,gpt-luna\nOPENAI_API_KEY={KEY}\n"


def serve(tmp: Path, prepare: Callable[[Path], None] | None = None) -> tuple[Harness, int]:
    tmp.mkdir()
    if prepare is not None:
        prepare(tmp / OBS_FILE)
    h = harness(tmp, limit=3, secrets_text=GPT_SECRETS, model=scripted_model())
    return h, h.service.run()


def outcome(h: Harness) -> tuple[Any, ...]:
    status = read_status(status_path(h.db))
    assert status is not None
    return (status.state, status.last_result, status.last_error, status.failures)


def obs_status(h: Harness) -> ServiceStatus:
    status = read_status(status_path(h.db))
    assert status is not None
    return status


def obs_counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in OBS_TABLES}
    finally:
        con.close()


@pytest.fixture
def reference(tmp_path: Path) -> tuple[Harness, int]:
    return serve(tmp_path / "reference")


def test_service_records_each_round_after_a_start_marker(
    reference: tuple[Harness, int],
) -> None:
    h, code = reference
    assert code == EXIT_OK
    counts = obs_counts(h.db.parent / OBS_FILE)
    assert counts["service_starts"] == 1 and counts["rounds"] == 3
    assert counts["books"] > 0 and counts["windows"] == 3 * 8 and counts["snapshots"] == 6
    assert counts["decision_meta"] > 0 and counts["llm_attempts"] > 0
    status = obs_status(h)
    assert (status.observations_written, status.observations_failed) == (4, 0)
    assert status.observations_last_error is None
    assert "observations not recorded" not in h.log()


def test_service_rounds_same_with_raising_recorder(
    tmp_path: Path, reference: tuple[Harness, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "ObservationBuffer", RaisingBuffer)
    h, code = serve(tmp_path / "raising")
    ref, ref_code = reference
    assert code == ref_code == EXIT_OK
    assert dump(h.db) == dump(ref.db)
    assert outcome(h) == outcome(ref)
    # Only the start marker was written; the rounds' buffers could not even say they were empty.
    status = obs_status(h)
    assert status.observations_written == 1 and status.observations_failed == 3


def test_busy_lock_keeps_trading(tmp_path: Path, reference: tuple[Harness, int]) -> None:
    path = tmp_path / "busy" / OBS_FILE
    blockers: list[sqlite3.Connection] = []

    def lock(target: Path) -> None:
        create(target)
        blocker = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
        blocker.execute("BEGIN IMMEDIATE")
        blockers.append(blocker)

    try:
        h, code = serve(tmp_path / "busy", lock)
    finally:
        for blocker in blockers:
            blocker.execute("ROLLBACK")
            blocker.close()
    ref, ref_code = reference
    assert code == ref_code == EXIT_OK
    assert dump(h.db) == dump(ref.db)
    assert outcome(h) == outcome(ref)
    status = obs_status(h)
    assert (status.observations_written, status.observations_failed) == (0, 4)
    assert status.observations_last_error is not None
    assert "locked" in status.observations_last_error
    assert h.log().count("observations not recorded") == 1  # said once, not every round
    # Busy is not damage: the file stays as it was and recording was not switched off
    # (writing again once free: tests/paper/test_observations.py).
    assert obs_counts(path)["rounds"] == 0
    assert not h.service.observer.off


def test_v99_file_keeps_trading(tmp_path: Path, reference: tuple[Harness, int]) -> None:
    def v99(target: Path) -> None:
        create(target)
        con = sqlite3.connect(target)
        con.execute("PRAGMA user_version=99")
        con.close()

    h, code = serve(tmp_path / "v99", v99)
    ref, ref_code = reference
    assert code == ref_code == EXIT_OK
    assert dump(h.db) == dump(ref.db)
    assert outcome(h) == outcome(ref)
    status = obs_status(h)
    assert (status.observations_written, status.observations_failed) == (0, 4)
    assert status.observations_last_error is not None
    assert status.observations_last_error.startswith("recording off: SchemaError")
    con = sqlite3.connect(h.db.parent / OBS_FILE)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 99
        assert con.execute("SELECT COUNT(*) FROM service_starts").fetchone()[0] == 0
    finally:
        con.close()


def test_corrupt_file_not_recreated(tmp_path: Path, reference: tuple[Harness, int]) -> None:
    garbage = b"\x00 not a database \xff" * 512

    def corrupt(target: Path) -> None:
        target.write_bytes(garbage)

    h, code = serve(tmp_path / "corrupt", corrupt)
    ref, ref_code = reference
    assert code == ref_code == EXIT_OK
    assert dump(h.db) == dump(ref.db)
    assert outcome(h) == outcome(ref)
    assert (h.db.parent / OBS_FILE).read_bytes() == garbage
    status = obs_status(h)
    assert status.observations_written == 0 and status.observations_failed == 4
    assert status.observations_last_error is not None
    assert "recording off: DatabaseError" in status.observations_last_error


def test_paper_cycle_command_writes_no_observations(tmp_path: Path) -> None:
    """ADR-0027: only the service (the WriterLock holder for its whole run) records."""
    db = tmp_path / "bot.db"
    code = run_paper_cycle(
        db, ScenarioClient(), env={"BOT_AGENTS": "trend"}, out=io.StringIO(), clock=lambda: NOW
    )
    assert code == EXIT_OK and db.exists()
    assert not (tmp_path / OBS_FILE).exists()


def test_status_file_of_the_service_has_the_observation_fields(
    reference: tuple[Harness, int],
) -> None:
    h, _code = reference
    data = json.loads(status_path(h.db).read_text(encoding="utf-8"))
    assert {"observations_written", "observations_failed", "observations_last_error"} <= set(data)
    assert obs_module.BUSY_TIMEOUT_MS <= 1000
