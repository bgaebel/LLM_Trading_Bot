"""Spec 008 AK10, Spec 009 AK4: the systemd files in deploy/ - options, placeholders, no real data.

`scripts/check-units.sh` lets systemd itself verify them in the Linux CI; these tests pin what the
service relies on (ADR-0017) and that the public repository holds no server details.
"""

import configparser
import re
from pathlib import Path

import pytest

from llm_trading_bot.ops.backup import KEEP
from llm_trading_bot.service.run import EXIT_LOCKED, PING_SECONDS, STALL_SECONDS, STOP_SECONDS

DEPLOY = Path(__file__).parent.parent / "deploy"
SERVICE = DEPLOY / "llm-trading-bot.service"
WEB = DEPLOY / "llm-trading-bot-web.service"
JOURNAL = DEPLOY / "journald@llm-trading-bot.conf"
BACKUP = DEPLOY / "llm-trading-bot-backup.service"
TIMER = DEPLOY / "llm-trading-bot-backup.timer"
PLACEHOLDERS = {"__SERVICE_USER__", "__APP_DIR__", "__DATA_DIR__", "__BACKUP_DIR__"}


def options(path: Path) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text(encoding="utf-8"))
    return {section: dict(parser[section]) for section in parser.sections()}


def test_service_unit_matches_the_service_loop() -> None:
    s = options(SERVICE)["Service"]
    assert s["Type"] == "notify" and s["NotifyAccess"] == "main"
    assert s["ExecStart"] == "__APP_DIR__/.venv/bin/trading-bot run --db __DATA_DIR__/bot.db"
    assert s["Restart"] == "on-failure"
    # The watchdog sees a ping at least twice per interval; the stop deadline fits the timeout.
    assert int(s["WatchdogSec"]) >= 2 * PING_SECONDS
    assert int(s["TimeoutStopSec"]) > STOP_SECONDS
    assert int(s["WatchdogSec"]) < STALL_SECONDS
    # ADR-0018: another writer holding the database is never fought by restarting.
    assert s["RestartPreventExitStatus"] == str(EXIT_LOCKED)


def test_web_unit_stays_on_localhost() -> None:
    s = options(WEB)["Service"]
    assert "--host 127.0.0.1" in s["ExecStart"] and "--data-dir __DATA_DIR__" in s["ExecStart"]
    assert s["User"] == options(SERVICE)["Service"]["User"]


@pytest.mark.parametrize("path", [SERVICE, WEB], ids=["service", "web"])
def test_units_are_hardened_and_write_only_the_data_directory(path: Path) -> None:
    s = options(path)["Service"]
    assert s["User"] == "__SERVICE_USER__"
    assert s["ProtectSystem"] == "strict" and s["ReadWritePaths"] == "__DATA_DIR__"
    assert s["NoNewPrivileges"] == "yes" and s["PrivateTmp"] == "yes"
    assert s["UMask"] == "0077" and s["CapabilityBoundingSet"] == ""
    assert s["LogNamespace"] == "llm-trading-bot"
    assert options(path)["Install"]["WantedBy"] == "multi-user.target"


def test_backup_unit_copies_read_only_into_the_backup_directory() -> None:
    s = options(BACKUP)["Service"]
    assert s["Type"] == "oneshot" and s["User"] == "__SERVICE_USER__"
    assert s["ExecStart"] == (
        "__APP_DIR__/.venv/bin/trading-bot backup --db __DATA_DIR__/bot.db"
        f" --dir __BACKUP_DIR__/daily --keep {KEEP}"
    )
    assert s["ReadWritePaths"] == "__DATA_DIR__ __BACKUP_DIR__"
    assert s["ProtectSystem"] == "strict" and s["NoNewPrivileges"] == "yes"
    assert s["UMask"] == "0077" and s["CapabilityBoundingSet"] == ""
    assert s["RestrictAddressFamilies"] == "AF_UNIX"  # a backup never needs the network
    assert "Install" not in options(BACKUP)  # started by the timer only


def test_backup_timer_runs_daily_before_the_reboot_window() -> None:
    t = options(TIMER)["Timer"]
    assert t["OnCalendar"] == "*-*-* 03:30:00 UTC" and t["Persistent"] == "true"
    assert t["Unit"] == BACKUP.name and int(t["RandomizedDelaySec"]) <= 600
    assert options(TIMER)["Install"]["WantedBy"] == "timers.target"


def test_journal_is_bounded() -> None:
    j = options(JOURNAL)["Journal"]
    assert j["SystemMaxUse"] == "200M" and j["MaxRetentionSec"] == "14day"


@pytest.mark.parametrize(
    "path",
    [SERVICE, WEB, JOURNAL, BACKUP, TIMER],
    ids=["service", "web", "journal", "backup", "timer"],
)
def test_no_server_details_only_placeholders(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert set(re.findall(r"__[A-Z_]+__", text)) <= PLACEHOLDERS
    # CLAUDE.md: the repository is public - no IPs (but 127.0.0.1), homes or real users.
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {"127.0.0.1"}
    assert "/home/" not in text and "/root" not in text
