"""Raw model answers and cadence situations shared by the LLM golden generator and the tests.

Standard library only: tools/golden/generate_llm_golden.py imports this next to the Arena code.
Every rule of the answer check and every cadence threshold gets cases just below, on and above it.
"""

import itertools
import json
from typing import Any

T0 = 1_793_000_000


def _answer(**fields: Any) -> str:
    base: dict[str, Any] = {
        "action": "BUY",
        "target_fraction": 0.25,
        "confidence": 0.6,
        "reason": "Trend intakt",
    }
    base.update(fields)
    return json.dumps({k: v for k, v in base.items() if v is not ...}, ensure_ascii=False)


def answer_texts() -> list[str]:
    texts: list[str] = []
    for action, target in itertools.product(("BUY", "SELL", "HOLD"), (0, 0.0, 0.25, 0.45)):
        texts.append(_answer(action=action, target_fraction=target))
    confidence: Any
    for confidence in (0, 1, 0.0, 1.0, -0.0001, 1.0001, 0.5, True, "0.5", None):
        texts.append(_answer(confidence=confidence))
    target_value: Any
    for target_value in (0.3, 0.250001, 1, -0.25, "0.25", True, False, None):
        texts.append(_answer(target_fraction=target_value))
    action_value: Any
    for action_value in ("buy", "SHORT", "", None, 1):
        texts.append(_answer(action=action_value))
    reason: Any
    for reason in ("", "   ", "  gepolstert  ", "ä" * 600, "x" * 549, None, 5):
        texts.append(_answer(reason=reason))
    texts += [
        _answer(reason=...),
        _answer(confidence=...),
        _answer(extra="feld"),
        '{"action":"HOLD","target_fraction":0.45,"confidence":NaN,"reason":"r"}',
        '{"action":"HOLD","target_fraction":0.45,"confidence":Infinity,"reason":"r"}',
        '["BUY",0.25,0.6,"r"]',
        '"BUY"',
        "kein JSON",
        "",
        '{"action":"BUY","target_fraction":0.25,"confidence":0.6,"reason":"r"} trailing',
        '{"action":"BUY","action":"SELL","target_fraction":0,"confidence":0.6,"reason":"r"}',
        '{"action":"SELL","target_fraction":0,"confidence":1,"reason":"Abwärts"}',
    ]
    return texts


def due_cases() -> list[dict[str, Any]]:
    """Each case: environment, previous attempts, current mid and time -> due?"""
    envs: list[dict[str, str]] = [
        {},
        {
            "LLM_MIN_INTERVAL_SECONDS": "100",
            "LLM_EVENT_COOLDOWN_SECONDS": "10",
            "LLM_EVENT_MOVE_THRESHOLD": "0.001",
        },
        {
            "LLM_MIN_INTERVAL_SECONDS": "3600",
            "LLM_EVENT_COOLDOWN_SECONDS": "900",
            "LLM_EVENT_MOVE_THRESHOLD": "0.03",
        },
    ]
    elapsed = (
        -5,
        0,
        299,
        300,
        301,
        599,
        600,
        601,
        899,
        900,
        901,
        1799,
        1800,
        1801,
        3599,
        3600,
        3601,
    )
    moves = (
        0.0,
        0.0049,
        0.005,
        0.0051,
        0.0179,
        0.018,
        0.0181,
        0.0299,
        0.03,
        0.0301,
        -0.018,
        -0.0181,
    )
    cases: list[dict[str, Any]] = []
    for env in envs:
        cases.append({"env": env, "rows": [], "mid": 100.0, "now": T0})
        for e, m, ref in itertools.product(elapsed, moves, (100.0, 0.0)):
            rows = [
                {"agent": "gpt", "symbol": "BTCEUR", "ref_mid": 50.0, "at": T0 - 7200},
                {"agent": "gpt", "symbol": "BTCEUR", "ref_mid": ref, "at": T0 - e},
                # Other market and other agent must not influence BTCEUR / gpt.
                {"agent": "gpt", "symbol": "ETHEUR", "ref_mid": 1.0, "at": T0 - 10_000},
                {"agent": "trend", "symbol": "BTCEUR", "ref_mid": 1.0, "at": T0 - 10_000},
            ]
            mid = (ref or 100.0) * (1 + m)
            cases.append({"env": env, "rows": rows, "mid": mid, "now": T0 + 0.5 * (e % 2)})
    return cases
