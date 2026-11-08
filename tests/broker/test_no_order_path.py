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
# Spec 016: the strategy lab's candle/trade downloader, the only caller of the header variant.
# It builds its URLs only through ``public_url`` and sends only what ``checked_url`` lets
# through (see ``rule_lab_fetch``). The src scans key files relative to the package, the
# literal scan relative to the repository.
LAB_FETCH = "lab/archive.py"
LAB_FETCH_IN_REPO = "src/llm_trading_bot/" + LAB_FETCH
# Spec 016 AK8: the register's git runner, the only src file allowed to import subprocess.
GIT_MODULE = "lab/gitstate.py"
# Spec 016 AK2: the backtest's worker pool, the only process start besides git. It starts the
# same Python interpreter with the lab's own top-level function (``run_quarter``), never a
# program of its choosing; multiprocessing itself stays forbidden in src, so neither a start
# method nor an executable can be set (``rule_process_starts``).
PROCESS_POOL_MODULE = "lab/backtest.py"
HEADERS_METHOD = "get_json_and_headers"
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


def _strings(tree: ast.AST, skip: set[int] | None = None) -> Iterator[tuple[int, str]]:
    """Every str and bytes literal except docstrings and the ``skip`` nodes (bytes: a request
    line or signing payload is written as bytes just as often)."""
    skip = _docstrings(tree) | (skip or set())
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
        # No exemption, not even in LAB_FETCH: its public URL is joined from parts
        # (``public_url``), so no literal there holds ``/trades`` (final review of Spec 016).
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
            if _name(call.func) in ("_get", "get_json", HEADERS_METHOD):
                if path == BITVAVO_MODULE and _name(call.func) == "_get":
                    if not call.args or not _public_path(call.args[0]):
                        out.append(f"{path}:{call.lineno}: _get with a non-public path")
                elif path == LAB_FETCH and _name(call.func) == HEADERS_METHOD:
                    continue  # held by rule_lab_fetch: two call sites, each through checked_url
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
                # The register's git runner; what it may run is held by rule_subprocess_calls
                # and its own allow-list (tests/lab/test_gitstate.py).
                if (
                    name == "subprocess"
                    and path == GIT_MODULE
                    and isinstance(node, ast.Import)
                    and not any(a.asname for a in node.names)
                ):
                    continue
                if any(name == m or name.startswith(m + ".") for m in FORBIDDEN_IMPORTS):
                    out.append(f"{path}:{node.lineno}: import {name}")
                if name == "hmac" and path != "web/auth.py":
                    out.append(f"{path}:{node.lineno}: hmac outside web/auth.py")
    return out


# What GIT_MODULE may pass to subprocess.run. Left out on purpose: ``executable`` (runs another
# program under git's name), ``preexec_fn``, ``start_new_session``, ``pass_fds``, ``input``.
RUN_KEYWORDS = frozenset(
    {
        "cwd",
        "stdin",
        "capture_output",
        "text",
        "encoding",
        "errors",
        "timeout",
        "check",
        "shell",
        "env",
    }
)


# Also CPython's ``_posixsubprocess`` behind it.
PROCESS_MODULE_NAME = re.compile(r"\s*(_posix)?subprocess(\.\w+)*\s*")


def _subprocess_attr(node: ast.AST, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
        and node.attr == attr
        and isinstance(node.ctx, ast.Load)
    )


def rule_subprocess_calls(files: Files) -> list[str]:
    """``subprocess`` is used only in GIT_MODULE, only as ``subprocess.DEVNULL`` or as the
    direct callee of ``subprocess.run(<one arg>, shell=False, timeout=..., <RUN_KEYWORDS>)``:
    no Popen, no other helper (check_output, call, getoutput), no ``getattr(subprocess, ...)``,
    no alias of the module, and ``subprocess.run`` never as a value (``f = subprocess.run`` or
    ``partial(subprocess.run, ...)`` would call it with arguments no one checks). A string
    that is just the module name (``"subprocess"``, ``"subprocess.run"``) counts too:
    ``__import__``/``importlib``/``sys.modules`` would reach the module without an import
    statement. Prose that mentions the word (an attribute docstring) is not a module name."""
    out = []
    for path, text in files.items():
        tree = ast.parse(text)
        runs = [c for c in _calls(tree) if _subprocess_attr(c.func, "run")]
        used_ok = {id(c.func.value) for c in runs if isinstance(c.func, ast.Attribute)}
        used_ok |= {
            id(n.value)
            for n in ast.walk(tree)
            if _subprocess_attr(n, "DEVNULL") and isinstance(n, ast.Attribute)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "subprocess":
                if path != GIT_MODULE:
                    out.append(f"{path}:{node.lineno}: subprocess outside {GIT_MODULE}")
                elif id(node) not in used_ok:
                    out.append(f"{path}:{node.lineno}: subprocess beyond run(...)/DEVNULL")
            # ``gitstate.subprocess.Popen`` reaches the module through gitstate's namespace.
            if isinstance(node, ast.Attribute) and node.attr == "subprocess":
                out.append(f"{path}:{node.lineno}: subprocess reached as an attribute")
            # ``gitstate.REV_PARSE_HEAD = ["curl", ...]`` from another module would change what
            # the pinned run call starts.
            if (
                isinstance(node, ast.Attribute)
                and node.attr in GIT_PINS
                and not isinstance(node.ctx, ast.Load)
            ):
                out.append(f"{path}:{node.lineno}: {node.attr} rebound from outside")
        for line, value in _strings(tree):
            if PROCESS_MODULE_NAME.fullmatch(value):
                out.append(f"{path}:{line}: string {value!r} names the subprocess module")
            if value in GIT_PINS:
                out.append(f"{path}:{line}: string {value!r} names a pinned git command")
        if path != GIT_MODULE:
            continue
        # Final review of Spec 016: the shape of a run call alone let ``run(["curl", "-X",
        # "POST", ...], shell=False, timeout=5)`` through. Now what it starts is pinned too.
        out += _pinned(path, tree, GIT_PINS, GIT_IMPORTS, GIT_UNBOUND)
        if len(runs) != 1:
            out.append(f"{path}: {len(runs)} subprocess.run calls, exactly one allowed")
        for call in runs:
            if _enclosing(tree, call) != ("git",):
                out.append(f"{path}:{call.lineno}: run outside the module-level def git")
            if not call.args or not _fixed_git_argv(call.args[0]):
                out.append(f"{path}:{call.lineno}: run argument is not list(<a GIT_PINS name>)")
            keywords = {k.arg: k.value for k in call.keywords}
            shell = keywords.get("shell")
            unknown = sorted(str(k) for k in keywords if k not in RUN_KEYWORDS)
            if (
                not isinstance(shell, ast.Constant)
                or shell.value is not False
                or "timeout" not in keywords
                or None in keywords  # **kwargs could smuggle shell=True in
                or len(call.args) != 1
                or isinstance(call.args[0], ast.Starred)
            ):
                out.append(f"{path}:{call.lineno}: run without shell=False and a timeout")
            if unknown:
                out.append(f"{path}:{call.lineno}: run with keyword(s) {unknown}")
            env = keywords.get("env")
            if env is not None and ast.unparse(env) != GIT_ENV:
                out.append(f"{path}:{call.lineno}: run with an env other than {GIT_ENV}")
    return out


# The two git commands GIT_MODULE may start, as their one module-level binding must read.
GIT_PINS = {
    "REV_PARSE_HEAD": "REV_PARSE_HEAD = ('git', 'rev-parse', 'HEAD')",
    "STATUS_PORCELAIN": "STATUS_PORCELAIN = ('git', 'status', '--porcelain')",
}
GIT_IMPORTS = {"os": "os"}
"""Names GIT_MODULE binds only by a plain ``import``: ``os = fake`` would change the env."""
GIT_UNBOUND = frozenset({"list"})
"""Never bound in GIT_MODULE: a local ``list`` would turn ``list(REV_PARSE_HEAD)`` into anything."""
GIT_ENV = "{**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}"
"""The env the run call passes: a different ``PATH`` would pick another program named git."""
# In a pinned file these could rebind a pinned name behind the AST's back.
DYNAMIC_NAMES = frozenset(
    {"globals", "vars", "setattr", "delattr", "exec", "eval", "compile", "__builtins__"}
)


def _fixed_git_argv(arg: ast.expr) -> bool:
    """``list(NAME)`` or ``list(NAME if <test> else NAME)``, each NAME one of GIT_PINS."""
    if not (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "list"
        and len(arg.args) == 1
        and not arg.keywords
    ):
        return False
    inner = arg.args[0]
    branches = [inner.body, inner.orelse] if isinstance(inner, ast.IfExp) else [inner]
    return all(isinstance(b, ast.Name) and b.id in GIT_PINS for b in branches)


def _enclosing(tree: ast.Module, target: ast.AST) -> tuple[str, ...]:
    """Names of the classes and functions around ``target``, outermost first."""

    def walk(node: ast.AST, names: tuple[str, ...]) -> tuple[str, ...] | None:
        for child in ast.iter_child_nodes(node):
            if child is target:
                return names
            scope = (
                (*names, child.name)
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                else names
            )
            found = walk(child, scope)
            if found is not None:
                return found
        return None

    return walk(tree, ()) or ()


def _bindings(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Every node that binds a name, whatever the syntax (assignment, def, import, argument,
    ``global``, ``except ... as``, ``match`` capture, type parameter)."""
    out: dict[str, list[ast.AST]] = {}

    def add(name: str | None, node: ast.AST) -> None:
        if name is not None:
            out.setdefault(name, []).append(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            add(node.id, node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            add(node.name, node)
        elif isinstance(node, ast.arg):
            add(node.arg, node)
        elif isinstance(node, ast.alias):
            add(node.asname or node.name.split(".")[0], node)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for name in node.names:
                add(name, node)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            add(node.name, node)
        elif isinstance(node, ast.MatchMapping):
            add(node.rest, node)
        elif isinstance(node, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple):
            add(node.name, node)
    return out


def _import_site_ok(site: ast.AST, parent: ast.AST | None, module: str) -> bool:
    if not isinstance(site, ast.alias) or site.asname is not None:
        return False
    if isinstance(parent, ast.Import):
        return site.name == module
    return isinstance(parent, ast.ImportFrom) and parent.module == module and parent.level == 0


def _pinned(
    path: str,
    tree: ast.Module,
    assigns: dict[str, str],
    imports: dict[str, str],
    unbound: frozenset[str] = frozenset(),
    functions: frozenset[str] = frozenset(),
) -> list[str]:
    """Each name in ``assigns`` is bound once, by a module-level assignment reading exactly as
    given; each in ``imports`` once, by a plain module-level import from the given module; each
    in ``functions`` once, by a module-level def; ``unbound`` never. No star import and no
    DYNAMIC_NAMES, which could rebind any of them unseen."""
    out = []
    bound = _bindings(tree)
    top = {id(n) for n in tree.body}
    parents = {
        id(a): n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names
    }
    for name, expected in assigns.items():
        sites = bound.get(name, [])
        stmt = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.Assign) and any(t in sites for t in n.targets)
            ),
            None,
        )
        if len(sites) != 1 or stmt is None or ast.unparse(stmt) != expected:
            out.append(f"{path}: {name} must be bound once, as {expected!r}")
    for name, module in imports.items():
        sites = bound.get(name, [])
        if len(sites) != 1 or not _import_site_ok(sites[0], parents.get(id(sites[0])), module):
            out.append(f"{path}: {name} must be bound once, by an import from {module}")
    for name in sorted(functions):
        sites = bound.get(name, [])
        if len(sites) != 1 or not isinstance(sites[0], ast.FunctionDef) or id(sites[0]) not in top:
            out.append(f"{path}: {name} must be bound once, by a module-level def")
    for name in sorted(unbound):
        for site in bound.get(name, []):
            out.append(f"{path}:{getattr(site, 'lineno', 0)}: {name} rebound")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            out.append(f"{path}:{node.lineno}: star import")
        if isinstance(node, ast.Name) and node.id in DYNAMIC_NAMES:
            out.append(f"{path}:{node.lineno}: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            out.append(f"{path}:{node.lineno}: __dict__")
    return out


# How LAB_FETCH must build and check every URL it sends (final review of Spec 016: the old
# file-wide exemption let ``t.get_json_and_headers(base + f"/{ver}/trades?{q}")`` through).
LAB_PINS = {
    "_PUBLIC_URL": ast.unparse(
        ast.parse(
            "_PUBLIC_URL = re.compile(re.escape(API) + "
            r'r"/[A-Z0-9]{2,10}-[A-Z0-9]{2,10}/(candles|trades)\?[A-Za-z0-9=&.%-]*")'
        )
    ),
}
LAB_IMPORTS = {"re": "re", "API": "llm_trading_bot.market.bitvavo"}
LAB_FUNCTIONS = frozenset({"checked_url", "public_url"})
PUBLIC_URL_RETURN = (
    "return checked_url(API + '/' + _market_segment(market) + '/' + endpoint + '?' + query)"
)
# The two places that send, and the only argument shape each may pass.
LAB_SENDERS = {("PublicApi", "_request"), ("LabTransport", HEADERS_METHOD)}


def _body(func: ast.FunctionDef) -> list[ast.stmt]:
    """The statements of ``func`` without its docstring."""
    first = func.body[0] if func.body else None
    doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
    return func.body[1:] if doc else func.body


def _params(func: ast.FunctionDef) -> list[str]:
    a = func.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.defaults or a.posonlyargs:
        return []
    return [x.arg for x in a.args]


def _checked_url_ok(func: ast.FunctionDef) -> bool:
    """``if not _PUBLIC_URL.fullmatch(url): raise ...`` then ``return url``, nothing else."""
    params, body = _params(func), _body(func)
    if len(params) != 1 or len(body) != 2:
        return False
    guard, done = body
    return (
        isinstance(guard, ast.If)
        and ast.unparse(guard.test) == f"not _PUBLIC_URL.fullmatch({params[0]})"
        and not guard.orelse
        and all(isinstance(n, ast.Raise) for n in guard.body)
        and ast.unparse(done) == f"return {params[0]}"
    )


def _public_url_ok(func: ast.FunctionDef) -> bool:
    """Ends in PUBLIC_URL_RETURN over its own three parameters, which nothing rebinds."""
    body = _body(func)
    rebinds = any(
        isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)
        for stmt in body
        for n in ast.walk(stmt)
    )
    return (
        _params(func) == ["market", "endpoint", "query"]
        and bool(body)
        and ast.unparse(body[-1]) == PUBLIC_URL_RETURN
        and not rebinds
    )


def rule_lab_fetch(files: Files) -> list[str]:
    """LAB_FETCH sends only ``API/<market>/<candles|trades>?<query>``: exactly two
    ``get_json_and_headers`` calls (``PublicApi._request`` and ``LabTransport``), each passing
    ``checked_url(...)``; ``checked_url`` tests ``_PUBLIC_URL`` and returns its argument;
    ``public_url`` joins exactly the parts above; ``_PUBLIC_URL``, ``re`` and ``API`` are bound
    once, as pinned. The run-time half: ``tests/lab/test_archive.py``."""
    out = []
    for path, text in files.items():
        tree = ast.parse(text)
        if path != LAB_FETCH:
            # ``archive.checked_url = lambda u: u`` from another module would empty the check.
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in LAB_FUNCTIONS | set(LAB_PINS)
                    and not isinstance(node.ctx, ast.Load)
                ):
                    out.append(f"{path}:{node.lineno}: {node.attr} rebound from outside")
            continue
        out += _pinned(path, tree, LAB_PINS, LAB_IMPORTS, functions=LAB_FUNCTIONS)
        defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        if "checked_url" in defs and not _checked_url_ok(defs["checked_url"]):
            out.append(f"{path}: checked_url is not the plain _PUBLIC_URL test")
        if "public_url" in defs and not _public_url_ok(defs["public_url"]):
            out.append(f"{path}: public_url does not end in {PUBLIC_URL_RETURN!r}")
        senders = [c for c in _calls(tree) if _name(c.func) == HEADERS_METHOD]
        if len(senders) != len(LAB_SENDERS):
            out.append(f"{path}: {len(senders)} {HEADERS_METHOD} calls, {len(LAB_SENDERS)} allowed")
        for call in senders:
            arg = call.args[0] if len(call.args) == 1 else None
            if (
                _enclosing(tree, call) not in LAB_SENDERS
                or call.keywords
                or not isinstance(arg, ast.Call)
                or not isinstance(arg.func, ast.Name)
                or arg.func.id != "checked_url"
            ):
                out.append(f"{path}:{call.lineno}: {HEADERS_METHOD} without checked_url(...)")
    return out


# Names that start a process (os, pty, asyncio, _winapi, multiprocessing), matched in full:
# ``execute`` (sqlite) or ``spawned`` are not among them.
PROCESS_STARTS = re.compile(
    r"system|popen|startfile|fork|forkpty|spawn[lv]?p?e?|exec[lv]p?e?|posix_spawnp?"
    r"|create_subprocess_(exec|shell)|CreateProcess\w*|ShellExecute\w*|WinExec"
    r"|set_executable|Popen"
)
# Modules whose purpose is starting a program; ``os`` stays allowed, its starters are names.
PROCESS_MODULES = (
    "multiprocessing",
    "pty",
    "_posixsubprocess",
    "_winapi",
    "ctypes",
    "_ctypes",
    "webbrowser",
    "posix",
    "nt",
    "concurrent.futures.process",
)
POOL = "ProcessPoolExecutor"
POOL_KEYWORDS = frozenset({"max_workers"})
"""``mp_context`` (start method, executable) and ``initializer`` stay out."""
MODULE_HANDLES = frozenset({"os", "sys", "pty", "asyncio", "posix", "nt"})
"""``getattr(os, name)`` with a computed ``name`` could reach a starter unseen."""


def rule_process_starts(files: Files) -> list[str]:
    """No src module starts a program, except GIT_MODULE (rule_subprocess_calls) and the
    worker pool in PROCESS_POOL_MODULE: a starter called or taken as a value, imported by
    name, named in a string, or reached through ``getattr`` on a module; no PROCESS_MODULES;
    ``sys.executable`` never rebound (the pool starts whatever it names)."""
    out = []
    for path, text in files.items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            name = ""
            if isinstance(node, ast.Attribute):
                name = node.attr
                if name in ("executable", "_executable") and not isinstance(node.ctx, ast.Load):
                    out.append(f"{path}:{node.lineno}: {name} rebound")
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.alias):
                name = node.name.rsplit(".", 1)[-1]
            if PROCESS_STARTS.fullmatch(name):
                out.append(f"{path}:{getattr(node, 'lineno', 0)}: process start {name}")
            if name == POOL and path != PROCESS_POOL_MODULE:
                out.append(f"{path}:{getattr(node, 'lineno', 0)}: {POOL} outside the backtest")
            if isinstance(node, ast.Call) and _name(node.func) == POOL:
                extra = {str(k.arg) for k in node.keywords} - POOL_KEYWORDS
                if node.args or extra:
                    out.append(f"{path}:{node.lineno}: {POOL} beyond max_workers")
            if (
                isinstance(node, ast.Call)
                and _name(node.func) == "getattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in MODULE_HANDLES
                and not (
                    len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                )  # a literal name is checked as a string below (``getattr(os, "O_BINARY", 0)``)
            ):
                out.append(f"{path}:{node.lineno}: computed getattr on {node.args[0].id}")
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for module in modules:
                if any(module == m or module.startswith(m + ".") for m in PROCESS_MODULES):
                    out.append(f"{path}:{getattr(node, 'lineno', 0)}: import {module}")
        for line, value in _strings(tree):
            if PROCESS_STARTS.fullmatch(value.strip()) or value.strip() in PROCESS_MODULES:
                out.append(f"{path}:{line}: string {value!r} names a process start")
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
    ("subprocess_calls", rule_subprocess_calls, src_files),
    ("lab_fetch", rule_lab_fetch, src_files),
    ("process_starts", rule_process_starts, src_files),
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
    # The named exceptions point at real files that really use them (a renamed file would
    # otherwise leave a dead exemption behind, ready for whatever takes its name).
    assert {LAB_FETCH, GIT_MODULE, PROCESS_POOL_MODULE} <= set(src)
    assert "def public_url(" in src[LAB_FETCH] and "def checked_url(" in src[LAB_FETCH]
    assert "import subprocess" in src[GIT_MODULE]
    assert f"{POOL}(max_workers=" in src[PROCESS_POOL_MODULE]
    assert LAB_FETCH_IN_REPO in python_files()
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
    ("headers elsewhere", rule_public_paths, "lab/x.py", 't.get_json_and_headers("u")'),
    ("get_json in lab", rule_public_paths, LAB_FETCH, 't.get_json("u")'),
    ("private trades", rule_literals, "lab/x.py", 'P = "/trades?market=BTC-EUR"'),
    ("trades f-string", rule_literals, "lab/x.py", 'u = f"{base}/trades"'),
    ("trades no market", rule_literals, "lab/x.py", 'u = f"/trades?{q}"'),
    ("trades with prefix", rule_literals, "lab/x.py", 'u = f"/{m}/trades?{q}/order"'),
    (
        "trades shape in broker",
        rule_literals,
        "src/llm_trading_bot/broker/live.py",
        'u = f"/{ver}/trades?{q}"',
    ),
    (
        "trades shape in spread",
        rule_literals,
        "src/llm_trading_bot/lab/spread.py",
        'u = f"/{m}/trades?{q}"',
    ),
    ("trades shape in tests", rule_literals, "tests/lab/x.py", 'u = f"/{m}/trades?{q}"'),
    ("trades shape in tools", rule_literals, "tools/x.py", 'u = f"/{m}/trades?{q}"'),
    ("trades shape, package key", rule_literals, LAB_FETCH, 'u = f"/{m}/trades?{q}"'),
    # Final review of Spec 016: no longer exempt in the lab downloader either.
    ("trades shape in lab fetch", rule_literals, LAB_FETCH_IN_REPO, 'u = f"/{m}/trades?{q}"'),
    ("private trades in lab fetch", rule_literals, LAB_FETCH_IN_REPO, 'P = "/trades?market=X"'),
    ("trades no market in lab fetch", rule_literals, LAB_FETCH_IN_REPO, 'u = f"/trades?{q}"'),
    ("order in lab fetch", rule_literals, LAB_FETCH_IN_REPO, 'u = f"/{m}/order?{q}"'),
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
    # subprocess is exempt in GIT_MODULE only, as a plain un-aliased import.
    ("subprocess in lab fetch", rule_imports, LAB_FETCH, "import subprocess"),
    ("subprocess in register", rule_imports, "lab/register.py", "import subprocess"),
    ("subprocess in broker", rule_imports, "broker/x.py", "import subprocess"),
    (
        "subprocess, repo key",
        rule_imports,
        "src/llm_trading_bot/" + GIT_MODULE,
        "import subprocess",
    ),
    ("from subprocess in git", rule_imports, GIT_MODULE, "from subprocess import run"),
    ("subprocess alias in git", rule_imports, GIT_MODULE, "import subprocess as sp"),
    ("requests in git", rule_imports, GIT_MODULE, "import requests"),
    (
        "run elsewhere",
        rule_subprocess_calls,
        "lab/register.py",
        "subprocess.run(a, shell=False, timeout=1)",
    ),
    (
        "importlib subprocess",
        rule_subprocess_calls,
        "lab/register.py",
        'importlib.import_module("subprocess")',
    ),
    ("sys.modules subprocess", rule_subprocess_calls, "x.py", 'sys.modules["subprocess"].run(a)'),
    (
        "posixsubprocess",
        rule_subprocess_calls,
        "x.py",
        'importlib.import_module("_posixsubprocess")',
    ),
    # Final review of Spec 016: gitstate's pinned names reached from another module.
    ("via gitstate", rule_subprocess_calls, "lab/x.py", 'gitstate.subprocess.Popen(["sh"])'),
    ("pin patched", rule_subprocess_calls, "lab/x.py", 'gitstate.REV_PARSE_HEAD = ("curl",)'),
    ("pin by name", rule_subprocess_calls, "lab/x.py", 'setattr(g, "STATUS_PORCELAIN", x)'),
    ("lab check patched", rule_lab_fetch, "lab/x.py", "archive.checked_url = str"),
    ("lab regex patched", rule_lab_fetch, "lab/x.py", "archive._PUBLIC_URL = re.compile('.*')"),
    # Final review of Spec 016: every way to start a program, outside the two named ones.
    ("os.spawnv", rule_process_starts, "paper/x.py", 'os.spawnv(os.P_WAIT, "c.exe", ["c"])'),
    ("os.spawnlp", rule_process_starts, "paper/x.py", 'os.spawnlp(os.P_WAIT, "c", "c")'),
    ("os.execv", rule_process_starts, "paper/x.py", 'os.execv("/bin/sh", ["sh"])'),
    ("os.execlpe", rule_process_starts, "paper/x.py", 'os.execlpe("sh", "sh", env)'),
    ("os.posix_spawn", rule_process_starts, "paper/x.py", 'os.posix_spawn("/bin/sh", a, {})'),
    ("os.posix_spawnp", rule_process_starts, "paper/x.py", 'os.posix_spawnp("sh", a, {})'),
    ("os.startfile", rule_process_starts, "paper/x.py", 'os.startfile("x.exe")'),
    ("os.system", rule_process_starts, "paper/x.py", 'os.system("curl")'),
    ("os.popen", rule_process_starts, "paper/x.py", 'os.popen("curl")'),
    ("os.fork", rule_process_starts, "paper/x.py", "pid = os.fork()"),
    ("os.forkpty", rule_process_starts, "paper/x.py", "pid, fd = os.forkpty()"),
    ("pty.spawn", rule_process_starts, "paper/x.py", 'pty.spawn("sh")'),
    ("import pty", rule_process_starts, "paper/x.py", "import pty"),
    ("from os import", rule_process_starts, "paper/x.py", "from os import spawnv as s"),
    ("starter as value", rule_process_starts, "paper/x.py", "f = os.execv"),
    ("starter by string", rule_process_starts, "paper/x.py", 'getattr(os, "spawnv")(a)'),
    ("computed getattr", rule_process_starts, "paper/x.py", "getattr(os, name)(a)"),
    ("asyncio exec", rule_process_starts, "llm/x.py", 'asyncio.create_subprocess_exec("c")'),
    ("asyncio shell", rule_process_starts, "llm/x.py", 'asyncio.create_subprocess_shell("c")'),
    ("winapi", rule_process_starts, "x.py", "_winapi.CreateProcess(None, c, None)"),
    ("import winapi", rule_process_starts, "x.py", "import _winapi"),
    ("import ctypes", rule_process_starts, "x.py", "import ctypes"),
    ("import webbrowser", rule_process_starts, "x.py", "import webbrowser"),
    ("import posix", rule_process_starts, "x.py", "import posix"),
    ("import multiprocessing", rule_process_starts, "x.py", "import multiprocessing"),
    ("from multiprocessing", rule_process_starts, "x.py", "from multiprocessing import Process"),
    ("mp by string", rule_process_starts, "x.py", 'importlib.import_module("multiprocessing")'),
    ("set_executable", rule_process_starts, "x.py", 'ctx.set_executable("curl")'),
    ("Popen by name", rule_process_starts, "x.py", "Popen(a)"),
    ("sys.executable", rule_process_starts, PROCESS_POOL_MODULE, 'sys.executable = "curl"'),
    (
        "pool internals",
        rule_process_starts,
        PROCESS_POOL_MODULE,
        "from concurrent.futures.process import _ExecutorManagerThread",
    ),
    ("pool elsewhere", rule_process_starts, "paper/x.py", f"from concurrent.futures import {POOL}"),
    (
        "pool mp_context",
        rule_process_starts,
        PROCESS_POOL_MODULE,
        f"{POOL}(max_workers=2, mp_context=ctx)",
    ),
    (
        "pool initializer",
        rule_process_starts,
        PROCESS_POOL_MODULE,
        f"{POOL}(max_workers=2, initializer=f)",
    ),
    ("pool positional", rule_process_starts, PROCESS_POOL_MODULE, f"{POOL}(2, ctx)"),
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


# ------------------------------------------------------------------ GIT_MODULE, check by check

# A minimal GIT_MODULE that passes rule_subprocess_calls; each case below changes one thing,
# and the test asserts the message of the check it is about (any GIT_MODULE snippet without
# the pins would be flagged anyway, which would prove nothing about the other checks).
GIT_HEAD = (
    "import os\nimport subprocess\n\n"
    "REV_PARSE_HEAD = ('git', 'rev-parse', 'HEAD')\n"
    "STATUS_PORCELAIN = ('git', 'status', '--porcelain')\n\n\n"
    "def git(wanted, t):\n"
)
GIT_RUN = "subprocess.run(list(REV_PARSE_HEAD), stdin=subprocess.DEVNULL, shell=False, timeout=t)"


def git_module(*body: str, tail: str = "", head: str = GIT_HEAD) -> str:
    return head + "".join(f"    {line}\n" for line in body) + tail


def run_with(args: str) -> str:
    return f"return subprocess.run({args})"


GIT_BAD: list[tuple[str, str, str]] = [
    ("shell in git", git_module(run_with("list(REV_PARSE_HEAD), shell=True, timeout=t")), "shell"),
    ("shell missing in git", git_module(run_with("list(REV_PARSE_HEAD), timeout=t")), "shell"),
    ("no timeout in git", git_module(run_with("list(REV_PARSE_HEAD), shell=False")), "timeout"),
    (
        "kwargs in git",
        git_module(run_with("list(REV_PARSE_HEAD), shell=False, timeout=t, **kw")),
        "shell=False",
    ),
    (
        "star args in git",
        git_module(run_with("*a, shell=False, timeout=t")),
        "shell=False",
    ),
    (
        "executable in git",
        git_module(run_with("list(REV_PARSE_HEAD), shell=False, timeout=t, executable='c.exe'")),
        "keyword",
    ),
    (
        "preexec_fn in git",
        git_module(run_with("list(REV_PARSE_HEAD), shell=False, timeout=t, preexec_fn=f")),
        "keyword",
    ),
    ("popen in git", git_module(GIT_RUN, 'subprocess.Popen(["git"])'), "beyond run"),
    ("check_output in git", git_module(GIT_RUN, "subprocess.check_output(a)"), "beyond run"),
    ("getattr in git", git_module(GIT_RUN, 'getattr(subprocess, "Popen")(a)'), "beyond run"),
    ("module alias in git", git_module(GIT_RUN, "p = subprocess", "p.Popen(a)"), "beyond run"),
    ("run aliased in git", git_module("f = subprocess.run", "f(['sh'], shell=True)"), "beyond"),
    ("run in partial in git", git_module("partial(subprocess.run, shell=True)(a)"), "beyond"),
    ("run walrus in git", git_module("(f := subprocess.run)(a)"), "beyond run"),
    ("run in a list in git", git_module("[subprocess.run][0](a)"), "beyond run"),
    ("run reassigned in git", git_module(GIT_RUN, "subprocess.run = g"), "beyond run"),
    ("dunder import in git", git_module(GIT_RUN, '__import__("subprocess")'), "module"),
    # Final review of Spec 016: what the one run call starts is pinned, not only its shape.
    (
        "curl probe in git",
        git_module(run_with("['curl', '-s', '-X', 'POST', u, '-d', b], shell=False, timeout=5")),
        "not list(",
    ),
    ("caller's list in git", git_module(run_with("wanted, shell=False, timeout=t")), "not list("),
    (
        "copy of caller's list in git",
        git_module(run_with("list(wanted), shell=False, timeout=t")),
        "not list(",
    ),
    (
        "variable in git",
        git_module("fixed = REV_PARSE_HEAD", run_with("list(fixed), shell=False, timeout=t")),
        "not list(",
    ),
    (
        "other branch in git",
        git_module(run_with("list(REV_PARSE_HEAD if w else other), shell=False, timeout=t")),
        "not list(",
    ),
    ("second run in git", git_module(GIT_RUN, "return " + GIT_RUN), "exactly one"),
    (
        "run outside def git",
        git_module("return 1", tail=f"\n\ndef other(t):\n    return {GIT_RUN}\n"),
        "outside the module-level def git",
    ),
    (
        "run nested in git",
        git_module("def inner():", f"    return {GIT_RUN}", "return inner()"),
        "outside the module-level def git",
    ),
    (
        "pin changed in git",
        git_module("return " + GIT_RUN).replace("('git', 'rev-parse', 'HEAD')", "('curl', 'x')"),
        "REV_PARSE_HEAD must be bound once",
    ),
    (
        "pin rebound locally in git",
        git_module("REV_PARSE_HEAD = ['curl']", "return " + GIT_RUN),
        "REV_PARSE_HEAD must be bound once",
    ),
    (
        "pin rebound at module level",
        git_module("return " + GIT_RUN, tail="STATUS_PORCELAIN = ('sh',)\n"),
        "STATUS_PORCELAIN must be bound once",
    ),
    (
        "pin as argument in git",
        git_module("return " + GIT_RUN).replace("def git(wanted, t)", "def git(REV_PARSE_HEAD, t)"),
        "REV_PARSE_HEAD must be bound once",
    ),
    (
        "pin via global in git",
        git_module("global REV_PARSE_HEAD", "return " + GIT_RUN),
        "REV_PARSE_HEAD must be bound once",
    ),
    (
        "pin via for in git",
        git_module("for REV_PARSE_HEAD in x: pass", "return " + GIT_RUN),
        "REV_PARSE_HEAD must be bound once",
    ),
    (
        "pin via import in git",
        git_module("return " + GIT_RUN, tail="from x import y as STATUS_PORCELAIN\n"),
        "STATUS_PORCELAIN must be bound once",
    ),
    (
        "list shadowed in git",
        git_module("return " + GIT_RUN, tail="list = lambda a: ['curl']\n"),
        "list rebound",
    ),
    (
        "list as def in git",
        git_module("return " + GIT_RUN, tail="def list(a):\n    return ['curl']\n"),
        "list rebound",
    ),
    (
        "list imported in git",
        git_module("return " + GIT_RUN, tail="from x import y as list\n"),
        "list rebound",
    ),
    ("os rebound in git", git_module("os = fake", "return " + GIT_RUN), "os must be bound once"),
    (
        "os aliased in git",
        git_module("return " + GIT_RUN).replace("import os\n", "import posixpath as os\n"),
        "os must be bound once",
    ),
    (
        "env with PATH in git",
        git_module(
            run_with(
                "list(REV_PARSE_HEAD), shell=False, timeout=t, env={**os.environ, 'PATH': 'C:/x'}"
            )
        ),
        "env other",
    ),
    ("star import in git", git_module("return " + GIT_RUN, tail="from x import *\n"), "star"),
    (
        "globals in git",
        git_module("globals()['REV_PARSE_HEAD'] = ('curl',)", "return " + GIT_RUN),
        "globals",
    ),
    ("setattr in git", git_module("setattr(m, n, v)", "return " + GIT_RUN), "setattr"),
    ("__dict__ in git", git_module("m.__dict__[n] = v", "return " + GIT_RUN), "__dict__"),
]


def test_minimal_git_module_is_clean() -> None:
    assert rule_subprocess_calls({GIT_MODULE: git_module("return " + GIT_RUN)}) == []


@pytest.mark.parametrize(("case", "source", "message"), GIT_BAD, ids=[b[0] for b in GIT_BAD])
def test_git_module_checks_catch_their_case(case: str, source: str, message: str) -> None:
    found = rule_subprocess_calls({GIT_MODULE: source})
    assert any(message in line for line in found), (case, found)


# ------------------------------------------------------------------ review probes, real files

# The three probes of the final review of Spec 016, appended to the real files, exactly as
# they slipped through every rule before.
GIT_PROBE = (
    "\nfrom llm_trading_bot.market.bitvavo import API\n\n\n"
    "def ping(path, body):\n"
    '    return subprocess.run(["curl", "-s", "-X", "POST", API + path, "-d", body],'
    " shell=False, timeout=5)\n"
)
LAB_PROBE = (
    "\n\ndef private_trades(t: LabTransport, ver, q):\n"
    '    base = API.rsplit("/", 1)[0]\n'
    '    return t.get_json_and_headers(base + f"/{ver}/trades?{q}")\n'
)
PROCESS_PROBE = (
    "import os\n"
    'os.spawnv(os.P_WAIT, "C:/curl.exe", ["curl", "-d", "x"])\n'
    'os.execv("/bin/sh", ["sh"])\n'
    'os.startfile("x.exe")\n'
    'os.posix_spawn("/bin/sh", ["sh"], {})\n'
)


def test_git_probe_on_the_real_module_is_caught() -> None:
    real = src_files()[GIT_MODULE]
    assert rule_subprocess_calls({GIT_MODULE: real}) == []
    found = rule_subprocess_calls({GIT_MODULE: real + GIT_PROBE})
    assert any("not list(" in line for line in found), found
    assert any("exactly one" in line for line in found), found
    assert any("outside the module-level def git" in line for line in found), found


def test_lab_probe_on_the_real_module_is_caught() -> None:
    real = src_files()[LAB_FETCH]
    probed = real + LAB_PROBE
    assert rule_lab_fetch({LAB_FETCH: real}) == []
    found = rule_lab_fetch({LAB_FETCH: probed})
    assert any("without checked_url" in line for line in found), found
    assert rule_literals({LAB_FETCH_IN_REPO: probed}), "the /trades? literal is not exempt"


def test_process_probe_is_caught_line_by_line() -> None:
    found = rule_process_starts({"paper/x.py": PROCESS_PROBE})
    assert sorted(line.split(": ")[-1] for line in found) == [
        "process start execv",
        "process start posix_spawn",
        "process start spawnv",
        "process start startfile",
    ]


# Changes to the real LAB_FETCH, each of which would let a URL out unchecked.
LAB_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "request without check",
        "self._transport.get_json_and_headers(checked_url(url))",
        "self._transport.get_json_and_headers(url)",
        "without checked_url",
    ),
    (
        "transport without check",
        "self._http.get_json_and_headers(checked_url(url))",
        "self._http.get_json_and_headers(url)",
        "without checked_url",
    ),
    (
        "check by match only",
        "if not _PUBLIC_URL.fullmatch(url):",
        "if not _PUBLIC_URL.match(url):",
        "checked_url is not",
    ),
    ("check returns other", "    return url\n", "    return url + q\n", "checked_url is not"),
    ("pattern widened", "(candles|trades)", "(candles|trades|order)", "_PUBLIC_URL must be"),
    (
        "url built otherwise",
        '"/" + endpoint + "?" + query',
        '"/" + endpoint + "/" + query',
        "public_url does not end",
    ),
    (
        "check rebound",
        "\nclass LabTransport:",
        "\nchecked_url = str\n\n\nclass LabTransport:",
        "once",
    ),
    ("re rebound", "\nclass LabTransport:", "\nre = fake\n\n\nclass LabTransport:", "re must be"),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "message"), LAB_MUTATIONS, ids=[m[0] for m in LAB_MUTATIONS]
)
def test_lab_fetch_mutations_are_caught(case: str, old: str, new: str, message: str) -> None:
    real = src_files()[LAB_FETCH]
    assert real.count(old) == 1, f"{case}: mutation no longer applies"
    found = rule_lab_fetch({LAB_FETCH: real.replace(old, new)})
    assert any(message in line for line in found), (case, found)


@pytest.mark.parametrize(
    ("rule", "path", "source"),
    [
        (rule_literals, "x.py", '"""Explains /order in a docstring."""'),
        (rule_http, HTTP_MODULE, 'urllib.request.Request(u, headers=h, method="GET")'),
        (rule_http, HTTP_MODULE, "urllib.request.urlopen(request, timeout=5)"),
        (rule_public_paths, BITVAVO_MODULE, 'self._get("/ticker/book")'),
        (rule_public_paths, BITVAVO_MODULE, 'self._get(f"/{m}/candles?{q}")'),
        (rule_public_paths, LAB_FETCH, "self._transport.get_json_and_headers(checked_url(u))"),
        (rule_imports, GIT_MODULE, "import subprocess"),
        (
            rule_subprocess_calls,
            GIT_MODULE,
            git_module(
                "return subprocess.run(list(REV_PARSE_HEAD if wanted == REV_PARSE_HEAD else"
                " STATUS_PORCELAIN), cwd=d, stdin=subprocess.DEVNULL, capture_output=True,"
                ' text=True, encoding="utf-8", errors="replace", timeout=t, check=True,'
                " shell=False, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})"
            ),
        ),
        (
            rule_subprocess_calls,
            GIT_MODULE,
            '"""Starts git via subprocess."""\n' + git_module("return " + GIT_RUN),
        ),
        (
            rule_subprocess_calls,
            "lab/register.py",
            'R = 1\n"""Starts nothing (no ``subprocess``)."""',
        ),
        (rule_process_starts, PROCESS_POOL_MODULE, f"from concurrent.futures import {POOL}"),
        (rule_process_starts, PROCESS_POOL_MODULE, f"{POOL}(max_workers=min(w, n))"),
        (rule_process_starts, "llm/x.py", "from concurrent.futures import ThreadPoolExecutor"),
        (rule_process_starts, LAB_FETCH, 'con.execute("x"); con.executescript(s)'),
        (rule_process_starts, "web/x.py", 'f = os.O_RDONLY | getattr(os, "O_BINARY", 0)'),
        (rule_process_starts, "x.py", "spawned = 1\nexecutor = 2\nsys.executable"),
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
