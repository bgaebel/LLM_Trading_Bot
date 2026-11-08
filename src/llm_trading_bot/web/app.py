"""Routes and HTTP server of the web UI (Spec 006, ADR-0015; public way: Spec 013, ADR-0024).

``WebApp.handle`` is plain request -> response logic (tested without sockets); ``serve`` wraps it
in standard-library HTTP servers bound to 127.0.0.1 only: one for the SSH tunnel and, with a
public host, one that only Caddy talks to. The way in is known from the socket, not from headers.
"""

import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlsplit

from llm_trading_bot.llm.agents import LLM_DEFAULTS
from llm_trading_bot.llm.due import iso
from llm_trading_bot.llm.openai_client import check_key
from llm_trading_bot.paper.risk import PAUSE_FIELD, RESUME_FIELD, parse_resumes, release_problem
from llm_trading_bot.service.status import ServiceStatus, read_status, status_path
from llm_trading_bot.web import datenschutz, legal, overview, pages
from llm_trading_bot.web.auth import (
    DEVICE_SECONDS,
    PRUNE_SECONDS,
    SESSION_SECONDS,
    DeviceKeys,
    LoginGuard,
    PasswordStore,
    PublicGuard,
    Session,
    Sessions,
    client_key,
    new_setup_code,
    password_problem,
    same,
)
from llm_trading_bot.web.credentials import SecretsFile, mask
from llm_trading_bot.web.overview_page import overview_page
from llm_trading_bot.web.readonly import DbUnavailable, reading
from llm_trading_bot.web.script import JS

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
# ADR-0024: on the public way the __Host- prefix makes the browser refuse cookies that another
# host under the domain tries to plant (Secure, Path=/, no Domain attribute).
PUBLIC_COOKIE = "__Host-ltb_session"
PUBLIC_PRE_COOKIE = "__Host-ltb_pre"
DEVICE_COOKIE = "__Host-ltb_device"
# The session cookie lives as long as the session behind it; the privacy policy names both
# lifetimes and a test reads them from real Set-Cookie headers (ADR-0025).
SESSION_COOKIE_SECONDS = SESSION_SECONDS
PRE_COOKIE_SECONDS = 3600
PUBLIC_HOST = re.compile(r"[a-z0-9.-]+\.[a-z]{2,}")
DEFAULT_PUBLIC_PORT = 8788
# Spec 014: readable without login on both ways, also before setup. Rendered once - the policy
# is a constant; the Impressum is read per request because it is replaced on the server.
LEGAL_PATHS = ("/impressum", "/datenschutz")
DATENSCHUTZ_HTML = legal.render(datenschutz.TEXT)
EXPIRED = "Formular abgelaufen – bitte erneut."
PASSWORD_RULE = "Das Passwort braucht mindestens 12 Zeichen und darf nicht leicht zu erraten sein."
PUBLIC_PASSWORD_RULE = (
    "Für den Zugang aus dem Internet braucht das Passwort mindestens 12 Zeichen und darf nicht "
    "leicht zu erraten sein. Bitte unter Einstellungen ändern (per SSH-Tunnel)."
)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # ADR-0028: the overview is readable without login, but not meant to be found.
    "X-Robots-Tag": "noindex, nofollow",
}
ROBOTS = "User-agent: *\nDisallow: /\n"
LIVE_HEADER = "X-Live-Update"
"""Sent by /app.js (script.py) with its update requests of the overview."""
PUBLIC_VIEW_SECONDS = 10.0
"""ADR-0028: without login the overview reads the database at most this often, for everyone."""
PUBLIC_PROBLEM = "Daten gerade nicht lesbar."
"""Without login no details: they name files, versions or exception types."""


@dataclass
class Response:
    status: int
    body: str = ""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)
    """One Set-Cookie header each."""


@dataclass(frozen=True)
class Access:
    """How a request came in: through the SSH tunnel (default) or from the internet via Caddy."""

    public: bool = False
    client: str = ""
    """Public way only: the client address Caddy put last into X-Forwarded-For."""

    @property
    def session_cookie(self) -> str:
        return PUBLIC_COOKIE if self.public else COOKIE

    @property
    def pre_cookie(self) -> str:
        return PUBLIC_PRE_COOKIE if self.public else PRE_COOKIE

    def cookie(self, name: str, value: str, max_age: int) -> str:
        secure = "; Secure" if self.public else ""
        return f"{name}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}"


TUNNEL = Access()


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
class OverviewParts:
    """What the overview shows apart from the clock: rendered per request, kept by the view
    without login for PUBLIC_VIEW_SECONDS (ADR-0028)."""

    view: overview.Overview | None
    problem: str
    trading: pages.Trading


def _aged(view: overview.Overview | None, seconds: float) -> overview.Overview | None:
    """The cached view as if read ``seconds`` later: ages move on, levels stay as read (they
    lag at most PUBLIC_VIEW_SECONDS at a threshold of minutes)."""
    if view is None or seconds <= 0:
        return view
    pulses = [
        p if p.age_seconds is None else replace(p, age_seconds=p.age_seconds + seconds)
        for p in view.pulses
    ]
    return replace(view, pulses=pulses)


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
        public_host: str | None = None,
        impressum: Path | None = None,
    ) -> None:
        if public_host is not None and not PUBLIC_HOST.fullmatch(public_host):
            raise ValueError(f"public host must be a lower-case domain: {public_host!r}")
        # Next to secrets.env, like paper-cycle expects it (Spec 007 AK10).
        self.db = db if db is not None else data_dir / "bot.db"
        self.wall_clock = wall_clock
        self.clock = clock
        self.public_view: tuple[float, float, OverviewParts] | None = None
        """(monotonic and wall time read, parts) of the overview without login (ADR-0028)."""
        self.signals = overview.SignalCache()
        """Shared by both views; only touched under ``self.lock``."""
        self.public_host = public_host
        self.impressum = impressum
        self.passwords = PasswordStore(data_dir / "web-auth.json")
        self.credentials = SecretsFile(data_dir / "secrets.env")
        self.sessions = Sessions(clock)
        self.guard = LoginGuard(clock)  # the tunnel's
        self.public_guard = PublicGuard(clock)  # ADR-0024: per client, doubling, global ceiling
        self.devices = DeviceKeys(data_dir / "web-device.key", wall_clock)
        self.key_check = key_check
        self.setup_code: str | None = None if self.passwords.is_set() else new_setup_code()
        # One request at a time through the app, for both ways in: sessions, lockouts and file
        # writes stay consistent.
        self.lock = threading.Lock()

    # -- helpers -----------------------------------------------------------------------------

    def _session(self, cookies: Mapping[str, str], access: Access) -> Session | None:
        return self.sessions.get(cookies.get(access.session_cookie))

    def _login(self, access: Access, cookies: Mapping[str, str]) -> Response:
        session = self.sessions.create()
        response = redirect("/")
        response.cookies.append(
            access.cookie(access.session_cookie, session.token, SESSION_COOKIE_SECONDS)
        )
        if access.public:
            # ADR-0024: this device logged in; the lockout of strangers spares it. Renewed on
            # every login, so a device in use never expires.
            response.cookies.append(
                access.cookie(DEVICE_COOKIE, self.devices.issue(), DEVICE_SECONDS)
            )
        return response

    def _public(
        self,
        build: Callable[..., str],
        cookies: Mapping[str, str],
        message: str = "",
        status: int = 200,
        access: Access = TUNNEL,
    ) -> Response:
        """Login and setup pages carry a pre-session CSRF token (double-submit cookie): the
        SameSite=Strict cookie never travels with a cross-site form post."""
        token = cookies.get(access.pre_cookie) or secrets.token_urlsafe(24)
        page = build(message, csrf=token)
        return Response(
            status, page, cookies=[access.cookie(access.pre_cookie, token, PRE_COOKIE_SECONDS)]
        )

    @staticmethod
    def _pre_ok(form: Form, cookies: Mapping[str, str], access: Access = TUNNEL) -> bool:
        expected = cookies.get(access.pre_cookie, "")
        return bool(expected) and same(_one(form, "csrf"), expected)

    def _status(self) -> ServiceStatus | None:
        try:
            return read_status(status_path(self.db))
        except (OSError, ValueError, TypeError):
            return None

    def _trading(self, values: Mapping[str, str], *, depots: bool = True) -> pages.Trading:
        """Requested (secrets.env) and applied (status file) pause, and the stopped depots."""
        status = self._status()
        requested = values.get(PAUSE_FIELD, "") == "1"
        applied = None if status is None else status.trading_paused
        source = "" if status is None else status.pause_source
        if not depots:
            return pages.Trading(requested, applied, source)
        resumes, _bad = parse_resumes(values.get(RESUME_FIELD, ""))
        now = self.wall_clock()
        try:
            with reading(self.db) as con:
                rows = [d for d in overview.depots(con) if d.halted]
                # Pending = the service will act on it: same check, read only. An expired or
                # used-up request shows the button again instead of locking the depot.
                pending = {
                    d.agent
                    for d in rows
                    if d.agent in resumes
                    and not release_problem(con, d.agent, resumes[d.agent], now)
                }
        except DbUnavailable as exc:
            return pages.Trading(requested, applied, source, problem=str(exc))
        except (sqlite3.Error, ValueError, ArithmeticError) as exc:
            problem = f"Datenbank nicht lesbar ({type(exc).__name__})."
            return pages.Trading(requested, applied, source, problem=problem)
        stopped = tuple(pages.Stopped(d.agent, d.stopped_at, d.agent in pending) for d in rows)
        return pages.Trading(requested, applied, source, stopped)

    def _overview(self, session: Session, live: bool = False) -> Response:
        parts = self._overview_parts(self.credentials.read())
        page = overview_page(
            parts.view,
            csrf=session.csrf,
            problem=parts.problem,
            now=self.wall_clock(),
            trading=pages.trading_note(parts.trading),
            live=live,
        )
        return Response(200, page)

    def _readable_values(self) -> Mapping[str, str] | None:
        """secrets.env when the overview may be shown without login (ADR-0028), else None;
        unreadable counts as switched off (fail closed)."""
        try:
            values = self.credentials.read()
        except (OSError, UnicodeDecodeError):
            return None
        return values if pages.public_overview_on(values) else None

    def _anonymous_overview(self, values: Mapping[str, str], live: bool = False) -> Response:
        """ADR-0028: read only, no form, no token, no cookie; the database at most every
        PUBLIC_VIEW_SECONDS, the clock of the page and the ages on every request."""
        at, now = self.clock(), self.wall_clock()
        cached = self.public_view
        if cached is None or not 0 <= at - cached[0] < PUBLIC_VIEW_SECONDS:
            cached = (at, now, self._overview_parts(values, public=True))
            self.public_view = cached
        _at, read_at, parts = cached
        page = overview_page(
            _aged(parts.view, max(0.0, now - read_at)),
            csrf=None,
            problem=PUBLIC_PROBLEM if parts.problem else "",
            now=now,
            trading=pages.public_trading_note(parts.trading),
            public=True,
            live=live,
        )
        return Response(200, page)

    def _overview_parts(self, values: Mapping[str, str], *, public: bool = False) -> OverviewParts:
        shares = {
            agent: pages.budget_value(
                values, f"LLM_DAILY_BUDGET_EUR_{agent.upper().replace('-', '_')}", default
            )
            for agent, (_model, default) in LLM_DEFAULTS.items()
        }
        now = self.wall_clock()
        trading = self._trading(values, depots=False)
        try:
            service = overview.service_pulse(read_status(status_path(self.db)), now)
        except (OSError, ValueError, TypeError) as exc:  # TypeError: a field of the wrong type
            # Without login no exception type: it describes internals (ADR-0028).
            problem = (
                "Status nicht lesbar" if public else f"Statusdatei unlesbar ({type(exc).__name__})"
            )
            service = overview.service_pulse(None, now, problem=problem)
        try:
            # ADR-0016: one short read-only snapshot per page view, never the WriterLock.
            with reading(self.db) as con:
                view = overview.load(con, now, shares, service=service, signals=self.signals)
        except DbUnavailable as exc:
            problem = str(exc)
        except (sqlite3.Error, ValueError, ArithmeticError) as exc:
            # Busy past the timeout or an odd stored value: a message, not an empty response.
            problem = f"Datenbank nicht lesbar ({type(exc).__name__})."
        else:
            return OverviewParts(view, "", trading)
        return OverviewParts(None, problem, trading)

    def _settings(
        self, session: Session, message: str = "", ok: bool = True, status: int = 200
    ) -> Response:
        values = self.credentials.read()
        page = pages.settings_page(
            values, csrf=session.csrf, message=message, ok=ok, trading=self._trading(values)
        )
        return Response(status, page)

    def _legal(self, method: str, path: str) -> Response:
        """Impressum and privacy policy: no session, no cookie, same headers as every page."""
        if method != "GET":
            return Response(
                405, "method not allowed", "text/plain; charset=utf-8", headers={"Allow": "GET"}
            )
        if path == "/datenschutz":
            return Response(200, pages.legal_page("Datenschutz", DATENSCHUTZ_HTML))
        text = None if self.impressum is None else legal.read_impressum(self.impressum)
        if text is None:
            # AK3: neither the path nor the reason - both would describe the server.
            return Response(503, pages.impressum_missing_page())
        return Response(200, pages.legal_page("Impressum", legal.render(text)))

    # -- routing -----------------------------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        cookies: Mapping[str, str],
        form: Form,
        access: Access = TUNNEL,
        *,
        live: bool = False,
    ) -> Response | KeyCheck:
        """A ``KeyCheck`` asks the caller to run ``key_check`` and then ``key_checked``. ``live``:
        the update request of /app.js, which only uses the changing parts of the overview."""
        if path == "/app.css" and method == "GET":
            return Response(200, pages.CSS, "text/css; charset=utf-8")
        if path == "/app.js" and method == "GET":
            # ADR-0019: holds no data, so like the stylesheet it needs no session.
            return Response(200, JS, "text/javascript; charset=utf-8")
        if path in LEGAL_PATHS:
            # Before the setup gate: a site without a password still has to name its operator.
            return self._legal(method, path)
        if path == "/robots.txt" and method == "GET":
            return Response(200, ROBOTS, "text/plain; charset=utf-8")
        if not self.passwords.is_set():
            if access.public:
                # ADR-0024: the setup code sits in the journal; never set a password from the
                # internet. No form, no cookie, no code.
                return Response(503, pages.setup_closed_page())
            return self._setup(method, path, form, cookies)
        session = self._session(cookies, access)
        if path == "/login":
            return self._login_route(method, form, session, cookies, access)
        if session is None and path == "/" and method == "GET":
            # ADR-0028: read only without login, as long as the owner has not switched it off.
            values = self._readable_values()
            if values is not None:
                return self._anonymous_overview(values, live)
        if session is None:
            return redirect("/login")
        if method == "POST" and not same(_one(form, "csrf"), session.csrf):
            return self._public(
                pages.login_page,
                cookies,
                "Sitzung ungültig – bitte erneut anmelden.",
                403,
                access,
            )
        if path == "/" and method == "GET":
            return self._overview(session, live)
        if path == "/setup" and method == "GET":
            return redirect("/")
        if path == "/logout" and method == "POST":
            self.sessions.drop(session.token)
            response = redirect("/login")
            response.cookies.append(access.cookie(access.session_cookie, "", 0))
            return response
        if path == "/settings" and method == "GET":
            return self._settings(session)
        if path == "/settings" and method == "POST":
            return self._save(session, form)
        if path == "/settings/test" and method == "POST":
            return self._test(session)
        if path == "/settings/pause" and method == "POST":
            return self._pause(session, form)
        if path == "/settings/resume" and method == "POST":
            return self._resume(session, form)
        if path == "/settings/public" and method == "POST":
            return self._public_switch(session, form)
        if path == "/settings/password" and method == "POST":
            return self._change_password(session, form, cookies, access)
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
            return self._public(pages.setup_page, cookies, PASSWORD_RULE, 400)
        self.setup_code = None
        self.guard.succeeded()
        return self._login(TUNNEL, cookies)

    def _guard_for(self, cookies: Mapping[str, str], access: Access) -> tuple[str, bool]:
        """Public way: the lockout key and whether this is a known device (ADR-0024)."""
        device = self.devices.verify(cookies.get(DEVICE_COOKIE))
        if device is not None:
            return f"device:{device}", True
        return f"ip:{client_key(access.client)}", False

    def _seconds_locked(self, cookies: Mapping[str, str], access: Access) -> int:
        if not access.public:
            return self.guard.seconds_locked()
        key, device = self._guard_for(cookies, access)
        return self.public_guard.seconds_locked(key, device=device)

    def _failed(self, cookies: Mapping[str, str], access: Access) -> None:
        if not access.public:
            self.guard.failed()
            return
        key, device = self._guard_for(cookies, access)
        self.public_guard.failed(key, device=device)

    def _succeeded(self, cookies: Mapping[str, str], access: Access) -> None:
        if not access.public:
            self.guard.succeeded()
            return
        key, _device = self._guard_for(cookies, access)
        self.public_guard.succeeded(key)

    def _login_route(
        self,
        method: str,
        form: Form,
        session: Session | None,
        cookies: Mapping[str, str],
        access: Access = TUNNEL,
    ) -> Response:
        if method != "POST":
            if session:
                return redirect("/")
            return self._public(pages.login_page, cookies, access=access)
        if not self._pre_ok(form, cookies, access):
            return self._public(pages.login_page, cookies, EXPIRED, 403, access)
        locked = self._seconds_locked(cookies, access)
        if locked:
            message = f"Zu viele Fehlversuche – {locked} s warten."
            return self._public(pages.login_page, cookies, message, 429, access)
        password = _one(form, "password")
        if access.public and password_problem(password, public=True):
            # ADR-0024: checked before the hash, so the answer never tells whether it was right.
            self._failed(cookies, access)
            return self._public(pages.login_page, cookies, PUBLIC_PASSWORD_RULE, 401, access)
        if not self.passwords.verify(password):
            self._failed(cookies, access)
            return self._public(pages.login_page, cookies, "Passwort falsch.", 401, access)
        self._succeeded(cookies, access)
        return self._login(access, cookies)

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

    def _pause(self, session: Session, form: Form) -> Response:
        """ADR-0021: the emergency stop; the service applies it from its next round on."""
        wanted = _one(form, "paused")
        if wanted not in ("0", "1"):
            return self._settings(session, "Ungültige Anfrage.", ok=False, status=400)
        try:
            self.credentials.set_paused(wanted == "1")
        except OSError as exc:
            return self._settings(
                session, f"Nicht gespeichert: {type(exc).__name__}", ok=False, status=500
            )
        if wanted == "1":
            return self._settings(session, "Pause gespeichert – greift in der nächsten Runde.")
        return self._settings(session, "Fortsetzen gespeichert – greift in der nächsten Runde.")

    def _public_switch(self, session: Session, form: Form) -> Response:
        """ADR-0028: the overview without login on or off; applies to the next request."""
        wanted = _one(form, "public")
        if wanted not in ("0", "1"):
            return self._settings(session, "Ungültige Anfrage.", ok=False, status=400)
        try:
            self.credentials.set_public_overview(wanted == "1")
        except OSError as exc:
            return self._settings(
                session, f"Nicht gespeichert: {type(exc).__name__}", ok=False, status=500
            )
        if wanted == "1":
            return self._settings(session, "Gespeichert – die Übersicht ist ohne Login lesbar.")
        return self._settings(session, "Gespeichert – die Übersicht verlangt wieder den Login.")

    def _resume(self, session: Session, form: Form) -> Response:
        """ADR-0021: ask the service (the only writer) to release a stopped depot."""
        agent = _one(form, "agent")
        trading = self._trading(self.credentials.read())
        if agent not in {s.agent for s in trading.stopped}:
            return self._settings(
                session, "Dieses Depot ist nicht im Risikostopp.", ok=False, status=400
            )
        try:
            self.credentials.request_resume(agent, iso(self.wall_clock()))
        except (OSError, ValueError) as exc:
            return self._settings(
                session, f"Nicht gespeichert: {type(exc).__name__}", ok=False, status=500
            )
        return self._settings(
            session, f"Freigabe für {agent} gespeichert – greift in der nächsten Runde."
        )

    def _change_password(
        self, session: Session, form: Form, cookies: Mapping[str, str], access: Access
    ) -> Response:
        """ADR-0024: new passwords follow the public rule; other sessions are logged out."""
        locked = self._seconds_locked(cookies, access)
        if locked:
            message = f"Zu viele Fehlversuche – {locked} s warten."
            return self._settings(session, message, ok=False, status=429)
        if not self.passwords.verify(_one(form, "old")):
            self._failed(cookies, access)
            return self._settings(session, "Altes Passwort falsch.", ok=False, status=401)
        new = _one(form, "new")
        if new != _one(form, "new2"):
            return self._settings(
                session, "Die neuen Passwörter stimmen nicht überein.", ok=False, status=400
            )
        try:
            self.passwords.set(new)
        except ValueError:
            return self._settings(session, PASSWORD_RULE, ok=False, status=400)
        self.sessions.drop_others(session.token)
        # Device cookies issued while the old password was valid (maybe to someone who knew it)
        # must not keep their lockout exemption.
        self.devices.rotate()
        return self._settings(
            session, "Passwort geändert. Andere Sitzungen und Geräte sind abgemeldet."
        )

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


def make_handler(app: WebApp, *, public: bool = False) -> type[BaseHTTPRequestHandler]:
    """``public``: the handler of the socket only Caddy talks to (ADR-0024)."""
    app_lock = app.lock
    if public and app.public_host is None:
        raise ValueError("a public handler needs the public host")

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
            for cookie in response.cookies:
                self.send_header("Set-Cookie", cookie)
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

        def _access(self) -> Access | None:
            """The way in, or None for a request that does not belong to this socket."""
            host = self.headers.get("Host", "")
            if public:
                # Only Caddy talks to this socket; it forwards the visitor's Host and appends
                # the visitor's address to X-Forwarded-For.
                if host.lower() != app.public_host:
                    return None
                forwarded = self.headers.get("X-Forwarded-For", "")
                return Access(public=True, client=forwarded.rsplit(",", 1)[-1].strip())
            # A foreign Host header means DNS rebinding or a misrouted request; proxy headers
            # mean a proxy that should have used the public socket.
            if not is_local_host(host) or any(
                self.headers.get(h) for h in ("X-Forwarded-For", "X-Forwarded-Host")
            ):
                return None
            return TUNNEL

        def _origin_ok(self, origin: str, host: str) -> bool:
            if public:
                return origin == f"https://{app.public_host}"
            netloc = urlsplit(origin).netloc
            return is_local_host(netloc) and same_port(netloc, host)

        def _dispatch(self, method: str) -> None:
            access = self._access()
            if access is None:
                self._plain(421, "wrong host")
                return
            host = self.headers.get("Host", "")
            form: dict[str, list[str]] = {}
            if method == "POST":
                # Cross-site posts (login lockout, guessing) are refused before anything else.
                # "null" comes from privacy contexts (sandbox, redirects) and proves nothing, so
                # Sec-Fetch-Site and the form tokens decide - refusing it would lock the owner out.
                origin = self.headers.get("Origin")
                if origin and origin != "null" and not self._origin_ok(origin, host):
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
            # /app.js marks its updates; without the 121 KB backdrop they stay small (Spec 019).
            live = self.headers.get(LIVE_HEADER) == "1"
            with app_lock:
                response = app.handle(method, path, cookies, form, access, live=live)
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


def make_server(app: WebApp, host: str, port: int, *, public: bool = False) -> ThreadingHTTPServer:
    # ADR-0015/0024: never bound to the network; from outside only through Caddy (public
    # socket) or an SSH tunnel.
    if host != LOCALHOST:
        raise ValueError(f"the web UI binds to {LOCALHOST} only, not {host!r}")
    server = ThreadingHTTPServer((host, port), make_handler(app, public=public))
    server.daemon_threads = True
    return server


def forget_loop(app: WebApp, stop: threading.Event, interval: float = PRUNE_SECONDS) -> None:
    """ADR-0025: addresses leave memory on time even if nobody visits the site any more - the
    lazy check on access alone would keep them until the next request."""
    while not stop.wait(interval):
        with app.lock:
            app.public_guard.prune()


def serve(
    data_dir: Path,
    *,
    port: int,
    out: TextIO,
    host: str = LOCALHOST,
    db: Path | None = None,
    public_host: str | None = None,
    public_port: int = DEFAULT_PUBLIC_PORT,
    impressum: Path | None = None,
) -> None:
    app = WebApp(data_dir, db=db, public_host=public_host, impressum=impressum)
    server = make_server(app, host, port)
    bound = server.server_address[1]
    print(
        f"Web UI: http://{LOCALHOST}:{bound}/  (data: {data_dir}, db: {app.db}, read-only)",
        file=out,
        flush=True,
    )
    public_server = None
    stop = threading.Event()
    if public_host is not None:
        public_server = make_server(app, host, public_port, public=True)
        print(
            f"Public: https://{public_host}/ through Caddy -> {LOCALHOST}:"
            f"{public_server.server_address[1]}",
            file=out,
            flush=True,
        )
        threading.Thread(target=public_server.serve_forever, daemon=True).start()
        threading.Thread(target=forget_loop, args=(app, stop), daemon=True).start()
    if app.setup_code:
        print(f"Einrichtungscode (einmalig): {app.setup_code}", file=out, flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        if public_server is not None:
            public_server.shutdown()
            public_server.server_close()
