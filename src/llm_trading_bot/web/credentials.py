"""The credentials file (ADR-0015): KEY=VALUE lines, mode 600, outside the repository.

Only known fields are stored. Every change is validated with the same rules the bot uses
(``load_config``) before it is written, so the bot never starts on a broken configuration.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from llm_trading_bot.llm.agents import (
    CLASSIC_AGENTS,
    GPT_USDC,
    LLM_DEFAULTS,
    USDC_AGENTS,
    USDC_FIELD,
    load_config,
)
from llm_trading_bot.llm.budget import amount
from llm_trading_bot.llm.openai_client import EFFORTS
from llm_trading_bot.web.files import write_private

SECRET_FIELDS = ("OPENAI_API_KEY",)
SETTING_FIELDS = (
    "BOT_AGENTS",
    USDC_FIELD,
    "LLM_EFFORT",
    "LLM_DAILY_BUDGET_EUR",
    "LLM_DAILY_BUDGET_EUR_GPT_LUNA",
    "LLM_DAILY_BUDGET_EUR_GPT_SOL",
)
CONTROL_FIELDS = ("BOT_PAUSED", "BOT_RESUME")
"""Trading pause and release requests (Spec 011, ADR-0021); read by the service every round."""
PUBLIC_OVERVIEW_FIELD = "WEB_PUBLIC_OVERVIEW"
"""ADR-0028: ``0`` hides the overview behind the login again; missing means readable without."""
WEB_FIELDS = (PUBLIC_OVERVIEW_FIELD,)
"""Read by the web UI only; the bot ignores them."""
FIELDS = SECRET_FIELDS + SETTING_FIELDS + CONTROL_FIELDS + WEB_FIELDS
AGENT_CHOICES = (*GPT_USDC, *USDC_AGENTS)
"""Spec 022 (ADR-0032): only the USDC depots can be chosen; the euro depots no longer run."""
STOPPABLE = (*LLM_DEFAULTS, *CLASSIC_AGENTS, *AGENT_CHOICES)
"""Every depot the drawdown stop can halt: also a euro depot stopped before Spec 022, whose
release the settings still offer (Spec 011 AK5)."""
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
    # parse() splits with str.splitlines(), which knows more line breaks than \n; any of them
    # (or any other control character) could smuggle a second KEY=VALUE line into the file.
    if len(value.splitlines()) > 1 or not value.isprintable():
        raise ValueError(f"{name}: line breaks and control characters are not allowed")
    return value


def _check_settings(values: Mapping[str, str]) -> None:
    """Also when no GPT depot is selected (load_config then skips them): stored values must be
    valid the moment a GPT depot is switched on."""
    effort = values.get("LLM_EFFORT")
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"LLM_EFFORT must be one of {list(EFFORTS)}: {effort!r}")
    for name in SETTING_FIELDS:
        if name.startswith("LLM_DAILY_BUDGET_EUR") and name in values:
            amount(values[name], name)


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
            chosen = [a for a in AGENT_CHOICES if a in agents]
            if not chosen:
                raise ValueError("choose at least one depot")
            # Spec 022: BOT_AGENTS holds the euro twins of everything chosen - the GPT choice
            # for this release (gpt-luna -> gpt-luna-usdc), and for a release from before 022
            # (rollback) a list it knows and never empty. The bot ignores its classic names.
            twins = {GPT_USDC.get(a, a.removesuffix("-usdc")) for a in chosen}
            values["BOT_AGENTS"] = ",".join(
                a for a in (*LLM_DEFAULTS, *CLASSIC_AGENTS) if a in twins
            )
            # Spec 020: USDC depots in their own field. Stored even when empty: "none" is a
            # choice, a missing field means all of them.
            values[USDC_FIELD] = ",".join(a for a in chosen if a in USDC_AGENTS)
        for name in SETTING_FIELDS:
            if name in ("BOT_AGENTS", USDC_FIELD) or name not in form:
                continue
            value = _clean(name, form[name])
            if value:
                values[name] = value
            else:
                values.pop(name, None)
        _check_settings(values)
        load_config(values)  # same rules as the bot; raises ValueError with the reason
        self._write(values)
        return values

    def _write(self, values: Mapping[str, str]) -> None:
        body = "".join(f"{name}={values[name]}\n" for name in FIELDS if name in values)
        write_private(self.path, _HEADER + body)

    def set_paused(self, paused: bool) -> None:
        """The emergency stop: written without checking the other settings, so it works even
        while they are broken (the service reads it on its own, ADR-0021)."""
        values = self.read()
        if paused:
            values["BOT_PAUSED"] = "1"
        else:
            values.pop("BOT_PAUSED", None)
        self._write(values)

    def set_public_overview(self, readable: bool) -> None:
        """ADR-0028: like the pause, written without checking the other settings."""
        values = self.read()
        if readable:
            values.pop(PUBLIC_OVERVIEW_FIELD, None)
        else:
            values[PUBLIC_OVERVIEW_FIELD] = "0"
        self._write(values)

    def request_resume(self, agent: str, at: str) -> None:
        """Ask the service to release ``agent`` from its drawdown stop (once, see paper.risk)."""
        if agent not in STOPPABLE:
            raise ValueError(f"unknown depot: {agent!r}")
        values = self.read()
        requests = dict(
            item.split("@", 1) for item in values.get("BOT_RESUME", "").split(",") if "@" in item
        )
        requests[agent] = _clean("BOT_RESUME", at)
        values["BOT_RESUME"] = ",".join(f"{a}@{t}" for a, t in sorted(requests.items()))
        self._write(values)
