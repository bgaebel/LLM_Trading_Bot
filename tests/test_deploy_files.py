"""Spec 008 AK10, Spec 009 AK4, Spec 013 AK7, Spec 014 AK8/AK10/AK11: the server files in deploy/ -
options, placeholders, log limits, no real data.

`scripts/check-units.sh` lets systemd and caddy themselves verify them in the Linux CI; these tests
pin what the service relies on (ADR-0017, ADR-0024) and that the public repository holds no server
details. They run on every platform.
"""

import configparser
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_trading_bot.ops.backup import KEEP
from llm_trading_bot.service.run import EXIT_LOCKED, PING_SECONDS, STALL_SECONDS, STOP_SECONDS

DEPLOY = Path(__file__).parent.parent / "deploy"
SERVICE = DEPLOY / "llm-trading-bot.service"
WEB = DEPLOY / "llm-trading-bot-web.service"
JOURNAL = DEPLOY / "journald@llm-trading-bot.conf"
BACKUP = DEPLOY / "llm-trading-bot-backup.service"
TIMER = DEPLOY / "llm-trading-bot-backup.timer"
CADDYFILE = DEPLOY / "Caddyfile"
IMPRESSUM_EXAMPLE = DEPLOY / "impressum.example.md"
RELEASE = DEPLOY / "release.sh"
REPO = DEPLOY.parent
SERVER_MD = REPO / "docs" / "betrieb" / "server.md"
PLACEHOLDERS = {
    "__SERVICE_USER__",
    "__APP_DIR__",
    "__DATA_DIR__",
    "__BACKUP_DIR__",
    "__PUBLIC_HOST_ARG__",
    "__PUBLIC_HOST__",
    # deploy/impressum.example.md (Spec 014): the operator fills them in a copy outside the repo.
    "__OPERATOR_NAME__",
    "__CO_ADDRESS__",
    "__CO_STREET__",
    "__CO_POSTCODE_CITY__",
}


def options(path: Path) -> dict[str, dict[str, str]]:
    return parse_options(path.read_text(encoding="utf-8"))


def parse_options(text: str) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return {section: dict(parser[section]) for section in parser.sections()}


def shell_text(name: str) -> str:
    """The single-quoted value of a variable in release.sh, such as CADDY_DROPIN_TEXT."""
    found = re.search(rf"^{name}='([^']*)'$", RELEASE.read_text(encoding="utf-8"), re.M)
    assert found, name
    return found.group(1)


def test_service_unit_matches_the_service_loop() -> None:
    s = options(SERVICE)["Service"]
    assert s["Type"] == "notify" and s["NotifyAccess"] == "main"
    assert s["ExecStart"] == "__APP_DIR__/.venv/bin/trading-bot run --db __DATA_DIR__/bot.db"
    assert s["Restart"] == "on-failure"
    # The watchdog sees a ping at least twice per interval; the stop deadline fits the timeout.
    assert int(s["WatchdogSec"]) >= 2 * PING_SECONDS
    assert int(s["TimeoutStopSec"]) > STOP_SECONDS
    assert int(s["WatchdogSec"]) < STALL_SECONDS
    # ADR-0020: another writer holding the database is never fought by restarting.
    assert s["RestartPreventExitStatus"] == str(EXIT_LOCKED)


def test_web_unit_stays_on_localhost() -> None:
    s = options(WEB)["Service"]
    assert "--host 127.0.0.1" in s["ExecStart"] and "--data-dir __DATA_DIR__" in s["ExecStart"]
    assert s["User"] == options(SERVICE)["Service"]["User"]


@pytest.mark.parametrize("path", [SERVICE, WEB], ids=["service", "web"])
def test_units_are_hardened_and_write_only_the_data_directory(path: Path) -> None:
    s = options(path)["Service"]
    assert s["User"] == "__SERVICE_USER__"
    assert s["ProtectSystem"] == "strict" and s["ReadWritePaths"] == "__DATA_DIR__"
    assert s["NoNewPrivileges"] == "yes" and s["PrivateTmp"] == "yes"
    assert s["UMask"] == "0077" and s["CapabilityBoundingSet"] == ""
    assert s["LogNamespace"] == "llm-trading-bot"
    assert options(path)["Install"]["WantedBy"] == "multi-user.target"


def exec_starts(path: Path) -> list[str]:
    """The ExecStart lines in order: a oneshot unit may have several, which configparser refuses."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.removeprefix("ExecStart=") for line in lines if line.startswith("ExecStart=")]


def options_besides_exec_start(path: Path) -> dict[str, dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return parse_options("\n".join(ln for ln in lines if not ln.startswith("ExecStart=")))


def test_backup_unit_copies_read_only_into_the_backup_directory() -> None:
    s = options_besides_exec_start(BACKUP)["Service"]
    assert s["Type"] == "oneshot" and s["User"] == "__SERVICE_USER__"
    assert exec_starts(BACKUP)[0] == (
        "__APP_DIR__/.venv/bin/trading-bot backup --db __DATA_DIR__/bot.db"
        f" --dir __BACKUP_DIR__/daily --keep {KEEP}"
    )
    assert s["ReadWritePaths"] == "__DATA_DIR__ __BACKUP_DIR__"
    assert s["ProtectSystem"] == "strict" and s["NoNewPrivileges"] == "yes"
    assert s["UMask"] == "0077" and s["CapabilityBoundingSet"] == ""
    assert s["RestrictAddressFamilies"] == "AF_UNIX"  # a backup never needs the network
    assert "Install" not in options_besides_exec_start(BACKUP)  # started by the timer only


def test_backup_unit_copies_observations_in_its_own_invocation_after_bot_db() -> None:
    # Spec 015 AK6: "-" so its failure never fails the unit; second, so it never delays or
    # skips the copy of the trade history; its own directory, so rotations never meet.
    bot, observations = exec_starts(BACKUP)
    assert not bot.startswith("-")
    assert observations == (
        "-__APP_DIR__/.venv/bin/trading-bot backup --profile observations --if-exists"
        f" --db __DATA_DIR__/observations.db --dir __BACKUP_DIR__/observations/daily --keep {KEEP}"
    )
    release = RELEASE.read_text(encoding="utf-8")
    # release.sh tells a release from Spec 015 on by this line (knows_observations).
    pattern = re.search(r"grep -qsE '([^']+)'", release[release.index("knows_observations() {") :])
    assert pattern and re.search(pattern.group(1), f"ExecStart={observations}")
    assert not re.search(pattern.group(1), f"ExecStart={bot}")
    comments = [ln for ln in BACKUP.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
    assert not any(re.search(pattern.group(1), ln) for ln in comments)


def test_backup_timer_runs_daily_before_the_reboot_window() -> None:
    t = options(TIMER)["Timer"]
    assert t["OnCalendar"] == "*-*-* 03:30:00 UTC" and t["Persistent"] == "true"
    assert t["Unit"] == BACKUP.name and int(t["RandomizedDelaySec"]) <= 600
    assert options(TIMER)["Install"]["WantedBy"] == "timers.target"


def test_journal_is_bounded() -> None:
    j = options(JOURNAL)["Journal"]
    assert j["SystemMaxUse"] == "200M" and j["MaxRetentionSec"] == "14day"


def days(value: str) -> int:
    found = re.fullmatch(r"(\d+)day", value)
    assert found, value
    return int(found.group(1))


def test_journal_namespace_keeps_at_most_15_days_and_nothing_goes_to_syslog() -> None:
    # Spec 014 AK8: journald drops whole files - an entry lives up to MaxRetentionSec + MaxFileSec.
    j = options(JOURNAL)["Journal"]
    assert days(j["MaxRetentionSec"]) + days(j["MaxFileSec"]) == 15
    assert j["MaxFileSec"] == "1day" and j["ForwardToSyslog"] == "no"


def test_journal_system_drop_in_of_release_sh_keeps_at_most_15_days() -> None:
    release = RELEASE.read_text(encoding="utf-8")
    path = 'SYSTEM_JOURNALD_DROPIN="$ROOT/etc/systemd/journald.conf.d/50-llm-trading-bot.conf"'
    assert path in release
    j = parse_options(shell_text("SYSTEM_JOURNALD_TEXT"))["Journal"]
    assert j == {"MaxRetentionSec": "14day", "MaxFileSec": "1day"}
    assert "restart systemd-journald.service" in release


def test_journal_caddy_logs_into_the_namespace() -> None:
    s = parse_options(shell_text("CADDY_DROPIN_TEXT"))["Service"]
    assert s["LogNamespace"] == options(WEB)["Service"]["LogNamespace"] == "llm-trading-bot"
    assert s["RuntimeDirectory"] == "caddy" and s["RuntimeDirectoryMode"] == "0750"
    check = (REPO / "scripts" / "check-units.sh").read_text(encoding="utf-8")
    assert "CADDY_DROPIN_TEXT" in check and 'systemd-analyze verify "$dir/caddy.service"' in check


def test_journal_ufw_logging_off_in_public_and_kept_by_public_off() -> None:
    release = RELEASE.read_text(encoding="utf-8")
    assert '"$UFW" logging off' in release and "'^Logging: off'" in release
    public = release[release.index("cmd_public() {") : release.index("cmd_public_off() {")]
    off = release[release.index("cmd_public_off() {") : release.index("cmd_impressum() {")]
    assert "\n  journal_limits\n" in public and "\n  ufw_logging_off\n" in public
    # public off leaves the limits in place: they protect SSH and firewall logs too.
    assert "journal_limits" not in off and "ufw_logging" not in off
    assert "logging on" not in release


@pytest.mark.parametrize("path", [RELEASE, SERVER_MD], ids=["release.sh", "server.md"])
def test_journal_hints_name_the_namespace_for_caddy(path: Path) -> None:
    # Spec 014 AK8: caddy's own messages are in the namespace, systemd's about the unit are not.
    text = path.read_text(encoding="utf-8")
    assert "journalctl --namespace=llm-trading-bot -u caddy" in text
    for line in text.splitlines():
        if "journalctl -u caddy" in line:
            assert "--namespace=llm-trading-bot -u caddy" in line or "systemd" in line, line


@pytest.mark.parametrize(
    "path",
    [SERVICE, WEB, JOURNAL, BACKUP, TIMER, CADDYFILE, IMPRESSUM_EXAMPLE],
    ids=["service", "web", "journal", "backup", "timer", "caddyfile", "impressum"],
)
def test_no_server_details_only_placeholders(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert set(re.findall(r"__[A-Z_]+__", text)) <= PLACEHOLDERS
    # CLAUDE.md: the repository is public - no IPs (but 127.0.0.1), homes or real users.
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {"127.0.0.1"}
    assert "/home/" not in text and "/root" not in text


def test_impressum_example_holds_only_placeholders() -> None:
    # Spec 014 AK11: name, address and domain exist only on the server and in a local copy.
    text = IMPRESSUM_EXAMPLE.read_text(encoding="utf-8")
    assert re.findall(r"\S+@\S+", text) == ["kontakt@<domain>"]
    assert not re.search(r"\b\d{5}\b", text)  # no postcode
    for heading in (
        "# Impressum",
        "## Angaben gemäß § 5 DDG",
        "## Kontakt",
        "## Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV",
        "## Haftung für Inhalte",
        "## Haftung für Links",
    ):
        assert f"\n{heading}\n\n" in f"\n{text}", heading
    # The markup web/legal.py renders: headings, "- " lists, paragraphs; LF, no BOM.
    assert "\n- " in text and "\r" not in text and not text.startswith("\ufeff")


def test_impressum_only_example_in_repo() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    done = subprocess.run(
        [git, "ls-files", "-z"], cwd=REPO, capture_output=True, check=False, timeout=60
    )
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    files = done.stdout.decode("utf-8").split("\0")
    imprints = [f for f in files if re.search(r"impressum[^/]*\.md$", f, re.I)]
    assert imprints == ["deploy/impressum.example.md"]


def test_web_unit_takes_the_public_host_from_release_sh_and_is_bounded() -> None:
    s = options(WEB)["Service"]
    # release.sh fills in nothing or exactly `--public-host <domain> --public-port 8788`. The
    # imprint path is fixed (Spec 014 AK10): the file may be missing, /impressum then answers 503.
    assert s["ExecStart"].endswith(
        "--host 127.0.0.1 --port 8787 --impressum /etc/llm-trading-bot/impressum.md"
        " __PUBLIC_HOST_ARG__"
    )
    assert 'IMPRESSUM_FILE="$ROOT/etc/llm-trading-bot/impressum.md"' in RELEASE.read_text(
        encoding="utf-8"
    )
    assert s["MemoryMax"] == "256M" and s["TasksMax"] == "64" and s["OOMScoreAdjust"] == "500"
    release = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    assert "--public-host $PUBLIC_HOST --public-port $PUBLIC_PORT" in release
    assert re.search(r"^PUBLIC_PORT=8788$", release, re.M)


def caddy_blocks() -> dict[str, list[str]]:
    """Top-level blocks of the Caddyfile without comments: "" is the global options block."""
    blocks: dict[str, list[str]] = {}
    name, depth = "", 0
    for raw in CADDYFILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if depth == 0:
            assert line.endswith("{"), line
            name, depth = line[:-1].strip(), 1
            assert name not in blocks, f"block {name!r} twice"
            blocks[name] = []
            continue
        depth += line.count("{") - line.count("}")
        if depth > 0:
            blocks[name].append(line)
    assert depth == 0
    return blocks


def test_caddyfile_has_exactly_the_domain_and_www() -> None:
    assert set(caddy_blocks()) == {"", "__PUBLIC_HOST__", "www.__PUBLIC_HOST__"}


def test_caddyfile_global_options_admin_socket_timeouts_and_protocols() -> None:
    g = caddy_blocks()[""]
    assert "admin unix//run/caddy/admin.sock" in g  # no admin port 2019 (AK9)
    assert "protocols h1 h2" in g  # no HTTP/3: only 80/tcp and 443/tcp are open
    for line in ("read_header 5s", "read_body 10s", "write 30s", "idle 60s"):
        assert line in g, line


def test_caddyfile_proxies_only_to_the_public_listener_with_limits() -> None:
    site = caddy_blocks()["__PUBLIC_HOST__"]
    assert "reverse_proxy 127.0.0.1:8788 {" in site
    assert "request_buffers 64KB" in site and "max_size 64KB" in site
    assert "max_conns_per_host 16" in site and "response_header_timeout 30s" in site
    text = "\n".join(line for lines in caddy_blocks().values() for line in lines)
    assert text.count("reverse_proxy") == 1 and "8787" not in text  # never the tunnel port


def test_caddyfile_www_redirects_permanently_to_the_domain() -> None:
    www = caddy_blocks()["www.__PUBLIC_HOST__"]
    assert "redir https://__PUBLIC_HOST__{uri} permanent" in www
    assert not any(line.startswith("reverse_proxy") for line in www)


@pytest.mark.parametrize("site", ["__PUBLIC_HOST__", "www.__PUBLIC_HOST__"])
def test_caddyfile_sends_hsts_and_no_server_header(site: str) -> None:
    block = caddy_blocks()[site]
    assert 'Strict-Transport-Security "max-age=31536000"' in block  # no preload
    assert "-Server" in block


@pytest.mark.parametrize("site", ["__PUBLIC_HOST__", "www.__PUBLIC_HOST__"])
def test_caddyfile_error_responses_keep_hsts_and_hide_the_server(site: str) -> None:
    block = caddy_blocks()[site]
    start = block.index("handle_errors {")
    errors = block[start : start + 6]
    assert 'Strict-Transport-Security "max-age=31536000"' in errors and "-Server" in errors
    assert 'respond "{err.status_code} {err.status_text}"' in errors


def test_caddyfile_keeps_no_access_log_and_changes_no_request_headers() -> None:
    for name, lines in caddy_blocks().items():
        for line in lines:
            assert not re.match(r"(log|header_up)\b", line), (name, line)
            # Only removing the app's Server header on the way out is allowed.
            if line.startswith("header_down"):
                assert line == "header_down -Server", (name, line)
            assert "trusted_proxies" not in line  # X-Forwarded-For only from Caddy itself


def _host_pattern(path: Path) -> str:
    found = re.search(r"^HOST_RE='([^']+)'$", path.read_text(encoding="utf-8"), re.M)
    assert found, path
    return found.group(1)


def test_caddy_domain_pattern_is_the_same_in_both_scripts() -> None:
    pattern = _host_pattern(DEPLOY / "release.sh")
    assert pattern == _host_pattern(REPO / "scripts" / "deploy.sh")
    assert pattern == r"^[a-z0-9.-]+\.[a-z]{2,}$"  # Spec 013 AK7
    for good in ("example.com", "bot.example.co.uk", "a-b.example.test"):
        assert re.search(pattern, good), good
    for bad in ("Example.com", "example", "exa mple.com", "example.com;id", "example.c0m", ""):
        assert not re.search(pattern, bad), bad


def test_caddy_is_validated_in_ci_from_the_official_repository() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check = (REPO / "scripts" / "check-units.sh").read_text(encoding="utf-8")
    release = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    repo = "https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main"
    assert repo in ci and repo in release
    assert ci.index("install --no-install-recommends caddy") < ci.index("scripts/check-units.sh")
    assert "caddy validate --adapter caddyfile --config" in check
    assert '"$CADDY" validate --adapter caddyfile --config' in release
