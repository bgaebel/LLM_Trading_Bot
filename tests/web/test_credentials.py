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
