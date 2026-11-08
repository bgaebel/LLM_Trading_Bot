"""Spec 006 AK5: the credentials file - validated like the bot, masked, never broken."""

import os
import sys
from pathlib import Path

import pytest

from llm_trading_bot.web.credentials import SecretsFile, load_env_file, mask

KEY = "sk-test-not-a-real-key-0000abcd"
OTHER = "sk-test-another-fake-key-9999wxyz"


@pytest.fixture
def secrets_file(tmp_path: Path) -> SecretsFile:
    return SecretsFile(tmp_path / "data" / "secrets.env")


def test_key_and_settings_are_stored(secrets_file: SecretsFile) -> None:
    values = secrets_file.update(
        {"OPENAI_API_KEY": f"  {KEY}  ", "LLM_EFFORT": "medium", "LLM_DAILY_BUDGET_EUR": "2"},
        agents=["gpt-sol", "gpt-luna"],
    )
    assert values == {
        "OPENAI_API_KEY": KEY,
        "BOT_AGENTS": "gpt-luna,gpt-sol",
        "LLM_EFFORT": "medium",
        "LLM_DAILY_BUDGET_EUR": "2",
    }
    assert load_env_file(secrets_file.path) == values
    assert secrets_file.path.read_text(encoding="utf-8").startswith("# llm-trading-bot")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_private(secrets_file: SecretsFile) -> None:
    secrets_file.update({"OPENAI_API_KEY": KEY})
    assert os.stat(secrets_file.path).st_mode & 0o777 == 0o600


def test_empty_key_field_keeps_the_stored_key(secrets_file: SecretsFile) -> None:
    secrets_file.update({"OPENAI_API_KEY": KEY})
    secrets_file.update({"OPENAI_API_KEY": "", "LLM_EFFORT": "medium"})
    assert secrets_file.read()["OPENAI_API_KEY"] == KEY
    secrets_file.update({"OPENAI_API_KEY": OTHER})
    assert secrets_file.read()["OPENAI_API_KEY"] == OTHER


def test_clearing_the_key_needs_classic_depots(secrets_file: SecretsFile) -> None:
    secrets_file.update({"OPENAI_API_KEY": KEY})
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        secrets_file.update({}, clear_key=True)
    assert secrets_file.read()["OPENAI_API_KEY"] == KEY  # nothing written on error
    secrets_file.update({}, agents=["trend"], clear_key=True)
    assert "OPENAI_API_KEY" not in secrets_file.read()


def test_empty_setting_falls_back_to_the_default(secrets_file: SecretsFile) -> None:
    secrets_file.update({"OPENAI_API_KEY": KEY, "LLM_EFFORT": "medium"})
    secrets_file.update({"LLM_EFFORT": " "})
    assert "LLM_EFFORT" not in secrets_file.read()


@pytest.mark.parametrize(
    ("form", "agents", "message"),
    [
        ({"OPENAI_API_KEY": "kein-key"}, None, "does not look like"),
        ({"OPENAI_API_KEY": "sk-short"}, None, "does not look like"),
        ({"OPENAI_API_KEY": KEY + "\nBOT_AGENTS=trend"}, None, "line breaks"),
        ({"OPENAI_API_KEY": KEY, "LLM_EFFORT": "turbo"}, None, "LLM_EFFORT"),
        ({"OPENAI_API_KEY": KEY, "LLM_DAILY_BUDGET_EUR": "-1"}, None, "LLM_DAILY_BUDGET_EUR"),
        ({"OPENAI_API_KEY": KEY}, ["gpt-terra"], "unknown depots"),
        ({"OPENAI_API_KEY": KEY}, [], "at least one"),
        ({}, None, "OPENAI_API_KEY is required"),
    ],
    ids=["no-sk", "too-short", "newline", "effort", "budget", "depot", "no-depot", "no-key"],
)
def test_invalid_input_writes_nothing(
    secrets_file: SecretsFile, form: dict[str, str], agents: list[str] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        secrets_file.update(form, agents=agents)
    assert not secrets_file.path.exists()


def test_unknown_lines_in_the_file_are_ignored(secrets_file: SecretsFile) -> None:
    secrets_file.path.parent.mkdir(parents=True)
    secrets_file.path.write_text(f"# c\nFOO=bar\nOPENAI_API_KEY={KEY}\nbroken\n", encoding="utf-8")
    assert secrets_file.read() == {"OPENAI_API_KEY": KEY}


@pytest.mark.parametrize(
    ("value", "masked"), [(KEY, "…abcd"), ("", ""), (None, ""), ("short", "…")]
)
def test_mask_shows_only_the_last_four(value: str | None, masked: str) -> None:
    assert mask(value) == masked


@pytest.mark.parametrize(
    "separator", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\t"]
)
def test_no_line_break_of_any_kind_can_smuggle_a_second_line(
    secrets_file: SecretsFile, separator: str
) -> None:
    injected = f"low{separator}OPENAI_API_KEY=sk-injectedinjectedinjected"
    with pytest.raises(ValueError, match="line breaks"):
        secrets_file.update({"LLM_EFFORT": injected}, agents=["trend"])
    assert not secrets_file.path.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("LLM_EFFORT", "turbo"),
        ("LLM_DAILY_BUDGET_EUR", "-1"),
        ("LLM_DAILY_BUDGET_EUR_GPT_LUNA", "x"),
    ],
)
def test_gpt_settings_are_checked_even_with_classic_depots_only(
    secrets_file: SecretsFile, field: str, value: str
) -> None:
    with pytest.raises(ValueError, match=field):
        secrets_file.update({field: value}, agents=["trend"])
    assert not secrets_file.path.exists()


# ------------------------------------------------------------------ Spec 011: pause and release


def test_pause_switch_writes_and_removes_the_field(secrets_file: SecretsFile) -> None:
    secrets_file.update({"OPENAI_API_KEY": KEY})
    secrets_file.set_paused(True)
    assert secrets_file.read() == {"OPENAI_API_KEY": KEY, "BOT_PAUSED": "1"}
    secrets_file.update({"LLM_EFFORT": "medium"})  # saving settings keeps the pause
    assert secrets_file.read()["BOT_PAUSED"] == "1"
    secrets_file.set_paused(False)
    assert "BOT_PAUSED" not in secrets_file.read()


def test_pause_works_even_while_other_settings_are_broken(secrets_file: SecretsFile) -> None:
    secrets_file.path.parent.mkdir(parents=True)
    secrets_file.path.write_text("LLM_DAILY_BUDGET_EUR=lots\n", encoding="utf-8")
    secrets_file.set_paused(True)
    assert secrets_file.read() == {"LLM_DAILY_BUDGET_EUR": "lots", "BOT_PAUSED": "1"}


def test_public_overview_switch_writes_and_removes_the_field(secrets_file: SecretsFile) -> None:
    # Spec 019 AK6 (ADR-0028): missing = readable without login, "0" = login again.
    secrets_file.update({"OPENAI_API_KEY": KEY})
    secrets_file.set_public_overview(False)
    assert secrets_file.read() == {"OPENAI_API_KEY": KEY, "WEB_PUBLIC_OVERVIEW": "0"}
    secrets_file.update({"LLM_EFFORT": "medium"})  # saving settings keeps the switch
    secrets_file.set_paused(True)
    assert secrets_file.read()["WEB_PUBLIC_OVERVIEW"] == "0"
    secrets_file.set_public_overview(True)
    assert "WEB_PUBLIC_OVERVIEW" not in secrets_file.read()
    assert secrets_file.read()["BOT_PAUSED"] == "1"


def test_resume_request_is_stored_per_depot(secrets_file: SecretsFile) -> None:
    secrets_file.request_resume("trend", "2026-11-07T08:00:00+00:00")
    secrets_file.request_resume("gpt-luna", "2026-11-07T08:05:00+00:00")
    secrets_file.request_resume("trend", "2026-11-07T09:00:00+00:00")
    assert secrets_file.read()["BOT_RESUME"] == (
        "gpt-luna@2026-11-07T08:05:00+00:00,trend@2026-11-07T09:00:00+00:00"
    )


def test_resume_request_refuses_unknown_depots_and_line_breaks(secrets_file: SecretsFile) -> None:
    with pytest.raises(ValueError, match="unknown depot"):
        secrets_file.request_resume("buyhold", "2026-11-07T08:00:00+00:00")
    with pytest.raises(ValueError, match="line breaks"):
        secrets_file.request_resume("trend", "2026-11-07T08:00:00+00:00\nBOT_PAUSED=0")
    assert not secrets_file.path.exists()
