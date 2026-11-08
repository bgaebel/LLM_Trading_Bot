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


def test_market_report_shows_a_bad_quote_and_still_the_other_market() -> None:
    """Spec 011 AK7: one broken quote is reported per market instead of a crash."""
    code, text = run(eth_quote_bad=True)
    assert code == EXIT_INVALID
    assert "bid 66000.00  ask 66005.00" in text
    assert "quote  INVALID - ticker/book: unusable quote" in text
    assert text.count("snapshot: OK") == 1


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


def test_default_gpt_depots_without_a_key_create_no_database(tmp_path: Path) -> None:
    # Spec 005 AK9: the standard depots need OPENAI_API_KEY; refuse before the DB is touched.
    out = io.StringIO()
    code = run_paper_cycle(tmp_path / "bot.db", ScenarioClient(), env={}, out=out)
    assert code == EXIT_INVALID
    assert "OPENAI_API_KEY is required for the GPT depots" in out.getvalue()
    assert not (tmp_path / "bot.db").exists()


def test_paper_cycle_exit_2_without_quotes(tmp_path: Path) -> None:
    client = ScenarioClient()
    client.fail_quotes = True
    code, text = run_cycle(tmp_path / "bot.db", client)
    assert code == EXIT_FETCH_FAILED
    assert "no fresh quotes - no decisions, nothing traded" in text


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


def test_paper_cycle_reads_values_saved_by_the_web_ui(tmp_path: Path) -> None:
    (tmp_path / "secrets.env").write_text("BOT_AGENTS=meanrev\n", encoding="utf-8")
    out = io.StringIO()
    code = run_paper_cycle(
        tmp_path / "bot.db", ScenarioClient(), env={}, out=out, clock=lambda: GOLDEN_NOW
    )
    assert code == EXIT_OK and "depots meanrev" in out.getvalue()


def test_environment_wins_over_saved_values(tmp_path: Path) -> None:
    secrets = tmp_path / "elsewhere.env"
    secrets.write_text("BOT_AGENTS=meanrev\n", encoding="utf-8")
    out = io.StringIO()
    code = run_paper_cycle(
        tmp_path / "bot.db",
        ScenarioClient(),
        env={"BOT_AGENTS": "trend"},
        out=out,
        clock=lambda: GOLDEN_NOW,
        secrets=secrets,
    )
    assert code == EXIT_OK and "depots trend" in out.getvalue()


def test_stored_key_from_an_explicit_secrets_file_reaches_the_model(tmp_path: Path) -> None:
    from llm_fakes import FakeModel

    key = "sk-test-stored-in-the-web-ui-0000"
    secrets = tmp_path / "elsewhere" / "secrets.env"
    secrets.parent.mkdir()
    secrets.write_text(f"OPENAI_API_KEY={key}\nBOT_AGENTS=gpt-luna\n", encoding="utf-8")
    seen: list[str] = []

    def factory(api_key: str) -> FakeModel:
        seen.append(api_key)
        return FakeModel()

    out = io.StringIO()
    code = run_paper_cycle(
        tmp_path / "bot.db",
        ScenarioClient(),
        env={},
        out=out,
        clock=lambda: GOLDEN_NOW,
        model_factory=factory,
        secrets=secrets,
    )
    assert code == EXIT_OK and seen == [key] and key not in out.getvalue()
    assert "gpt-luna (gpt-6-luna): ASKED" in out.getvalue()


def test_web_refuses_a_public_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["web", "--data-dir", str(tmp_path), "--host", "0.0.0.0"])
    assert code == EXIT_INVALID and "web refused" in capsys.readouterr().out


def test_web_reports_a_taken_port(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        code = main(["web", "--data-dir", str(tmp_path), "--port", str(port)])
    assert code == EXIT_INVALID and "could not start" in capsys.readouterr().out


def test_llm_decision_line_shows_the_real_attempt_number() -> None:
    from llm_trading_bot.cli import _market_line
    from llm_trading_bot.paper.report import MarketResult

    line = _market_line(
        MarketResult(
            "gpt-luna",
            Symbol.BTCEUR,
            1793000005 * 10000 + 12345,
            True,
            "BUY",
            0.25,
            "OK",
            True,
            "r",
            attempt=12345,
        )
    )
    assert "attempt 12345 new: BUY" in line


def test_run_starts_the_service_and_a_stop_ends_it_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from llm_trading_bot import cli
    from llm_trading_bot.service.status import read_status, status_path

    def stop_at_once(stop: threading.Event) -> None:
        stop.set()  # as if SIGTERM arrived right after the start

    monkeypatch.setattr(cli, "install_stop_signals", stop_at_once)
    db = tmp_path / "bot.db"
    out = io.StringIO()
    code = cli.run_service(db, ScenarioClient(), env={"BOT_AGENTS": "trend"}, out=out)
    assert code == EXIT_OK
    assert "INFO service started: db bot.db, depots trend" in out.getvalue()
    assert "INFO service stopped" in out.getvalue()
    status = read_status(status_path(db))
    assert status is not None and status.state == "stopped" and status.last_round_at is None


def test_run_refuses_an_unknown_log_level(tmp_path: Path) -> None:
    from llm_trading_bot.cli import run_service

    out = io.StringIO()
    code = run_service(tmp_path / "bot.db", ScenarioClient(), env={"LOG_LEVEL": "LOUD"}, out=out)
    assert code == EXIT_INVALID and "LOG_LEVEL" in out.getvalue()
    assert not (tmp_path / "bot.db").exists()


def test_run_is_refused_while_another_writer_holds_the_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_trading_bot import cli

    monkeypatch.setattr(cli, "install_stop_signals", lambda stop: None)
    db = tmp_path / "bot.db"
    out = io.StringIO()
    with WriterLock(db):
        code = cli.run_service(db, ScenarioClient(), env={"BOT_AGENTS": "trend"}, out=out)
    assert code == EXIT_LOCKED and "refused: another process owns" in out.getvalue()


def test_run_command_passes_db_and_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_trading_bot import cli

    seen: list[object] = []

    def fake(db: Path, client: object, **kw: object) -> int:
        seen.append((db, kw["secrets"]))
        return EXIT_OK

    monkeypatch.setattr(cli, "run_service", fake)
    assert main(["run", "--db", str(tmp_path / "bot.db")]) == EXIT_OK
    assert main(["run", "--db", "x.db", "--secrets", str(tmp_path / "s.env")]) == EXIT_OK
    assert seen == [(tmp_path / "bot.db", None), (Path("x.db"), tmp_path / "s.env")]


def test_web_db_defaults_to_bot_db_in_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_trading_bot import cli

    seen: list[object] = []
    monkeypatch.setattr(cli, "serve", lambda data_dir, **kw: seen.append((data_dir, kw["db"])))
    assert main(["web", "--data-dir", str(tmp_path)]) == EXIT_OK
    assert main(["web", "--data-dir", str(tmp_path), "--db", str(tmp_path / "x.db")]) == EXIT_OK
    assert seen == [(tmp_path, None), (tmp_path, tmp_path / "x.db")]


def test_paper_cycle_reads_the_pause_from_secrets(tmp_path: Path) -> None:
    """Spec 011 AK6: the one-off cycle honours the pause, and names the file as its source."""
    from llm_trading_bot.paper.risk import read_controls

    (tmp_path / "secrets.env").write_text("BOT_AGENTS=trend\nBOT_PAUSED=1\n", encoding="utf-8")
    assert read_controls({}, tmp_path / "secrets.env").source == "file"
    code, text = run_cycle(tmp_path / "bot.db", ScenarioClient())
    assert code == EXIT_OK and text.count("[PAUSED]") == 2
    assert "buy & hold: already invested" in text  # nothing bought while paused


def test_paper_cycle_reports_controls_releases_and_stops(tmp_path: Path) -> None:
    from llm_trading_bot.paper.db import open_db

    db = tmp_path / "bot.db"
    con = open_db(db, agents=("trend", "buyhold"))
    con.execute("UPDATE portfolios SET halted=1 WHERE agent='trend'")
    con.commit()
    con.close()
    (tmp_path / "secrets.env").write_text(
        "BOT_PAUSED=yes\nBOT_RESUME=trend@2027-01-15T07:59:00+00:00\n", encoding="utf-8"
    )
    _code, text = run_cycle(db, ScenarioClient())
    assert "trading controls: BOT_PAUSED='yes' is not 1 or 0: trading paused" in text
    assert "trading paused: no trades except forced sales" in text
    assert "risk stop trend released on request" in text


@pytest.mark.parametrize(
    "argv",
    [
        ["market"],
        ["signals"],
        ["paper-cycle", "--db", "{db}"],
        ["run", "--db", "{db}"],
        ["web", "--data-dir", "{dir}"],
        ["backup", "--db", "{db}", "--dir", "{dir}/b"],
        ["verify-db", "--db", "{db}"],
        ["backup", "--profile", "observations", "--if-exists", "--db", "{db}", "--dir", "{dir}/o"],
    ],
)
def test_live_mode_stops_every_command_before_any_client_or_database(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 012 AK4 (ADR-0004): TRADING_MODE=live never reaches Bitvavo or a database."""
    from llm_trading_bot import cli

    def no_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("a Bitvavo client was built")

    monkeypatch.setattr(cli, "BitvavoClient", no_client)
    monkeypatch.setenv("TRADING_MODE", "live")
    args = [a.format(db=tmp_path / "bot.db", dir=tmp_path) for a in argv]
    with pytest.raises(NotImplementedError):
        cli.main(args)
    assert list(tmp_path.iterdir()) == []


def test_web_passes_the_public_host_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 013: the unit's `--public-host <domain> --public-port 8788` reach serve()."""
    from llm_trading_bot import cli

    seen: list[object] = []
    monkeypatch.setattr(
        cli, "serve", lambda data_dir, **kw: seen.append((kw["public_host"], kw["public_port"]))
    )
    assert main(["web", "--data-dir", str(tmp_path)]) == EXIT_OK
    args = ["--public-host", "bot.example.test", "--public-port", "9999"]
    assert main(["web", "--data-dir", str(tmp_path), *args]) == EXIT_OK
    assert seen == [(None, 8788), ("bot.example.test", 9999)]


def test_web_passes_the_impressum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 014: the unit's `--impressum <path>` reaches serve(); without it there is none."""
    from llm_trading_bot import cli

    seen: list[object] = []
    monkeypatch.setattr(cli, "serve", lambda data_dir, **kw: seen.append(kw["impressum"]))
    assert main(["web", "--data-dir", str(tmp_path)]) == EXIT_OK
    impressum = tmp_path / "missing-is-fine" / "impressum.md"
    assert main(["web", "--data-dir", str(tmp_path), "--impressum", str(impressum)]) == EXIT_OK
    assert seen == [None, impressum]


def test_web_serve_hands_the_impressum_to_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_trading_bot.web import app as app_module

    apps: list[object] = []

    def stop(app: object, *args: object, **kwargs: object) -> object:
        apps.append(app)
        raise OSError("port taken")

    monkeypatch.setattr(app_module, "make_server", stop)
    impressum = tmp_path / "impressum.md"
    with pytest.raises(OSError):
        app_module.serve(tmp_path, port=0, out=io.StringIO(), impressum=impressum)
    (app,) = apps
    assert isinstance(app, app_module.WebApp) and app.impressum == impressum
