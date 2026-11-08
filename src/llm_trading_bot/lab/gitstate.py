"""The git runner behind the register's code state (Spec 016 AK8): commit and clean work tree.

``register.code_state`` needs ``git rev-parse HEAD`` and ``git status --porcelain`` to prove a
clean tree; reading ``.git`` files cannot show uncommitted changes. This is the only module in
``src`` that may start a program (named exception in ``tests/broker/test_no_order_path.py``;
the backtest's process pool only starts this same Python again), and it is kept as small as
the guard can check by eye:

- exactly the two argument lists in ``ALLOWED``, compared as a whole; anything else is refused
  before a process starts;
- one ``subprocess.run`` call, and its argument names the constants themselves
  (``list(REV_PARSE_HEAD if ... else STATUS_PORCELAIN)``), never the caller's list or a
  variable: the guard pins that shape and that both names are bound once, to these literals;
- no shell, no stdin, a timeout, and ``GIT_OPTIONAL_LOCKS=0`` so ``git status`` never writes
  the index while the developer works in the same checkout.
"""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from llm_trading_bot.lab.register import CodeState, code_state

REV_PARSE_HEAD = ("git", "rev-parse", "HEAD")
STATUS_PORCELAIN = ("git", "status", "--porcelain")
ALLOWED = (REV_PARSE_HEAD, STATUS_PORCELAIN)
TIMEOUT = 15.0
"""Seconds; ``git status`` on a cold Dropbox checkout can take a few, anything longer is a hang
and makes the run count as dirty (``code_state`` treats every failure that way)."""


class GitRefused(ValueError):
    """An argument list outside ``ALLOWED``; no process was started."""


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def git(argv: Sequence[str], *, cwd: Path | None = None, timeout: float = TIMEOUT) -> str:
    """Run one of the ``ALLOWED`` git commands in ``cwd`` (default: this package's checkout)
    and return its stdout. Raises ``GitRefused``, ``OSError`` (no git),
    ``subprocess.CalledProcessError`` or ``subprocess.TimeoutExpired``."""
    wanted = tuple(argv)
    if wanted not in ALLOWED:
        raise GitRefused(f"git command not allowed: {list(wanted)!r}")
    done = subprocess.run(
        list(REV_PARSE_HEAD if wanted == REV_PARSE_HEAD else STATUS_PORCELAIN),
        cwd=cwd if cwd is not None else _package_dir(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=True,
        shell=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    return done.stdout


def git_runner(args: Sequence[str]) -> str:
    """``register.GitRunner`` shape: ``code_state`` passes the arguments after ``git``."""
    return git(["git", *args])


def current_code_state() -> CodeState:
    """Commit and clean flag of the checkout this package runs from, proven by git."""
    return code_state(git_runner)
