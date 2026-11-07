"""Spec 012 AK2: the PaperBroker hands every argument to paper.execution unchanged."""

from typing import Any

import pytest

from llm_trading_bot.broker import PaperBroker
from llm_trading_bot.core import TradingMode
from llm_trading_bot.paper import execution


class Spy:
    def __init__(self, result: object) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.result = result

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append((args, kwargs))
        return self.result


# Distinct sentinels: a mixed-up or dropped argument cannot go unnoticed.
CON: Any = object()
SYM: Any = object()
SIG: Any = object()
DATA: Any = object()
COSTS: Any = object()
QS: Any = object()
STOPS: Any = object()


def test_record_decision_passes_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(True)
    monkeypatch.setattr(execution, "record_decision", spy)
    out = PaperBroker().record_decision(
        CON,
        "trend",
        SYM,
        3600,
        "TREND",
        SIG,
        DATA,
        costs=COSTS,
        now="2026-11-07T08:00:00+00:00",
        qs=QS,
        status="PAUSED",
        stops=STOPS,
        partial=True,
    )
    assert out is True
    assert spy.calls == [
        (
            (CON, "trend", SYM, 3600, "TREND", SIG, DATA),
            {
                "costs": COSTS,
                "now": "2026-11-07T08:00:00+00:00",
                "qs": QS,
                "status": "PAUSED",
                "stops": STOPS,
                "partial": True,
            },
        )
    ]


def test_record_decision_defaults_are_the_executions(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(False)
    monkeypatch.setattr(execution, "record_decision", spy)
    PaperBroker().record_decision(
        CON,
        "trend",
        SYM,
        1,
        "M",
        SIG,
        DATA,
        costs=COSTS,
        now="t",
        qs=None,
    )
    assert spy.calls[0][1] == {
        "costs": COSTS,
        "now": "t",
        "qs": None,
        "status": "OK",
        "stops": None,
        "partial": False,
    }


def test_mark_and_buyhold_pass_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    stop: Any = object()
    mark, buyhold = Spy(stop), Spy(2)
    monkeypatch.setattr(execution, "mark", mark)
    monkeypatch.setattr(execution, "initialize_buyhold", buyhold)
    broker = PaperBroker()
    assert broker.mark(CON, "trend", QS, "now", COSTS) is stop
    assert broker.initialize_buyhold(CON, QS, COSTS, when="when") == 2
    assert mark.calls == [((CON, "trend", QS, "now", COSTS), {})]
    assert buyhold.calls == [((CON, QS, COSTS), {"when": "when"})]


def test_paper_has_nothing_open_and_nothing_to_send() -> None:
    broker = PaperBroker()
    assert broker.mode is TradingMode.PAPER
    assert broker.unresolved(CON) == ()
    broker.dispatch(CON)  # returns None: nothing to send in paper mode
