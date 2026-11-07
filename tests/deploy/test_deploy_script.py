"""Spec 009 AK6: scripts/deploy.sh sends only a checked commit - real git, stand-ins for gh and ssh.

Linux only (the CI), like the server script it drives.
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "deploy.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("git") is None, reason="Linux tools (CI)"
)

# Each ssh call: its arguments in ssh-N.args, what it read on stdin in ssh-N.stdin.
FAKE_SSH = r"""#!/usr/bin/env bash
n=$(( $(ls "$FAKE_STATE" | grep -c '^ssh-.*\.args$') + 1 ))
printf '%s\n' "$@" >"$FAKE_STATE/ssh-$n.args"
cat >"$FAKE_STATE/ssh-$n.stdin"
"""

# gh run list ... --jq ... -> the number of successful CI runs for the commit.
FAKE_GH = r"""#!/usr/bin/env bash
echo "gh $*" >>"$FAKE_STATE/gh"
sha="$(printf '%s\n' "$*" | sed -n 's/.*--commit \([0-9a-f]*\).*/\1/p')"
answer="$FAKE_STATE/ci-success"
[ -e "$FAKE_STATE/ci-$sha" ] && answer="$FAKE_STATE/ci-$sha"
cat "$answer"
"""


@dataclass
class Local:
    work: Path
    state: Path
    env: dict[str, str]

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.work, env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def commit(self, name: str, text: str) -> str:
        (self.work / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", f"add {name}")
        return self.git("rev-parse", "HEAD")

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def ssh_calls(self) -> list[tuple[list[str], bytes]]:
        calls = []
        n = 1
        while (self.state / f"ssh-{n}.args").exists():
            args = (self.state / f"ssh-{n}.args").read_text(encoding="utf-8").splitlines()
            calls.append((args, (self.state / f"ssh-{n}.stdin").read_bytes()))
            n += 1
        return calls


def executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def local(tmp_path: Path) -> Iterator[Local]:
    origin, work, state, bin_dir = (tmp_path / d for d in ("origin.git", "work", "state", "bin"))
    for d in (work, state, bin_dir):
        d.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "LTB_SSH": str(executable(bin_dir / "ssh", FAKE_SSH)),
        "LTB_GH": str(executable(bin_dir / "gh", FAKE_GH)),
        "FAKE_STATE": str(state),
    }
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=env)
    box = Local(work, state, env)
    box.git("init", "-q", "-b", "main")
    box.git("remote", "add", "origin", str(origin))
    (work / "deploy").mkdir()
    shutil.copy(REPO / "deploy" / "release.sh", work / "deploy" / "release.sh")
    box.git("add", "deploy")
    box.git("commit", "-q", "-m", "release script")
    box.git("push", "-q", "origin", "main")
    (state / "ci-success").write_text("1\n", encoding="utf-8")
    yield box


def test_deploy_uploads_exactly_the_commit_and_runs_its_release_script(local: Local) -> None:
    sha = local.commit("app.txt", "v1")
    local.git("push", "-q", "origin", "main")
    (local.work / "untracked-secret.env").write_text("OPENAI_API_KEY=sk-x", encoding="utf-8")
    done = local.run("srv", "botsvc", "deploy")
    assert done.returncode == 0, done.stderr
    (upload_args, archive), (run_args, script) = local.ssh_calls()
    assert (
        upload_args[0] == "srv" and f"/opt/llm-trading-bot/incoming/{sha}.tar.gz" in upload_args[1]
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = sorted(m.name for m in tar.getmembers() if m.isfile())
    assert names == ["app.txt", "deploy/release.sh"]  # nothing from the working tree beyond it
    assert run_args == ["srv", f"sudo bash -s -- deploy {sha} 'botsvc'"]
    assert script == (REPO / "deploy" / "release.sh").read_bytes()
    assert f"--commit {sha}" in (local.state / "gh").read_text(encoding="utf-8")


def test_a_commit_that_is_not_on_origin_main_is_refused(local: Local) -> None:
    local.git("switch", "-q", "-c", "side")
    side = local.commit("side.txt", "x")
    done = local.run("srv", "botsvc", "deploy", side)
    assert done.returncode != 0 and "is not on origin/main - refused" in done.stderr
    assert local.ssh_calls() == []


def test_a_commit_without_green_ci_is_refused(local: Local) -> None:
    (local.state / "ci-success").write_text("0\n", encoding="utf-8")
    done = local.run("srv", "botsvc", "deploy")
    assert done.returncode != 0 and "no successful CI run" in done.stderr
    assert local.ssh_calls() == []


@pytest.mark.parametrize(
    "args",
    [
        ["srv", "Bad User", "deploy"],
        ["srv;rm", "botsvc", "deploy"],
        ["srv", "botsvc", "deploy", "a", "b"],
        ["srv", "botsvc", "launch"],
        ["srv", "botsvc"],
    ],
)
def test_bad_arguments_never_reach_the_server(local: Local, args: list[str]) -> None:
    assert local.run(*args).returncode != 0
    assert local.ssh_calls() == []


@pytest.mark.parametrize("mode", ["install", "rollback"])
def test_install_and_rollback_run_the_release_script_of_origin_main(
    local: Local, mode: str
) -> None:
    done = local.run("srv", "botsvc", mode)
    assert done.returncode == 0, done.stderr
    ((args, script),) = local.ssh_calls()
    assert args == ["srv", f"sudo bash -s -- {mode} 'botsvc'"]
    assert script == (REPO / "deploy" / "release.sh").read_bytes()


def test_rollback_uses_the_newest_green_commit_when_the_tip_is_not_green(local: Local) -> None:
    green = local.git("rev-parse", "HEAD")
    script = local.work / "deploy" / "release.sh"
    tip = local.commit("deploy/release.sh", script.read_text(encoding="utf-8") + "# tip\n")
    local.git("push", "-q", "origin", "main")
    (local.state / f"ci-{tip}").write_text("0\n", encoding="utf-8")  # still running or red
    done = local.run("srv", "botsvc", "rollback")
    assert done.returncode == 0, done.stderr
    ((args, sent),) = local.ssh_calls()
    assert args == ["srv", "sudo bash -s -- rollback 'botsvc'"]
    assert sent == (REPO / "deploy" / "release.sh").read_bytes()  # the green commit's copy
    assert b"# tip" not in sent and green != tip


@pytest.mark.parametrize(
    ("args", "remote"),
    [
        (["public", "example.test"], "public 'example.test' 'botsvc'"),
        (["public", "bot.example.co.uk"], "public 'bot.example.co.uk' 'botsvc'"),
        (["public", "off"], "public-off 'botsvc'"),
    ],
)
def test_public_runs_the_release_script_of_origin_main_in_one_connection(
    local: Local, args: list[str], remote: str
) -> None:
    done = local.run("srv", "botsvc", *args)
    assert done.returncode == 0, done.stderr
    ((ssh_args, script),) = local.ssh_calls()  # one connection: the ufw rate limit
    assert ssh_args == ["srv", f"sudo bash -s -- {remote}"]
    assert script == (REPO / "deploy" / "release.sh").read_bytes()


@pytest.mark.parametrize(
    "domain",
    [
        "Example.test",
        "example",
        "a..test",
        "-a.test",
        "a.test;id",
        "a.test x",
        "'a.test'",
        "",
        "Off",
    ],
)
def test_public_bad_domains_never_reach_the_server(local: Local, domain: str) -> None:
    done = local.run("srv", "botsvc", "public", domain)
    assert done.returncode != 0 and "invalid domain" in done.stderr
    assert local.ssh_calls() == []


@pytest.mark.parametrize("args", [["public"], ["public", "example.test", "x"], ["status", "x"]])
def test_public_and_status_take_exactly_their_arguments(local: Local, args: list[str]) -> None:
    assert local.run("srv", "botsvc", *args).returncode != 0
    assert local.ssh_calls() == []


def test_public_status_runs_the_release_script_status(local: Local) -> None:
    done = local.run("srv", "botsvc", "status")
    assert done.returncode == 0, done.stderr
    ((ssh_args, _),) = local.ssh_calls()
    assert ssh_args == ["srv", "sudo bash -s -- status"]


def test_deploy_refuses_when_origin_cannot_be_fetched(local: Local) -> None:
    local.git("remote", "set-url", "origin", str(local.work.parent / "gone.git"))
    done = local.run("srv", "botsvc", "deploy")
    assert done.returncode != 0 and "git fetch failed" in done.stderr
    assert local.ssh_calls() == []
