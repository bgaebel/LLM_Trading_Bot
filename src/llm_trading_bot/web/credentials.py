"""The credentials file (ADR-0015): KEY=VALUE lines, mode 600, outside the repository.

Only known fields are stored. Every change is validated with the same rules the bot uses
(``load_config``) before it is written, so the bot never starts on a broken configuration.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from llm_trading_bot.llm.agents import CLASSIC_AGENTS, LLM_DEFAULTS, load_config
from llm_trading_bot.web.files import write_private

SECRET_FIELDS = ("OPENAI_API_KEY",)
SETTING_FIELDS = (
    "BOT_AGENTS",
    "LLM_EFFORT",
    "LLM_DAILY_BUDGET_EUR",
    "LLM_DAILY_BUDGET_EUR_GPT_LUNA",
    "LLM_DAILY_BUDGET_EUR_GPT_SOL",
)
FIELDS = SECRET_FIELDS + SETTING_FIELDS
AGENT_CHOICES = (*LLM_DEFAULTS, *CLASSIC_AGENTS)
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,200}")
_HEADER = "# llm-trading-bot credentials - written by `trading-bot web`; never commit this file\n"


def mask(value: str | None) -> str:
    """Enough to recognise a key, too little to use it."""
    if not value:
        return ""
    return "…" + value[-4:] if len(value) > 8 else "…"


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in FIELDS:
            values[name.strip()] = value.strip()
    return values


def load_env_file(path: Path) -> dict[str, str]:
    """Missing file = no stored values."""
    return parse(path.read_text(encoding="utf-8")) if path.exists() else {}


def _clean(name: str, value: str) -> str:
    value = value.strip()
    if any(c in value for c in "\r\n\x00"):
        raise ValueError(f"{name}: line breaks are not allowed")
    return value


class SecretsFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, str]:
        return load_env_file(self.path)

    def update(
        self, form: Mapping[str, str], *, agents: list[str] | None = None, clear_key: bool = False
    ) -> dict[str, str]:
        """Apply a settings form. An empty key field keeps the stored key; empty settings fall
        back to the bot's defaults. Raises ValueError and writes nothing if anything is off."""
        values = self.read()
        key = _clean("OPENAI_API_KEY", form.get("OPENAI_API_KEY", ""))
        if clear_key:
            values.pop("OPENAI_API_KEY", None)
        elif key:
            if not _KEY_PATTERN.fullmatch(key):
                raise ValueError("OPENAI_API_KEY does not look like an OpenAI key (sk-...)")
            values["OPENAI_API_KEY"] = key
        if agents is not None:
            unknown = [a for a in agents if a not in AGENT_CHOICES]
            if unknown:
                raise ValueError(f"unknown depots: {unknown}")
            values["BOT_AGENTS"] = ",".join(a for a in AGENT_CHOICES if a in agents)
        for name in SETTING_FIELDS:
            if name == "BOT_AGENTS" or name not in form:
                continue
            value = _clean(name, form[name])
            if value:
                values[name] = value
            else:
                values.pop(name, None)
        load_config(values)  # same rules as the bot; raises ValueError with the reason
        body = "".join(f"{name}={values[name]}\n" for name in FIELDS if name in values)
        write_private(self.path, _HEADER + body)
        return values
