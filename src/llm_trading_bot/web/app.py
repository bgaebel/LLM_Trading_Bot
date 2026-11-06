"""Routes and HTTP server of the local web UI (Spec 006, ADR-0015).

``WebApp.handle`` is plain request -> response logic (tested without sockets); ``serve`` wraps it
in a standard-library HTTP server bound to 127.0.0.1 only.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs

from llm_trading_bot.llm.openai_client import check_key
from llm_trading_bot.web import pages
from llm_trading_bot.web.auth import (
    LoginGuard,
    PasswordStore,
    Session,
    Sessions,
    new_setup_code,
    same,
)
from llm_trading_bot.web.credentials import SecretsFile

LOCALHOST = "127.0.0.1"
MAX_BODY = 16 * 1024
COOKIE = "session"
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


class WebApp:
    def __init__(
        self,
        data_dir: Path,
        *,
        key_check: Callable[[str], str] = check_key,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.passwords = PasswordStore(data_dir / "web-auth.json")
        self.credentials = SecretsFile(data_dir / "secrets.env")
        self.sessions = Sessions(clock)
        self.guard = LoginGuard(clock)
        self.key_check = key_check
        self.setup_code: str | None = None if self.passwords.is_set() else new_setup_code()

    # -- helpers -----------------------------------------------------------------------------

    def _session(self, cookie_header: str | None) -> Session | None:
        if not cookie_header:
            return None
        cookie: SimpleCookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get(COOKIE)
        return self.sessions.get(morsel.value if morsel else None)

    def _login(self) -> Response:
        session = self.sessions.create()
        cookie = f"{COOKIE}={session.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
        return redirect("/settings", **{"Set-Cookie": cookie})

    def _settings(
        self, session: Session, message: str = "", ok: bool = True, status: int = 200
    ) -> Response:
        page = pages.settings_page(
            self.credentials.read(), csrf=session.csrf, message=message, ok=ok
        )
        return Response(status, page)

    # -- routing -----------------------------------------------------------------------------

    def handle(self, method: str, path: str, cookie_header: str | None, form: Form) -> Response:
        if path == "/app.css" and method == "GET":
            return Response(200, pages.CSS, "text/css; charset=utf-8")
        if not self.passwords.is_set():
            return self._setup(method, path, form)
        session = self._session(cookie_header)
        if path == "/login":
            return self._login_route(method, form, session)
        if session is None:
            return redirect("/login")
        if method == "POST" and not same(_one(form, "csrf"), session.csrf):
            return Response(403, pages.login_page("Sitzung ungültig – bitte erneut anmelden."))
        if path in ("/", "/setup") and method == "GET":
            return redirect("/settings")
        if path == "/logout" and method == "POST":
            self.sessions.drop(session.token)
            return redirect("/login", **{"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"})
        if path == "/settings" and method == "GET":
            return self._settings(session)
        if path == "/settings" and method == "POST":
            return self._save(session, form)
        if path == "/settings/test" and method == "POST":
            return self._test(session)
        return Response(404, pages.login_page("Seite nicht gefunden."))

    def _setup(self, method: str, path: str, form: Form) -> Response:
        if path != "/setup":
            return redirect("/setup")
        if method != "POST":
            return Response(200, pages.setup_page())
        if self.guard.seconds_locked():
            return Response(
                429,
                pages.setup_page(f"Zu viele Versuche – {self.guard.seconds_locked()} s warten."),
            )
        if self.setup_code is None or not same(_one(form, "code"), self.setup_code):
            self.guard.failed()
            return Response(403, pages.setup_page("Einrichtungscode stimmt nicht."))
        password = _one(form, "password")
        if password != _one(form, "password2"):
            return Response(400, pages.setup_page("Die Passwörter stimmen nicht überein."))
        try:
            self.passwords.set(password)
        except ValueError:
            return Response(400, pages.setup_page("Das Passwort braucht mindestens 8 Zeichen."))
        self.setup_code = None
        self.guard.succeeded()
        return self._login()

    def _login_route(self, method: str, form: Form, session: Session | None) -> Response:
        if method != "POST":
            return redirect("/settings") if session else Response(200, pages.login_page())
        locked = self.guard.seconds_locked()
        if locked:
            return Response(429, pages.login_page(f"Zu viele Fehlversuche – {locked} s warten."))
        if not self.passwords.verify(_one(form, "password")):
            self.guard.failed()
            return Response(401, pages.login_page("Passwort falsch."))
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

    def _test(self, session: Session) -> Response:
        key = self.credentials.read().get("OPENAI_API_KEY")
        if not key:
            return self._settings(session, "Kein API-Key gespeichert.", ok=False)
        problem = self.key_check(key)
        if problem:
            return self._settings(session, f"Verbindung fehlgeschlagen: {problem}", ok=False)
        return self._settings(session, "Verbindung zu OpenAI in Ordnung (gpt-6-luna erreichbar).")


def make_handler(app: WebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "llm-trading-bot"
        sys_version = ""

        def _send(self, response: Response) -> None:
            body = response.body.encode("utf-8")
            self.send_response(response.status)
            for name, value in {**SECURITY_HEADERS, **response.headers}.items():
                self.send_header(name, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The browser gave up on this request (reload, closed tab): nothing to do.
                return

        def _dispatch(self, method: str) -> None:
            # A foreign Host header means DNS rebinding or a misrouted request: refuse it.
            port = self.server.server_port  # type: ignore[attr-defined]
            if self.headers.get("Host", "") not in {f"{LOCALHOST}:{port}", f"localhost:{port}"}:
                self._send(Response(421, "wrong host", "text/plain; charset=utf-8"))
                return
            form: dict[str, list[str]] = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._send(Response(413, "too large", "text/plain; charset=utf-8"))
                    return
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(raw, keep_blank_values=True)
            path = self.path.split("?", 1)[0]
            self._send(app.handle(method, path, self.headers.get("Cookie"), form))

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, format: str, *args: object) -> None:
            # Request lines only (method, path, status) - never bodies, cookies or headers.
            pass

    return Handler


def make_server(app: WebApp, host: str, port: int) -> HTTPServer:
    # ADR-0005/0014: never reachable from the network; on the server only through an SSH tunnel.
    if host != LOCALHOST:
        raise ValueError(f"the web UI binds to {LOCALHOST} only, not {host!r}")
    return HTTPServer((host, port), make_handler(app))


def serve(data_dir: Path, *, port: int, out: TextIO, host: str = LOCALHOST) -> None:
    app = WebApp(data_dir)
    server = make_server(app, host, port)
    bound = server.server_address[1]
    print(f"Web UI: http://{LOCALHOST}:{bound}/  (data: {data_dir})", file=out, flush=True)
    if app.setup_code:
        print(f"Einrichtungscode (einmalig): {app.setup_code}", file=out, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
