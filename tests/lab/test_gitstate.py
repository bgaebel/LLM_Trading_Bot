"""Spec 016 AK8: the register's git runner runs exactly two fixed git commands, nothing else."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from llm_trading_bot.lab import gitstate
from llm_trading_bot.lab.gitstate import (
    ALLOWED,
    ENV,
    FIXED,
    INHERITED,
    REV_PARSE_HEAD,
    STATUS_PORCELAIN,
    TIMEOUT,
    GitRefused,
    current_code_state,
    git,
    git_runner,
)
from llm_trading_bot.lab.register import UNKNOWN_COMMIT, CodeState, head_from_files

COMMIT = "0123456789abcdef0123456789abcdef01234567"


class FakeRun:
    """Stands in for ``subprocess.run``: records each call and answers per command."""

    def __init__(self, answers: dict[tuple[str, ...], str | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        answer = self.answers[tuple(args)]
        if isinstance(answer, Exception):
            raise answer
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun({REV_PARSE_HEAD: COMMIT + "\n", STATUS_PORCELAIN: ""})
    monkeypatch.setattr(subprocess, "run", fake)  # gitstate calls it as ``subprocess.run``
    return fake


def test_only_the_two_commands_are_allowed() -> None:
    assert ALLOWED == (("git", "rev-parse", "HEAD"), ("git", "status", "--porcelain"))


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "push"],
        ["git", "rev-parse", "HEAD", "--short"],
        ["git", "status"],
        ["git", "status", "--porcelain", "-uno"],
        ["rev-parse", "HEAD"],  # the GitRunner shape belongs to git_runner, not git
        ["git", "-c", "core.pager=sh", "status", "--porcelain"],
        ["sh", "-c", "git rev-parse HEAD"],
        ["git rev-parse HEAD"],
        "git",
        [],
    ],
)
def test_anything_else_is_refused_before_a_process_starts(fake_run: FakeRun, argv: Any) -> None:
    with pytest.raises(GitRefused):
        git(argv)
    assert fake_run.calls == []


def test_runs_the_fixed_list_without_shell_and_with_timeout(fake_run: FakeRun) -> None:
    mine = ["git", "rev-parse", "HEAD"]
    assert git(mine) == COMMIT + "\n"
    [(args, kwargs)] = fake_run.calls
    assert args == mine and args is not mine  # the constant runs, never the caller's list
    assert kwargs["shell"] is False and kwargs["timeout"] == TIMEOUT
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["check"] is True
    assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert kwargs["cwd"] == Path(gitstate.__file__).resolve().parent
    git(STATUS_PORCELAIN, cwd=Path("elsewhere"), timeout=2.0)
    assert fake_run.calls[1][0] == ["git", "status", "--porcelain"]
    assert fake_run.calls[1][1]["cwd"] == Path("elsewhere")
    assert fake_run.calls[1][1]["timeout"] == 2.0


def test_runner_shape_for_the_register(fake_run: FakeRun) -> None:
    assert git_runner(["rev-parse", "HEAD"]) == COMMIT + "\n"
    with pytest.raises(GitRefused):
        git_runner(["push", "origin"])
    assert [c[0] for c in fake_run.calls] == [list(REV_PARSE_HEAD)]


def test_code_state_clean_dirty_and_failing(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert current_code_state() == CodeState(COMMIT, True)
    fake_run.answers[STATUS_PORCELAIN] = " M src/x.py\n"
    assert current_code_state() == CodeState(COMMIT, False)
    fake_run.answers[STATUS_PORCELAIN] = subprocess.TimeoutExpired(["git"], TIMEOUT)
    assert current_code_state() == CodeState(COMMIT, False)
    fake_run.answers[REV_PARSE_HEAD] = FileNotFoundError("git")
    assert current_code_state() == CodeState(UNKNOWN_COMMIT, False)


def test_real_git_agrees_with_the_git_files() -> None:
    package = Path(gitstate.__file__).resolve().parent
    expected = head_from_files(package)
    if shutil.which("git") is None or expected == UNKNOWN_COMMIT:
        pytest.skip("no git or not running from a git checkout")
    assert git(REV_PARSE_HEAD).strip() == expected
    assert current_code_state().commit == expected


def test_env_is_built_from_scratch(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch) -> None:
    # Second review of Spec 016: set after import, none of this reaches git.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_DIR", "elsewhere")
    monkeypatch.setenv("PATH", "evil")
    monkeypatch.setenv("HOME", "evil")
    git(STATUS_PORCELAIN)
    [(_, kwargs)] = fake_run.calls
    env = kwargs["env"]
    fixed = dict(FIXED)
    assert env == dict(ENV)
    assert set(env) <= set(INHERITED) | set(fixed)
    assert fixed == {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }
    assert {k: env[k] for k in fixed} == fixed
    assert env.get("PATH") != "evil"


def _clean_env() -> dict[str, str]:
    """The test's own git calls, without GIT_* from a hook that may run this suite."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def test_fsmonitor_in_the_callers_env_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's run-time probe: ``core.fsmonitor`` set through ``GIT_CONFIG_*`` made
    ``git status`` run a shell command. The control run proves this git honours it."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_clean_env(), timeout=30)
    control, probe = tmp_path / "control.txt", tmp_path / "probe.txt"

    def hook(marker: Path) -> dict[str, str]:
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": f"echo hit > '{marker.as_posix()}'; true",
        }

    status = ["git", "status", "--porcelain"]
    env = {**_clean_env(), **hook(control)}
    subprocess.run(status, cwd=repo, env=env, capture_output=True, timeout=30, check=False)
    if not control.exists():
        pytest.skip("this git does not run core.fsmonitor commands")
    for key, value in hook(probe).items():
        monkeypatch.setenv(key, value)
    assert git(STATUS_PORCELAIN, cwd=repo) == ""
    assert not probe.exists()
