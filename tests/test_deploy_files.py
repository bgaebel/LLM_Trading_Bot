"""Spec 008 AK10, Spec 009 AK4, Spec 013 AK7: the server files in deploy/ - options, placeholders,
no real data.

`scripts/check-units.sh` lets systemd and caddy themselves verify them in the Linux CI; these tests
pin what the service relies on (ADR-0017, ADR-0024) and that the public repository holds no server
details. They run on every platform.
"""

import configparser
import re
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
REPO = DEPLOY.parent
PLACEHOLDERS = {
    "__SERVICE_USER__",
    "__APP_DIR__",
    "__DATA_DIR__",
    "__BACKUP_DIR__",
    "__PUBLIC_HOST_ARG__",
    "__PUBLIC_HOST__",
}


def options(path: Path) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text(encoding="utf-8"))
    return {section: dict(parser[section]) for section in parser.sections()}


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


def test_backup_unit_copies_read_only_into_the_backup_directory() -> None:
    s = options(BACKUP)["Service"]
    assert s["Type"] == "oneshot" and s["User"] == "__SERVICE_USER__"
    assert s["ExecStart"] == (
        "__APP_DIR__/.venv/bin/trading-bot backup --db __DATA_DIR__/bot.db"
        f" --dir __BACKUP_DIR__/daily --keep {KEEP}"
    )
    assert s["ReadWritePaths"] == "__DATA_DIR__ __BACKUP_DIR__"
    assert s["ProtectSystem"] == "strict" and s["NoNewPrivileges"] == "yes"
    assert s["UMask"] == "0077" and s["CapabilityBoundingSet"] == ""
    assert s["RestrictAddressFamilies"] == "AF_UNIX"  # a backup never needs the network
    assert "Install" not in options(BACKUP)  # started by the timer only


def test_backup_timer_runs_daily_before_the_reboot_window() -> None:
    t = options(TIMER)["Timer"]
    assert t["OnCalendar"] == "*-*-* 03:30:00 UTC" and t["Persistent"] == "true"
    assert t["Unit"] == BACKUP.name and int(t["RandomizedDelaySec"]) <= 600
    assert options(TIMER)["Install"]["WantedBy"] == "timers.target"


def test_journal_is_bounded() -> None:
    j = options(JOURNAL)["Journal"]
    assert j["SystemMaxUse"] == "200M" and j["MaxRetentionSec"] == "14day"


@pytest.mark.parametrize(
    "path",
    [SERVICE, WEB, JOURNAL, BACKUP, TIMER, CADDYFILE],
    ids=["service", "web", "journal", "backup", "timer", "caddyfile"],
)
def test_no_server_details_only_placeholders(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert set(re.findall(r"__[A-Z_]+__", text)) <= PLACEHOLDERS
    # CLAUDE.md: the repository is public - no IPs (but 127.0.0.1), homes or real users.
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {"127.0.0.1"}
    assert "/home/" not in text and "/root" not in text


def test_web_unit_takes_the_public_host_from_release_sh_and_is_bounded() -> None:
    s = options(WEB)["Service"]
    # release.sh fills in nothing or exactly `--public-host <domain> --public-port 8788`.
    assert s["ExecStart"].endswith("--host 127.0.0.1 --port 8787 __PUBLIC_HOST_ARG__")
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
