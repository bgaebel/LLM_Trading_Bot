"""Spec 008 AK9: log lines on stdout, level from LOG_LEVEL, keys never readable."""

import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from llm_fakes import KEY
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.logs import redact, setup_logging
from service_fakes import harness


def test_redact_hides_every_key_shape() -> None:
    text = f"bad {KEY} and sk-proj-AbC_123-xyz end"
    assert redact(text) == "bad sk-*** and sk-*** end"
    assert redact(f"key={KEY}x") == "key=sk-***"
    # A key glued to a word is caught as well; a mangled word is the accepted price.
    assert redact(f"token{KEY}") == "tokensk-***"
    assert redact("short sk-ab stays") == "short sk-ab stays"


def test_keys_in_messages_arguments_and_tracebacks_are_redacted() -> None:
    out = io.StringIO()
    log = setup_logging("INFO", out, journal=True)
    log.info("key %s", KEY)
    try:
        raise RuntimeError(f"auth failed for {KEY}")
    except RuntimeError:
        log.exception("boom")
    assert KEY not in out.getvalue() and out.getvalue().count("sk-***") == 2


def test_a_failing_round_with_a_key_in_its_message_logs_no_key(tmp_path: Path) -> None:
    def leaky(con: sqlite3.Connection, client: Any, config: Any, **kw: Any) -> CycleReport:
        raise PermissionError(f"Incorrect API key provided: {KEY}")

    h = harness(tmp_path, limit=1, action=leaky)
    h.service.run()
    assert "PermissionError: Incorrect API key provided: sk-***" in h.log()
    assert KEY not in h.log()
    assert KEY not in (tmp_path / "service-status.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(("name", "shown"), [("INFO", True), ("warning", False), ("DEBUG", True)])
def test_level_comes_from_log_level(name: str, shown: bool) -> None:
    out = io.StringIO()
    setup_logging(name, out, journal=True).info("hello")
    assert ("INFO hello" in out.getvalue()) is shown


@pytest.mark.parametrize("name", ["LOUD", "", "NOTSET"])
def test_unknown_level_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        setup_logging(name, io.StringIO(), journal=True)


def test_local_lines_carry_a_utc_time_journal_lines_do_not() -> None:
    local, journal = io.StringIO(), io.StringIO()
    setup_logging("INFO", local, journal=False).info("x")
    setup_logging("INFO", journal, journal=True).info("x")
    assert journal.getvalue() == "INFO x\n"
    assert local.getvalue()[4] == "-" and local.getvalue().endswith(" INFO x\n")
