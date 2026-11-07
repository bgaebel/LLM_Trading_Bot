"""Spec 009 AK5: deploy/release.sh in a sandbox - real bash, tar, flock and python3; systemctl,
systemd-analyze and uv are stand-ins that record what they are asked and play the service.

Linux only (the CI): the script targets Ubuntu and needs GNU tools and flock.
"""

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "release.sh"
_PINNED = re.search(r'^UV_VERSION="([\d.]+)"', SCRIPT.read_text(encoding="utf-8"), re.M)
assert _PINNED, "release.sh pins no uv version"
UV_VERSION = _PINNED.group(1)
UNITS = [
    "llm-trading-bot.service",
    "llm-trading-bot-web.service",
    "llm-trading-bot-backup.service",
    "llm-trading-bot-backup.timer",
    "journald@llm-trading-bot.conf",
]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("flock") is None, reason="Linux tools (CI)"
)

FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
# Records every call; keeps unit state as files; the bot writes a status file when started.
state="$FAKE_STATE"
echo "$*" >>"$state/calls"
quiet=0
args=()
for a in "$@"; do
  case "$a" in --quiet) quiet=1 ;; --now) now=1 ;; *) args+=("$a") ;; esac
done
cmd="${args[0]}"
start_unit() {
  [ "$1" = llm-trading-bot-web.service ] && [ -e "$state/bad-web" ] && return 0
  touch "$state/active-$1"
  if [ "$1" = llm-trading-bot.service ]; then
    pid=$((RANDOM + 1000))
    echo "$pid" >"$state/pid"
    release="$(basename "$(readlink "$FAKE_ROOT/opt/llm-trading-bot/current")")"
    # stale-<sha>: the new process never writes; the old process's ok status stays behind.
    [ -e "$state/stale-$release" ] && return 0
    result=ok
    [ -e "$state/bad-$release" ] && result=error
    shift=1
    # oldround-<sha>: the new pid, but the last ok round is from before the start.
    [ -e "$state/oldround-$release" ] && shift=-600
    at="$(date -u -d @"$(( $(date +%s) + shift ))" +%Y-%m-%dT%H:%M:%S+00:00)"
    svc=running
    error=""
    # datagap-<sha>: a round without ETH decisions because Bitvavo left out minutes.
    if [ -e "$state/datagap-$release" ]; then
      svc=degraded result=incomplete
      error="trend ETH-EUR: MarketDataError: ETH-EUR: gap in recent 1m bars - no decision"
    fi
    # codeerror-<sha>: incomplete because of a programming error, not market data.
    if [ -e "$state/codeerror-$release" ]; then
      svc=degraded result=incomplete error="trend BTC-EUR: KeyError: 'close'"
    fi
    out="$FAKE_ROOT/var/lib/llm-trading-bot/service-status.json"
    printf '{"state": "%s", "pid": %s, "last_round_at": "%s",' "$svc" "$pid" "$at" >"$out"
    printf ' "last_result": "%s", "last_error": "%s"}' "$result" "$error" >>"$out"
  fi
}
case "$cmd" in
  is-enabled) [ -e "$state/enabled-${args[1]}" ] ;;
  enable)
    for u in "${args[@]:1}"; do
      touch "$state/enabled-$u"
      [ "${now:-0}" = 1 ] && start_unit "$u"
    done
    exit 0 ;;
  disable) for u in "${args[@]:1}"; do rm -f "$state/enabled-$u" "$state/active-$u"; done ;;
  start | restart) for u in "${args[@]:1}"; do start_unit "$u"; done ;;
  stop) for u in "${args[@]:1}"; do rm -f "$state/active-$u"; done ;;
  is-active)
    if [ -e "$state/active-${args[1]}" ]; then
      [ "$quiet" = 1 ] || echo active
    else
      [ "$quiet" = 1 ] || echo inactive
      exit 3
    fi ;;
  show) if [ -e "$state/active-llm-trading-bot.service" ]; then cat "$state/pid"; else echo 0; fi ;;
  daemon-reload | try-restart) exit 0 ;;
  *) echo "fake systemctl: unknown $cmd" >&2; exit 1 ;;
esac
"""

FAKE_UV = r"""#!/usr/bin/env bash
echo "uv $*" >>"$FAKE_STATE/calls"
if [ "$1" = "--version" ]; then echo "uv $FAKE_UV_VERSION"; exit 0; fi
[ "$1" = sync ] || exit 1
[ -e "$FAKE_STATE/bad-sync" ] && exit 1
mkdir -p .venv/bin
cat >.venv/bin/trading-bot <<'BOT'
#!/usr/bin/env bash
echo "trading-bot $*" >>"$FAKE_STATE/calls"
case "$1" in
  --help) exit 0 ;;
  verify-db) [ ! -e "$FAKE_STATE/bad-verify" ] ;;
  backup)
    [ -e "$FAKE_STATE/bad-backup" ] && exit 1
    mkdir -p "$5" && cp "$3" "$5/bot-$(date -u +%Y%m%dT%H%M%S%NZ).db" ;;
  *) exit 1 ;;
esac
BOT
chmod +x .venv/bin/trading-bot
"""


@dataclass
class Sandbox:
    root: Path
    state: Path
    env: dict[str, str]

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        with SCRIPT.open("rb") as script:
            done = subprocess.run(
                ["bash", "-s", "--", *args],
                stdin=script,
                capture_output=True,
                env=self.env,
                timeout=120,
            )
        result = subprocess.CompletedProcess(
            done.args, done.returncode, done.stdout.decode(), done.stderr.decode()
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    @property
    def app(self) -> Path:
        return self.root / "opt" / "llm-trading-bot"

    @property
    def data(self) -> Path:
        return self.root / "var" / "lib" / "llm-trading-bot"

    def current(self) -> str:
        return os.readlink(self.app / "current").rsplit("/", 1)[-1]

    def calls(self) -> list[str]:
        path = self.state / "calls"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def upload(self, sha: str, extra: dict[str, str] | None = None) -> None:
        """What scripts/deploy.sh streams: a tar.gz of the commit with deploy/ inside."""
        incoming = self.app / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        with tarfile.open(incoming / f"{sha}.tar.gz", "w:gz") as tar:
            files = {
                f"deploy/{u}": (REPO / "deploy" / u).read_text(encoding="utf-8") for u in UNITS
            }
            files["pyproject.toml"] = "[project]\nname = 'x'\n"
            files.update(extra or {})
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    def secrets(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "secrets.env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
        (self.data / "bot.db").write_bytes(b"the trade history")


def sha(n: int) -> str:
    return f"{n:040x}"


def executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path: Path) -> Iterator[Sandbox]:
    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for d in (root, state, bin_dir):
        d.mkdir()
    systemctl = executable(bin_dir / "systemctl", FAKE_SYSTEMCTL)
    analyze = executable(
        bin_dir / "systemd-analyze", '#!/bin/sh\necho "analyze $*" >>"$FAKE_STATE/calls"\n'
    )
    uv = executable(bin_dir / "uv", FAKE_UV)
    # The uv release tarball install() downloads: here a local file:// URL with its checksum.
    tarball = tmp_path / "uv.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(uv, arcname="uv-x86_64-unknown-linux-gnu/uv")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LTB_ROOT": str(root),
        "LTB_SYSTEMCTL": str(systemctl),
        "LTB_ANALYZE": str(analyze),
        "LTB_UV_URL": tarball.as_uri(),
        "LTB_UV_SHA256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "LTB_HEALTH_SECONDS": "3",
        "LTB_POLL_SECONDS": "0.2",
        "FAKE_STATE": str(state),
        "FAKE_ROOT": str(root),
        "FAKE_UV_VERSION": UV_VERSION,
        "HOME": str(tmp_path),
    }
    yield Sandbox(root, state, env)


def installed(box: Sandbox) -> Sandbox:
    box.run("install", "botsvc")
    return box


def test_install_creates_the_layout_and_uv_and_is_idempotent(box: Sandbox) -> None:
    first = box.run("install", "botsvc").stdout
    assert f"installed  uv {UV_VERSION}" in first and "enabled    systemd-time-wait-sync" in first
    for path, mode in [
        (box.app, 0o755),
        (box.app / "releases", 0o755),
        (box.app / "incoming", 0o700),
        (box.data, 0o700),
        (box.root / "var/backups/llm-trading-bot/daily", 0o700),
        (box.root / "var/backups/llm-trading-bot/pre-update", 0o700),
    ]:
        assert path.is_dir() and path.stat().st_mode & 0o777 == mode, path
    assert (box.root / "usr/local/bin/uv").is_file()
    assert box.run("install", "botsvc").stdout.rstrip().endswith("changes: 0")


def test_a_wrong_uv_checksum_installs_nothing(box: Sandbox) -> None:
    box.env["LTB_UV_SHA256"] = "0" * 64
    result = box.run("install", "botsvc", check=False)
    assert result.returncode != 0 and "uv checksum mismatch" in result.stderr
    assert not (box.root / "usr/local/bin/uv").exists()


def test_first_deploy_without_secrets_starts_only_the_web_ui(box: Sandbox) -> None:
    installed(box).upload(sha(1))
    out = box.run("deploy", sha(1), "botsvc").stdout
    assert box.current() == sha(1)
    assert "no secrets.env yet: only the web UI runs" in out
    assert (box.state / "active-llm-trading-bot-web.service").exists()
    assert (box.state / "active-llm-trading-bot-backup.timer").exists()
    assert not (box.state / "active-llm-trading-bot.service").exists()
    unit = (box.root / "etc/systemd/system/llm-trading-bot.service").read_text(encoding="utf-8")
    assert f"ExecStart={box.app}/current/.venv/bin/trading-bot run --db {box.data}/bot.db" in unit
    assert "User=botsvc" in unit and "__" not in unit
    assert not (box.app / "incoming" / f"{sha(1)}.tar.gz").exists()


def test_an_update_is_healthy_backs_up_first_and_keeps_the_previous_release(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2))
    out = box.run("deploy", sha(2), "botsvc").stdout
    assert box.current() == sha(2) and f"previous: {sha(1)}" in out
    assert os.readlink(box.app / "previous").endswith(sha(1))
    assert (box.app / "releases" / sha(1) / ".ready").exists()
    backups = list((box.root / "var/backups/llm-trading-bot/pre-update").iterdir())
    assert len(backups) == 2 and backups[0].read_bytes() == b"the trade history"
    calls = box.calls()
    # Order: the DB is checked by the new release, then stop, backup, start.
    checked = calls.index(next(c for c in calls if c.startswith("trading-bot verify-db")))
    stopped = calls.index(next(c for c in calls[checked:] if c.startswith("stop ")), checked)
    backed = calls.index(next(c for c in calls[stopped:] if c.startswith("trading-bot backup")))
    started = calls.index(
        next(c for c in calls[backed:] if c.startswith("restart llm-trading-bot"))
    )
    assert checked < stopped < backed < started
    assert any("uv sync --frozen --no-dev --no-editable" in c for c in calls)


def test_an_unhealthy_release_rolls_back_code_and_units_never_the_data(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    history = (box.data / "bot.db").read_bytes()
    (box.state / f"bad-{sha(2)}").touch()
    box.upload(sha(2))
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0
    assert f"rolled back to {sha(1)}, which is healthy" in result.stderr
    assert box.current() == sha(1)
    assert (box.data / "bot.db").read_bytes() == history
    assert (box.state / "active-llm-trading-bot.service").exists()


def test_no_fallback_on_the_first_release_stops_the_bot(box: Sandbox) -> None:
    installed(box).secrets()
    (box.state / f"bad-{sha(1)}").touch()
    box.upload(sha(1))
    result = box.run("deploy", sha(1), "botsvc", check=False)
    assert result.returncode != 0 and "no other release to fall back to" in result.stderr
    assert not (box.state / "active-llm-trading-bot.service").exists()
    assert not (box.state / "enabled-llm-trading-bot.service").exists()  # no restart loop
    # The web UI is where the settings get fixed; the backups go on.
    assert (box.state / "active-llm-trading-bot-web.service").exists()
    assert (box.state / "active-llm-trading-bot-backup.timer").exists()


@pytest.mark.parametrize(
    "name", ["secrets.env", "app/.env", "data/bot.db", "bot.db-wal", "web-auth.json", "../escape"]
)
def test_an_archive_with_secrets_or_data_is_refused_before_anything_stops(
    box: Sandbox, name: str
) -> None:
    installed(box).upload(sha(1), {name: "x"})
    result = box.run("deploy", sha(1), "botsvc", check=False)
    assert result.returncode != 0
    assert "refused" in result.stderr or "leaves the release" in result.stderr
    assert not any(c.startswith("stop ") for c in box.calls())
    assert not (box.app / "releases" / sha(1)).exists()


def test_a_database_the_release_cannot_use_changes_nothing(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / "bad-verify").touch()
    box.upload(sha(2))
    before = len(box.calls())
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and "cannot use the database" in result.stderr
    assert not any(c.startswith("stop ") for c in box.calls()[before:])
    assert box.current() == sha(1)


def test_a_failed_backup_before_the_update_stays_on_the_old_release(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / "bad-backup").touch()
    box.upload(sha(2))
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and "backup before the update failed" in result.stderr
    assert box.current() == sha(1) and (box.state / "active-llm-trading-bot.service").exists()


def test_rollback_switches_to_the_previous_release(box: Sandbox) -> None:
    installed(box).secrets()
    for n in (1, 2):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    box.run("rollback", "botsvc")
    assert box.current() == sha(1)
    assert os.readlink(box.app / "previous").endswith(sha(2))


def test_old_releases_are_removed_but_current_and_previous_stay(box: Sandbox) -> None:
    installed(box).secrets()
    for n in range(1, 6):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    left = sorted(p.name for p in (box.app / "releases").iterdir())
    assert left == [sha(3), sha(4), sha(5)]


def test_drop_ins_and_secrets_survive_an_update(box: Sandbox) -> None:
    installed(box).secrets()
    drop_in = box.root / "etc/systemd/system/llm-trading-bot.service.d/env.conf"
    drop_in.parent.mkdir(parents=True)
    drop_in.write_text("[Service]\nEnvironment=TAKER_FEE=0.0015\n", encoding="utf-8")
    for n in (1, 2):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    assert drop_in.read_text(encoding="utf-8").endswith("TAKER_FEE=0.0015\n")
    assert (box.data / "secrets.env").read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-test\n"


def test_redeploying_the_same_commit_reuses_the_built_release(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(1))
    out = box.run("deploy", sha(1), "botsvc").stdout
    assert f"release {sha(1)} already built" in out and box.current() == sha(1)
    assert f"release {sha(1)} active\n" in out and "previous" not in out


def test_status_and_bad_arguments(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    out = box.run("status").stdout
    assert f"current:  {sha(1)}" in out and "llm-trading-bot.service: active" in out
    assert box.run("deploy", "abc", "botsvc", check=False).returncode != 0
    assert box.run("deploy", sha(2), "Bad User", check=False).returncode != 0
    assert box.run("nonsense", check=False).returncode != 0


@pytest.mark.parametrize("trap", ["stale", "oldround"])
def test_a_status_that_is_not_from_the_new_process_is_not_healthy(box: Sandbox, trap: str) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / f"{trap}-{sha(2)}").touch()
    box.upload(sha(2))
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and f"rolled back to {sha(1)}" in result.stderr
    assert box.current() == sha(1)


def test_a_rollback_reinstalls_the_units_of_the_old_release(box: Sandbox) -> None:
    installed(box).secrets()
    unit = (REPO / "deploy" / "llm-trading-bot.service").read_text(encoding="utf-8")
    for n in (1, 2):
        box.upload(sha(n), {"deploy/llm-trading-bot.service": f"{unit}# release {n}\n"})
        if n == 1:
            box.run("deploy", sha(1), "botsvc")
    (box.state / f"bad-{sha(2)}").touch()
    assert box.run("deploy", sha(2), "botsvc", check=False).returncode != 0
    installed_unit = box.root / "etc/systemd/system/llm-trading-bot.service"
    assert installed_unit.read_text(encoding="utf-8").endswith("# release 1\n")


def test_a_failing_redeploy_of_the_same_commit_falls_back_to_the_previous_one(box: Sandbox) -> None:
    installed(box).secrets()
    for n in (1, 2):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    (box.state / f"bad-{sha(2)}").touch()
    box.upload(sha(2))
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and f"rolled back to {sha(1)}" in result.stderr
    assert box.current() == sha(1)


def test_the_previous_release_survives_pruning_even_when_older(box: Sandbox) -> None:
    installed(box).secrets()
    for n in (1, 2, 3, 4, 2, 5):  # 2 again: its build is old, but it becomes "previous" for 5
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    assert box.current() == sha(5) and os.readlink(box.app / "previous").endswith(sha(2))
    left = sorted(p.name for p in (box.app / "releases").iterdir())
    assert left == [sha(2), sha(3), sha(4), sha(5)]


def test_without_secrets_a_web_ui_that_does_not_start_is_a_failure(box: Sandbox) -> None:
    installed(box)
    (box.state / "bad-web").touch()
    box.upload(sha(1))
    assert box.run("deploy", sha(1), "botsvc", check=False).returncode != 0


def test_a_dropped_ssh_session_does_not_stop_an_update(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2))
    with SCRIPT.open("rb") as script:
        proc = subprocess.Popen(
            ["bash", "-s", "--", "deploy", sha(2), "botsvc"],
            stdin=script,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=box.env,
        )
        assert proc.stdout is not None and proc.stderr is not None
        proc.stdout.close()  # the terminal is gone: every write now fails with EPIPE
        proc.stderr.close()
        assert proc.wait(timeout=120) == 0
    assert box.current() == sha(2)
    log = (box.app / "deploy.log").read_text(encoding="utf-8")
    assert f"release {sha(2)} active" in log


def test_the_pinned_uv_satisfies_the_build_backend_bound() -> None:
    # Otherwise uv fetches uv_build from PyPI unpinned and runs it as root.
    version = re.search(r'^UV_VERSION="([\d.]+)"', SCRIPT.read_text(encoding="utf-8"), re.M)
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    bound = re.search(r'"uv_build>=([\d.]+),<([\d.]+)"', pyproject)
    assert version and bound

    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    assert parts(bound.group(1)) <= parts(version.group(1)) < parts(bound.group(2))


def test_a_round_blocked_only_by_market_data_counts_as_healthy(box: Sandbox) -> None:
    # Bitvavo leaves out minutes without trades; ETH-EUR may have no decision for up to an hour.
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / f"datagap-{sha(2)}").touch()
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    assert box.current() == sha(2)


def test_an_incomplete_round_from_a_code_error_still_rolls_back(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / f"codeerror-{sha(2)}").touch()
    box.upload(sha(2))
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and f"rolled back to {sha(1)}" in result.stderr
    assert box.current() == sha(1)


def test_a_failed_unit_install_never_switches_or_starts_the_release(box: Sandbox) -> None:
    # start_release runs where set -e is off; a failing install must still stop the switch.
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    units = box.root / "etc/systemd/system"
    box.upload(sha(2))
    # Neither replace nor overwrite: read-only files in a read-only directory (tests do not run
    # as root, so `install -m 644` really fails).
    files = [p for p in units.iterdir() if p.is_file()]
    for p in files:
        p.chmod(0o444)
    units.chmod(0o555)
    try:
        result = box.run("deploy", sha(2), "botsvc", check=False)
    finally:
        units.chmod(0o755)
        for p in files:
            p.chmod(0o644)
    assert result.returncode != 0
    assert box.current() == sha(1)  # never switched to the half-installed release


def test_pruning_counts_only_releases_that_were_active(box: Sandbox) -> None:
    installed(box).secrets()
    for n in (1, 2):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    (box.state / f"bad-{sha(3)}").touch()
    box.upload(sha(3))
    assert box.run("deploy", sha(3), "botsvc", check=False).returncode != 0  # rolled back
    for n in (4, 5, 6):
        box.upload(sha(n))
        box.run("deploy", sha(n), "botsvc")
    left = sorted(p.name for p in (box.app / "releases").iterdir())
    # Three that ran (6 current, 5 previous, 4) plus the newest failed build to look at.
    assert left == [sha(3), sha(4), sha(5), sha(6)]
