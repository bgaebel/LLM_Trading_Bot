"""Spec 013 AK1-AK6 (ADR-0024): the public way through Caddy, over real HTTP on 127.0.0.1.

Two sockets like on the server: the tunnel port and the port only Caddy uses. Caddy itself is
not here; the tests send what it sends (the visitor's Host, X-Forwarded-For with the visitor's
address last).
"""

import hashlib
import http.client
import json
import re
import secrets
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import pytest

from llm_trading_bot.web.app import WebApp, make_handler, make_server
from llm_trading_bot.web.auth import GLOBAL_FAILURES_PER_HOUR, MAX_FAILURES

DOMAIN = "bot.example.test"
PASSWORD = "ein-langes-Passwort-2026"
WRONG = "falsches-Passwort-2026"


@dataclass
class Client:
    port: int
    host: str = DOMAIN
    ip: str = "203.0.113.10"
    cookies: dict[str, str] = field(default_factory=dict)
    set_cookies: list[str] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
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
        text = response.read().decode("utf-8")
        self.set_cookies = response.msg.get_all("Set-Cookie") or []
        for cookie in self.set_cookies:
            name, value = cookie.split(";", 1)[0].split("=", 1)
            if value:
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)
        conn.close()
        return response.status, text

    def token(self, path: str = "/login") -> str:
        _, page = self.request("GET", path)
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        assert match, page
        return match.group(1)

    def login(self, password: str = PASSWORD) -> tuple[int, str]:
        return self.request("POST", "/login", {"password": password, "csrf": self.token()})


@dataclass
class Site:
    app: WebApp
    tunnel: int
    public: int
    data: Path

    def outside(self, ip: str = "203.0.113.10") -> Client:
        return Client(self.public, ip=ip)

    def inside(self) -> Client:
        return Client(self.tunnel, host=f"127.0.0.1:{self.tunnel}", ip="")


def _start(app: WebApp, public: bool) -> tuple[int, object]:
    server = make_server(app, "127.0.0.1", 0, public=public)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], server


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Site]:
    app = WebApp(tmp_path, public_host=DOMAIN, key_check=lambda key: "")
    app.passwords.set(PASSWORD)
    app.setup_code = None
    tunnel, s1 = _start(app, public=False)
    public, s2 = _start(app, public=True)
    try:
        yield Site(app, tunnel, public, tmp_path)
    finally:
        for server in (s1, s2):
            server.shutdown()  # type: ignore[attr-defined]
            server.server_close()  # type: ignore[attr-defined]


# ------------------------------------------------------------------ AK1: the way in


def test_public_host_accepts_only_its_domain(site: Site) -> None:
    assert site.outside().request("GET", "/")[0] == 303
    for host in ("evil.example", f"{DOMAIN}:443", f"x.{DOMAIN}", "127.0.0.1", ""):
        client = site.outside()
        client.host = host
        assert client.request("GET", "/")[0] == 421, host
    shouting = site.outside()
    shouting.host = DOMAIN.upper()
    assert shouting.request("GET", "/")[0] == 303


def test_public_host_tunnel_refuses_the_domain_and_proxy_headers(site: Site) -> None:
    inside = site.inside()
    assert inside.request("GET", "/")[0] == 303
    domain = site.inside()
    domain.host = DOMAIN
    assert domain.request("GET", "/")[0] == 421
    for header in ("X-Forwarded-For", "X-Forwarded-Host"):
        assert site.inside().request("GET", "/", headers={header: "1.2.3.4"})[0] == 421


def test_public_host_needs_a_valid_domain(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_handler(WebApp(tmp_path), public=True)
    for bad in ("Bot.Example.test", "localhost", "a b.de", "x.de:443", "x"):
        with pytest.raises(ValueError):
            WebApp(tmp_path, public_host=bad)


# ------------------------------------------------------------------ AK2: origin


@pytest.mark.parametrize(
    "origin",
    [f"http://{DOMAIN}", "https://evil.example", "http://127.0.0.1:8787", f"https://{DOMAIN}:8443"],
)
def test_public_origin_must_be_the_https_domain(site: Site, origin: str) -> None:
    client = site.outside()
    token = client.token()
    status, _ = client.request(
        "POST", "/login", {"password": PASSWORD, "csrf": token}, headers={"Origin": origin}
    )
    assert status == 403


def test_public_origin_of_the_domain_passes(site: Site) -> None:
    client = site.outside()
    token = client.token()
    status, _ = client.request(
        "POST",
        "/login",
        {"password": PASSWORD, "csrf": token},
        headers={"Origin": f"https://{DOMAIN}"},
    )
    assert status == 303


# ------------------------------------------------------------------ AK3: cookies


def test_public_cookies_are_host_prefixed_and_secure(site: Site) -> None:
    client = site.outside()
    client.token()
    (pre,) = client.set_cookies
    assert pre.startswith("__Host-ltb_pre=") and pre.endswith("; Secure")
    assert "HttpOnly" in pre and "SameSite=Strict" in pre and "Path=/" in pre
    assert "Domain" not in pre
    client.request(
        "POST", "/login", {"password": PASSWORD, "csrf": client.cookies["__Host-ltb_pre"]}
    )
    names = sorted(c.split("=", 1)[0] for c in client.set_cookies)
    assert names == ["__Host-ltb_device", "__Host-ltb_session"]
    assert all(c.endswith("; Secure") for c in client.set_cookies)
    assert client.request("GET", "/")[0] == 200


def test_public_cookies_ignore_the_tunnel_names(site: Site) -> None:
    inside = site.inside()
    inside.login()
    stolen = inside.cookies["ltb_session"]
    outside = site.outside()
    outside.cookies["ltb_session"] = stolen
    status, _ = outside.request("GET", "/")
    assert status == 303  # to the login: the tunnel cookie means nothing here


def test_public_cookies_tunnel_keeps_its_plain_ones(site: Site) -> None:
    inside = site.inside()
    inside.token()
    assert inside.set_cookies and not inside.set_cookies[0].endswith("Secure")
    assert inside.set_cookies[0].startswith("ltb_pre=")


def test_public_cookies_logout_clears_the_secure_session(site: Site) -> None:
    client = site.outside()
    client.login()
    page = client.request("GET", "/settings")[1]
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf
    status, _ = client.request("POST", "/logout", {"csrf": csrf.group(1)})
    assert status == 303
    (cleared,) = client.set_cookies
    assert cleared.startswith("__Host-ltb_session=;") and "Max-Age=0" in cleared
    assert cleared.endswith("; Secure")
    assert client.request("GET", "/")[0] == 303


# ------------------------------------------------------------------ AK4: lockout


def _lock(client: Client) -> None:
    for _ in range(MAX_FAILURES):
        assert client.login(WRONG)[0] == 401


def test_public_lockout_hits_the_guessing_client_only(site: Site) -> None:
    attacker = site.outside("198.51.100.66")
    _lock(attacker)
    status, page = attacker.login(PASSWORD)
    assert status == 429 and "Zu viele Fehlversuche" in page
    assert site.outside("203.0.113.10").login()[0] == 303  # another address
    assert site.inside().login()[0] == 303  # the tunnel is never locked from outside


def test_public_lockout_uses_the_last_forwarded_address(site: Site) -> None:
    # A client may send its own X-Forwarded-For; Caddy appends the real address last.
    for i in range(MAX_FAILURES):
        client = site.outside()
        client.ip = f"10.0.0.{i}, 198.51.100.77"
        assert client.login(WRONG)[0] == 401
    client = site.outside()
    client.ip = "10.9.9.9, 198.51.100.77"
    assert client.login(PASSWORD)[0] == 429


def test_public_lockout_global_ceiling_spares_the_owners_device(site: Site) -> None:
    owner = site.outside("203.0.113.10")
    assert owner.login()[0] == 303
    assert "__Host-ltb_device" in owner.cookies
    owner.cookies.pop("__Host-ltb_session")
    for i in range(GLOBAL_FAILURES_PER_HOUR):
        site.app.public_guard.failed(f"ip:192.0.2.{i % 250}-{i}", device=False)
    assert site.outside("192.0.2.200").login(PASSWORD)[0] == 429  # unknown device
    assert owner.login()[0] == 303  # the owner's device is not affected


# ------------------------------------------------------------------ AK5: no setup from outside


def test_public_setup_is_closed_without_a_password(tmp_path: Path) -> None:
    app = WebApp(tmp_path, public_host=DOMAIN)
    assert app.setup_code
    port, server = _start(app, public=True)
    try:
        for method, path in (("GET", "/"), ("GET", "/setup"), ("POST", "/setup")):
            client = Client(port)
            status, page = client.request(method, path, {} if method == "POST" else None)
            assert status == 503 and "nur per SSH-Tunnel" in page
            assert client.set_cookies == [] and app.setup_code not in page
            assert 'name="code"' not in page
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


# ------------------------------------------------------------------ AK6: password rule


def _old_short_password(site: Site, password: str) -> None:
    """A password set before ADR-0024 (8 characters were enough then)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=2**26)
    record = {"version": 1, "n": 2**14, "r": 8, "p": 1, "salt": salt.hex(), "hash": digest.hex()}
    (site.data / "web-auth.json").write_text(json.dumps(record), encoding="utf-8")


def test_public_password_short_one_is_refused_without_telling(site: Site) -> None:
    _old_short_password(site, "kurz-123")
    right = site.outside("203.0.113.20").login("kurz-123")
    wrong = site.outside("203.0.113.21").login("kurz-999")
    assert right[0] == wrong[0] == 401
    assert "mindestens 12 Zeichen" in right[1] and "mindestens 12 Zeichen" in wrong[1]
    assert "Passwort falsch" not in right[1] + wrong[1]
    assert site.inside().login("kurz-123")[0] == 303  # the tunnel keeps ADR-0015's 8


def test_change_password_through_the_settings(site: Site) -> None:
    client = site.inside()
    client.login()
    other = site.outside()
    other.login()

    def change(old: str, new: str, new2: str | None = None) -> tuple[int, str]:
        page = client.request("GET", "/settings")[1]
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        assert match
        form = {"old": old, "new": new, "new2": new if new2 is None else new2, "csrf": match[1]}
        return client.request("POST", "/settings/password", form)

    assert change(WRONG, "ganz-neues-Passwort-1")[0] == 401
    assert change(PASSWORD, "ganz-neues-Passwort-1", "anders-als-vorher-1")[0] == 400
    assert change(PASSWORD, "password1234")[0] == 400
    status, page = change(PASSWORD, "ganz-neues-Passwort-1")
    assert status == 200 and "Passwort geändert" in page
    assert other.request("GET", "/")[0] == 303  # the other session was logged out
    assert client.request("GET", "/")[0] == 200  # this one stays
    assert site.outside("203.0.113.30").login("ganz-neues-Passwort-1")[0] == 303
