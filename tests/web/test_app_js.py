"""Spec 010 AK2/AK3: the one script of the web UI (ADR-0019) - what it may and may not do.

The source checks run everywhere; the age steps are compared with Python's ``age`` by running
the script's own age function in Node.js when it is installed (the CI image has it).
"""

import json
import re
import shutil
import subprocess

import pytest

from llm_trading_bot.web.overview_page import age
from llm_trading_bot.web.script import JS


@pytest.mark.parametrize(
    "forbidden",
    [
        r"\beval\s*\(",
        r"new\s+Function",
        r"set(?:Timeout|Interval)\s*\(\s*[\"'`]",
        r"document\.write",
        r"\.innerHTML\b",
        r"\.outerHTML\b",
        r"insertAdjacentHTML",
        r"https?://",
        r"\bimport\s*\(",
        r"localStorage|sessionStorage|indexedDB",
        r"\.setAttribute\(\s*[\"']on",
        r"\.style\b",
    ],
)
def test_the_script_avoids_what_the_csp_and_adr_0019_rule_out(forbidden: str) -> None:
    assert not re.search(forbidden, JS), forbidden


def test_the_script_only_asks_its_own_origin() -> None:
    assert re.findall(r"fetch\(\s*([^,)]+)", JS) == ['"/"']
    assert 'credentials: "same-origin"' in JS
    assert re.findall(r"location\.assign\(\s*([^)]+)\)", JS) == ['"/login"']
    assert JS.lstrip().startswith("// Live overview") and '"use strict";' in JS


def test_the_script_updates_without_reloading_and_backs_off_on_errors() -> None:
    assert "const PERIOD = 5000;" in JS and "const MAX_WAIT = 60000;" in JS
    assert "Math.min(wait * 2, MAX_WAIT)" in JS
    assert "Verbindung unterbrochen" in JS
    assert "location.reload" not in JS  # live means: no reload
    assert 'querySelector("main").replaceWith' in JS
    assert "visibilitychange" in JS


SAMPLES = [0, 0.4, 1, 59.9, 89, 89.99, 90, 149, 150, 151, 2700, 5399, 5400, 9000, 172799, 172800]
SAMPLES += [1.5 * 60, 2.5 * 60, 3.5 * 3600, 2.5 * 86400, 30 * 60 + 30]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_the_script_counts_ages_with_the_same_steps_as_the_server() -> None:
    body = JS.split("// age:start")[1].split("// age:end")[0]
    program = body + f"\nconsole.log(JSON.stringify({json.dumps(SAMPLES)}.map(age)));\n"
    done = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, timeout=30, encoding="utf-8"
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [age(s) for s in SAMPLES]
