"""Spec 016 AK11: the CLI and the service never import the lab at start."""

import subprocess
import sys

PROBE = """
import sys
import llm_trading_bot.cli
import llm_trading_bot.service.run
try:
    llm_trading_bot.cli.main(["--help"])
except SystemExit:
    pass
print(sorted(m for m in sys.modules if m.startswith("llm_trading_bot.lab")))
"""


def test_cli_and_service_do_not_import_lab() -> None:
    # A fresh interpreter: the test process itself has imported the lab already.
    done = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, timeout=120
    )
    assert done.stdout.strip().splitlines()[-1] == "[]"


def test_lab_command_names_the_lab_modules() -> None:
    from llm_trading_bot.cli import LAB_COMMANDS

    assert set(LAB_COMMANDS) == {"fetch", "check", "spread", "backtest", "fidelity", "report"}
    assert set(LAB_COMMANDS.values()) <= {"archive", "spread", "backtest", "fidelity", "report"}

