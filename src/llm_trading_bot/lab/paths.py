"""Where the lab keeps its data (ADR-0026): outside the repository and outside Dropbox."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

LAB_DIR_ENV = "LTB_LAB_DIR"
"""Overrides the default folder, e.g. for tests or a second disk."""
DIR_NAME = "llm-trading-bot-lab"
CANDLES_FILE = "candles.db"
RUNS_FILE = "runs.db"
CHAIN_FILE = "register.jsonl"
REPORTS_DIR = "reports"


def lab_dir(env: Mapping[str, str] | None = None, *, platform: str | None = None) -> Path:
    """``%LOCALAPPDATA%\\llm-trading-bot-lab`` on Windows, ``$XDG_DATA_HOME`` or
    ``~/.local/share`` elsewhere; ``LTB_LAB_DIR`` wins. Never inside the repository."""
    env = dict(os.environ) if env is None else env  # a copy: the guard keeps os.environ read-only
    if override := env.get(LAB_DIR_ENV):
        return Path(override)
    if (platform or sys.platform).startswith("win"):
        base = env.get("LOCALAPPDATA")
        if base:
            return Path(base) / DIR_NAME
        return Path.home() / "AppData" / "Local" / DIR_NAME
    data = env.get("XDG_DATA_HOME")
    return (Path(data) if data else Path.home() / ".local" / "share") / DIR_NAME
