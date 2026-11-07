"""Spec 008 AK7: the status file - atomic, private, no secrets, strict to read."""

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from llm_fakes import KEY
from llm_trading_bot.service.status import (
    FILE_NAME,
    ServiceStatus,
    read_status,
    status_path,
    write_status,
)

BASE = ServiceStatus(
    "running", 1234, "2027-01-15T08:00:00+00:00", "2027-01-15T08:00:00+00:00", 60, 5
)


def test_status_lives_next_to_the_db(tmp_path: Path) -> None:
    assert status_path(tmp_path / "data" / "bot.db") == tmp_path / "data" / FILE_NAME


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    status = BASE.but(last_result="ok", last_round_seconds=2.5, failures=0)
    write_status(path, status)
    assert read_status(path) == status


def test_missing_file_means_no_service(tmp_path: Path) -> None:
    assert read_status(tmp_path / FILE_NAME) is None


@pytest.mark.parametrize(
    "text",
    ["{", "[]", json.dumps({"state": "running"}), json.dumps({**BASE.__dict__, "state": "zombie"})],
)
def test_foreign_content_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / FILE_NAME
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_status(path)


def test_keys_never_reach_the_file(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    write_status(path, BASE.but(last_error=f"AuthError: bad key {KEY}", config_warning=KEY))
    text = path.read_text(encoding="utf-8")
    assert KEY not in text and "sk-***" in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    write_status(path, BASE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unknown_state_is_refused() -> None:
    with pytest.raises(ValueError):
        BASE.but(state="zombie")


def test_older_status_without_the_pause_fields_is_readable() -> None:
    """Spec 011 AK6: right after an update the file still comes from the previous version."""
    import json

    from llm_trading_bot.service.status import ADDED, ServiceStatus

    new = ServiceStatus(
        "running", 7, "2026-11-07T08:00:00+00:00", "2026-11-07T08:00:00+00:00", 60, 5
    )
    data = json.loads(new.to_json())
    for name in ADDED:
        del data[name]
    old = ServiceStatus.from_json(json.dumps(data))
    assert (old.trading_paused, old.pause_source) == (False, "")
    # Spec 015 AK5: a field of a newer version is ignored (rollback), see test_observation_*.
    assert ServiceStatus.from_json(json.dumps({**data, "surprise": 1})) == old
    del data["state"]
    with pytest.raises(ValueError, match="this version"):
        ServiceStatus.from_json(json.dumps(data))


OBSERVATION_FIELDS = ("observations_written", "observations_failed", "observations_last_error")


def test_observation_fields_are_added_with_defaults() -> None:
    """Spec 015 AK5: a status written before 015 has none of them - zero, zero, no error."""
    from llm_trading_bot.service.status import ADDED

    assert set(OBSERVATION_FIELDS) <= ADDED
    data = json.loads(BASE.to_json())
    for name in OBSERVATION_FIELDS:
        del data[name]
    old = ServiceStatus.from_json(json.dumps(data))
    assert (old.observations_written, old.observations_failed) == (0, 0)
    assert old.observations_last_error is None


def test_observation_fields_round_trip_without_secrets(tmp_path: Path) -> None:
    path = tmp_path / FILE_NAME
    status = BASE.but(
        observations_written=12,
        observations_failed=3,
        observations_last_error=f"OperationalError: database is locked {KEY}",
    )
    write_status(path, status)
    text = path.read_text(encoding="utf-8")
    assert KEY not in text and "sk-***" in text
    back = read_status(path)
    assert back is not None
    assert (back.observations_written, back.observations_failed) == (12, 3)
    assert back.observations_last_error == "OperationalError: database is locked sk-***"


def test_observation_fields_tolerated_by_a_reader_that_does_not_know_them() -> None:
    """A newer file (after a rollback) is read by this version; unknown fields are ignored."""
    data = {**json.loads(BASE.to_json()), "observations_future_field": [1, 2]}
    assert ServiceStatus.from_json(json.dumps(data)) == BASE


def test_observation_fields_pass_the_deploy_health_check(tmp_path: Path) -> None:
    """The deploy's health check (release.sh, system Python, plain json) reads the new file."""
    script = (Path(__file__).parents[2] / "deploy" / "release.sh").read_text(encoding="utf-8")
    check = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / FILE_NAME
    write_status(
        path,
        BASE.but(
            last_round_at="2027-01-15T08:01:05+00:00",
            last_result="ok",
            observations_written=5,
            observations_failed=1,
            observations_last_error="OperationalError: database is locked",
        ),
    )
    since = "1800000000"  # before the round above
    done = subprocess.run(
        [sys.executable, "-c", check, str(path), str(BASE.pid), since], check=False
    )
    assert done.returncode == 0
