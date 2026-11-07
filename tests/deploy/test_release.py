"""Spec 009 AK5, Spec 013 AK7/AK8, Spec 014 AK8-AK10, Spec 015 AK5-AK7: deploy/release.sh in a
sandbox - real bash, tar, flock, curl and python3; systemctl, systemd-analyze, uv, caddy, ufw,
apt-get, apt-cache, gpg and journalctl are stand-ins that record what they are asked and play the
service.

Linux only (the CI): the script targets Ubuntu and needs GNU tools and flock.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
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
CADDYFILE = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")
WEB_UNIT = "llm-trading-bot-web.service"
BACKUP_UNIT = "llm-trading-bot-backup.service"
# Spec 014: fixed in the web unit; the sandbox keeps it as it is (not below LTB_ROOT).
IMPRESSUM_ARG = "--impressum /etc/llm-trading-bot/impressum.md"
TUNNEL_END = f"--host 127.0.0.1 --port 8787 {IMPRESSUM_ARG}"
PUBLIC_END = f"{TUNNEL_END} --public-host example.test --public-port 8788"
# Stands for the operator's name and address: it must never show up in output or logs.
SECRET = "Erika Mustermann, Musterweg 7"
IMPRINT = f"# Impressum\n\n## Angaben gemäß § 5 DDG\n\n{SECRET}\n".encode()

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
  # bad-caddy: caddy does not start (e.g. port 80 taken).
  [ "$1" = caddy.service ] && [ -e "$state/bad-caddy" ] && return 1
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
  # bad-reload: caddy refuses the new configuration and keeps running the old one.
  reload) [ ! -e "$state/bad-reload" ] ;;
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
    shift
    profile=bot db="" dir=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --profile) profile="$2"; shift ;;
        --db) db="$2"; shift ;;
        --dir) dir="$2"; shift ;;
      esac
      shift
    done
    # bad-backup: bot.db cannot be copied; bad-backup-observations: only observations.db.
    [ "$profile" = bot ] && [ -e "$FAKE_STATE/bad-backup" ] && exit 1
    [ -e "$FAKE_STATE/bad-backup-$profile" ] && { echo "backup failed: $profile broken"; exit 1; }
    mkdir -p "$dir" && cp "$db" "$dir/$profile-$(date -u +%Y%m%dT%H%M%S%NZ).db" ;;
  *) exit 1 ;;
esac
BOT
chmod +x .venv/bin/trading-bot
"""

# Installed once the fake apt-get installed it; validate must get throwaway storage.
FAKE_CADDY = r"""#!/usr/bin/env bash
echo "caddy $*" >>"$FAKE_STATE/calls"
[ -e "$FAKE_STATE/caddy-installed" ] || exit 127
case "$1" in
  version) echo "v2.10.2 h1:fake" ;;
  validate)
    [ "$2 $3 $4" = "--adapter caddyfile --config" ] || exit 2
    [ -n "${XDG_DATA_HOME:-}" ] && [ -n "${XDG_CONFIG_HOME:-}" ] || exit 2
    cp "$5" "$FAKE_STATE/validated"
    if [ -e "$FAKE_STATE/bad-caddyfile" ]; then echo "Error: adapting config: bad"; exit 1; fi ;;
  *) exit 2 ;;
esac
"""

# Rules and the logging switch as files; the messages real ufw prints.
FAKE_UFW = r"""#!/usr/bin/env bash
echo "ufw $*" >>"$FAKE_STATE/calls"
if [ "$*" = "status verbose" ]; then
  echo "Status: active"
  if [ -e "$FAKE_STATE/ufw-logging-off" ]; then echo "Logging: off"
  else echo "Logging: on (low)"; fi
  echo "Default: deny (incoming), allow (outgoing), disabled (routed)"
  exit 0
fi
if [ "$1" = logging ]; then
  case "$2" in
    off) touch "$FAKE_STATE/ufw-logging-off"; echo "Logging disabled" ;;
    on | low) rm -f "$FAKE_STATE/ufw-logging-off"; echo "Logging enabled" ;;
    *) exit 1 ;;
  esac
  exit 0
fi
del=0
if [ "$1" = delete ]; then del=1; shift; fi
[ "$1" = allow ] || exit 1
rule="$FAKE_STATE/ufw-${2/\//-}"
if [ "$del" = 1 ]; then
  if [ -e "$rule" ]; then rm "$rule"; echo "Rule deleted"; echo "Rule deleted (v6)"
  else echo "Could not delete non-existent rule"; fi
elif [ -e "$rule" ]; then echo "Skipping adding existing rule"
else touch "$rule"; echo "Rule added"; echo "Rule added (v6)"; fi
"""

# Like the real package: installing caddy enables and starts it with its welcome site.
FAKE_APT_GET = r"""#!/usr/bin/env bash
echo "apt-get $*" >>"$FAKE_STATE/calls"
[ "${DEBIAN_FRONTEND:-}" = noninteractive ] || exit 1
[ -f "$FAKE_ROOT/etc/apt/sources.list.d/caddy-stable.list" ] || exit 100
case " $* " in
  *" install "*)
    touch "$FAKE_STATE/caddy-installed" "$FAKE_STATE/enabled-caddy.service" \
      "$FAKE_STATE/active-caddy.service" ;;
esac
"""

FAKE_APT_CACHE = r"""#!/usr/bin/env bash
echo "apt-cache $*" >>"$FAKE_STATE/calls"
origin=cloudsmith/caddy/stable
[ -e "$FAKE_STATE/bad-origin" ] && origin=someone/else
echo " 500 https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version/main amd64 Packages"
echo "     release o=$origin,a=any-version,n=any-version,l=source=none,c=main,b=amd64"
"""

# gpg --batch --yes --dearmor -o <out> <in>: only an armored key is accepted.
FAKE_GPG = r"""#!/usr/bin/env bash
echo "gpg $*" >>"$FAKE_STATE/calls"
out="" prev=""
for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done
in="${*: -1}"
grep -q "BEGIN PGP PUBLIC KEY BLOCK" "$in" || exit 2
cp "$in" "$out"
"""

FAKE_JOURNALCTL = r"""#!/usr/bin/env bash
echo "journalctl $*" >>"$FAKE_STATE/calls"
echo "Archived and active journals take up 8.0M in the file system."
"""

KEY = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n"


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

    @property
    def caddyfile(self) -> Path:
        return self.root / "etc" / "caddy" / "Caddyfile"

    @property
    def public_file(self) -> Path:
        return self.root / "etc" / "llm-trading-bot" / "public-host"

    def unit(self, name: str) -> str:
        return (self.root / "etc/systemd/system" / name).read_text(encoding="utf-8")

    def public_host(self, text: str) -> None:
        self.public_file.parent.mkdir(parents=True, exist_ok=True)
        self.public_file.write_text(text, encoding="utf-8", newline="\n")

    def caddy(self, active: bool = True) -> None:
        """caddy installed (and running) as `public` leaves it."""
        (self.state / "caddy-installed").touch()
        if active:
            (self.state / "enabled-caddy.service").touch()
            (self.state / "active-caddy.service").touch()

    @property
    def impressum(self) -> Path:
        return self.root / "etc" / "llm-trading-bot" / "impressum.md"

    def upload(
        self,
        sha: str,
        extra: dict[str, str] | None = None,
        pre013: bool = False,
        pre014: bool = False,
        pre015: bool = False,
    ) -> None:
        """What scripts/deploy.sh streams: a tar.gz of the commit with deploy/ inside. pre013: a
        release from before Spec 013 - no Caddyfile, no public host in the web unit (and no
        imprint). pre014: a release from Spec 013 - no --impressum in the web unit. pre015: a
        release from Spec 014 - its backup unit copies bot.db only (and so do the older ones)."""
        incoming = self.app / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        with tarfile.open(incoming / f"{sha}.tar.gz", "w:gz") as tar:
            files = {
                f"deploy/{u}": (REPO / "deploy" / u).read_text(encoding="utf-8") for u in UNITS
            }
            if pre013 or pre014 or pre015:
                backup = f"deploy/{BACKUP_UNIT}"
                files[backup] = "".join(
                    line
                    for line in files[backup].splitlines(keepends=True)
                    if "--profile observations" not in line
                )
                starts = [ln for ln in files[backup].splitlines() if ln.startswith("ExecStart=")]
                assert len(starts) == 1 and "observations" not in starts[0]
            web = f"deploy/{WEB_UNIT}"
            if pre013 or pre014:
                files[web] = files[web].replace(f" {IMPRESSUM_ARG}", "")
                assert "--impressum" not in files[web]
            if pre013 or pre014:
                # Spec 013's namespace journal: no MaxFileSec, syslog forwarding by default.
                journald = "deploy/journald@llm-trading-bot.conf"
                files[journald] = "".join(
                    line
                    for line in files[journald].splitlines(keepends=True)
                    if not line.startswith(("MaxFileSec=", "ForwardToSyslog="))
                )
            if pre013:
                files[web] = files[web].replace(" __PUBLIC_HOST_ARG__", "")
            else:
                files["deploy/Caddyfile"] = CADDYFILE
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

    def upload_imprint(self, data: bytes, digest: str | None = None) -> str:
        """What scripts/deploy.sh streams in its first connection; returns the sha256 it names."""
        digest = digest or hashlib.sha256(data).hexdigest()
        incoming = self.app / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        (incoming / f"impressum-{digest}.md").write_bytes(data)
        return digest

    def exec_start(self) -> str:
        (line,) = [ln for ln in self.unit(WEB_UNIT).splitlines() if ln.startswith("ExecStart=")]
        return line

    @property
    def caddy_drop_in(self) -> Path:
        return self.root / "etc/systemd/system/caddy.service.d/10-llm-trading-bot.conf"

    @property
    def journald_drop_in(self) -> Path:
        return self.root / "etc/systemd/journald.conf.d/50-llm-trading-bot.conf"

    @property
    def namespace_drop_in(self) -> Path:
        return self.root / "etc/systemd/journald@llm-trading-bot.conf.d/50-llm-trading-bot.conf"


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
    fakes = {
        name: str(executable(bin_dir / name, text))
        for name, text in [
            ("caddy", FAKE_CADDY),
            ("ufw", FAKE_UFW),
            ("apt-get", FAKE_APT_GET),
            ("apt-cache", FAKE_APT_CACHE),
            ("gpg", FAKE_GPG),
            ("journalctl", FAKE_JOURNALCTL),
        ]
    }
    key = tmp_path / "caddy-gpg.key"
    key.write_text(KEY, encoding="utf-8")
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
        "LTB_CADDY": fakes["caddy"],
        "LTB_UFW": fakes["ufw"],
        "LTB_APT_GET": fakes["apt-get"],
        "LTB_APT_CACHE": fakes["apt-cache"],
        "LTB_GPG": fakes["gpg"],
        "LTB_JOURNALCTL": fakes["journalctl"],
        "LTB_CADDY_KEY_URL": key.as_uri(),
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
        (box.root / "var/backups/llm-trading-bot/observations", 0o700),
        (box.root / "var/backups/llm-trading-bot/observations/daily", 0o700),
        (box.root / "var/backups/llm-trading-bot/observations/pre-update", 0o700),
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
    # A file where the unit directory belongs: `install -d` fails, even for root (CI may be root,
    # so permissions alone do not make it fail).
    units.rename(units.with_name("system.moved"))
    units.write_text("not a directory", encoding="utf-8")
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0, result.stdout + result.stderr
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


# ------------------------------------------------------------------ Spec 013: public HTTPS


def index(calls: list[str], prefix: str, start: int = 0) -> int:
    return next(i for i in range(start, len(calls)) if calls[i].startswith(prefix))


def public_box(box: Sandbox) -> Sandbox:
    """Installed, with secrets, caddy running and the public host set."""
    installed(box).secrets()
    box.caddy()
    box.public_host("example.test\n")
    return box


def test_public_host_file_adds_the_listener_and_installs_the_caddyfile(box: Sandbox) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    assert box.exec_start().endswith(PUBLIC_END)
    caddy = box.caddyfile.read_text(encoding="utf-8")
    assert "\nexample.test {\n" in caddy and "\nwww.example.test {\n" in caddy
    assert "redir https://example.test{uri} permanent" in caddy and "__" not in caddy
    assert (box.state / "validated").read_text(encoding="utf-8") == caddy
    assert "reload caddy.service" in box.calls()


def test_public_without_the_host_file_neither_listener_nor_caddy(box: Sandbox) -> None:
    installed(box).secrets()
    box.caddy()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    assert box.exec_start().endswith(TUNNEL_END)
    assert not box.caddyfile.exists()
    assert not any(c.startswith(("caddy ", "reload ")) for c in box.calls())


@pytest.mark.parametrize(
    "text",
    [
        "Example.test\n",
        "example.test\nother.test\n",
        "",
        "\n",
        "example\n",
        "exa mple.test\n",
        "a..test\n",
        "-a.test\n",
    ],
)
def test_public_host_file_that_is_not_one_domain_stops_nothing(box: Sandbox, text: str) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.public_host(text)
    box.upload(sha(2))
    before = len(box.calls())
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and "nothing changed" in result.stderr, result.stderr
    assert not any(c.startswith("stop ") for c in box.calls()[before:])
    assert box.current() == sha(1)


def test_public_caddyfile_is_validated_before_the_stop_and_reloaded_once_healthy(
    box: Sandbox,
) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2), {"deploy/Caddyfile": CADDYFILE + "# release 2\n"})
    start = len(box.calls())
    box.run("deploy", sha(2), "botsvc")
    calls = box.calls()
    validated = index(calls, "caddy validate --adapter caddyfile --config", start)
    stopped = index(calls, "stop ", start)
    started = index(calls, "restart llm-trading-bot.service", start)
    healthy = max(i for i in range(start, len(calls)) if calls[i].startswith("show "))
    reloaded = index(calls, "reload caddy.service", start)
    assert validated < stopped < started < healthy < reloaded
    assert box.caddyfile.read_text(encoding="utf-8").endswith("# release 2\n")


def test_public_an_invalid_caddyfile_changes_nothing(box: Sandbox) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    old = box.caddyfile.read_text(encoding="utf-8")
    (box.state / "bad-caddyfile").touch()
    box.upload(sha(2), {"deploy/Caddyfile": CADDYFILE + "# release 2\n"})
    before = len(box.calls())
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and "caddy validate rejected" in result.stderr
    assert "nothing changed" in result.stderr
    assert not any(c.startswith("stop ") for c in box.calls()[before:])
    assert box.current() == sha(1) and box.caddyfile.read_text(encoding="utf-8") == old


def test_public_host_without_caddy_installed_stops_nothing(box: Sandbox) -> None:
    installed(box).secrets()
    box.public_host("example.test\n")
    box.upload(sha(1))
    result = box.run("deploy", sha(1), "botsvc", check=False)
    assert result.returncode != 0 and "caddy is not installed" in result.stderr
    assert not any(c.startswith("stop ") for c in box.calls())
    assert not os.path.lexists(box.app / "current")


def test_public_a_failed_caddy_reload_keeps_the_old_caddyfile_and_the_release(
    box: Sandbox,
) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    old = box.caddyfile.read_text(encoding="utf-8")
    (box.state / "bad-reload").touch()
    box.upload(sha(2), {"deploy/Caddyfile": CADDYFILE + "# release 2\n"})
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and "previous Caddyfile" in result.stderr, result.stderr
    assert "rolling back" not in result.stdout + result.stderr
    assert box.current() == sha(2) and (box.state / "active-llm-trading-bot.service").exists()
    assert box.caddyfile.read_text(encoding="utf-8") == old
    deployed = (box.app / "deployed.log").read_text(encoding="utf-8")
    assert f" {sha(2)} active" in deployed


@pytest.mark.parametrize("public", [False, True], ids=["tunnel", "public"])
def test_public_first_deploy_over_a_release_from_before_013(box: Sandbox, public: bool) -> None:
    installed(box).secrets()
    box.caddy()
    if public:
        box.public_host("example.test\n")
    box.upload(sha(1), pre013=True)
    out = box.run("deploy", sha(1), "botsvc").stdout
    assert "--host 127.0.0.1 --port 8787\n" in box.unit(WEB_UNIT)
    assert not box.caddyfile.exists()
    assert ("has no deploy/Caddyfile" in out) == public
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    assert box.current() == sha(2)
    assert box.caddyfile.exists() == public
    assert ("--public-host example.test --public-port 8788" in box.unit(WEB_UNIT)) == public
    assert IMPRESSUM_ARG in box.exec_start()


def test_public_rollback_to_a_release_from_before_013_leaves_caddy_alone(box: Sandbox) -> None:
    public_box(box).upload(sha(1), pre013=True)
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    caddy = box.caddyfile.read_text(encoding="utf-8")
    before = len(box.calls())
    box.run("rollback", "botsvc")
    assert box.current() == sha(1)
    assert not any(c.startswith("caddy ") or "caddy.service" in c for c in box.calls()[before:])
    assert box.caddyfile.read_text(encoding="utf-8") == caddy
    # The release from before 013 has no public listener: only the tunnel port in ExecStart.
    (exec_start,) = [ln for ln in box.unit(WEB_UNIT).splitlines() if ln.startswith("ExecStart=")]
    assert "--public-host" not in exec_start and exec_start.endswith("--port 8787")


def test_public_an_unhealthy_release_rolls_back_without_touching_caddy(box: Sandbox) -> None:
    public_box(box).upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    caddy = box.caddyfile.read_text(encoding="utf-8")
    (box.state / f"bad-{sha(2)}").touch()
    box.upload(sha(2), {"deploy/Caddyfile": CADDYFILE + "# release 2\n"})
    before = len(box.calls())
    result = box.run("deploy", sha(2), "botsvc", check=False)
    assert result.returncode != 0 and f"rolled back to {sha(1)}" in result.stderr
    assert "reload caddy.service" not in box.calls()[before:]
    assert box.caddyfile.read_text(encoding="utf-8") == caddy


def test_public_command_sets_up_caddy_in_order_and_twice_changes_nothing(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    start = len(box.calls())
    out = box.run("public", "example.test", "botsvc").stdout
    assert not out.rstrip().endswith("changes: 0")
    calls = box.calls()
    apt = "apt-get -o DPkg::Lock::Timeout=300"
    order = [
        index(calls, f"{apt} -qq update", start),
        index(calls, f"{apt} -y -qq install --no-install-recommends caddy", start),
        index(calls, "stop caddy.service", start),  # the package's welcome site stays off
        index(calls, "caddy validate --adapter caddyfile --config", start),
        index(calls, "daemon-reload", start),
        index(calls, "ufw allow 80/tcp", start),
        index(calls, "ufw allow 443/tcp", start),
        index(calls, "restart caddy.service", start),
        index(calls, f"restart {WEB_UNIT}", start),
    ]
    assert order == sorted(order), calls[start:]
    assert not any(c.startswith("ufw limit") for c in calls[start:])  # allow, not limit
    assert box.public_file.read_text(encoding="utf-8") == "example.test\n"
    assert box.public_file.stat().st_mode & 0o777 == 0o644
    assert box.exec_start().endswith(PUBLIC_END)
    assert "\nexample.test {\n" in box.caddyfile.read_text(encoding="utf-8")
    drop_in = box.root / "etc/systemd/system/caddy.service.d/10-llm-trading-bot.conf"
    assert "RuntimeDirectory=caddy\n" in drop_in.read_text(encoding="utf-8")
    keyring = box.root / "usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    assert keyring.read_text(encoding="utf-8") == KEY
    source = (box.root / "etc/apt/sources.list.d/caddy-stable.list").read_text(encoding="utf-8")
    assert source == (
        "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg]"
        " https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main\n"
    )
    upgrades = box.root / "etc/apt/apt.conf.d/53-llm-trading-bot-caddy"
    assert '"origin=cloudsmith/caddy/stable";' in upgrades.read_text(encoding="utf-8")
    for name in ("ufw-80-tcp", "ufw-443-tcp", "enabled-caddy.service", "active-caddy.service"):
        assert (box.state / name).exists(), name

    second = box.run("public", "example.test", "botsvc").stdout
    assert second.rstrip().endswith("changes: 0"), second
    again = box.calls()[len(calls) :]
    assert not any(c.startswith(("apt-get", "restart ", "stop ", "daemon-reload")) for c in again)


def test_public_command_after_a_failed_caddy_start_restarts_the_web_ui(box: Sandbox) -> None:
    """A rerun that only has to start caddy still restarts the web UI with the public listener."""
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / "bad-caddy").touch()
    failed = box.run("public", "example.test", "botsvc", check=False)
    assert failed.returncode != 0 and "caddy did not start" in failed.stderr, failed.stderr
    (box.state / "bad-caddy").unlink()
    start = len(box.calls())
    out = box.run("public", "example.test", "botsvc").stdout
    calls = box.calls()[start:]
    assert "restart caddy.service" in calls and f"restart {WEB_UNIT}" in calls, calls
    assert out.rstrip().endswith("changes: 0")  # nothing on disk changed, only started


def test_public_command_refuses_a_release_without_caddyfile(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1), pre013=True)
    box.run("deploy", sha(1), "botsvc")
    before = len(box.calls())
    result = box.run("public", "example.test", "botsvc", check=False)
    assert result.returncode != 0 and "has no deploy/Caddyfile" in result.stderr
    assert not any(c.startswith(("apt-get", "ufw", "gpg")) for c in box.calls()[before:])
    assert not box.public_file.exists()


@pytest.mark.parametrize(
    "domain", ["Example.test", "example", "a..test", "-a.test", "a.test;id", "a.test x", ""]
)
def test_public_command_refuses_a_bad_domain_before_anything(box: Sandbox, domain: str) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    before = len(box.calls())
    result = box.run("public", domain, "botsvc", check=False)
    assert result.returncode != 0 and "invalid public host" in result.stderr
    assert box.calls()[before:] == []
    assert not box.public_file.exists()


def test_public_command_without_the_expected_origin_opens_no_port(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    (box.state / "bad-origin").touch()
    result = box.run("public", "example.test", "botsvc", check=False)
    assert result.returncode != 0 and "automatic updates" in result.stderr
    assert not any(c.startswith("ufw") for c in box.calls())
    assert not box.public_file.exists()


def test_public_off_undoes_everything_and_twice_changes_nothing(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    box.run("public", "example.test", "botsvc")
    start = len(box.calls())
    box.run("public-off", "botsvc")
    assert not box.public_file.exists()
    for name in ("enabled-caddy.service", "active-caddy.service", "ufw-80-tcp", "ufw-443-tcp"):
        assert not (box.state / name).exists(), name
    assert box.exec_start().endswith(TUNNEL_END)
    assert f"restart {WEB_UNIT}" in box.calls()[start:]
    assert box.run("public-off", "botsvc").stdout.rstrip().endswith("changes: 0")
    # Off stays off: the next deploy neither validates nor reloads caddy.
    box.upload(sha(2))
    before = len(box.calls())
    box.run("deploy", sha(2), "botsvc")
    assert not any(c.startswith("caddy ") or "caddy.service" in c for c in box.calls()[before:])


def test_public_status_names_the_public_host_and_caddy(box: Sandbox) -> None:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    out = box.run("status").stdout
    assert "public host: off" in out and "caddy: not installed" in out
    box.run("public", "example.test", "botsvc")
    out = box.run("status").stdout
    assert "public host: example.test" in out and "caddy: v2.10.2, active" in out
    box.public_host("Example.test\n")
    assert "public host: INVALID" in box.run("status").stdout


def test_public_bad_arguments(box: Sandbox) -> None:
    installed(box)
    for args in (["public", "example.test"], ["public-off"], ["public-off", "Bad User"]):
        assert box.run(*args, check=False).returncode != 0, args


# ------------------------------------------------------------------ Spec 014: log limits (AK8)

OLD_CADDY_DROP_IN = """# Spec 013 (deploy/release.sh): the admin API is a unix socket in /run/caddy.
[Service]
RuntimeDirectory=caddy
RuntimeDirectoryMode=0750
"""


def deployed(box: Sandbox) -> Sandbox:
    installed(box).secrets()
    box.upload(sha(1))
    box.run("deploy", sha(1), "botsvc")
    return box


def test_public_sets_log_limits_and_twice_changes_nothing(box: Sandbox) -> None:
    deployed(box)
    start = len(box.calls())
    out = box.run("public", "example.test", "botsvc").stdout
    drop_in = box.caddy_drop_in.read_text(encoding="utf-8")
    assert "\nLogNamespace=llm-trading-bot\n" in drop_in
    assert "\nRuntimeDirectory=caddy\nRuntimeDirectoryMode=0750\n" in drop_in
    journald = box.journald_drop_in.read_text(encoding="utf-8")
    assert "\n[Journal]\nMaxRetentionSec=14day\nMaxFileSec=1day\n" in journald
    assert box.journald_drop_in.stat().st_mode & 0o777 == 0o644
    namespace = box.namespace_drop_in.read_text(encoding="utf-8")
    assert "\n[Journal]\nMaxRetentionSec=14day\nMaxFileSec=1day\nForwardToSyslog=no\n" in namespace
    assert box.namespace_drop_in.stat().st_mode & 0o777 == 0o644
    assert "try-restart systemd-journald@llm-trading-bot.service" in box.calls()[start:]
    assert (box.state / "ufw-logging-off").exists()
    assert "disabled   ufw logging" in out and "restarted  systemd-journald" in out
    calls = box.calls()
    # The limits are in place before caddy starts and writes its first lines.
    order = [
        index(calls, "daemon-reload", start),
        index(calls, "restart systemd-journald.service", start),
        index(calls, "ufw status verbose", start),
        index(calls, "ufw logging off", start),
        index(calls, "restart caddy.service", start),
    ]
    assert order == sorted(order), calls[start:]

    second = box.run("public", "example.test", "botsvc").stdout
    assert second.rstrip().endswith("changes: 0"), second
    again = box.calls()[len(calls) :]
    assert "ufw status verbose" in again and "ufw logging off" not in again
    assert not any(c.startswith(("restart ", "daemon-reload")) for c in again), again


def test_public_sets_log_limits_on_a_server_set_up_with_013(box: Sandbox) -> None:
    """`public` again after the 014 deploy (AK12 step 4): drop-in, journald and ufw change, caddy
    restarts once with its new namespace."""
    deployed(box).run("public", "example.test", "botsvc")
    box.caddy_drop_in.write_text(OLD_CADDY_DROP_IN, encoding="utf-8", newline="\n")
    box.journald_drop_in.unlink()
    (box.state / "ufw-logging-off").unlink()
    start = len(box.calls())
    out = box.run("public", "example.test", "botsvc").stdout
    assert out.rstrip().endswith("changes: 3"), out
    calls = box.calls()[start:]
    for call in (
        "daemon-reload",
        "restart systemd-journald.service",
        "ufw logging off",
        "restart caddy.service",
    ):
        assert calls.count(call) == 1, (call, calls)
    assert "LogNamespace=llm-trading-bot" in box.caddy_drop_in.read_text(encoding="utf-8")
    assert box.run("public", "example.test", "botsvc").stdout.rstrip().endswith("changes: 0")


def test_public_sets_log_limits_that_public_off_leaves_in_place(box: Sandbox) -> None:
    deployed(box).run("public", "example.test", "botsvc")
    drop_in = box.caddy_drop_in.read_bytes()
    journald = box.journald_drop_in.read_bytes()
    start = len(box.calls())
    box.run("public-off", "botsvc")
    assert box.caddy_drop_in.read_bytes() == drop_in
    assert box.journald_drop_in.read_bytes() == journald
    assert (box.state / "ufw-logging-off").exists()
    calls = box.calls()[start:]
    assert not any(c.startswith("ufw logging") or "systemd-journald" in c for c in calls), calls


def test_public_sets_log_limits_in_the_namespace_journal_with_every_deploy(box: Sandbox) -> None:
    # deploy/journald@llm-trading-bot.conf comes with each release, not with `public`.
    deployed(box)
    conf = box.root / "etc/systemd/journald@llm-trading-bot.conf"
    j = conf.read_text(encoding="utf-8")
    assert "\nMaxRetentionSec=14day\nMaxFileSec=1day\nForwardToSyslog=no\n" in j
    restart = "try-restart systemd-journald@llm-trading-bot.service"
    assert box.calls().count(restart) == 1
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    assert box.calls().count(restart) == 1  # unchanged file: the namespace journal keeps running


def test_public_sets_log_limits_and_names_the_namespace_when_caddy_fails(box: Sandbox) -> None:
    deployed(box)
    (box.state / "bad-caddy").touch()
    failed = box.run("public", "example.test", "botsvc", check=False)
    assert failed.returncode != 0
    assert "journalctl --namespace=llm-trading-bot -u caddy" in failed.stderr, failed.stderr
    # Written before caddy's start failed: they stay, a rerun only has to start caddy.
    assert box.journald_drop_in.exists() and (box.state / "ufw-logging-off").exists()


# ------------------------------------------------------------------ Spec 014: Rollback (AK10)


@pytest.mark.parametrize("public", [False, True], ids=["tunnel", "public"])
def test_rollback_before_014_drops_the_argument_and_keeps_the_imprint(
    box: Sandbox, public: bool
) -> None:
    installed(box).secrets()
    if public:
        box.caddy()
        box.public_host("example.test\n")
    end = " --public-host example.test --public-port 8788" if public else ""
    box.upload(sha(1), pre014=True)
    box.run("deploy", sha(1), "botsvc")
    assert box.exec_start().endswith(f"--host 127.0.0.1 --port 8787{end}")
    assert "web unit: without --impressum" in box.run("status").stdout
    # 014 over the old release: the argument comes with the unit, the file may still be missing.
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    assert box.exec_start().endswith(f"{TUNNEL_END}{end}")
    box.run("impressum", box.upload_imprint(IMPRINT))
    before = box.impressum.stat()
    box.run("rollback", "botsvc")
    assert box.current() == sha(1)
    exec_start = box.exec_start()
    assert "--impressum" not in exec_start
    assert exec_start.endswith(f"--host 127.0.0.1 --port 8787{end}")
    assert box.impressum.read_bytes() == IMPRINT
    after = box.impressum.stat()
    assert (after.st_mtime_ns, after.st_ino, after.st_mode) == (
        before.st_mtime_ns,
        before.st_ino,
        before.st_mode,
    )
    assert "web unit: without --impressum" in box.run("status").stdout


# ------------------------------------------------------------------ Spec 014: imprint (AK9)


def no_secret_anywhere(box: Sandbox, *outputs: str) -> None:
    for text in outputs:
        assert SECRET not in text
    log = box.app / "deploy.log"
    assert not log.exists() or SECRET not in log.read_text(encoding="utf-8")


def incoming_imprints(box: Sandbox) -> list[str]:
    return sorted(p.name for p in (box.app / "incoming").glob("impressum-*"))


def test_public_log_limits_survive_a_rollback_before_014(box: Sandbox) -> None:
    """The release's journald@ file of Spec 013 has no MaxFileSec; the drop-in from `public`
    keeps the 15 days the privacy notice names while caddy goes on logging into the namespace."""
    installed(box).secrets()
    box.upload(sha(1), pre014=True)
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    box.run("public", "example.test", "botsvc")
    drop_in = box.namespace_drop_in.read_bytes()
    box.run("rollback", "botsvc")
    assert box.current() == sha(1)
    installed_conf = (box.root / "etc/systemd/journald@llm-trading-bot.conf").read_text(
        encoding="utf-8"
    )
    assert "\nMaxFileSec=" not in installed_conf  # the old release's file is back ...
    assert box.namespace_drop_in.read_bytes() == drop_in  # ... the limits stay
    assert "LogNamespace=llm-trading-bot" in box.caddy_drop_in.read_text(encoding="utf-8")


def test_impressum_removes_copies_of_earlier_uploads(box: Sandbox) -> None:
    installed(box)
    stale = box.upload_imprint(IMPRINT.replace(b"Impressum", b"Alt"))
    digest = box.upload_imprint(IMPRINT)
    box.run("impressum", digest)
    assert stale != digest and incoming_imprints(box) == []


def test_impressum_installs_the_file_and_twice_changes_nothing(box: Sandbox) -> None:
    public_box(box)  # /etc/llm-trading-bot exists already
    first = box.run("impressum", box.upload_imprint(IMPRINT))
    assert first.stdout.rstrip().endswith("changes: 1"), first.stdout
    assert box.impressum.read_bytes() == IMPRINT
    assert box.impressum.stat().st_mode & 0o777 == 0o644
    assert incoming_imprints(box) == []
    assert not (box.impressum.parent / ".impressum.md.new").exists()
    digest = hashlib.sha256(IMPRINT).hexdigest()
    assert f"({len(IMPRINT)} B, sha256 {digest[:12]})" in first.stdout
    second = box.run("impressum", box.upload_imprint(IMPRINT))
    assert second.stdout.rstrip().endswith("changes: 0"), second.stdout
    assert incoming_imprints(box) == []
    no_secret_anywhere(box, first.stdout, first.stderr, second.stdout, second.stderr)


def test_impressum_creates_the_directory_on_a_tunnel_only_server(box: Sandbox) -> None:
    installed(box)
    assert not box.impressum.parent.exists()
    out = box.run("impressum", box.upload_imprint(IMPRINT)).stdout
    assert box.impressum.parent.stat().st_mode & 0o777 == 0o755
    assert box.impressum.read_bytes() == IMPRINT
    assert out.rstrip().endswith("changes: 2"), out  # the directory and the file


def test_impressum_replaces_a_changed_file_in_one_rename(box: Sandbox) -> None:
    installed(box)
    box.run("impressum", box.upload_imprint(IMPRINT))
    old_inode = box.impressum.stat().st_ino
    new = IMPRINT + b"\n## Kontakt\n\n- E-Mail: kontakt@example.test\n"
    out = box.run("impressum", box.upload_imprint(new)).stdout
    assert out.rstrip().endswith("changes: 1")
    assert box.impressum.read_bytes() == new
    assert box.impressum.stat().st_ino != old_inode  # a new file renamed over, not rewritten
    assert sorted(p.name for p in box.impressum.parent.iterdir()) == ["impressum.md"]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"x" * 16385, "too big"),
        ("ä".encode("latin-1") + b"\n", "not UTF-8"),
        (b"\xed\xa0\x80\n", "not UTF-8"),  # a surrogate
        (b"# Impressum\n\x00\n", "NUL"),
        (b"", "empty"),
        (b" \n\n\t\n", "empty"),
    ],
    ids=["too-big", "latin-1", "surrogate", "nul", "empty", "blank"],
)
def test_impressum_refuses_an_invalid_upload(box: Sandbox, data: bytes, reason: str) -> None:
    installed(box)
    box.run("impressum", box.upload_imprint(IMPRINT))
    result = box.run("impressum", box.upload_imprint(data), check=False)
    assert result.returncode != 0
    assert reason in result.stderr and "nothing changed" in result.stderr, result.stderr
    assert box.impressum.read_bytes() == IMPRINT  # the served file stays
    assert incoming_imprints(box) == []


def test_impressum_accepts_exactly_the_limit(box: Sandbox) -> None:
    installed(box)
    data = ("ä" * 8191 + "\n\n").encode()
    assert len(data) == 16384
    box.run("impressum", box.upload_imprint(data))
    assert box.impressum.read_bytes() == data


def test_impressum_refuses_a_file_that_does_not_match_its_hash(box: Sandbox) -> None:
    installed(box)
    digest = hashlib.sha256(b"something else").hexdigest()
    result = box.run("impressum", box.upload_imprint(IMPRINT, digest=digest), check=False)
    assert result.returncode != 0 and "does not match its sha256" in result.stderr
    assert not box.impressum.exists() and incoming_imprints(box) == []
    no_secret_anywhere(box, result.stdout, result.stderr)


def test_impressum_without_an_upload_or_with_a_bad_hash_changes_nothing(box: Sandbox) -> None:
    installed(box)
    missing = box.run("impressum", "a" * 64, check=False)
    assert missing.returncode != 0 and "no uploaded imprint" in missing.stderr
    for bad in ("A" * 64, "a" * 63, "../../etc/passwd", ""):
        result = box.run("impressum", bad, check=False)
        assert result.returncode != 0 and "not a sha256" in result.stderr, bad
    assert not box.impressum.parent.exists()


def test_impressum_needs_the_install_and_one_argument(box: Sandbox) -> None:
    result = box.run("impressum", hashlib.sha256(IMPRINT).hexdigest(), check=False)
    assert result.returncode != 0 and "run install first" in result.stderr
    installed(box)
    for args in (["impressum"], ["impressum", "a" * 64, "b"]):
        assert box.run(*args, check=False).returncode != 0, args


def test_impressum_another_deploy_running_is_refused(box: Sandbox) -> None:
    installed(box)
    digest = box.upload_imprint(IMPRINT)
    lock = str(box.app / ".deploy.lock")
    # -o: only flock itself holds the lock, so killing it frees the lock at once.
    holder = subprocess.Popen(["flock", "-o", lock, "sleep", "30"])
    try:
        for _ in range(100):  # until the holder has it
            if subprocess.run(["flock", "-n", lock, "true"], check=False).returncode != 0:
                break
            time.sleep(0.05)
        result = box.run("impressum", digest, check=False)
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode != 0 and "another deploy is running" in result.stderr
    assert not box.impressum.exists()
    assert incoming_imprints(box) == []  # name and address do not stay behind


def test_impressum_status_lines(box: Sandbox) -> None:
    deployed(box)
    out = box.run("status").stdout
    assert "\nimpressum: missing" in out and "\nweb unit: passes --impressum\n" in out
    box.run("impressum", box.upload_imprint(IMPRINT))
    out = box.run("status").stdout
    digest = hashlib.sha256(IMPRINT).hexdigest()
    assert f"\nimpressum: ok ({len(IMPRINT)} B, sha256 {digest[:12]})\n" in out, out
    assert "\njournal: Archived and active journals take up" in out
    no_secret_anywhere(box, out)
    box.impressum.chmod(0o600)
    unreadable = "\nimpressum: INVALID (not readable for the web UI: mode 600)"
    assert unreadable in box.run("status").stdout
    box.impressum.write_bytes(b"caf\xe9\n")
    box.impressum.chmod(0o644)
    assert "\nimpressum: INVALID (not UTF-8)" in box.run("status").stdout
    box.impressum.unlink()
    box.impressum.symlink_to(box.data / "secrets.env")
    assert "\nimpressum: INVALID (a symlink)" in box.run("status").stdout


# ------------------------------------------------------------------ Spec 015: observations.db

# Stands for the recording the 015 service writes next to bot.db.
OBSERVED = b"what the bot saw"


def obs_dir(box: Sandbox, kind: str = "") -> Path:
    path = box.root / "var/backups/llm-trading-bot/observations"
    return path / kind if kind else path


def obs_copies(box: Sandbox, kind: str) -> list[str]:
    directory = obs_dir(box, kind)
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def recorded(box: Sandbox) -> Path:
    path = box.data / "observations.db"
    path.write_bytes(OBSERVED)
    return path


def files_below(path: Path) -> dict[str, tuple[bytes, int, int]]:
    """Content, mtime and inode of every file: "untouched" means none of them changed."""
    return {
        str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def test_observations_backup_before_the_update_follows_the_bot_db(box: Sandbox) -> None:
    deployed(box)
    recorded(box)
    box.upload(sha(2))
    start = len(box.calls())
    box.run("deploy", sha(2), "botsvc")
    assert box.current() == sha(2)
    (copy,) = obs_copies(box, "pre-update")
    assert copy.startswith("observations-")
    assert (obs_dir(box, "pre-update") / copy).read_bytes() == OBSERVED
    calls = box.calls()
    stopped = index(calls, "stop ", start)
    bot = index(calls, "trading-bot backup --db", start)
    obs = index(calls, "trading-bot backup --profile observations", start)
    started = index(calls, "restart llm-trading-bot.service", start)
    # The trade history first, both while nothing writes.
    assert stopped < bot < obs < started
    assert calls[obs].endswith(f"--dir {obs_dir(box, 'pre-update')} --keep 3"), calls[obs]
    pre = box.root / "var/backups/llm-trading-bot/pre-update"
    assert all(p.name.startswith("bot-") for p in pre.iterdir())
    assert (box.data / "observations.db").read_bytes() == OBSERVED


def test_observations_backup_that_fails_only_warns(box: Sandbox) -> None:
    deployed(box)
    recorded(box)
    (box.state / "bad-backup-observations").touch()
    box.upload(sha(2))
    out = box.run("deploy", sha(2), "botsvc").stdout  # exit 0: the deploy went through
    warning = "WARNING: the backup of observations.db before the update failed - the deploy goes on"
    assert warning in out and "backup failed: observations broken" in out
    assert box.current() == sha(2) and (box.state / "active-llm-trading-bot.service").exists()
    assert "rolling back" not in out
    assert obs_copies(box, "pre-update") == []
    pre = box.root / "var/backups/llm-trading-bot/pre-update"
    assert len(list(pre.iterdir())) == 2  # bot.db's copies of both deploys
    assert (box.data / "observations.db").read_bytes() == OBSERVED


def test_observations_backup_of_a_missing_file_is_skipped(box: Sandbox) -> None:
    # A server before its first 015 round: nothing to copy, nothing to complain about.
    deployed(box)
    box.upload(sha(2))
    start = len(box.calls())
    out = box.run("deploy", sha(2), "botsvc").stdout
    assert "no observations.db yet - nothing to back up besides bot.db" in out
    assert not any("--profile observations" in c for c in box.calls()[start:])
    assert not (box.data / "observations.db").exists() and obs_copies(box, "pre-update") == []


def test_observations_backup_daily_is_its_own_invocation(box: Sandbox) -> None:
    deployed(box)
    starts = [ln for ln in box.unit(BACKUP_UNIT).splitlines() if ln.startswith("ExecStart=")]
    trading_bot = f"{box.app}/current/.venv/bin/trading-bot"
    assert starts == [
        f"ExecStart={trading_bot} backup --db {box.data}/bot.db"
        f" --dir {box.root}/var/backups/llm-trading-bot/daily --keep 14",
        f"ExecStart=-{trading_bot} backup --profile observations --if-exists"
        f" --db {box.data}/observations.db --dir {obs_dir(box, 'daily')} --keep 14",
    ]


def test_status_observations(box: Sandbox) -> None:
    deployed(box)
    out = box.run("status").stdout
    # The fake service writes the status file of a release before 015: no counters.
    assert "\nobservations: no counters in the status file (release before Spec 015)" in out, out
    assert "\nobservations.db: missing" in out
    assert "\nlatest observations backup: none\n" in out
    status_file = box.data / "service-status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    status.update(observations_written=1234, observations_failed=0, observations_last_error=None)
    status_file.write_text(json.dumps(status), encoding="utf-8")
    recorded(box)
    for kind, name, data in [
        ("daily", "observations-20261107T033000Z.db", b"older"),
        ("daily", "observations-20261108T033000Z.db", b"newest"),
        ("pre-update", "observations-20261101T120000Z.db", b"old"),
    ]:
        (obs_dir(box, kind) / name).write_bytes(data)
    out = box.run("status").stdout
    assert "\nobservations: written 1234, failed 0, last error: none\n" in out, out
    assert f"\nobservations.db: {len(OBSERVED)} B\n" in out
    newest = "observations/daily/observations-20261108T033000Z.db (6 B)"
    assert f"\nlatest observations backup: {newest}\n" in out
    assert "\nlatest backups: " in out  # bot.db's line stays as it was
    status.update(
        observations_failed=3, observations_last_error="OperationalError: database is locked\nx"
    )
    status_file.write_text(json.dumps(status), encoding="utf-8")
    out = box.run("status").stdout
    assert "\nobservations: written 1234, failed 3, last error: OperationalError: database" in out
    assert "is locked x\n" in out  # one line, whatever the message holds
    status_file.write_text("{half a file", encoding="utf-8")
    assert "\nobservations: status file unreadable\n" in box.run("status").stdout


def rolled_back_before_015(box: Sandbox) -> tuple[dict[str, tuple[bytes, int, int]], str]:
    """A release from 014, then one from 015 that recorded and was backed up, then the rollback.
    Returns the files of the recording as they were before the rollback, and its output."""
    installed(box).secrets()
    box.upload(sha(1), pre015=True)
    box.run("deploy", sha(1), "botsvc")
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    recorded(box)
    (obs_dir(box, "daily") / "observations-20261108T033000Z.db").write_bytes(b"daily copy")
    (obs_dir(box, "pre-update") / "observations-20261108T120000Z.db").write_bytes(b"pre copy")
    before = {
        "data": files_below(box.data)["observations.db"],
        **{f"backup/{k}": v for k, v in files_below(obs_dir(box)).items()},
    }
    start = len(box.calls())
    out = box.run("rollback", "botsvc").stdout
    assert box.current() == sha(1)
    touched = [c for c in box.calls()[start:] if "observations" in c.replace(str(box.root), "")]
    assert touched == []
    return before, out


def test_rollback_before_015_leaves_observations_and_backups_untouched(box: Sandbox) -> None:
    before, out = rolled_back_before_015(box)
    assert f"release {sha(1)} is from before Spec 015: it neither backs up nor reads" in out
    after = {
        "data": files_below(box.data)["observations.db"],
        **{f"backup/{k}": v for k, v in files_below(obs_dir(box)).items()},
    }
    assert after == before and len(after) == 3


def test_rollback_before_015_units_ignore_observations_until_the_next_deploy(
    box: Sandbox,
) -> None:
    rolled_back_before_015(box)
    starts = [ln for ln in box.unit(BACKUP_UNIT).splitlines() if ln.startswith("ExecStart=")]
    assert len(starts) == 1 and "observations" not in starts[0]  # the old release's daily unit
    out = box.run("status").stdout
    assert "\nobservations: no counters in the status file (release before Spec 015)" in out
    assert "observations/pre-update/observations-20261108T120000Z.db" in out  # still listed
    # Forward again: the 015 release backs the recording up before its start once more.
    box.upload(sha(2))
    box.run("deploy", sha(2), "botsvc")
    assert box.current() == sha(2) and len(obs_copies(box, "pre-update")) == 2
    starts = [ln for ln in box.unit(BACKUP_UNIT).splitlines() if ln.startswith("ExecStart=")]
    assert len(starts) == 2 and "--profile observations" in starts[1]
