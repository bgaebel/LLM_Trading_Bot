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
- no shell, no stdin, a timeout, and an environment built from scratch (``ENV``): nothing of
  the caller's environment reaches git except ``INHERITED``, read once at import.
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

INHERITED = ("PATH", "SYSTEMROOT")
"""The only variables git gets from the caller: ``PATH`` finds git and its helpers (on POSIX the
child's ``PATH`` picks the program), ``SYSTEMROOT`` is needed by Windows system DLLs. Not
``HOME``/``USERPROFILE``: with the global config off, git would only read the user's global
ignore and attributes files from there, and this repository's own ``.gitignore`` and
``.gitattributes`` decide what counts as a change. No ``GIT_*`` variable: ``GIT_CONFIG_COUNT``
with ``core.fsmonitor`` would make ``git status`` run a shell command, ``GIT_DIR`` from a git hook
would point it at another repository. Read once at import, so a later change of ``os.environ``
cannot reach git (the order-path guard also forbids writing ``os.environ`` in src)."""
FIXED = (
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_GLOBAL", os.devnull),
    ("GIT_OPTIONAL_LOCKS", "0"),
)
"""No system or global git config (they could name hooks or commands to run), and
``GIT_OPTIONAL_LOCKS=0`` so ``git status`` never writes the index while the developer works in
the same checkout. The checkout's own ``.git/config`` still applies."""
ENV = tuple((k, os.environ[k]) for k in INHERITED if k in os.environ) + FIXED
"""Pairs, not a dict: another module cannot change what git gets by adding a key."""


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
        env=dict(ENV),
    )
    return done.stdout


def git_runner(args: Sequence[str]) -> str:
    """``register.GitRunner`` shape: ``code_state`` passes the arguments after ``git``."""
    return git(["git", *args])


def current_code_state() -> CodeState:
    """Commit and clean flag of the checkout this package runs from, proven by git."""
    return code_state(git_runner)
