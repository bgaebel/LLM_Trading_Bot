"""Spec 006 AK1-AK8: the web UI end to end over real HTTP on 127.0.0.1 (no external network)."""

import http.client
import re
import socket
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import pytest

from llm_trading_bot.web.app import COOKIE, PRE_COOKIE, WebApp, make_server, parse_cookies
from llm_trading_bot.web.auth import MAX_FAILURES
from llm_trading_bot.web.credentials import load_env_file

PASSWORD = "ein-langes-Passwort-2026"
KEY = "sk-test-not-a-real-key-0000abcd"
FormData = Mapping[str, str | list[str]]


@dataclass
class Browser:
    port: int
    cookies: dict[str, str] = field(default_factory=dict)

    def request(
        self,
        method: str,
        path: str,
        form: FormData | None = None,
        host: str | None = None,
        extra: Mapping[str, str] | None = None,
        cookie_header: str | None = None,
    ) -> tuple[int, dict[str, str], str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", **(extra or {})}
        if cookie_header is not None:
            headers["Cookie"] = cookie_header
        elif self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = None
        if form is not None:
            body = urlencode(form, doseq=True)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        response_headers = {k.lower(): v for k, v in response.getheaders()}
        cookie = response_headers.get("set-cookie", "")
        if cookie:
            name, value = cookie.split(";", 1)[0].split("=", 1)
            if value:
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)
        conn.close()
        return response.status, response_headers, text

    def token(self, path: str) -> str:
        _, _, page = self.request("GET", path)
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        assert match, page
        return match.group(1)

    def csrf(self) -> str:
        return self.token("/settings")

    def submit(self, path: str, form: FormData) -> tuple[int, dict[str, str], str]:
        """Like a browser: open the form page first (gets the token), then post it."""
        return self.request("POST", path, {**form, "csrf": self.token(path)})


@dataclass
class Running:
    app: WebApp
    browser: Browser
    data: Path
    checked: list[str]


@pytest.fixture
def running(tmp_path: Path) -> Iterator[Running]:
    checked: list[str] = []

    def key_check(key: str) -> str:
        checked.append(key)
        return "" if key == KEY else "AuthenticationError"

    app = WebApp(tmp_path, key_check=key_check)
    server = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Running(app, Browser(server.server_address[1]), tmp_path, checked)
    finally:
        server.shutdown()
        server.server_close()


def setup(r: Running) -> None:
    assert r.app.setup_code
    status, headers, _ = r.browser.submit(
        "/setup", {"code": r.app.setup_code, "password": PASSWORD, "password2": PASSWORD}
    )
    assert status == 303 and headers["location"] == "/settings"


def setup_form(r: Running, password2: str = PASSWORD, code: str | None = None) -> FormData:
    return {"code": code or r.app.setup_code or "", "password": PASSWORD, "password2": password2}


# ------------------------------------------------------------------ binding and headers


def test_server_refuses_any_address_but_localhost(tmp_path: Path) -> None:
    for host in ("0.0.0.0", "::", "192.168.1.10", "localhost"):
        with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
            make_server(WebApp(tmp_path), host, 0)


def test_every_response_has_the_security_headers(running: Running) -> None:
    for path in ("/", "/setup", "/app.css", "/nope"):
        _, headers, _ = running.browser.request("GET", path)
        assert "script-src 'none'" in headers["content-security-policy"]
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["cache-control"] == "no-store"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"


def test_errors_written_by_http_server_itself_have_the_headers_too(running: Running) -> None:
    status, headers, _ = running.browser.request("DELETE", "/settings")
    assert status == 501 and headers["x-frame-options"] == "DENY"


def test_foreign_host_header_is_refused(running: Running) -> None:
    status, _, _ = running.browser.request("GET", "/setup", host="evil.example:80")
    assert status == 421


@pytest.mark.parametrize("host", ["127.0.0.1:9999", "localhost:8788", "localhost"])
def test_any_local_port_works_for_an_ssh_tunnel(running: Running, host: str) -> None:
    assert running.browser.request("GET", "/setup", host=host)[0] == 200


def test_pages_contain_no_inline_script_or_style(running: Running) -> None:
    _, _, page = running.browser.request("GET", "/setup")
    assert "<script" not in page and "style=" not in page and "<style" not in page


@pytest.mark.parametrize("length", ["-1", "abc", "1e3"])
def test_bad_content_length_is_refused(running: Running, length: str) -> None:
    status, _, _ = running.browser.request(
        "POST", "/login", extra={"Content-Length": length}, cookie_header=""
    )
    assert status == 400


def test_too_large_body_is_refused(running: Running) -> None:
    status, _, _ = running.browser.request("POST", "/login", {"password": "x" * 20_000})
    assert status == 413


# ------------------------------------------------------------------ setup


def test_first_start_needs_the_setup_code(running: Running) -> None:
    status, headers, _ = running.browser.request("GET", "/settings")
    assert (status, headers["location"]) == (303, "/setup")
    assert running.browser.submit("/setup", setup_form(running, code="falsch"))[0] == 403
    short = {"code": running.app.setup_code or "", "password": "kurz", "password2": "kurz"}
    assert running.browser.submit("/setup", short)[0] == 400
    assert running.browser.submit("/setup", setup_form(running, PASSWORD + "x"))[0] == 400
    setup(running)
    assert running.app.setup_code is None
    assert PASSWORD not in (running.data / "web-auth.json").read_text(encoding="utf-8")
    # Setup is over: the route only leads to the settings now.
    status, headers, _ = running.browser.request("GET", "/setup")
    assert (status, headers["location"]) == (303, "/settings")


def test_setup_without_form_token_is_refused(running: Running) -> None:
    status, _, _ = running.browser.request("POST", "/setup", setup_form(running))
    assert status == 403 and not (running.data / "web-auth.json").exists()


def test_session_cookie_is_http_only_strict_and_uniquely_named(running: Running) -> None:
    _, headers, _ = running.browser.submit("/setup", setup_form(running))
    cookie = headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=") and COOKIE != "session"
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Max-Age=43200" in cookie


# ------------------------------------------------------------------ login, logout, cross-site


def test_login_logout_and_lockout(running: Running) -> None:
    setup(running)
    csrf = running.browser.csrf()
    assert running.browser.request("POST", "/logout", {"csrf": csrf})[0] == 303
    status, headers, _ = running.browser.request("GET", "/settings")
    assert (status, headers["location"]) == (303, "/login")
    assert running.browser.submit("/login", {"password": "falsch"})[0] == 401
    status, headers, _ = running.browser.submit("/login", {"password": PASSWORD})
    assert (status, headers["location"]) == (303, "/settings")
    assert running.browser.request("GET", "/settings")[0] == 200
    # Five wrong passwords lock the login, also for the right password.
    other = Browser(running.browser.port)
    for _ in range(MAX_FAILURES):
        other.submit("/login", {"password": "falsch"})
    status, _, page = other.submit("/login", {"password": PASSWORD})
    assert status == 429 and "warten" in page


def test_logout_invalidates_the_session_on_the_server(running: Running) -> None:
    setup(running)
    old = running.browser.cookies[COOKIE]
    running.browser.request("POST", "/logout", {"csrf": running.browser.csrf()})
    status, headers, _ = running.browser.request(
        "GET", "/settings", cookie_header=f"{COOKIE}={old}"
    )
    assert (status, headers["location"]) == (303, "/login")


def test_login_without_form_token_is_refused(running: Running) -> None:
    setup(running)
    status, _, _ = running.browser.request("POST", "/login", {"password": PASSWORD})
    assert status == 403


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}],
    ids=["origin", "fetch-site"],
)
def test_cross_site_posts_are_refused_and_do_not_count_as_failures(
    running: Running, headers: dict[str, str]
) -> None:
    setup(running)
    guest = Browser(running.browser.port)
    token = guest.token("/login")
    for _ in range(MAX_FAILURES + 1):
        status, _, _ = guest.request(
            "POST", "/login", {"password": "falsch", "csrf": token}, extra=headers
        )
        assert status == 403
    # No lockout was triggered from outside: the owner can still log in.
    assert guest.submit("/login", {"password": PASSWORD})[0] == 303


def test_same_origin_post_with_local_origin_header_is_accepted(running: Running) -> None:
    setup(running)
    guest = Browser(running.browser.port)
    token = guest.token("/login")
    status, _, _ = guest.request(
        "POST",
        "/login",
        {"password": PASSWORD, "csrf": token},
        extra={
            "Origin": f"http://localhost:{running.browser.port}",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert status == 303


def test_foreign_cookies_do_not_break_the_session(running: Running) -> None:
    setup(running)
    token = running.browser.cookies[COOKIE]
    header = f'other={{"a":1}}; theme=dark mode; a="b; {COOKIE}={token}'
    status, _, _ = running.browser.request("GET", "/settings", cookie_header=header)
    assert status == 200


def test_parse_cookies_is_tolerant() -> None:
    assert parse_cookies('x={"a":1}; theme=dark mode; ltb_session=abc; =bad; noeq') == {
        "x": '{"a":1}',
        "theme": "dark mode",
        "ltb_session": "abc",
    }
    assert parse_cookies(None) == {} and PRE_COOKIE.startswith("ltb_")


@pytest.mark.parametrize("path", ["/settings", "/settings/test", "/logout"])
def test_posts_without_a_session_change_nothing(running: Running, path: str) -> None:
    setup(running)
    stranger = Browser(running.browser.port)
    form: FormData = {"csrf": "x", "OPENAI_API_KEY": KEY, "agents": ["gpt-luna"]}
    status, headers, _ = stranger.request("POST", path, form)
    assert (status, headers["location"]) == (303, "/login")
    assert not (running.data / "secrets.env").exists() and running.checked == []


# ------------------------------------------------------------------ settings


def test_forms_without_csrf_token_are_refused(running: Running) -> None:
    setup(running)
    status, _, _ = running.browser.request("POST", "/settings", {"OPENAI_API_KEY": KEY})
    assert status == 403
    assert not (running.data / "secrets.env").exists()
    assert running.browser.request("POST", "/logout", {"csrf": "falsch"})[0] == 403


def test_key_is_saved_masked_and_never_shown_again(running: Running) -> None:
    setup(running)
    form: FormData = {
        "csrf": running.browser.csrf(),
        "OPENAI_API_KEY": KEY,
        "agents": ["gpt-luna", "gpt-sol"],
        "LLM_EFFORT": "low",
        "LLM_DAILY_BUDGET_EUR": "",
    }
    status, _, page = running.browser.request("POST", "/settings", form)
    assert status == 200 and "Gespeichert." in page
    assert KEY not in page and "…abcd" in page
    assert load_env_file(running.data / "secrets.env")["OPENAI_API_KEY"] == KEY
    _, _, page = running.browser.request("GET", "/settings")
    assert KEY not in page


@pytest.mark.parametrize("effort", ["none", "minimal", "xhigh", "max"])
def test_every_valid_effort_survives_a_save(running: Running, effort: str) -> None:
    setup(running)
    csrf = running.browser.csrf()
    base: FormData = {"csrf": csrf, "OPENAI_API_KEY": KEY, "agents": ["gpt-luna"]}
    running.browser.request("POST", "/settings", {**base, "LLM_EFFORT": effort})
    _, _, page = running.browser.request("GET", "/settings")
    assert f'<option value="{effort}" selected>' in page


def test_invalid_settings_are_explained_and_not_written(running: Running) -> None:
    setup(running)
    form: FormData = {
        "csrf": running.browser.csrf(),
        "OPENAI_API_KEY": "kein-key",
        "agents": ["gpt-luna"],
    }
    status, _, page = running.browser.request("POST", "/settings", form)
    assert status == 400 and "Nicht gespeichert" in page and "sk-" in page
    assert not (running.data / "secrets.env").exists()


def test_html_in_values_is_escaped(running: Running) -> None:
    setup(running)
    form: FormData = {
        "csrf": running.browser.csrf(),
        "OPENAI_API_KEY": KEY,
        "agents": ["gpt-luna"],
        "LLM_DAILY_BUDGET_EUR": "<script>x</script>",
    }
    _, _, page = running.browser.request("POST", "/settings", form)
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_connection_check_uses_the_stored_key_and_shows_no_key(running: Running) -> None:
    setup(running)
    csrf = running.browser.csrf()
    _, _, page = running.browser.request("POST", "/settings/test", {"csrf": csrf})
    assert "Kein API-Key gespeichert" in page and running.checked == []
    form: FormData = {"csrf": csrf, "OPENAI_API_KEY": KEY, "agents": ["gpt-luna"]}
    running.browser.request("POST", "/settings", form)
    _, _, page = running.browser.request("POST", "/settings/test", {"csrf": csrf})
    assert "in Ordnung" in page and KEY not in page and running.checked == [KEY]


def test_failed_connection_check_names_only_the_error_type(running: Running) -> None:
    setup(running)
    csrf = running.browser.csrf()
    other = "sk-test-another-fake-key-9999wxyz"
    running.browser.request(
        "POST", "/settings", {"csrf": csrf, "OPENAI_API_KEY": other, "agents": ["gpt-luna"]}
    )
    _, _, page = running.browser.request("POST", "/settings/test", {"csrf": csrf})
    assert "AuthenticationError" in page and other not in page


# ------------------------------------------------------------------ robustness


def test_restart_keeps_password_but_ends_sessions(tmp_path: Path) -> None:
    first = WebApp(tmp_path)
    assert first.setup_code
    first.passwords.set(PASSWORD)
    second = WebApp(tmp_path)
    assert second.setup_code is None and second.passwords.verify(PASSWORD)


def test_client_hanging_up_does_not_break_the_server(running: Running) -> None:
    sock = socket.create_connection(("127.0.0.1", running.browser.port), timeout=5)
    sock.sendall(f"GET /setup HTTP/1.1\r\nHost: 127.0.0.1:{running.browser.port}\r\n\r\n".encode())
    sock.close()  # hang up before reading the answer
    assert running.browser.request("GET", "/setup")[0] == 200


def test_silent_spare_connection_does_not_block_a_form_post(running: Running) -> None:
    """Browsers pre-open connections and send nothing on them; a POST must still be answered."""
    idle = socket.create_connection(("127.0.0.1", running.browser.port), timeout=5)
    try:
        status, _, _ = running.browser.submit("/setup", setup_form(running))
        assert status == 303
    finally:
        idle.close()
