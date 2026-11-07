"""Spec 012 AK5: there is no order path, and one smuggled in would be caught (ADR-0004, ADR-0022).

Every rule is a function over ``{relative path: source}``; the tests run each rule on the real
repository (must be clean) and on a small injected example (must be caught), so a rule that
silently stopped working would fail its negative test. Exceptions are named, never implied.
"""

import ast
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "llm_trading_bot"
THIS = Path(__file__).resolve()

Files = dict[str, str]

# Private Bitvavo REST paths, auth header and WebSocket actions for orders and accounts.
FORBIDDEN_LITERALS = (
    "/order",  # also /orders, /ordersOpen
    "/trades",
    "/balance",
    "/account",
    "/cancelOrdersAfter",
    "Bitvavo-Access",
    "privateCreateOrder",
    "privateCancelOrder",
)
FORBIDDEN_IMPORTS = (
    "requests",
    "httpx",
    "httpx2",
    "aiohttp",
    "urllib3",
    "websocket",
    "websockets",
    "subprocess",
    "http.client",
)
EXCHANGE_METHODS = {"market_rules", "balances", "submit", "lookup", "cancel_all"}
ORDER_FUNCTIONS = {"place_order", "create_order", "cancel_order"}
HTTP_MODULE = "market/http.py"
BITVAVO_MODULE = "market/bitvavo.py"
EXCHANGE_PROTOCOL = ("broker/contract.py", "OrderExchange")


def _read(root: Path, pattern: str, base: Path) -> Files:
    return {
        p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob(pattern))
        if p.resolve() != THIS and "__pycache__" not in p.parts
    }


def src_files() -> Files:
    return _read(SRC, "*.py", SRC)


def python_files() -> Files:
    """src, tests and tools - everything that could run an order call."""
    out: Files = {}
    for part in ("src", "tests", "tools"):
        out.update(_read(REPO / part, "*.py", REPO))
    return out


def files_in_tests_and_tools() -> Files:
    return {p: t for p, t in python_files().items() if p.startswith(("tests/", "tools/"))}


def shell_files() -> Files:
    out: Files = {}
    for part in ("scripts", "deploy"):
        out.update({k: v for k, v in _read(REPO / part, "*", REPO).items()})
    return out


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _strings(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Every str and bytes literal except docstrings (bytes: a request line or signing payload
    is written as bytes just as often)."""
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        if isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node.value, bytes):
            yield node.lineno, node.value.decode("latin-1")


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


# ------------------------------------------------------------------ rules


def rule_literals(files: Files) -> list[str]:
    out = []
    for path, text in files.items():
        for line, value in _strings(ast.parse(text)):
            for bad in FORBIDDEN_LITERALS:
                if bad in value:
                    out.append(f"{path}:{line}: literal {bad!r}")
    return out


# --data but not --data-dir (the web unit's option).
SENDING = re.compile(r"-X\s*POST|--request\s+POST|--data\b(?!-)")


def rule_scripts(files: Files) -> list[str]:
    """The forbidden literals anywhere; a sending curl in any file that talks to Bitvavo - the
    host usually sits in a variable on another line."""
    out = []
    for path, text in files.items():
        mentions_bitvavo = "bitvavo.com" in text
        for n, line in enumerate(text.splitlines(), 1):
            if any(b in line for b in FORBIDDEN_LITERALS) or (
                mentions_bitvavo and SENDING.search(line)
            ):
                out.append(f"{path}:{n}: {line.strip()}")
    return out


def _public_path(arg: ast.expr) -> bool:
    if isinstance(arg, ast.Constant):
        return arg.value == "/ticker/book"
    if isinstance(arg, ast.JoinedStr):
        kinds = [
            v.value if isinstance(v, ast.Constant) else "{}" for v in arg.values
        ]  # literal parts, "{}" for each formatted value
        return (
            len(kinds) == 4
            and kinds[0] == "/"
            and kinds[1] == "{}"
            and isinstance(kinds[2], str)
            and kinds[2] == "/candles?"
            and kinds[3] == "{}"
        )
    return False


def rule_public_paths(files: Files) -> list[str]:
    out = []
    for path, text in files.items():
        tree = ast.parse(text)
        for line, value in _strings(tree):
            if "bitvavo.com" in value and path != BITVAVO_MODULE:
                out.append(f"{path}:{line}: bitvavo.com outside {BITVAVO_MODULE}")
        for call in _calls(tree):
            if _name(call.func) in ("_get", "get_json"):
                if path == BITVAVO_MODULE and _name(call.func) == "_get":
                    if not call.args or not _public_path(call.args[0]):
                        out.append(f"{path}:{call.lineno}: _get with a non-public path")
                elif path not in (BITVAVO_MODULE, HTTP_MODULE):
                    out.append(f"{path}:{call.lineno}: {_name(call.func)} outside market/")
    return out


def rule_http(files: Files) -> list[str]:
    out = []
    for path, text in files.items():
        for call in _calls(ast.parse(text)):
            name = _name(call.func)
            keywords = {k.arg: k.value for k in call.keywords}
            if name == "Request":
                method = keywords.get("method")
                get_only = method is None or (
                    isinstance(method, ast.Constant) and method.value == "GET"
                )
                if path != HTTP_MODULE or len(call.args) > 1 or "data" in keywords or not get_only:
                    out.append(f"{path}:{call.lineno}: Request beyond a plain GET")
            elif name == "urlopen":
                if path != HTTP_MODULE or len(call.args) != 1 or "data" in keywords:
                    out.append(f"{path}:{call.lineno}: urlopen beyond one Request")
            elif name in ("HTTPConnection", "HTTPSConnection", "system", "popen"):
                out.append(f"{path}:{call.lineno}: {name}")
    return out


def rule_imports(files: Files) -> list[str]:
    out = []
    for path, text in files.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                # market/http.py imports http.client for its exception type only; opening a
                # connection is caught by rule_http.
                if name == "http.client" and path == HTTP_MODULE:
                    continue
                if any(name == m or name.startswith(m + ".") for m in FORBIDDEN_IMPORTS):
                    out.append(f"{path}:{node.lineno}: import {name}")
                if name == "hmac" and path != "web/auth.py":
                    out.append(f"{path}:{node.lineno}: hmac outside web/auth.py")
    return out


def rule_secrets(files: Files) -> list[str]:
    """Nothing in src names a Bitvavo key or a live switch (env reads included)."""
    out = []
    for path, text in files.items():
        for line, value in _strings(ast.parse(text)):
            if value.startswith(("BITVAVO_", "LIVE_")):
                out.append(f"{path}:{line}: {value!r}")
    return out


def rule_exchange_shape(files: Files) -> list[str]:
    out = []
    for path, text in files.items():
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in ORDER_FUNCTIONS
            ):
                out.append(f"{path}:{node.lineno}: def {node.name}")
            if isinstance(node, ast.ClassDef) and (path, node.name) != EXCHANGE_PROTOCOL:
                methods = {
                    n.name
                    for n in node.body
                    if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                if len(methods & EXCHANGE_METHODS) >= 2:
                    out.append(f"{path}:{node.lineno}: class {node.name} looks like an exchange")
    return out


# uv.lock: `name = "pkg"`; pyproject.toml: a quoted requirement at the start of a line.
PACKAGE_LINE = re.compile(r'^\s*(?:name\s*=\s*)?"([A-Za-z0-9][A-Za-z0-9._-]*)')


def rule_packages(texts: Files) -> list[str]:
    out = []
    for path, text in texts.items():
        for n, line in enumerate(text.splitlines(), 1):
            match = PACKAGE_LINE.match(line)
            if match and ("bitvavo" in match[1].lower() or "ccxt" in match[1].lower()):
                out.append(f"{path}:{n}: {match[1]}")
    return out


def package_files() -> Files:
    return {
        name: (REPO / name).read_text(encoding="utf-8") for name in ("pyproject.toml", "uv.lock")
    }


# ------------------------------------------------------------------ the repository is clean

Rule = Callable[[Files], list[str]]
REAL: list[tuple[str, Rule, Callable[[], Files]]] = [
    ("literals", rule_literals, python_files),
    ("scripts", rule_scripts, shell_files),
    ("public_paths", rule_public_paths, src_files),
    ("http", rule_http, src_files),
    ("imports", rule_imports, src_files),
    ("secrets", rule_secrets, src_files),
    ("exchange_shape", rule_exchange_shape, src_files),
    ("exchange_shape_tests_tools", rule_exchange_shape, files_in_tests_and_tools),
    ("packages", rule_packages, package_files),
]


@pytest.mark.parametrize(("name", "rule", "files"), REAL, ids=[r[0] for r in REAL])
def test_repository_has_no_order_path(name: str, rule: Rule, files: Callable[[], Files]) -> None:
    found = files()
    assert found, f"{name}: nothing scanned"
    assert rule(found) == []


def test_the_scans_see_the_files_they_are_about() -> None:
    src = src_files()
    assert {HTTP_MODULE, BITVAVO_MODULE, "web/auth.py", "broker/contract.py"} <= set(src)
    assert any(p.startswith("tests/") for p in python_files())
    assert any(p.startswith("tools/") for p in python_files())
    assert {"scripts/deploy.sh", "deploy/release.sh"} <= set(shell_files())
    names = {
        m[1]
        for text in package_files().values()
        for line in text.splitlines()
        if (m := PACKAGE_LINE.match(line))
    }
    assert {"openai", "pytest", "llm-trading-bot"} <= names  # the package scan reads real names


# ------------------------------------------------------------------ every rule catches its case

BAD: list[tuple[str, Rule, str, str]] = [
    ("literal path", rule_literals, "x.py", 'URL = "https://example/v2/order"'),
    ("literal open", rule_literals, "x.py", 'P = "/ordersOpen"'),
    ("literal header", rule_literals, "tests/x.py", 'H = {"Bitvavo-Access-Key": "k"}'),
    ("literal ws", rule_literals, "tools/x.py", 'A = {"action": "privateCreateOrder"}'),
    ("literal f-string", rule_literals, "x.py", 'u = f"{base}/balance"'),
    ("script post", rule_scripts, "s.sh", "curl -X POST https://api.bitvavo.com/v2/x"),
    ("script path", rule_scripts, "s.sh", "curl https://api.bitvavo.com/v2/trades"),
    (
        "script host in variable",
        rule_scripts,
        "scripts/x.sh",
        'API=https://api.bitvavo.com/v2\ncurl -sf -X POST "$API/x" --data "$BODY"',
    ),
    ("script header", rule_scripts, "deploy/x.sh", 'curl -H "Bitvavo-Access-Key: $K" "$U"'),
    ("bytes literal", rule_literals, "x.py", 'B = b"POST /v2/order HTTP/1.1"'),
    ("bytes key", rule_secrets, "x.py", 'k = os.environb[b"BITVAVO_API_KEY"]'),
    ("other host file", rule_public_paths, "web/x.py", 'H = "https://api.bitvavo.com"'),
    ("private _get", rule_public_paths, BITVAVO_MODULE, 'self._get("/markets")'),
    ("odd f-string", rule_public_paths, BITVAVO_MODULE, 'self._get(f"/{m}/book")'),
    ("get_json elsewhere", rule_public_paths, "paper/x.py", 't.get_json("u")'),
    ("request elsewhere", rule_http, "paper/x.py", "urllib.request.Request(u)"),
    ("request post", rule_http, HTTP_MODULE, 'urllib.request.Request(u, method="POST")'),
    ("request data", rule_http, HTTP_MODULE, "urllib.request.Request(u, b'x')"),
    ("request data kw", rule_http, HTTP_MODULE, "urllib.request.Request(u, data=b)"),
    ("urlopen data", rule_http, HTTP_MODULE, "urllib.request.urlopen(u, b'x')"),
    ("urlopen elsewhere", rule_http, "paper/x.py", "urlopen(r)"),
    ("http.client", rule_http, "x.py", 'http.client.HTTPSConnection("h")'),
    ("os.system", rule_http, "x.py", 'os.system("curl")'),
    ("import requests", rule_imports, "x.py", "import requests"),
    ("from httpx", rule_imports, "x.py", "from httpx import Client"),
    ("import subprocess", rule_imports, "x.py", "import subprocess"),
    ("http.client import", rule_imports, "x.py", "from http.client import HTTPSConnection"),
    ("websockets", rule_imports, "x.py", "import websockets.sync.client"),
    ("hmac elsewhere", rule_imports, "market/x.py", "import hmac"),
    ("env key", rule_secrets, "x.py", 'k = os.environ["BITVAVO_API_KEY"]'),
    ("live switch", rule_secrets, "x.py", 'os.getenv("LIVE_TRADING")'),
    ("order function", rule_exchange_shape, "x.py", "def place_order(): pass"),
    (
        "exchange class",
        rule_exchange_shape,
        "x.py",
        "class X:\n    def submit(self): pass\n    def lookup(self): pass",
    ),
    ("sdk in lock", rule_packages, "uv.lock", 'name = "python-bitvavo-api"'),
    ("ccxt dep", rule_packages, "pyproject.toml", '    "ccxt>=4",'),
]


@pytest.mark.parametrize(("case", "rule", "path", "source"), BAD, ids=[b[0] for b in BAD])
def test_each_rule_catches_its_pattern(case: str, rule: Rule, path: str, source: str) -> None:
    assert rule({path: source}), case


@pytest.mark.parametrize(
    ("rule", "path", "source"),
    [
        (rule_literals, "x.py", '"""Explains /order in a docstring."""'),
        (rule_http, HTTP_MODULE, 'urllib.request.Request(u, headers=h, method="GET")'),
        (rule_http, HTTP_MODULE, "urllib.request.urlopen(request, timeout=5)"),
        (rule_public_paths, BITVAVO_MODULE, 'self._get("/ticker/book")'),
        (rule_public_paths, BITVAVO_MODULE, 'self._get(f"/{m}/candles?{q}")'),
        (rule_imports, "web/auth.py", "import hmac"),
        (rule_imports, HTTP_MODULE, "import http.client"),
        (rule_scripts, "deploy/web.service", "ExecStart=x web --data-dir /var/lib/x"),
        (rule_scripts, "scripts/x.sh", "curl -X POST https://example.org/hook --data x"),
        (rule_packages, "pyproject.toml", 'markers = ["net: talks to Bitvavo"]'),
        (
            rule_exchange_shape,
            "llm/x.py",
            "class Pool:\n    def submit(self): pass",  # one name alone is not an exchange
        ),
        (
            rule_exchange_shape,
            EXCHANGE_PROTOCOL[0],
            "class OrderExchange:\n    def submit(self): ...\n    def lookup(self): ...",
        ),
    ],
)
def test_named_exceptions_stay_allowed(rule: Rule, path: str, source: str) -> None:
    assert rule({path: source}) == []
