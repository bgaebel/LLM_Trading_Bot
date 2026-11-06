"""Load the LLM-contract golden fixture (Spec 005)."""

import json
from functools import cache
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "llm" / "fixtures" / "llm_golden_v0.3.1.json"


@cache
def llm_golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data
