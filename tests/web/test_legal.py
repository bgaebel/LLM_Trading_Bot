"""Spec 014 AK1-AK3, AK5, AK6 (ADR-0025): Impressum and privacy policy over real HTTP.

Two sockets like on the server (tunnel and the port only Caddy uses), as in test_public.py. The
binding tests read cookie names and lifetimes from real Set-Cookie headers and the lockout
numbers from auth.py, so the privacy policy cannot drift away from the code unnoticed.
"""

import http.client
import os
import re
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from urllib.parse import urlencode

import pytest

from llm_trading_bot.web import app as app_module
from llm_trading_bot.web import auth, legal
from llm_trading_bot.web.app import SECURITY_HEADERS, WebApp, make_server
from llm_trading_bot.web.auth import (
    DEVICE_SECONDS,
    FORGET_SECONDS,
    GLOBAL_FAILURES_PER_HOUR,
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    MAX_LOCKOUT_SECONDS,
    client_key,
)
from llm_trading_bot.web.datenschutz import TEXT
from llm_trading_bot.web.legal import MAX_IMPRESSUM_BYTES, read_impressum

DOMAIN = "bot.example.test"
PASSWORD = "ein-langes-Passwort-2026"
WRONG = "falsches-Passwort-2026"
IMPRESSUM = (
    "# Impressum\n\nAngaben gemäß § 5 DDG\n\n__OPERATOR_NAME__\nc/o __ADDRESS__\n\n"
    "E-Mail: kontakt@example.test\n"
)
LEGAL = ("/impressum", "/datenschutz")
FOOTER = ('<a href="/impressum">Impressum</a>', '<a href="/datenschutz">Datenschutz</a>')


@dataclass
class Reply:
    status: int
    headers: Message
    body: str

    @property
    def set_cookies(self) -> list[str]:
        return self.headers.get_all("Set-Cookie") or []


@dataclass
class Client:
    port: int
    host: str = DOMAIN
    ip: str = "203.0.113.10"
    cookies: dict[str, str] = field(default_factory=dict)

    def request(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": self.host, **(headers or {})}
        if self.ip and "X-Forwarded-For" not in h:
            h["X-Forwarded-For"] = self.ip
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = None
        if form is not None:
            body = urlencode(form)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        response = conn.getresponse()
        reply = Reply(response.status, response.msg, response.read().decode("utf-8"))
        conn.close()
        for cookie in reply.set_cookies:
            name, value = cookie.split(";", 1)[0].split("=", 1)
            if value:
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)
        return reply

    def token(self, path: str = "/login") -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', self.request("GET", path).body)
        assert match
        return match.group(1)

    def login(self, password: str = PASSWORD) -> Reply:
        return self.request("POST", "/login", {"password": password, "csrf": self.token()})


@dataclass
class Site:
    app: WebApp
    tunnel: int
    public: int
    impressum: Path

    def outside(self, ip: str = "203.0.113.10") -> Client:
        return Client(self.public, ip=ip)

    def inside(self) -> Client:
        return Client(self.tunnel, host=f"127.0.0.1:{self.tunnel}", ip="")

    def both(self) -> tuple[Client, Client]:
        return self.inside(), self.outside()


def _site(
    base: Path, *, password: bool = True, impressum: str | None = IMPRESSUM
) -> Iterator[Site]:
    path = base / "etc" / "impressum.md"
    path.parent.mkdir(parents=True)
    if impressum is not None:
        path.write_bytes(impressum.encode("utf-8"))
    data = base / "data"
    data.mkdir()
    app = WebApp(data, public_host=DOMAIN, key_check=lambda key: "", impressum=path)
    if password:
        app.passwords.set(PASSWORD)
        app.setup_code = None
    servers = [make_server(app, "127.0.0.1", 0, public=public) for public in (False, True)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Site(app, servers[0].server_address[1], servers[1].server_address[1], path)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Site]:
    yield from _site(tmp_path / "site")


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[Site]:
    """No password yet: the tunnel shows the setup, the public way is closed."""
    yield from _site(tmp_path / "fresh", password=False)


def _csrf(page: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match
    return match.group(1)


# ------------------------------------------------------------------ AK1: the routes


def _assert_plain_page(reply: Reply) -> None:
    assert reply.set_cookies == []
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"
    for name, value in SECURITY_HEADERS.items():
        assert reply.headers[name] == value, name
    assert reply.headers["Cache-Control"] == "no-store"
    assert "style=" not in reply.body and "<style" not in reply.body
    assert "<script" not in reply.body
    assert 'action="/logout"' not in reply.body


def test_legal_pages_answer_on_both_ways_without_session(site: Site) -> None:
    for client in site.both():
        impressum = client.request("GET", "/impressum")
        assert impressum.status == 200
        _assert_plain_page(impressum)
        assert "<h1>Impressum</h1>" in impressum.body
        assert '<a href="mailto:kontakt@example.test">kontakt@example.test</a>' in impressum.body
        datenschutz = client.request("GET", "/datenschutz")
        assert datenschutz.status == 200
        _assert_plain_page(datenschutz)
        assert "<h1>Datenschutzerklärung</h1>" in datenschutz.body
        assert "<h2>1. Verantwortlicher</h2>" in datenschutz.body
        assert client.cookies == {}


def test_legal_pages_before_setup(fresh: Site) -> None:
    inside, outside = fresh.both()
    for client in (inside, outside):
        for path in LEGAL:
            reply = client.request("GET", path)
            assert reply.status == 200, path
            _assert_plain_page(reply)
    # The gate itself is unchanged.
    assert inside.request("GET", "/").status == 303
    assert outside.request("GET", "/").status == 503


def test_legal_pages_logged_in_look_the_same(site: Site) -> None:
    client = site.outside()
    assert client.login().status == 303
    reply = client.request("GET", "/datenschutz")
    assert reply.status == 200
    _assert_plain_page(reply)  # no logout button, no cookie refreshed


def test_legal_pages_ignore_the_query(site: Site) -> None:
    assert site.outside().request("GET", "/impressum?x=<script>").status == 200


def test_legal_pages_refuse_post_with_405(site: Site, fresh: Site) -> None:
    for s in (site, fresh):
        for client in s.both():
            for path in LEGAL:
                reply = client.request("POST", path, {"x": "1"})
                assert reply.status == 405, path
                assert reply.headers["Allow"] == "GET"
                assert reply.set_cookies == []
                assert reply.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method", ["HEAD", "PUT", "DELETE", "OPTIONS"])
def test_legal_pages_other_methods_stay_501(site: Site, method: str) -> None:
    for client in site.both():
        for path in LEGAL:
            reply = client.request(method, path)
            assert reply.status == 501, (method, path)
            assert (
                reply.headers["Content-Security-Policy"]
                == SECURITY_HEADERS["Content-Security-Policy"]
            )


def test_legal_pages_keep_the_host_and_origin_checks(site: Site) -> None:
    for path in LEGAL:
        foreign = site.outside()
        foreign.host = "evil.example"
        assert foreign.request("GET", path).status == 421
        assert (
            site.inside().request("GET", path, headers={"X-Forwarded-For": "1.2.3.4"}).status == 421
        )
        cross = site.outside().request("POST", path, {}, headers={"Origin": "https://evil.example"})
        assert cross.status == 403


# ------------------------------------------------------------------ AK2: footer


def _assert_footer(reply: Reply, what: str) -> None:
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8", what
    for link in FOOTER:
        assert link in reply.body, what
    footer = reply.body[reply.body.index("<footer>") :]
    assert all(link in footer for link in FOOTER), what


def test_footer_links_on_every_html_page(site: Site, fresh: Site) -> None:
    seen: dict[str, int] = {}

    def check(what: str, reply: Reply, status: int) -> None:
        assert reply.status == status, (what, reply.status)
        _assert_footer(reply, what)
        seen[what] = status

    outside = site.outside("198.51.100.1")
    check("login", outside.request("GET", "/login"), 200)
    check("login wrong", outside.login(WRONG), 401)
    check(
        "login expired", outside.request("POST", "/login", {"password": PASSWORD, "csrf": "x"}), 403
    )
    for _ in range(MAX_FAILURES - 1):
        outside.login(WRONG)
    check("login locked", outside.login(PASSWORD), 429)
    owner = site.outside("203.0.113.10")
    assert owner.login().status == 303
    check("overview", owner.request("GET", "/"), 200)
    check("settings", owner.request("GET", "/settings"), 200)
    check("404", owner.request("GET", "/nope"), 404)
    check("impressum", owner.request("GET", "/impressum"), 200)
    check("datenschutz", owner.request("GET", "/datenschutz"), 200)
    site.impressum.unlink()
    check("impressum missing", owner.request("GET", "/impressum"), 503)

    check("setup", fresh.inside().request("GET", "/setup"), 200)
    check("setup closed", fresh.outside().request("GET", "/"), 503)
    assert len(seen) == 12


# ------------------------------------------------------------------ AK3: the Impressum file


def test_impressum_is_read_on_every_request(site: Site) -> None:
    client = site.outside()
    assert "__OPERATOR_NAME__" in client.request("GET", "/impressum").body
    site.impressum.write_bytes(b"# Impressum\n\nNeuer Inhalt\n")
    assert "Neuer Inhalt" in client.request("GET", "/impressum").body


def test_impressum_missing_is_503_without_path_or_reason(site: Site) -> None:
    site.impressum.unlink()
    for client in site.both():
        reply = client.request("GET", "/impressum")
        assert reply.status == 503
        _assert_plain_page(reply)
        assert "Impressum nicht hinterlegt" in reply.body
        for leak in (str(site.impressum), "impressum.md", "Errno", "No such file", "NotFound"):
            assert leak not in reply.body, leak


def test_impressum_without_a_path_is_503(tmp_path: Path) -> None:
    app = WebApp(tmp_path)
    response = app.handle("GET", "/impressum", {}, {})
    assert isinstance(response, app_module.Response)
    assert response.status == 503 and "Impressum nicht hinterlegt" in response.body
    assert response.cookies == []


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (b"", "empty"),
        (b" \n\t\r\n", "blank"),
        (b"\xef\xbb\xbf\n", "only a BOM"),
        (b"Impressum \xff\xfe", "invalid UTF-8"),
        (b"Impressum\x00", "NUL"),
        (b"a" * (MAX_IMPRESSUM_BYTES + 1), "too big"),
    ],
)
def test_impressum_unfit_file_is_503(site: Site, content: bytes, why: str) -> None:
    site.impressum.write_bytes(content)
    assert read_impressum(site.impressum) is None, why
    reply = site.outside().request("GET", "/impressum")
    assert reply.status == 503 and "Impressum nicht hinterlegt" in reply.body, why


def test_impressum_at_the_size_limit_is_shown(tmp_path: Path) -> None:
    path = tmp_path / "impressum.md"
    path.write_bytes(b"a" * MAX_IMPRESSUM_BYTES)
    assert read_impressum(path) == "a" * MAX_IMPRESSUM_BYTES


def test_impressum_bom_and_crlf_are_fine(tmp_path: Path) -> None:
    path = tmp_path / "impressum.md"
    path.write_bytes("﻿# Impressum\r\n\r\nÄrger & Co\r\n".encode())
    text = read_impressum(path)
    assert text is not None and not text.startswith("﻿")
    assert legal.render(text) == "<h1>Impressum</h1><p>Ärger &amp; Co</p>"


def test_impressum_directory_is_refused(site: Site) -> None:
    site.impressum.unlink()
    site.impressum.mkdir()
    assert read_impressum(site.impressum) is None
    assert site.outside().request("GET", "/impressum").status == 503


def test_impressum_missing_file_is_none(tmp_path: Path) -> None:
    assert read_impressum(tmp_path / "nope.md") is None
    assert read_impressum(tmp_path / "nope" / "impressum.md") is None


@pytest.mark.skipif(sys.platform == "win32", reason="O_NOFOLLOW and symlinks are POSIX")
def test_impressum_symlink_is_refused(site: Site, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.md"
    target.write_bytes(b"# Fremd\n\nnicht vom Upload\n")
    site.impressum.unlink()
    site.impressum.symlink_to(target)
    assert read_impressum(site.impressum) is None
    assert site.outside().request("GET", "/impressum").status == 503


def test_impressum_fifo_does_not_block(site: Site) -> None:
    if sys.platform == "win32":
        pytest.skip("FIFOs are POSIX")
    site.impressum.unlink()
    os.mkfifo(site.impressum)
    assert read_impressum(site.impressum) is None  # O_NONBLOCK: returns at once, not a file


# ------------------------------------------------------------------ AK5: what the policy says


def _sections() -> dict[str, str]:
    """'## N. Title' sections of TEXT by their number."""
    parts = TEXT.split("\n## ")[1:]
    return {p.split(".", 1)[0]: p for p in parts}


def _section(title: str) -> str:
    matches = [s for s in _sections().values() if s.split("\n", 1)[0].endswith(title)]
    assert len(matches) == 1, title
    return matches[0]


def test_datenschutz_has_the_short_policy_structure() -> None:
    assert TEXT.startswith("# Datenschutzerklärung\n")
    numbers = list(_sections())
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)]
    assert "Stand: 8. November 2026" in TEXT


def test_datenschutz_names_the_controller_by_the_impressum() -> None:
    section = _section("Verantwortlicher")
    assert "siehe Impressum" in section
    assert "Name und Anschrift stehen im Impressum" in section


def test_datenschutz_holds_no_personal_or_server_details() -> None:
    assert "@" not in TEXT and "http" not in TEXT and "www." not in TEXT
    assert "<domain>" not in TEXT and "example" not in TEXT
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", TEXT)  # no IPv4 address
    assert not re.search(r"\b[0-9a-f]{1,4}(:[0-9a-f]{0,4}){3,}", TEXT)  # no IPv6 address
    for detail in ("8787", "8788", "journal", "/etc/", "systemd", "Hetzner Cloud CX"):
        assert detail.lower() not in TEXT.lower(), detail
    assert not re.search(r"\bssh\b", TEXT, re.IGNORECASE)
    assert "<a " not in app_module.DATENSCHUTZ_HTML  # nothing to click on but the footer


def test_datenschutz_every_processing_has_purpose_basis_and_duration() -> None:
    for title in (
        "Hosting",
        "Aufruf der Website und Fehlerprotokoll",
        "Firewall",
        "Schutz vor Passwort-Raten",
        "Cookies",
        "HTTPS-Hinweis im Browser (HSTS)",
        "Zertifikat (Let's Encrypt)",
        "Kontakt per E-Mail",
    ):
        section = _section(title)
        for label in ("Zweck: ", "Rechtsgrundlage: ", "Speicherdauer: "):
            assert f"\n{label}" in section, (title, label)
        assert re.search(r"Art\. 6 Abs\. 1 lit\. [bf] DSGVO|§ 25 Abs\. 2 Nr\. 2 TDDDG", section)


def test_datenschutz_hosting() -> None:
    section = _section("Hosting")
    assert "Hetzner Online GmbH, Industriestr. 25, 91710 Gunzenhausen" in section
    assert "Rechenzentrum in Deutschland" in section
    assert "Auftragsverarbeitung (AVV)" in section and "Art. 28 DSGVO" in section


def test_datenschutz_caddy_error_log() -> None:
    section = _section("Aufruf der Website und Fehlerprotokoll")
    assert "Caddy" in section and "TLS" in section
    assert "kein Zugriffsprotokoll" in section
    for item in (
        "Zeitpunkt",
        "IP-Adresse",
        "aufgerufene Adresse samt Parametern",
        "User-Agent",
        "weitere Anfrage-Header",
        "Cookies sind darin geschwärzt",
    ):
        assert item in section, item
    assert "höchstens 15 Tage" in section  # the lead binds this to the journal files
    assert "Art. 6 Abs. 1 lit. f DSGVO" in section


def test_datenschutz_firewall_logs_nothing() -> None:
    assert "protokolliert keine Verbindungen" in _section("Firewall")


def test_datenschutz_login_protection_only_in_memory() -> None:
    section = _section("Schutz vor Passwort-Raten")
    assert "IP-Adresse (bei IPv6 das /64-Netz)" in section
    assert client_key("2001:db8:1:2::5").endswith("/64")  # what the code keeps
    assert "pseudonyme Geräte-ID" in section
    assert "nur im Arbeitsspeicher" in section
    assert "nie während einer laufenden Sperre" in section


def test_datenschutz_cookies_need_no_banner() -> None:
    section = _section("Cookies")
    assert "§ 25 Abs. 2 Nr. 2 TDDDG" in section and "unbedingt erforderlich" in section
    assert "Einwilligungsbanner ist deshalb nicht nötig" in section
    assert "Secure" in section and "HttpOnly" in section and "SameSite=Strict" in section
    assert (
        "Beim Abruf von Impressum, Datenschutzerklärung und der Übersicht ohne Anmeldung "
        "werden keine Cookies gesetzt" in section
    )


def test_datenschutz_hsts_is_no_cookie() -> None:
    section = _section("HTTPS-Hinweis im Browser (HSTS)")
    assert "für 1 Jahr" in section  # the lead binds this to the Caddyfile's max-age
    assert "kein Cookie und enthält keine Kennung" in section


def test_datenschutz_no_trackers_no_third_parties() -> None:
    overview = _section("Überblick")
    for item in (
        "keine Tracker",
        "keine externen Inhalte",
        "keine externen Schriften",
        "keine automatisierte Entscheidungsfindung",
        "nicht registrieren",
        "Nur der Betreiber kann sich anmelden",
        "Die Übersicht ist ohne Anmeldung lesbar",
        "keine Daten von Besuchern",
    ):
        assert item in overview, item
    nobody = _section("Keine Weitergabe an OpenAI oder Bitvavo")
    assert "weder an OpenAI noch an Bitvavo" in nobody
    assert "nur öffentliche Marktdaten" in nobody and "nur Marktdaten an OpenAI" in nobody
    certificate = _section("Zertifikat (Let's Encrypt)")
    assert "nur den Domainnamen" in certificate and "keine Daten von Besuchern" in certificate
    assert "Certificate-Transparency-Protokollen" in certificate


def test_datenschutz_email_via_migadu() -> None:
    section = _section("Kontakt per E-Mail")
    assert "im Impressum genannte E-Mail-Adresse" in section
    assert "Migadu-Mail GmbH, Rohnen 587, 9414 Schachen, Schweiz" in section
    assert "Angemessenheitsbeschluss" in section and "Art. 45 DSGVO" in section
    assert "Auftragsverarbeitung (AVV)" in section
    assert "gelöscht, sobald Ihre Anfrage erledigt ist" in section
    assert "Aufbewahrungspflichten" in section
    assert "lit. b DSGVO" in section


def test_datenschutz_rights_objection_and_complaint() -> None:
    rights = _section("Ihre Rechte")
    for article in ("15", "16", "17", "18", "20"):
        assert f"(Art. {article} DSGVO)" in rights, article
    assert "Widerspruchsrecht (Art. 21 DSGVO)" in rights
    complaint = _section("Beschwerderecht")
    assert "Art. 77 DSGVO" in complaint and "Aufsichtsbehörde" in complaint
    assert "Sitz" in complaint


def test_datenschutz_page_shows_the_text(site: Site) -> None:
    body = site.outside().request("GET", "/datenschutz").body
    assert app_module.DATENSCHUTZ_HTML in body
    assert legal.render(TEXT) == app_module.DATENSCHUTZ_HTML


# ------------------------------------------------------------------ AK6: text bound to the code


def german_duration(seconds: int) -> str:
    """How the policy writes a period: '60 Sekunden', '1 Stunde', '24 Stunden', '90 Tage'."""
    if seconds < 3600:
        return f"{seconds} Sekunden"
    if seconds % 86400 == 0 and seconds > 86400:
        return f"{seconds // 86400} Tage"
    assert seconds % 3600 == 0, seconds
    hours = seconds // 3600
    return "1 Stunde" if hours == 1 else f"{hours} Stunden"


def test_german_duration() -> None:
    assert [german_duration(s) for s in (60, 3600, 43200, 86400, 7776000)] == [
        "60 Sekunden",
        "1 Stunde",
        "12 Stunden",
        "24 Stunden",
        "90 Tage",
    ]


def _cookie_line(name: str) -> str:
    lines = [line for line in TEXT.split("\n") if line.startswith(f"- {name}:")]
    assert len(lines) == 1, name
    return lines[0]


def _public_set_cookies(site: Site) -> list[str]:
    """Every Set-Cookie a visitor of the public way can get: login page, login, logout."""
    client = site.outside()
    headers = list(client.request("GET", "/login").set_cookies)
    login = client.request("POST", "/login", {"password": PASSWORD, "csrf": _csrf_from(client)})
    assert login.status == 303
    headers += login.set_cookies
    csrf = _csrf(client.request("GET", "/settings").body)
    logout = client.request("POST", "/logout", {"csrf": csrf})
    assert logout.status == 303
    headers += logout.set_cookies
    for path in LEGAL:
        assert client.request("GET", path).set_cookies == []
    return headers


def _csrf_from(client: Client) -> str:
    return client.cookies["__Host-ltb_pre"]


def test_datenschutz_matches_cookies(site: Site) -> None:
    lifetimes: dict[str, int] = {}
    names: set[str] = set()
    for header in _public_set_cookies(site):
        name = header.split("=", 1)[0]
        names.add(name)
        attributes = [a.strip() for a in header.split(";")[1:]]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict"):
            assert flag in attributes, (name, flag)
        (max_age,) = [int(a.split("=", 1)[1]) for a in attributes if a.startswith("Max-Age=")]
        if max_age:  # Max-Age=0 is the logout clearing the session
            assert lifetimes.setdefault(name, max_age) == max_age, name
    assert (
        names
        == set(lifetimes)
        == {
            "__Host-ltb_pre",
            "__Host-ltb_session",
            "__Host-ltb_device",
        }
    )
    for name, seconds in lifetimes.items():
        # Anchored: "Dauer: 11 Stunden" must not pass for 1 hour.
        duration = re.escape(german_duration(seconds))
        assert re.search(rf"Dauer: {duration}\b", _cookie_line(name)), name
    # No other cookie name in the text: the tunnel's ltb_* concern only the operator.
    mentioned = set(re.findall(r"(?<![\w-])(?:__Host-)?ltb_\w+", TEXT))
    assert mentioned == names
    # The session cookie lives exactly as long as the session behind it.
    assert app_module.SESSION_COOKIE_SECONDS == auth.SESSION_SECONDS


def test_datenschutz_overview_without_login_sets_no_cookie(site: Site) -> None:
    # Spec 019 AK8 (ADR-0028): the policy says so - the real answers must agree.
    for client in site.both():
        for path in ("/", "/robots.txt"):
            reply = client.request("GET", path)
            assert reply.status == 200 and reply.set_cookies == [], path
        assert client.cookies == {}


def test_datenschutz_matches_lockout() -> None:
    section = _section("Schutz vor Passwort-Raten")
    assert f"Nach {MAX_FAILURES} Fehlversuche" in section
    assert f"für {german_duration(LOCKOUT_SECONDS)} gesperrt" in section
    assert re.search(rf"höchstens {re.escape(german_duration(MAX_LOCKOUT_SECONDS))}\b", section)
    assert f"Nach {GLOBAL_FAILURES_PER_HOUR} Fehlversuchen unbekannter Geräte" in section
    # The text says "innerhalb einer Stunde": the window of the global ceiling.
    assert auth.GLOBAL_WINDOW_SECONDS == 3600
    assert "unbekannter Geräte innerhalb einer Stunde" in section
    assert (
        f"\nSpeicherdauer: gelöscht {german_duration(FORGET_SECONDS)} nach dem letzten "
        "Fehlversuch bzw. nach dem Ende der Sperre"
    ) in section
    assert german_duration(FORGET_SECONDS) == "24 Stunden"


def test_datenschutz_matches_device_days() -> None:
    line = _cookie_line("__Host-ltb_device")
    assert f"Dauer: {german_duration(DEVICE_SECONDS)}, bei jeder Anmeldung erneuert" in line
    assert german_duration(DEVICE_SECONDS) == "90 Tage"
