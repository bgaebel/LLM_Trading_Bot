"""Spec 019 (ADR-0028): the overview is readable without login - read only, nothing else is."""

import http.client
import re
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from fakes import ScenarioClient
from golden_scenarios import NOW
from llm_fakes import KEY, FakeModel
from llm_trading_bot.core import parse_mode
from llm_trading_bot.llm.agents import load_config
from llm_trading_bot.paper.cycle import paper_cycle
from llm_trading_bot.paper.db import open_db
from llm_trading_bot.paper.execution import BUYHOLD, Costs
from llm_trading_bot.service.status import status_path
from llm_trading_bot.web import overview
from llm_trading_bot.web.app import (
    COOKIE,
    PUBLIC_PROBLEM,
    PUBLIC_VIEW_SECONDS,
    TUNNEL,
    Access,
    Response,
    WebApp,
    make_server,
)
from llm_trading_bot.web.credentials import PUBLIC_OVERVIEW_FIELD
from llm_trading_bot.web.readonly import reading

DOMAIN = "bot.example.test"
PASSWORD = "ein-langes-Passwort-2026"
OUTSIDE = Access(public=True, client="203.0.113.10")
WAYS = pytest.mark.parametrize("access", [TUNNEL, OUTSIDE], ids=["tunnel", "public"])


@dataclass
class Clock:
    at: float

    def __call__(self) -> float:
        return self.at


@dataclass
class Bot:
    app: WebApp
    data: Path
    clock: Clock
    wall: Clock

    def get(
        self, path: str = "/", access: Access = TUNNEL, cookies: dict[str, str] | None = None
    ) -> Response:
        response = self.app.handle("GET", path, cookies or {}, {}, access)
        assert isinstance(response, Response)
        return response

    def post(self, path: str, form: dict[str, str], cookies: dict[str, str]) -> Response:
        response = self.app.handle("POST", path, cookies, {k: [v] for k, v in form.items()})
        assert isinstance(response, Response)
        return response

    def owner(self) -> tuple[dict[str, str], str]:
        session = self.app.sessions.create()
        return {COOKIE: session.token}, session.csrf


def _cycles(path: Path, *at: float) -> None:
    con = open_db(path, agents=("gpt-luna", "gpt-sol", BUYHOLD))
    try:
        for t in at:
            paper_cycle(
                con,
                ScenarioClient(),
                load_config({"OPENAI_API_KEY": KEY, "BOT_USDC_AGENTS": ""}),
                costs=Costs(),
                model=FakeModel(),
                clock=Clock(t),
            )
    finally:
        con.close()


def _bot(data: Path, *, with_db: bool = True) -> Bot:
    if with_db:
        _cycles(data / "bot.db", NOW)
    clock, wall = Clock(1_000.0), Clock(NOW + 30)
    app = WebApp(data, public_host=DOMAIN, key_check=lambda key: "", clock=clock, wall_clock=wall)
    app.passwords.set(PASSWORD)
    app.setup_code = None
    return Bot(app, data, clock, wall)


@pytest.fixture
def bot(tmp_path: Path) -> Bot:
    return _bot(tmp_path)


# ------------------------------------------------------------------ AK1: read only


@WAYS
def test_anonymous_overview_is_read_only(bot: Bot, access: Access) -> None:
    response = bot.get("/", access)
    page = response.body
    assert response.status == 200 and response.cookies == []
    assert "<form" not in page and 'name="csrf"' not in page
    assert 'class="tabs"' not in page and 'href="/settings"' not in page
    assert '<a class="quiet" href="/login">Anmelden</a>' in page
    assert '<script src="/app.js" defer></script>' in page  # live like the owner's view
    assert "Depots" in page and "Equity-Verlauf" in page
    # No bind address on the page (user 2026-11-08): it means nothing to a visitor.
    assert "127.0.0.1" not in page and "chip net" not in page


@WAYS
def test_anonymous_overview_with_a_foreign_or_stale_cookie_is_still_read_only(
    bot: Bot, access: Access
) -> None:
    response = bot.get("/", access, {"ltb_session": "forged", "__Host-ltb_session": "forged"})
    assert response.status == 200 and response.cookies == [] and "<form" not in response.body


def test_owner_sees_the_full_overview(bot: Bot) -> None:
    cookies, csrf = bot.owner()
    page = bot.get("/", cookies=cookies).body
    assert f'name="csrf" value="{csrf}"' in page and 'href="/settings"' in page
    assert 'href="/login"' not in page and "Abmelden" in page


# ------------------------------------------------------------------ AK2: hidden without login


def test_hidden_error_texts_and_file_hints(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    real = overview.load

    def with_an_error(*args: object, **kwargs: object) -> overview.Overview:
        view = real(*args, **kwargs)  # type: ignore[arg-type]
        llm = [
            overview.LlmToday(**{**r.__dict__, "last_error": "openai: org-XYZ quota"})
            for r in view.llm
        ]
        assert llm
        return overview.Overview(**{**view.__dict__, "llm": llm})

    monkeypatch.setattr(overview, "load", with_an_error)
    public = bot.get("/", OUTSIDE).body
    cookies, _ = bot.owner()
    owner = bot.get("/", cookies=cookies).body
    assert "org-XYZ" in owner and "secrets.env" in owner
    assert "org-XYZ" not in public and "secrets.env" not in public


@WAYS
def test_hidden_database_details(tmp_path: Path, access: Access) -> None:
    bot = _bot(tmp_path, with_db=False)
    public = bot.get("/", access).body
    assert PUBLIC_PROBLEM in public
    assert "Noch keine Daten" not in public and "paper-cycle" not in public
    cookies, _ = bot.owner()
    assert "Noch keine Daten" in bot.get("/", cookies=cookies).body


def test_hidden_pause_source(bot: Bot) -> None:
    bot.app.credentials.set_paused(True)
    public = bot.get("/", OUTSIDE).body
    assert "Pause angefordert, greift in der nächsten Runde" in public
    assert "BOT_PAUSED" not in public and "Umgebung" not in public


# ------------------------------------------------------------------ AK3: everything else


PROTECTED = [
    ("GET", "/settings"),
    ("GET", "/setup"),
    ("GET", "/anything"),
    ("POST", "/"),
    ("POST", "/settings"),
    ("POST", "/settings/test"),
    ("POST", "/settings/pause"),
    ("POST", "/settings/resume"),
    ("POST", "/settings/password"),
    ("POST", "/settings/public"),
    ("POST", "/logout"),
]


@WAYS
@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_other_route_needs_the_login(
    bot: Bot, access: Access, method: str, path: str
) -> None:
    form = {"csrf": ["x"], "paused": ["1"], "public": ["0"], "agent": ["gpt-luna"]}
    before = bot.app.credentials.read()
    response = bot.app.handle(method, path, {}, form, access)
    assert isinstance(response, Response)
    assert (response.status, response.headers.get("Location")) == (303, "/login")
    assert bot.app.credentials.read() == before


def test_before_setup_nothing_changes(tmp_path: Path) -> None:
    _cycles(tmp_path / "bot.db", NOW)
    app = WebApp(tmp_path, public_host=DOMAIN, key_check=lambda key: "")
    outside = app.handle("GET", "/", {}, {}, OUTSIDE)
    inside = app.handle("GET", "/", {}, {}, TUNNEL)
    assert isinstance(outside, Response) and isinstance(inside, Response)
    assert outside.status == 503 and "Depots" not in outside.body
    assert (inside.status, inside.headers.get("Location")) == (303, "/setup")


# ------------------------------------------------------------------ AK4: load


def test_cache_reads_the_database_once_per_window(
    bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []
    real = overview.load

    def counted(con: object, now: float, *args: object, **kwargs: object) -> overview.Overview:
        calls.append(now)
        return real(con, now, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(overview, "load", counted)
    pages = []
    for step in range(20):
        bot.wall.at = NOW + 30 + step
        bot.clock.at = 1_000.0 + step * 0.4  # 20 visits within 8 s
        pages.append(bot.get("/", OUTSIDE).body)
    assert len(calls) == 1
    # The clock of the page is new on every request, so ages stay right.
    assert f'data-now="{int(NOW + 30)}"' in pages[0]
    assert f'data-now="{int(NOW + 49)}"' in pages[-1]
    bot.clock.at = 1_000.0 + PUBLIC_VIEW_SECONDS
    bot.get("/", OUTSIDE)
    assert len(calls) == 2
    bot.clock.at = 0.0  # a clock that went back reads again instead of trusting the cache
    bot.get("/", OUTSIDE)
    assert len(calls) == 3


def test_cache_is_not_used_by_the_owner(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = overview.load

    def counted(*args: object, **kwargs: object) -> overview.Overview:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(overview, "load", counted)
    bot.get("/", OUTSIDE)
    cookies, _ = bot.owner()
    bot.get("/", cookies=cookies)
    bot.get("/", cookies=cookies)
    assert len(calls) == 3


# ------------------------------------------------------------------ AK5: robots


@WAYS
def test_robots_txt_disallows_everything_without_a_cookie(bot: Bot, access: Access) -> None:
    response = bot.get("/robots.txt", access)
    assert (response.status, response.body) == (200, "User-agent: *\nDisallow: /\n")
    assert response.content_type.startswith("text/plain") and response.cookies == []


@pytest.fixture
def served(bot: Bot) -> Iterator[int]:
    server = make_server(bot.app, "127.0.0.1", 0, public=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("path", ["/", "/robots.txt", "/login", "/settings", "/nothing"])
def test_robots_header_on_every_answer(served: int, path: str) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", served, timeout=30)
    conn.request("GET", path, headers={"Host": DOMAIN, "X-Forwarded-For": "203.0.113.10"})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.getheader("X-Robots-Tag") == "noindex, nofollow"
    if path in ("/", "/robots.txt"):
        assert response.status == 200 and response.getheader("Set-Cookie") is None


# ------------------------------------------------------------------ AK6: the switch


def _switch(bot: Bot, value: str) -> Response:
    cookies, csrf = bot.owner()
    return bot.post("/settings/public", {"public": value, "csrf": csrf}, cookies)


def test_switch_off_and_on_again(bot: Bot) -> None:
    off = _switch(bot, "0")
    assert off.status == 200 and "verlangt wieder den Login" in off.body
    assert bot.app.credentials.read()[PUBLIC_OVERVIEW_FIELD] == "0"
    response = bot.get("/", OUTSIDE)
    assert (response.status, response.headers.get("Location")) == (303, "/login")
    on = _switch(bot, "1")
    assert on.status == 200 and "ohne Login lesbar" in on.body
    assert PUBLIC_OVERVIEW_FIELD not in bot.app.credentials.read()
    assert bot.get("/", OUTSIDE).status == 200


def test_switch_shows_its_state_in_the_settings(bot: Bot) -> None:
    cookies, _ = bot.owner()
    page = bot.get("/settings", cookies=cookies).body
    assert "Übersicht ohne Login" in page and 'action="/settings/public"' in page
    assert 'name="public" value="0"' in page
    _switch(bot, "0")
    assert 'name="public" value="1"' in bot.get("/settings", cookies=cookies).body


@pytest.mark.parametrize("value", ["", "2", "on"])
def test_switch_refuses_odd_values(bot: Bot, value: str) -> None:
    assert _switch(bot, value).status == 400
    assert PUBLIC_OVERVIEW_FIELD not in bot.app.credentials.read()


def test_switch_needs_the_csrf_token(bot: Bot) -> None:
    cookies, _ = bot.owner()
    response = bot.post("/settings/public", {"public": "0", "csrf": "wrong"}, cookies)
    assert response.status == 403
    assert PUBLIC_OVERVIEW_FIELD not in bot.app.credentials.read()


@pytest.mark.parametrize("stored", ["0", "yes", "1 ", "true"])
def test_switch_anything_but_one_is_off(bot: Bot, stored: str) -> None:
    (bot.data / "secrets.env").write_text(f"{PUBLIC_OVERVIEW_FIELD}={stored}\n", encoding="utf-8")
    expected = 200 if stored.strip() == "1" else 303  # parse() strips the value
    assert bot.get("/", OUTSIDE).status == expected


def test_switch_unreadable_secrets_mean_login(bot: Bot) -> None:
    (bot.data / "secrets.env").mkdir()
    response = bot.get("/", OUTSIDE)
    assert (response.status, response.headers.get("Location")) == (303, "/login")


def test_switch_is_ignored_by_the_bot() -> None:
    config = load_config({"OPENAI_API_KEY": KEY, PUBLIC_OVERVIEW_FIELD: "0"})
    assert config == load_config({"OPENAI_API_KEY": KEY})


# ------------------------------------------------------------------ AK7: paper only


def test_public_view_needs_a_new_adr_before_live() -> None:
    # ADR-0028: readable without login only while there is no real money. Unlocking live
    # (ADR-0004) must come with a new ADR on what stays public - this test is the reminder.
    with pytest.raises(NotImplementedError):
        parse_mode("live")


# ------------------------------------------------------------------ review of Spec 019


def test_hidden_status_file_details(bot: Bot) -> None:
    status_path(bot.data / "bot.db").write_text("{not json", encoding="utf-8")
    public = bot.get("/", OUTSIDE).body
    assert "Status nicht lesbar" in public
    assert "Statusdatei" not in public and "JSONDecodeError" not in public
    cookies, _ = bot.owner()
    assert "Statusdatei unlesbar (JSONDecodeError)" in bot.get("/", cookies=cookies).body


def test_owner_page_and_anonymous_page_are_marked_for_the_script(bot: Bot) -> None:
    # Spec 010: a session that ends while the page is open leads to the login; without the
    # marks /app.js would quietly swap the view without login into the owner's page.
    cookies, _ = bot.owner()
    assert '<main data-view="owner">' in bot.get("/", cookies=cookies).body
    assert '<main data-view="public">' in bot.get("/", OUTSIDE).body
    assert "data-view" not in bot.get("/login").body  # no script there


@pytest.mark.parametrize("owner", [False, True])
def test_live_updates_leave_out_the_backdrop(bot: Bot, owner: bool) -> None:
    cookies = bot.owner()[0] if owner else {}
    full = bot.app.handle("GET", "/", cookies, {}, TUNNEL)
    live = bot.app.handle("GET", "/", cookies, {}, TUNNEL, live=True)
    assert isinstance(full, Response) and isinstance(live, Response)
    assert 'class="backdrop"' in full.body and 'class="backdrop"' not in live.body
    assert len(live.body) < len(full.body) - 100_000
    for part in ("<main", "ticker-track", "data-now"):
        assert part in live.body, part


def test_live_header_reaches_the_app(served: int) -> None:
    sizes = []
    for headers in ({}, {"X-Live-Update": "1"}):
        conn = http.client.HTTPConnection("127.0.0.1", served, timeout=30)
        conn.request(
            "GET", "/", headers={"Host": DOMAIN, "X-Forwarded-For": "203.0.113.10", **headers}
        )
        sizes.append(len(conn.getresponse().read()))
        conn.close()
    assert sizes[1] < sizes[0] - 100_000


def test_cache_moves_the_ages_on(bot: Bot) -> None:
    first = bot.get("/", OUTSIDE).body
    bot.wall.at += 9
    bot.clock.at += 9
    later = bot.get("/", OUTSIDE).body
    cookies, _ = bot.owner()
    owner = bot.get("/", cookies=cookies).body
    ages = re.findall(r'data-at="\d+">vor (\d+) s<', first)
    assert ages and [int(a) + 9 for a in ages] == [
        int(a) for a in re.findall(r'data-at="\d+">vor (\d+) s<', later)
    ]
    assert re.findall(r'data-at="\d+">(vor \d+ s)<', later) == re.findall(
        r'data-at="\d+">(vor \d+ s)<', owner
    )


def test_signals_are_read_again_only_when_decisions_change(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    _cycles(path, NOW)
    cache = overview.SignalCache()
    statements: list[str] = []
    with reading(path) as con:
        first = cache.get(con)
        con.set_trace_callback(statements.append)
        assert cache.get(con) == first
        con.set_trace_callback(None)
    assert first and not any("MATERIALIZED" in s for s in statements)
    con = sqlite3.connect(path)  # the next closed bar: one new decision
    with con:
        con.execute(
            "INSERT INTO decisions(ts,agent,symbol,bar_ts,model,action,target,confidence,reason,"
            "inputs,status) VALUES ('2030-01-01T00:00:00+00:00','gpt-sol','BTCEUR',9,'m',"
            "'SELL',0,0.5,'neu','{}','OK')"
        )
    con.close()
    with reading(path) as con:
        statements.clear()
        con.set_trace_callback(statements.append)
        rows = cache.get(con)
        con.set_trace_callback(None)
    assert any("MATERIALIZED" in s for s in statements)
    assert ("gpt-sol", "SELL", "neu") in {(d.agent, d.action, d.reason) for d in rows}


def test_signals_of_a_replaced_database_are_read_again(tmp_path: Path) -> None:
    cache = overview.SignalCache()
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    _cycles(first, NOW)
    _cycles(second, NOW + 7 * 86400)  # same ids, other times
    with reading(first) as con:
        before = cache.get(con)
    with reading(second) as con:
        after = cache.get(con)
        assert after == overview.latest_decisions(con)
    assert {d.ts for d in before}.isdisjoint({d.ts for d in after})
