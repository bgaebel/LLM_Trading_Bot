import io
import sqlite3
from pathlib import Path

import pytest

from fakes import FakeTransport, ScenarioClient, bitvavo_handler
from golden_scenarios import NOW as GOLDEN_NOW
from llm_trading_bot.cli import (
    EXIT_FETCH_FAILED,
    EXIT_INVALID,
    EXIT_LOCKED,
    EXIT_OK,
    main,
    run_market,
    run_paper_cycle,
    run_signals,
)
from llm_trading_bot.market.bitvavo import BitvavoClient
from llm_trading_bot.market.models import Symbol
from llm_trading_bot.paper.lock import WriterLock
from llm_trading_bot.strategy.signals import STRATEGIES

NOW = 1_800_000_030


def run(**kwargs: bool) -> tuple[int, str]:
    out = io.StringIO()
    client = BitvavoClient(FakeTransport(bitvavo_handler(NOW, **kwargs)))
    code = run_market(client, out=out, clock=lambda: NOW)
    return code, out.getvalue()


def test_market_report_ok() -> None:
    code, text = run()
    assert code == EXIT_OK
    assert text.count("snapshot: OK") == 2
    assert "BTC-EUR" in text and "ETH-EUR" in text
    assert "bid 66000.00  ask 66005.00" in text
    assert "last closed 2027-01-15 08:00:00 UTC" in text


def test_market_report_shows_reason_and_exit_1_for_invalid_market() -> None:
    code, text = run(eth_candles_fail=True)
    assert code == EXIT_INVALID
    assert text.count("snapshot: OK") == 1
    assert "snapshot: INVALID - candles: HTTPFailure: HTTP 500" in text


def test_market_report_exit_2_when_quotes_fail() -> None:
    code, text = run(quotes_fail=True)
    assert code == EXIT_FETCH_FAILED
    assert "quotes FAILED: HTTPFailure: HTTP 503" in text


def test_default_command_prints_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "llm-trading-bot: mode=paper"


def run_sig(**kwargs: bool) -> tuple[int, str]:
    out = io.StringIO()
    client = BitvavoClient(FakeTransport(bitvavo_handler(NOW, **kwargs)))
    code = run_signals(client, out=out, clock=lambda: NOW)
    return code, out.getvalue()


def test_signals_report_lists_every_strategy_for_both_markets() -> None:
    code, text = run_sig()
    assert code == EXIT_OK
    for name in STRATEGIES:
        assert text.count(f"  {name:<9}") == 2, name
    assert "strategy v0.3.1" in text
    assert "regime" in text


def test_signals_report_names_invalid_market_and_exits_1() -> None:
    code, text = run_sig(eth_candles_fail=True)
    assert code == EXIT_INVALID
    assert "ETH-EUR: no signals - HTTPFailure: HTTP 500" in text
    assert text.count("  trend    ") == 1


def test_signals_report_exit_2_when_no_market_is_usable() -> None:
    code, text = run_sig(quotes_fail=True)
    assert code == EXIT_FETCH_FAILED
    assert text.count("no signals - HTTPFailure: HTTP 503") == 2


# ---------------------------------------------------------------- paper-cycle (Spec 004 AK9)


def run_cycle(db: Path, client: ScenarioClient, **env: str) -> tuple[int, str]:
    out = io.StringIO()
    env = {"BOT_AGENTS": "trend", **env}
    code = run_paper_cycle(db, client, env=env, out=out, clock=lambda: GOLDEN_NOW)
    return code, out.getvalue()


def test_paper_cycle_reports_decisions_and_equity(tmp_path: Path) -> None:
    code, text = run_cycle(tmp_path / "bot.db", ScenarioClient())
    assert code == EXIT_OK
    assert "depots trend, classic strategy v0.3.1" in text
    assert "fee 0.25% + slippage 0.05% per side (virtual orders only)" in text
    assert "buy & hold: bought 2 market(s)" in text
    assert text.count(": BUY target 0.45 [OK] traded - 1h/4h-Aufwärtstrend") == 2
    assert "equity trend" in text and "equity buyhold" in text


def test_paper_cycle_twice_in_one_bar_reports_already_decided(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    run_cycle(db, ScenarioClient())
    code, text = run_cycle(db, ScenarioClient())
    assert code == EXIT_OK
    assert "buy & hold: already invested" in text
    assert text.count("already decided") == 2


def test_paper_cycle_uses_configured_agent_and_costs(tmp_path: Path) -> None:
    code, text = run_cycle(
        tmp_path / "bot.db", ScenarioClient(), BOT_AGENTS="meanrev", TAKER_FEE="0.001"
    )
    assert code == EXIT_OK
    assert "depots meanrev" in text and "fee 0.10%" in text


@pytest.mark.parametrize(
    "env", [{"BOT_AGENTS": "gpt"}, {"SLIPPAGE": "0.5"}], ids=["agent", "slippage"]
)
def test_paper_cycle_refuses_bad_configuration(tmp_path: Path, env: dict[str, str]) -> None:
    code, text = run_cycle(tmp_path / "bot.db", ScenarioClient(), **env)
    assert code == EXIT_INVALID
    assert "configuration error" in text
    assert not (tmp_path / "bot.db").exists()


def test_paper_cycle_exit_2_without_quotes(tmp_path: Path) -> None:
    client = ScenarioClient()
    client.fail_quotes = True
    code, text = run_cycle(tmp_path / "bot.db", client)
    assert code == EXIT_FETCH_FAILED
    assert "no fresh quotes of both markets - no decisions, nothing traded" in text


def test_paper_cycle_exit_1_when_one_market_fails(tmp_path: Path) -> None:
    client = ScenarioClient()
    client.fail_candles = {Symbol.ETHEUR}
    code, text = run_cycle(tmp_path / "bot.db", client)
    assert code == EXIT_INVALID
    assert "ETH-EUR: no decision - HTTPFailure: HTTP 500" in text


def test_paper_cycle_refused_while_another_writer_holds_the_db(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    with WriterLock(db):
        code, text = run_cycle(db, ScenarioClient())
    assert code == EXIT_LOCKED
    assert "paper-cycle refused" in text


def test_paper_cycle_refuses_a_foreign_database(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE portfolios (agent TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    code, text = run_cycle(db, ScenarioClient())
    assert code == EXIT_INVALID
    assert "database error: SchemaError: schema version 0" in text


def test_paper_cycle_needs_a_db_path() -> None:
    with pytest.raises(SystemExit):
        main(["paper-cycle"])
