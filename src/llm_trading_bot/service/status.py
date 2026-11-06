"""``service-status.json`` next to the database: what the service does, for the overview page.

Written atomically with mode 600 (ADR-0017); it holds states, times and error types - never a key
or another secret (texts pass through ``redact``).
"""

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Self

from llm_trading_bot.service.logs import redact
from llm_trading_bot.web.files import write_private

FILE_NAME = "service-status.json"
STATES = ("starting", "running", "degraded", "stalled", "stopping", "stopped")
RESULTS = ("ok", "incomplete", "no_quotes", "error")


def status_path(db: Path) -> Path:
    return db.parent / FILE_NAME


@dataclass(frozen=True)
class ServiceStatus:
    state: str
    pid: int
    started_at: str
    updated_at: str
    period_seconds: int
    offset_seconds: int
    last_round_at: str | None = None
    last_round_seconds: float | None = None
    last_result: str | None = None
    last_error: str = ""
    failures: int = 0
    """Rounds in a row that were not ``ok``."""
    next_round_at: str | None = None
    config_warning: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown service state {self.state!r}")
        if self.last_result is not None and self.last_result not in RESULTS:
            raise ValueError(f"unknown round result {self.last_result!r}")

    def but(self, **changes: Any) -> Self:
        return replace(self, **changes)

    def to_json(self) -> str:
        data = asdict(self)
        data["last_error"] = redact(self.last_error)
        data["config_warning"] = redact(self.config_warning)
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Raises ValueError for anything that is not a status written by this version."""
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError("not a service status file of this version")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError(str(exc)) from None


def write_status(path: Path, status: ServiceStatus) -> None:
    write_private(path, status.to_json())


def read_status(path: Path) -> ServiceStatus | None:
    """None: no service has run next to this database yet. Raises ValueError/OSError otherwise."""
    if not path.exists():
        return None
    return ServiceStatus.from_json(path.read_text(encoding="utf-8"))
