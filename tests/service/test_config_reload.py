"""Spec 008 AK3: settings saved in the web UI apply from the next round on, without a restart."""

import sqlite3
from pathlib import Path
from typing import Any

from llm_fakes import KEY
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.report import CycleReport
from llm_trading_bot.service.run import EXIT_OK
from llm_trading_bot.service.status import read_status, status_path
from service_fakes import harness

OTHER_KEY = "sk-test-another-fake-key-1111"


def _run(tmp_path: Path, edits: dict[int, str], *, limit: int, secrets: str, **kw: Any) -> Any:
    """Run ``limit`` rounds; after round n, secrets.env is rewritten with ``edits[n]``."""
    done: list[int] = []

    def cycle(con: sqlite3.Connection, client: Any, config: Any, **kwargs: Any) -> CycleReport:
        done.append(1)
        report = paper_cycle(con, client, config, **kwargs)
        if len(done) in edits:
            (tmp_path / "secrets.env").write_text(edits[len(done)], encoding="utf-8")
        return report

    h = harness(tmp_path, limit=limit, action=cycle, secrets_text=secrets, **kw)
    assert h.secrets == tmp_path / "secrets.env"
    assert h.service.run() == EXIT_OK
    return h


def portfolios(db: Path) -> dict[str, tuple[float, float, float]]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT agent, cash, btc, eth FROM portfolios").fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def test_a_newly_chosen_depot_gets_start_cash_and_trades_next_round(tmp_path: Path) -> None:
    h = _run(tmp_path, {1: "BOT_AGENTS=trend,breakout\n"}, limit=2, secrets="BOT_AGENTS=trend\n")
    assert [c.agents for c in h.rounds.configs] == [("trend",), ("trend", "breakout")]
    assert "depots now trend, breakout (new with start cash: breakout)" in h.log()
    assert portfolios(h.db)["breakout"][0] <= 10_000.0


def test_a_removed_depot_keeps_its_history_and_holdings(tmp_path: Path) -> None:
    h = _run(tmp_path, {1: "BOT_AGENTS=breakout\n"}, limit=2, secrets="BOT_AGENTS=trend\n")
    trend = portfolios(h.db)["trend"]
    assert trend[1] > 0 and trend[2] > 0  # bought in round 1, still held
    con = sqlite3.connect(h.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM trades WHERE agent='trend'").fetchone()[0] == 2
    finally:
        con.close()
    assert [c.agents for c in h.rounds.configs] == [("trend",), ("breakout",)]


def test_invalid_values_keep_the_last_valid_configuration_and_warn_once(tmp_path: Path) -> None:
    h = _run(
        tmp_path,
        {1: "BOT_AGENTS=nonsense\n", 3: "BOT_AGENTS=trend\n"},
        limit=4,
        secrets="BOT_AGENTS=trend\n",
    )
    assert [c.agents for c in h.rounds.configs] == [("trend",)] * 4
    assert h.log().count("configuration invalid, keeping the last valid one") == 1
    assert "configuration valid again" in h.log()


def test_a_configuration_warning_is_shown_in_the_status_file(tmp_path: Path) -> None:
    h = _run(tmp_path, {1: "BOT_AGENTS=nonsense\n"}, limit=2, secrets="BOT_AGENTS=trend\n")
    status = read_status(status_path(h.db))
    assert status is not None and "unknown depots" in status.config_warning


def test_an_invalid_key_message_never_shows_the_key(tmp_path: Path) -> None:
    # A wrong effort with a key present: the warning names the effort, never the key.
    gpt = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={KEY}\n"
    h = _run(tmp_path, {1: gpt + "LLM_EFFORT=extreme\n"}, limit=2, secrets=gpt, start=1_800_000_030)
    assert "LLM_EFFORT" in h.log() and KEY not in h.log()
    status_text = status_path(h.db).read_text(encoding="utf-8")
    assert KEY not in status_text


def test_a_new_key_gets_a_new_client(tmp_path: Path) -> None:
    first = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={KEY}\n"
    second = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={OTHER_KEY}\n"
    h = _run(tmp_path, {1: second}, limit=3, secrets=first)
    assert h.keys == [KEY, OTHER_KEY]
    assert h.rounds.models[0] is not h.rounds.models[1]
    assert h.rounds.models[1] is h.rounds.models[2]  # the same key keeps its client
    assert "new OpenAI key: new client" in h.log()


def test_a_failing_client_for_a_new_key_is_tried_again_next_round(tmp_path: Path) -> None:
    second = f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={OTHER_KEY}\n"

    def cycle(con: sqlite3.Connection, client: Any, config: Any, **kwargs: Any) -> CycleReport:
        report = paper_cycle(con, client, config, **kwargs)
        (tmp_path / "secrets.env").write_text(second, encoding="utf-8")
        return report

    h = harness(
        tmp_path,
        limit=2,
        action=cycle,
        secrets_text=f"BOT_AGENTS=gpt-luna\nOPENAI_API_KEY={KEY}\n",
    )
    made = h.service.model_factory
    tries: list[str] = []

    def flaky(key: str) -> Any:
        tries.append(key)
        if tries.count(OTHER_KEY) == 1:
            raise RuntimeError("client setup failed")
        return made(key)

    h.service.model_factory = flaky
    assert h.service.run() == EXIT_OK
    # Round 2 failed while switching; round 3 tried again and got a client for the new key.
    assert tries == [KEY, OTHER_KEY, OTHER_KEY]
    assert h.keys == [KEY, OTHER_KEY]
    assert h.rounds.models[0] is not h.rounds.models[1]
    assert h.rounds.configs[1].llm.api_key == OTHER_KEY
    assert "round failed" in h.log() and "client setup failed" in h.log()


def test_the_environment_still_wins_over_saved_values(tmp_path: Path) -> None:
    h = _run(
        tmp_path,
        {1: "BOT_AGENTS=breakout\n"},
        limit=2,
        secrets="BOT_AGENTS=breakout\n",
        env={"BOT_AGENTS": "trend"},
    )
    assert [c.agents for c in h.rounds.configs] == [("trend",), ("trend",)]


# ------------------------------------------------------------------ Spec 011 AK5/AK6


def test_pause_applies_next_round_is_logged_once_and_shown_in_the_status(tmp_path: Path) -> None:
    states: list[tuple[bool, str]] = []

    def watch(con: sqlite3.Connection, client: Any, config: Any, **kwargs: Any) -> CycleReport:
        status = read_status(status_path(tmp_path / "bot.db"))
        if status is not None and status.last_round_at:
            states.append((status.trading_paused, status.pause_source))
        report = paper_cycle(con, client, config, **kwargs)
        rounds.append(report)
        if len(rounds) == 1:
            (tmp_path / "secrets.env").write_text(
                "BOT_AGENTS=trend\nBOT_PAUSED=1\n", encoding="utf-8"
            )
        if len(rounds) == 3:
            (tmp_path / "secrets.env").write_text("BOT_AGENTS=trend\n", encoding="utf-8")
        return report

    rounds: list[CycleReport] = []

    h = harness(tmp_path, limit=4, action=watch, secrets_text="BOT_AGENTS=trend\n")
    assert h.service.run() == EXIT_OK
    assert [r.paused for r in rounds] == [False, True, True, False]
    assert states == [(False, ""), (True, "file"), (True, "file")]
    lines = h.log().splitlines()
    assert lines.count("WARNING trading paused (file): no trades except forced sales") == 1
    assert lines.count("INFO trading resumed") == 1
    # A pause is no failure: the rounds stay ok.
    status = read_status(status_path(tmp_path / "bot.db"))
    assert status is not None and (status.last_result, status.failures) == ("ok", 0)


def test_pause_with_a_broken_value_fails_closed_and_warns_once(tmp_path: Path) -> None:
    h = _run(tmp_path, {}, limit=2, secrets="BOT_AGENTS=trend\nBOT_PAUSED=yes\n")
    status = read_status(status_path(h.db))
    assert status is not None and (status.trading_paused, status.pause_source) == (True, "invalid")
    assert h.log().count("is not 1 or 0: trading paused") == 1
    con = sqlite3.connect(h.db)
    try:
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    finally:
        con.close()


def test_resume_request_is_applied_and_logged(tmp_path: Path) -> None:
    from llm_trading_bot.llm.due import iso
    from service_fakes import MINUTE

    def stop_first(con: sqlite3.Connection, client: Any, config: Any, **kwargs: Any) -> CycleReport:
        if not done:
            con.execute("UPDATE portfolios SET halted=1 WHERE agent='trend'")
        done.append(1)
        return paper_cycle(con, client, config, **kwargs)

    done: list[int] = []

    secrets = f"BOT_AGENTS=trend\nBOT_RESUME=trend@{iso(MINUTE)},gpt-sol@{iso(MINUTE)}\n"
    h = harness(tmp_path, limit=2, action=stop_first, secrets_text=secrets)
    assert h.service.run() == EXIT_OK
    released = [x for x in h.log().splitlines() if "released on request" in x]
    assert released == ["INFO risk stop trend released on request: trading again from its cash"]


def test_a_round_that_values_no_depot_for_lack_of_quotes_is_incomplete(tmp_path: Path) -> None:
    """Review of Spec 011 (AK8): every depot holds ETH and the ETH quote breaks - the round
    ran, so it is 'incomplete' with a MarketDataError, which the deploy health check accepts
    (ADR-0020), and not 'no_quotes'."""
    from service_fakes import MovingClient

    client = MovingClient()
    seen: list[Any] = []

    def breaking(con: sqlite3.Connection, client_: Any, config: Any, **kwargs: Any) -> CycleReport:
        if seen:
            client.bad_quotes = {Symbol.ETHEUR}
        report = paper_cycle(con, client_, config, **kwargs)
        seen.append(report)
        return report

    h = harness(tmp_path, limit=2, action=breaking, client=client)
    assert h.service.run() == EXIT_OK
    assert not seen[1].equity and seen[1].unvalued
    status = read_status(status_path(h.db))
    assert status is not None and status.last_result == "incomplete"
    # The same test the deploy health check makes (deploy/release.sh healthy).
    assert "MarketDataError:" in status.last_error


def test_a_refused_release_is_logged_once_and_again_after_a_new_stop(tmp_path: Path) -> None:
    from llm_trading_bot.llm.due import iso
    from service_fakes import MINUTE

    calls: list[int] = []

    def halted(con: sqlite3.Connection, client: Any, config: Any, **kwargs: Any) -> CycleReport:
        calls.append(1)
        if len(calls) in (1, 3):  # stopped while still holding coins: refused
            con.execute("UPDATE portfolios SET halted=1, btc=0.001 WHERE agent='trend'")
        report = paper_cycle(con, client, config, **kwargs)
        if len(calls) == 2:  # a new stop of the same depot resets what was said
            from llm_trading_bot.paper.execution import RiskStop

            report.risk_stops.append(RiskStop("trend", 1.0, 2.0, iso(MINUTE)))
        return report

    secrets = f"BOT_AGENTS=trend\nBOT_RESUME=trend@{iso(MINUTE)}\n"
    h = harness(tmp_path, limit=4, action=halted, secrets_text=secrets)
    assert h.service.run() == EXIT_OK
    refused = [x for x in h.log().splitlines() if "release of trend not applied" in x]
    assert refused == ["WARNING release of trend not applied: the depot still holds coins"] * 2
