"""Spec 014 AK6/AK10 (ADR-0025): the privacy notice and the server files say the same.

The notice names periods the server must enforce. These tests read them from the files that
enforce them (journald, Caddyfile, release.sh), so changing one without the other fails here. The
web unit's command line goes through the real CLI parser, so a flag the release's CLI does not know
cannot reach the server.
"""

import configparser
import re
import shlex
from pathlib import Path

import pytest

from llm_trading_bot import cli
from llm_trading_bot.web.datenschutz import TEXT

REPO = Path(__file__).parent.parent
DEPLOY = REPO / "deploy"
RELEASE = (DEPLOY / "release.sh").read_text(encoding="utf-8")
DAY = 86400


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(day|d|h|s)?", value.strip())
    assert match, value
    return int(match[1]) * {"day": DAY, "d": DAY, "h": 3600, "s": 1, None: 1}[match[2]]


def _journal(text: str) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return dict(parser["Journal"])


def _shell_constant(name: str) -> str:
    found = re.search(rf"^{name}='([^']*)'", RELEASE, re.M | re.S)
    assert found, name
    return found[1]


def _days_in_text(journal: dict[str, str]) -> int:
    total = _seconds(journal["MaxRetentionSec"]) + _seconds(journal["MaxFileSec"])
    assert total % DAY == 0
    return total // DAY


def test_datenschutz_matches_the_namespace_journal() -> None:
    """Caddy's warnings (with IP addresses) live in the namespace: retention plus one file."""
    journal = _journal((DEPLOY / "journald@llm-trading-bot.conf").read_text(encoding="utf-8"))
    days = _days_in_text(journal)
    assert f"höchstens {days} Tage" in TEXT
    assert journal["ForwardToSyslog"] == "no"  # no second copy with syslog's longer rotation


def test_datenschutz_matches_the_system_journal() -> None:
    """Older entries (before 014, kernel lines) age out of the system journal just as fast."""
    namespace = _journal((DEPLOY / "journald@llm-trading-bot.conf").read_text(encoding="utf-8"))
    system = _journal(_shell_constant("SYSTEM_JOURNALD_TEXT"))
    assert _days_in_text(system) == _days_in_text(namespace)


def test_datenschutz_matches_caddy_logging_into_the_namespace() -> None:
    drop_in = _shell_constant("CADDY_DROPIN_TEXT")
    assert re.search(r"^LogNamespace=llm-trading-bot$", drop_in, re.M)
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in caddyfile.splitlines())
    assert not re.search(r"^\s*log\b", code, re.M)  # no access log
    assert "kein Zugriffsprotokoll" in TEXT or "keine Zugriffsprotokolle" in TEXT


def test_datenschutz_matches_lets_encrypt_without_contact_data() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in caddyfile.splitlines())
    # Only the domain goes to the CA: no account e-mail, no other CA (ZeroSSL would need an e-mail).
    assert not re.search(r"^\s*(email|acme_ca|acme_dns|cert_issuer)\b", code, re.M)
    assert "Let's Encrypt" in TEXT and "Certificate-Transparency" in TEXT


def test_datenschutz_matches_hsts() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    ages = set(re.findall(r'Strict-Transport-Security "max-age=(\d+)"', caddyfile))
    assert ages == {str(365 * DAY)}
    assert "für 1 Jahr" in TEXT


def test_datenschutz_matches_the_firewall_without_log() -> None:
    public = RELEASE[RELEASE.index("cmd_public() {") : RELEASE.index("cmd_public_off() {")]
    assert re.search(r"^\s+ufw_logging_off$", public, re.M)
    assert '"$UFW" logging off' in RELEASE
    assert "protokolliert keine Verbindungen" in TEXT


def _web_args(public_host: str | None) -> list[str]:
    """The web unit's ExecStart rendered like release.sh render_units, without the binary."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string((DEPLOY / "llm-trading-bot-web.service").read_text(encoding="utf-8"))
    line = parser["Service"]["ExecStart"]
    public = "" if public_host is None else f"--public-host {public_host} --public-port 8788"
    line = line.replace("__PUBLIC_HOST_ARG__", public).replace("__APP_DIR__", "/opt/app")
    line = line.replace("__DATA_DIR__", "/var/lib/llm-trading-bot")
    assert "__" not in line, line
    words = shlex.split(line)
    assert words[0].endswith("/trading-bot")
    return words[1:]


@pytest.mark.parametrize("public_host", [None, "bot.example.test"], ids=["tunnel", "public"])
def test_web_unit_execstart_is_accepted_by_the_cli(
    public_host: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "serve", lambda data_dir, **kw: seen.append({"data": data_dir, **kw}))
    assert cli.main(_web_args(public_host)) == cli.EXIT_OK
    (kw,) = seen
    assert kw["impressum"] == Path("/etc/llm-trading-bot/impressum.md")
    assert kw["public_host"] == public_host and kw["host"] == "127.0.0.1" and kw["port"] == 8787
