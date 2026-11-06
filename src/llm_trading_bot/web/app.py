"""Routes and HTTP server of the local web UI (Spec 006, ADR-0015).

``WebApp.handle`` is plain request -> response logic (tested without sockets); ``serve`` wraps it
in a standard-library HTTP server bound to 127.0.0.1 only.
"""

import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlsplit

from llm_trading_bot.llm.agents import LLM_DEFAULTS
from llm_trading_bot.llm.openai_client import check_key
from llm_trading_bot.service.status import read_status, status_path
from llm_trading_bot.web import overview, pages
from llm_trading_bot.web.auth import (
    LoginGuard,
    PasswordStore,
    Session,
    Sessions,
    new_setup_code,
    same,
)
from llm_trading_bot.web.credentials import SecretsFile, mask
from llm_trading_bot.web.overview_page import overview_page
from llm_trading_bot.web.readonly import DbUnavailable, reading

LOCALHOST = "127.0.0.1"
LOCAL_NAMES = ("127.0.0.1", "localhost")
# LEARNING(#web): Formular-POST hängt: einfädiger HTTPServer wartet auf leere Browser-Verbindung
# Browsers open spare connections that stay silent; they must not block real requests.
SOCKET_TIMEOUT = 10
MAX_BODY = 16 * 1024
LENGTH = re.compile(r"[0-9]{1,6}")
# Cookies on 127.0.0.1 are shared by every local app on any port: use names nobody else uses.
COOKIE = "ltb_session"
PRE_COOKIE = "ltb_pre"
EXPIRED = "Formular abgelaufen – bitte erneut."
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@dataclass
class Response:
    status: int
    body: str = ""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def redirect(location: str, **headers: str) -> Response:
    return Response(303, headers={"Location": location, **headers})


Form = Mapping[str, list[str]]


def _one(form: Form, name: str) -> str:
    values = form.get(name, [])
    return values[0] if values else ""


def parse_cookies(header: str | None) -> dict[str, str]:
    """Tolerant on purpose: other local apps may set cookies http.cookies cannot parse, and one
    odd cookie must not hide ours."""
    cookies: dict[str, str] = {}
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies.setdefault(name, value.strip())
    return cookies


def is_local_host(host: str) -> bool:
    """Host or Origin host must be 127.0.0.1/localhost; the port is free (SSH tunnel)."""
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    return name in LOCAL_NAMES


@dataclass(frozen=True)
class KeyCheck:
    """The connection test is due: ``key`` must be looked up, then ``WebApp.key_checked``."""

    session: Session
    key: str

    def __repr__(self) -> str:
        return f"KeyCheck(session=..., key={mask(self.key)!r})"


def same_port(origin_netloc: str, host: str) -> bool:
    """Cookies on 127.0.0.1 are shared by every port, so a page on another local port could plant
    the pre-session cookie and post a matching token: its Origin must name our port."""
    try:
        theirs = urlsplit(f"//{origin_netloc}").port or 80
        ours = urlsplit(f"//{host}").port or 80
    except ValueError:
        return False
    return theirs == ours


class WebApp:
    def __init__(
        self,
        data_dir: Path,
        *,
        db: Path | None = None,
        key_check: Callable[[str], str] = check_key,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        # Next to secrets.env, like paper-cycle expects it (Spec 007 AK10).
        self.db = db if db is not None else data_dir / "bot.db"
        self.wall_clock = wall_clock
        self.passwords = PasswordStore(data_dir / "web-auth.json")
        self.credentials = SecretsFile(data_dir / "secrets.env")
        self.sessions = Sessions(clock)
        self.guard = LoginGuard(clock)
        self.key_check = key_check
        self.setup_code: str | None = None if self.passwords.is_set() else new_setup_code()

    # -- helpers -----------------------------------------------------------------------------

    def _session(self, cookies: Mapping[str, str]) -> Session | None:
        return self.sessions.get(cookies.get(COOKIE))

    def _login(self) -> Response:
        session = self.sessions.create()
        cookie = f"{COOKIE}={session.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
        return redirect("/", **{"Set-Cookie": cookie})

    def _public(
        self,
        build: Callable[..., str],
        cookies: Mapping[str, str],
        message: str = "",
        status: int = 200,
    ) -> Response:
        """Login and setup pages carry a pre-session CSRF token (double-submit cookie): the
        SameSite=Strict cookie never travels with a cross-site form post."""
        token = cookies.get(PRE_COOKIE) or secrets.token_urlsafe(24)
        page = build(message, csrf=token)
        cookie = f"{PRE_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600"
        return Response(status, page, headers={"Set-Cookie": cookie})

    @staticmethod
    def _pre_ok(form: Form, cookies: Mapping[str, str]) -> bool:
        expected = cookies.get(PRE_COOKIE, "")
        return bool(expected) and same(_one(form, "csrf"), expected)

    def _overview(self, session: Session) -> Response:
        values = self.credentials.read()
        shares = {
            agent: pages.budget_value(
                values, f"LLM_DAILY_BUDGET_EUR_{agent.upper().replace('-', '_')}", default
            )
            for agent, (_model, default) in LLM_DEFAULTS.items()
        }
        now = self.wall_clock()
        try:
            service = overview.service_pulse(read_status(status_path(self.db)), now)
        except (OSError, ValueError) as exc:
            service = overview.service_pulse(
                None, now, problem=f"Statusdatei unlesbar ({type(exc).__name__})"
            )
        try:
            # ADR-0016: one short read-only snapshot per page view, never the WriterLock.
            with reading(self.db) as con:
                view = overview.load(con, now, shares, service=service)
        except DbUnavailable as exc:
            return Response(200, overview_page(None, csrf=session.csrf, problem=str(exc), now=now))
        return Response(200, overview_page(view, csrf=session.csrf, now=now))

    def _settings(
        self, session: Session, message: str = "", ok: bool = True, status: int = 200
    ) -> Response:
        page = pages.settings_page(
            self.credentials.read(), csrf=session.csrf, message=message, ok=ok
        )
        return Response(status, page)

    # -- routing -----------------------------------------------------------------------------

    def handle(
        self, method: str, path: str, cookies: Mapping[str, str], form: Form
    ) -> Response | KeyCheck:
        """A ``KeyCheck`` asks the caller to run ``key_check`` and then ``key_checked``."""
        if path == "/app.css" and method == "GET":
            return Response(200, pages.CSS, "text/css; charset=utf-8")
        if not self.passwords.is_set():
            return self._setup(method, path, form, cookies)
        session = self._session(cookies)
        if path == "/login":
            return self._login_route(method, form, session, cookies)
        if session is None:
            return redirect("/login")
        if method == "POST" and not same(_one(form, "csrf"), session.csrf):
            return self._public(
                pages.login_page, cookies, "Sitzung ungültig – bitte erneut anmelden.", 403
            )
        if path == "/" and method == "GET":
            return self._overview(session)
        if path == "/setup" and method == "GET":
            return redirect("/")
        if path == "/logout" and method == "POST":
            self.sessions.drop(session.token)
            return redirect("/login", **{"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"})
        if path == "/settings" and method == "GET":
            return self._settings(session)
        if path == "/settings" and method == "POST":
            return self._save(session, form)
        if path == "/settings/test" and method == "POST":
            return self._test(session)
        return self._settings(session, "Seite nicht gefunden.", ok=False, status=404)

    def _setup(self, method: str, path: str, form: Form, cookies: Mapping[str, str]) -> Response:
        if path != "/setup":
            return redirect("/setup")
        if method != "POST":
            return self._public(pages.setup_page, cookies)
        if not self._pre_ok(form, cookies):
            return self._public(pages.setup_page, cookies, EXPIRED, 403)
        locked = self.guard.seconds_locked()
        if locked:
            return self._public(
                pages.setup_page, cookies, f"Zu viele Versuche – {locked} s warten.", 429
            )
        if self.setup_code is None or not same(_one(form, "code"), self.setup_code):
            self.guard.failed()
            return self._public(pages.setup_page, cookies, "Einrichtungscode stimmt nicht.", 403)
        password = _one(form, "password")
        if password != _one(form, "password2"):
            return self._public(
                pages.setup_page, cookies, "Die Passwörter stimmen nicht überein.", 400
            )
        try:
            self.passwords.set(password)
        except ValueError:
            return self._public(
                pages.setup_page, cookies, "Das Passwort braucht mindestens 8 Zeichen.", 400
            )
        self.setup_code = None
        self.guard.succeeded()
        return self._login()

    def _login_route(
        self, method: str, form: Form, session: Session | None, cookies: Mapping[str, str]
    ) -> Response:
        if method != "POST":
            return redirect("/") if session else self._public(pages.login_page, cookies)
        if not self._pre_ok(form, cookies):
            return self._public(pages.login_page, cookies, EXPIRED, 403)
        locked = self.guard.seconds_locked()
        if locked:
            return self._public(
                pages.login_page, cookies, f"Zu viele Fehlversuche – {locked} s warten.", 429
            )
        if not self.passwords.verify(_one(form, "password")):
            self.guard.failed()
            return self._public(pages.login_page, cookies, "Passwort falsch.", 401)
        self.guard.succeeded()
        return self._login()

    def _save(self, session: Session, form: Form) -> Response:
        try:
            self.credentials.update(
                {k: v[0] for k, v in form.items() if v},
                agents=list(form.get("agents", [])),
                clear_key=_one(form, "clear_key") == "1",
            )
        except ValueError as exc:
            # Messages come from our own checks and never contain the key value.
            return self._settings(session, f"Nicht gespeichert: {exc}", ok=False, status=400)
        return self._settings(session, "Gespeichert.")

    def _test(self, session: Session) -> Response | KeyCheck:
        key = self.credentials.read().get("OPENAI_API_KEY")
        if not key:
            return self._settings(session, "Kein API-Key gespeichert.", ok=False)
        return KeyCheck(session, key)

    def key_checked(self, session: Session, problem: str) -> Response:
        """Second half of the connection test, after ``key_check`` ran outside the app lock."""
        if problem:
            return self._settings(session, f"Verbindung fehlgeschlagen: {problem}", ok=False)
        return self._settings(session, "Verbindung zu OpenAI in Ordnung (gpt-6-luna erreichbar).")


def make_handler(app: WebApp) -> type[BaseHTTPRequestHandler]:
    # One request at a time through the app: sessions, lockout and file writes stay consistent.
    app_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "llm-trading-bot"
        sys_version = ""
        timeout = SOCKET_TIMEOUT

        def end_headers(self) -> None:
            # Here and not in _send: also responses http.server writes itself (400, 414, 501 ...)
            # carry the security headers.
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            super().end_headers()

        def _send(self, response: Response) -> None:
            body = response.body.encode("utf-8")
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The browser gave up on this request (reload, closed tab): nothing to do.
                return

        def _plain(self, status: int, text: str) -> None:
            self._send(Response(status, text, "text/plain; charset=utf-8"))

        def _dispatch(self, method: str) -> None:
            # A foreign Host header means DNS rebinding or a misrouted request: refuse it.
            host = self.headers.get("Host", "")
            if not is_local_host(host):
                self._plain(421, "wrong host")
                return
            form: dict[str, list[str]] = {}
            if method == "POST":
                # Cross-site posts (login lockout, guessing) are refused before anything else.
                # "null" comes from privacy contexts (sandbox, redirects) and proves nothing, so
                # Sec-Fetch-Site and the form tokens decide - refusing it would lock the owner out.
                origin = self.headers.get("Origin")
                if origin and origin != "null":
                    netloc = urlsplit(origin).netloc
                    if not is_local_host(netloc) or not same_port(netloc, host):
                        self._plain(403, "cross-site request")
                        return
                if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                    self._plain(403, "cross-site request")
                    return
                raw_length = self.headers.get("Content-Length") or "0"
                # isdigit() also accepts digits like "²" that int() rejects: ASCII only.
                if not LENGTH.fullmatch(raw_length):
                    self._plain(400, "bad length")
                    return
                length = int(raw_length)
                if length > MAX_BODY:
                    self._plain(413, "too large")
                    return
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(raw, keep_blank_values=True)
            path = self.path.split("?", 1)[0]
            cookies = parse_cookies(self.headers.get("Cookie"))
            with app_lock:
                response = app.handle(method, path, cookies, form)
            if isinstance(response, KeyCheck):
                # The OpenAI lookup takes up to 15 s: outside the lock, so the overview and the
                # other pages keep answering meanwhile.
                problem = app.key_check(response.key)
                with app_lock:
                    response = app.key_checked(response.session, problem)
            self._send(response)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, format: str, *args: object) -> None:
            # Nothing is logged: request lines could still carry paths or odd input.
            pass

    return Handler


def make_server(app: WebApp, host: str, port: int) -> ThreadingHTTPServer:
    # ADR-0005/0015: never reachable from the network; on the server only through an SSH tunnel.
    if host != LOCALHOST:
        raise ValueError(f"the web UI binds to {LOCALHOST} only, not {host!r}")
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server


def serve(
    data_dir: Path, *, port: int, out: TextIO, host: str = LOCALHOST, db: Path | None = None
) -> None:
    app = WebApp(data_dir, db=db)
    server = make_server(app, host, port)
    bound = server.server_address[1]
    print(
        f"Web UI: http://{LOCALHOST}:{bound}/  (data: {data_dir}, db: {app.db}, read-only)",
        file=out,
        flush=True,
    )
    if app.setup_code:
        print(f"Einrichtungscode (einmalig): {app.setup_code}", file=out, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
