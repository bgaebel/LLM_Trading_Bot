"""Spec 005 AK5: every attempt records what was asked, of whom, at what cost - and the API key
appears nowhere: not in the database, not in the output, not in error texts."""

import io
import json
import sqlite3
from pathlib import Path

import httpx2
import openai

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.cli import EXIT_INVALID, EXIT_OK, run_paper_cycle
from llm_trading_bot.llm.contract import PROMPT_VERSION, prompt_sha256
from llm_trading_bot.llm.openai_client import ModelReply


def run(db: Path, model: FakeModel) -> tuple[int, str]:
    out = io.StringIO()
    code = run_paper_cycle(
        db,
        ScenarioClient(),
        env={"OPENAI_API_KEY": KEY},
        out=out,
        clock=lambda: NOW,
        model_factory=lambda key: model,
    )
    return code, out.getvalue()


def everything(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return json.dumps([con.execute(f"SELECT * FROM {t}").fetchall() for t in tables])
    finally:
        con.close()


def test_attempt_row_holds_the_full_provenance(tmp_path: Path) -> None:
    model = FakeModel()
    code, text = run(tmp_path / "bot.db", model)
    assert code == EXIT_OK
    con = sqlite3.connect(tmp_path / "bot.db")
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM llm_calls WHERE agent='gpt-luna'").fetchone()
    finally:
        con.close()
    assert (row["model"], row["prompt_version"], row["prompt_sha256"]) == (
        "gpt-6-luna",
        PROMPT_VERSION,
        prompt_sha256(),
    )
    assert (row["max_output_tokens"], row["effort"], row["status"]) == (8000, "low", "OK")
    assert row["context"] == model.calls[0]["message"]
    assert (row["response_id"], row["input_tokens"], row["output_tokens"]) == ("resp_1", 1500, 700)
    assert (row["reasoning_tokens"], row["cached_tokens"]) == (300, 0)
    assert row["cost_eur"] == "0.0005"  # 1500 * 0.10 + 700 * 0.50 per 1M EUR
    assert row["reserved_eur"] is not None and row["latency_seconds"] is not None
    assert row["ref_mid_btc"] > 0 and row["ref_mid_eth"] > 0
    assert "gpt-luna (gpt-6-luna): ASKED" in text and "cost ~0.0005 EUR" in text
    assert "reasoning 300" in text


def test_key_appears_nowhere_even_when_the_provider_echoes_it(tmp_path: Path) -> None:
    def leaky(_markets: list[str]) -> ModelReply:
        response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com"))
        raise openai.AuthenticationError(
            f"Incorrect API key provided: {KEY}", response=response, body=None
        )

    code, text = run(tmp_path / "bot.db", FakeModel({"gpt-6-luna": leaky}))
    assert code == EXIT_INVALID
    assert "gpt-luna (gpt-6-luna): ERROR - AuthenticationError" in text
    assert KEY not in text
    assert KEY not in everything(tmp_path / "bot.db")
