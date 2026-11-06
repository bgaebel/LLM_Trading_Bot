"""Spec 008 AK3: settings saved in the web UI apply from the next round on, without a restart."""

import sqlite3
from pathlib import Path
from typing import Any

from llm_fakes import KEY
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


def test_the_environment_still_wins_over_saved_values(tmp_path: Path) -> None:
    h = _run(
        tmp_path,
        {1: "BOT_AGENTS=breakout\n"},
        limit=2,
        secrets="BOT_AGENTS=breakout\n",
        env={"BOT_AGENTS": "trend"},
    )
    assert [c.agents for c in h.rounds.configs] == [("trend",), ("trend",)]
