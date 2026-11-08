"""Trial register of the strategy lab (Spec 016 AK8, ADR-0026).

Every backtest section the lab runs is a trial: config hash x data window x variant. The count
of trials is what multiple-testing corrections (deflated Sharpe ratio, Holm/BHY) need, so the
register must never lose one and never count an identical repeat twice.

Two stores, each append-only:

- ``runs.db``: one ``runs`` row per distinct outcome of a trial, with code commit, clean work
  tree, dataset checksum, status, daily returns per depot (table ``daily_returns``) and the full
  ``QuarterResult`` JSON. An identical repeat (same trial key, dataset checksum, commit and
  outcome) adds a row to ``repeats`` instead of a second run; the repeat counter is
  ``1 + COUNT(repeats)``. Triggers refuse every UPDATE and DELETE, so results are never
  overwritten or removed, not even by a careless ad-hoc query.
- the JSONL chain (``register.jsonl``): one line per register event, each line carrying the hash
  of the previous one. A copy goes with every report, so a reader can check that no trial was
  dropped, changed or reordered without access to ``runs.db``. The same lines are kept in the
  ``events`` table; the register refuses to write while file and table disagree (fail closed),
  because appending to a broken chain would hide the break.

A repeat whose outcome differs (e.g. from a dirty work tree on the same commit) is kept as its
own run under the same trial key: nothing is lost and the trial count does not grow.
"""

import dataclasses
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from llm_trading_bot.lab.model import (
    DEPOTS,
    STRATEGY_VERSION,
    DepotResult,
    Quarter,
    QuarterResult,
    Variant,
)
from llm_trading_bot.lab.paths import CHAIN_FILE, RUNS_FILE, lab_dir
from llm_trading_bot.paper.db import START_CASH, SchemaError

SCHEMA_VERSION = 1
UNKNOWN_COMMIT = "unknown"
GENESIS = "0" * 64
"""``prev_sha256`` of the first chain line."""
BUSY_TIMEOUT_MS = 10_000
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_STATUS = re.compile(r"[a-z][a-z_]*")
_LINE_KEYS = frozenset({"seq", "prev_sha256", "event", "sha256"})

_TABLES = ("runs", "repeats", "daily_returns", "events")
_SCHEMA = """
CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  trial_key TEXT NOT NULL, config_hash TEXT NOT NULL, strategy_version TEXT NOT NULL,
  depots TEXT NOT NULL, quarter TEXT NOT NULL, window_start INTEGER NOT NULL,
  window_end INTEGER NOT NULL, variant TEXT NOT NULL, diagnostic INTEGER NOT NULL,
  code_commit TEXT NOT NULL, clean INTEGER NOT NULL, dataset_sha256 TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT NOT NULL, graded INTEGER NOT NULL,
  outcome_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
  UNIQUE(trial_key,dataset_sha256,code_commit,outcome_sha256));
CREATE TABLE repeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, created_at TEXT NOT NULL,
  clean INTEGER NOT NULL, seconds REAL NOT NULL, result_sha256 TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id));
CREATE TABLE daily_returns (
  run_id INTEGER NOT NULL, depot TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL,
  ret REAL NOT NULL,
  PRIMARY KEY(run_id,depot,day), FOREIGN KEY(run_id) REFERENCES runs(id));
CREATE TABLE events (
  seq INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
  run_id INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE, line TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id));
CREATE INDEX idx_runs_trial ON runs(trial_key);
CREATE INDEX idx_repeats_run ON repeats(run_id);
""" + "".join(
    f"CREATE TRIGGER {t}_no_{op.lower()} BEFORE {op} ON {t} "
    f"BEGIN SELECT RAISE(ABORT,'trial register is append-only'); END;\n"
    for t in _TABLES
    for op in ("UPDATE", "DELETE")
)

_COLUMNS: dict[str, tuple[str, ...]] = {
    "runs": (
        "id",
        "created_at",
        "trial_key",
        "config_hash",
        "strategy_version",
        "depots",
        "quarter",
        "window_start",
        "window_end",
        "variant",
        "diagnostic",
        "code_commit",
        "clean",
        "dataset_sha256",
        "status",
        "error",
        "graded",
        "outcome_sha256",
        "result_sha256",
        "result_json",
    ),
    "repeats": ("id", "run_id", "created_at", "clean", "seconds", "result_sha256"),
    "daily_returns": ("run_id", "depot", "day", "value", "ret"),
    "events": ("seq", "created_at", "kind", "run_id", "sha256", "line"),
}
_TRIGGERS = frozenset(f"{t}_no_{op}" for t in _TABLES for op in ("update", "delete"))


class RegisterError(Exception):
    """The register cannot record safely; nothing was written."""


class ChainError(RegisterError):
    """Chain file and ``events`` table disagree; the register refuses to append."""


GitRunner = Callable[[Sequence[str]], str]
"""Runs ``git <args>`` (``rev-parse HEAD``, ``status --porcelain``) and returns stdout; raises
any Exception if git fails. Injected: src never starts processes (no ``subprocess``, Spec 012
order-path guard), so a runner comes from the caller."""


@dataclass(frozen=True)
class CodeState:
    commit: str
    """``git rev-parse HEAD``, or ``unknown`` outside a git checkout."""
    clean: bool
    """``git status --porcelain`` printed nothing; False whenever this cannot be shown."""


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def head_from_files(start: Path) -> str:
    """HEAD of the git checkout containing ``start``, read from the ``.git`` files without
    running git: plain checkouts, linked worktrees (``.git`` file + ``commondir``), detached
    HEAD, loose and packed refs. ``unknown`` if any of it cannot be read."""
    try:
        gitdir = None
        for folder in (start, *start.parents):
            dot = folder / ".git"
            if dot.is_dir():
                gitdir = dot
                break
            if dot.is_file():
                text = dot.read_text(encoding="utf-8").strip()
                if text.startswith("gitdir:"):
                    gitdir = (folder / text.removeprefix("gitdir:").strip()).resolve()
                    break
        if gitdir is None:
            return UNKNOWN_COMMIT
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if _COMMIT.fullmatch(head):
            return head
        if not head.startswith("ref: "):
            return UNKNOWN_COMMIT
        ref = head.removeprefix("ref: ").strip()
        for base in (gitdir, common):
            loose = base / ref
            if loose.is_file():
                value = loose.read_text(encoding="utf-8").strip()
                return value if _COMMIT.fullmatch(value) else UNKNOWN_COMMIT
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                sha, _, name = line.partition(" ")
                if name == ref and _COMMIT.fullmatch(sha):
                    return sha
    except (OSError, ValueError):
        pass
    return UNKNOWN_COMMIT


def code_state(runner: GitRunner | None = None, *, repo: Path | None = None) -> CodeState:
    """The code behind a run. Default: the checkout this package is imported from.

    Anything git cannot prove counts against the run: no commit -> ``unknown`` and dirty, a
    failing ``git status`` -> dirty. Untracked files count as dirty, since they may be imported.
    Without a runner the commit is read from the ``.git`` files, and the tree counts as dirty:
    cleanliness needs ``git status``.
    """
    if runner is None:
        return CodeState(head_from_files(repo or _package_dir()), False)
    try:
        commit = runner(["rev-parse", "HEAD"]).strip()
    except Exception:  # any failure of an injected runner: nothing is proven
        return CodeState(UNKNOWN_COMMIT, False)
    if not _COMMIT.fullmatch(commit):
        return CodeState(UNKNOWN_COMMIT, False)
    try:
        status = runner(["status", "--porcelain"])
    except Exception:
        return CodeState(commit, False)
    return CodeState(commit, status.strip() == "")


def canonical(obj: object) -> str:
    """One text per value: hashes must not depend on key order or whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(
    variant: Variant,
    commit: str,
    *,
    depots: Collection[str] = DEPOTS,
    strategy_version: str = STRATEGY_VERSION,
) -> str:
    """sha256 of what decides a run's outcome apart from the data: strategy version, depot set,
    variant and code commit. Depots are sorted - the same set is the same configuration."""
    return sha256_text(
        canonical(
            {
                "strategy_version": strategy_version,
                "depots": sorted(depots),
                "variant": dataclasses.asdict(variant),
                "commit": commit,
            }
        )
    )


def trial_key(config: str, quarter: Quarter, variant: Variant) -> str:
    """Config hash x data window x variant (ADR-0026)."""
    window = {"label": quarter.label, "start": quarter.start, "end": quarter.end}
    return sha256_text(canonical({"config": config, "window": window, "variant": variant.name}))


def outcome_sha256(result: QuarterResult) -> str:
    """Checksum of a result without its wall-clock runtime, which differs on every repeat."""
    return sha256_text(dataclasses.replace(result, seconds=0.0).to_json())


def daily_returns(depot: DepotResult, start_cash: float = START_CASH) -> list[tuple[str, float]]:
    """Simple return per UTC day from the end-of-day values; the first day against the fresh
    quarter capital (AK4), so the returns compound to the quarter's net return."""
    out, prev = [], start_cash
    for point in depot.days:
        out.append((point.day, point.value / prev - 1 if prev else 0.0))
        prev = point.value
    return out


@dataclass(frozen=True)
class ChainCheck:
    lines: int
    head: str
    """sha256 of the last valid line, GENESIS for an empty chain."""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _line_sha(seq: int, prev: str, event: dict[str, Any]) -> str:
    return sha256_text(canonical({"seq": seq, "prev_sha256": prev, "event": event}))


def _make_line(seq: int, prev: str, event: dict[str, Any]) -> tuple[str, str]:
    sha = _line_sha(seq, prev, event)
    return canonical({"seq": seq, "prev_sha256": prev, "event": event, "sha256": sha}), sha


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """Lines of the chain and whether the file ends cleanly (a crash mid-append does not)."""
    if not path.exists():
        return [], True
    text = path.read_text(encoding="utf-8")
    if not text:
        return [], True
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"), text.endswith("\n")


def verify_chain(
    path: Path, *, expected_lines: int | None = None, expected_head: str | None = None
) -> ChainCheck:
    """Check every line's hash, link and sequence number.

    A changed, removed or reordered line breaks a hash or a link. Only a cut-off tail leaves a
    valid chain, so the caller passes the length and head it knows (the register keeps both).
    """
    lines, complete = _read_lines(path)
    problems: list[str] = []
    prev = GENESIS
    if not complete:
        problems.append("last line incomplete (no newline at end of file)")
    for n, text in enumerate(lines, start=1):
        try:
            obj = json.loads(text)
            seq, line_prev, event, sha = obj["seq"], obj["prev_sha256"], obj["event"], obj["sha256"]
        except (ValueError, KeyError, TypeError):
            problems.append(f"line {n}: not a chain entry")
            continue
        # Byte-exact lines: a copy attached to a report must be the very text that was hashed.
        if not isinstance(event, dict) or text != canonical(obj) or set(obj) != _LINE_KEYS:
            problems.append(f"line {n}: not in canonical form")
        if seq != n:
            problems.append(f"line {n}: sequence number {seq}, expected {n}")
        if line_prev != prev:
            problems.append(f"line {n}: link to previous line broken")
        if isinstance(event, dict) and _line_sha(seq, line_prev, event) != sha:
            problems.append(f"line {n}: content changed (hash mismatch)")
        prev = sha if isinstance(sha, str) else prev
    if expected_lines is not None and len(lines) != expected_lines:
        problems.append(f"chain has {len(lines)} lines, register has {expected_lines}")
    if expected_head is not None and prev != expected_head:
        problems.append("last line is not the register's head")
    return ChainCheck(len(lines), prev, problems)


@dataclass(frozen=True)
class Recorded:
    run_id: int
    trial_key: str
    repeat: int
    """1 for a new run, n for the n-th identical execution."""
    merged: bool
    """True if this execution was merged into an existing run."""
    seq: int
    """Chain line of this event."""


@dataclass(frozen=True)
class RunRow:
    run_id: int
    created_at: str
    trial_key: str
    config_hash: str
    quarter: str
    variant: str
    diagnostic: bool
    commit: str
    clean: bool
    dataset_sha256: str
    status: str
    error: str
    graded: bool
    repeats: int
    """How often this exact outcome was produced (1 + merged repeats)."""


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


@contextmanager
def _tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def check_register_schema(con: sqlite3.Connection) -> None:
    """Raise SchemaError unless ``con`` is a register of exactly this version and shape,
    including the append-only triggers. Never migrated: the register is the evidence."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"runs.db schema version {version}, expected {SCHEMA_VERSION}; no automatic migration"
        )
    for table, expected in _COLUMNS.items():
        actual = tuple(r[1] for r in con.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise SchemaError(f"runs.db {table}: columns {actual}, expected {expected}")
    triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if missing := _TRIGGERS - triggers:
        raise SchemaError(f"runs.db: append-only triggers missing {sorted(missing)}")


class Register:
    """Append-only trial register. One writer at a time (``BEGIN IMMEDIATE`` serialises).

    The code state is taken once at ``open``: a backtest records all its quarters from the code
    it started with.
    """

    def __init__(
        self,
        con: sqlite3.Connection,
        chain_path: Path,
        code: CodeState,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.con = con
        self.chain_path = chain_path
        self.code = code
        self._now = now

    @classmethod
    def open(
        cls,
        db_path: Path | None = None,
        chain_path: Path | None = None,
        *,
        code: CodeState | None = None,
        runner: GitRunner | None = None,
        now: Callable[[], float] = time.time,
    ) -> Self:
        """Open (or create) ``runs.db`` and the chain, by default in the lab folder."""
        db_path = db_path or lab_dir() / RUNS_FILE
        chain_path = chain_path or lab_dir() / CHAIN_FILE
        db_path.parent.mkdir(parents=True, exist_ok=True)
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path, timeout=20, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            con.execute("PRAGMA foreign_keys=ON")
            blank = con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is None
            if con.execute("PRAGMA user_version").fetchone()[0] == 0 and blank:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(
                    f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={SCHEMA_VERSION};COMMIT;"
                )
            check_register_schema(con)
        except BaseException:
            con.close()
            raise
        return cls(con, chain_path, code or code_state(runner), now)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- writing -------------------------------------------------------------------------

    def record(self, result: QuarterResult, *, depots: Collection[str] = DEPOTS) -> Recorded:
        """Register one finished, refused or aborted quarter. Identical repeats are merged.

        ``depots`` is the configured depot set (an aborted result may carry none).
        """
        if not _STATUS.fullmatch(result.status):
            raise RegisterError(f"invalid status {result.status!r}")
        config = config_hash(result.variant, self.code.commit, depots=depots)
        key = trial_key(config, result.quarter, result.variant)
        outcome = outcome_sha256(result)
        result_json = result.to_json()
        result_sha = sha256_text(result_json)
        created = _iso(self._now())
        with _tx(self.con) as con:
            head_seq, head_sha = self._head()
            self._check_tail(head_seq, head_sha)
            row = con.execute(
                "SELECT id FROM runs WHERE trial_key=? AND dataset_sha256=? AND code_commit=? "
                "AND outcome_sha256=?",
                (key, result.dataset_sha256, self.code.commit, outcome),
            ).fetchone()
            if row is not None:
                run_id, merged, kind = int(row["id"]), True, "repeat"
                con.execute(
                    "INSERT INTO repeats(run_id,created_at,clean,seconds,result_sha256) "
                    "VALUES (?,?,?,?,?)",
                    (run_id, created, int(self.code.clean), result.seconds, result_sha),
                )
            else:
                merged, kind = False, "run"
                run_id = self._insert_run(
                    result, config, key, outcome, result_json, result_sha, created, depots
                )
            repeat = (
                1
                + con.execute("SELECT COUNT(*) FROM repeats WHERE run_id=?", (run_id,)).fetchone()[
                    0
                ]
            )
            event = {
                "kind": kind,
                "created_at": created,
                "run_id": run_id,
                "repeat": repeat,
                "trial_key": key,
                "config_hash": config,
                "quarter": result.quarter.label,
                "variant": result.variant.name,
                "diagnostic": result.variant.diagnostic,
                "status": result.status,
                "error": result.error,
                "graded": result.graded,
                "commit": self.code.commit,
                "clean": self.code.clean,
                "dataset_sha256": result.dataset_sha256,
                "outcome_sha256": outcome,
                "result_sha256": result_sha,
            }
            seq = head_seq + 1
            line, sha = _make_line(seq, head_sha, event)
            con.execute(
                "INSERT INTO events(seq,created_at,kind,run_id,sha256,line) VALUES (?,?,?,?,?,?)",
                (seq, created, kind, run_id, sha, line),
            )
            # Appended inside the transaction: if the append fails, nothing is recorded. A
            # failing COMMIT afterwards leaves one extra chain line, which _check_tail and
            # verify() report instead of building on it.
            with self.chain_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        return Recorded(run_id, key, repeat, merged, seq)

    def record_aborted(
        self,
        quarter: Quarter,
        variant: Variant,
        error: str,
        *,
        dataset_sha256: str = "",
        depots: Collection[str] = DEPOTS,
    ) -> Recorded:
        """A quarter that crashed is a trial too: recorded with status ``aborted`` and the error,
        so a rerun that happens to work cannot hide it."""
        result = QuarterResult(
            quarter,
            variant,
            status="aborted",
            error=error or "unknown error",
            dataset_sha256=dataset_sha256,
        )
        return self.record(result, depots=depots)

    def _insert_run(
        self,
        result: QuarterResult,
        config: str,
        key: str,
        outcome: str,
        result_json: str,
        result_sha: str,
        created: str,
        depots: Collection[str],
    ) -> int:
        q, v = result.quarter, result.variant
        cur = self.con.execute(
            "INSERT INTO runs(created_at,trial_key,config_hash,strategy_version,depots,quarter,"
            "window_start,window_end,variant,diagnostic,code_commit,clean,dataset_sha256,status,"
            "error,graded,outcome_sha256,result_sha256,result_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                created,
                key,
                config,
                STRATEGY_VERSION,
                canonical(sorted(depots)),
                q.label,
                q.start,
                q.end,
                v.name,
                int(v.diagnostic),
                self.code.commit,
                int(self.code.clean),
                result.dataset_sha256,
                result.status,
                result.error,
                int(result.graded),
                outcome,
                result_sha,
                result_json,
            ),
        )
        run_id = cur.lastrowid
        assert run_id is not None
        for name, depot in sorted(result.depots.items()):
            values = {p.day: p.value for p in depot.days}
            self.con.executemany(
                "INSERT INTO daily_returns(run_id,depot,day,value,ret) VALUES (?,?,?,?,?)",
                [(run_id, name, day, values[day], ret) for day, ret in daily_returns(depot)],
            )
        return run_id

    def _head(self) -> tuple[int, str]:
        row = self.con.execute(
            "SELECT seq, sha256 FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (0, GENESIS) if row is None else (int(row["seq"]), str(row["sha256"]))

    def _check_tail(self, seq: int, sha: str) -> None:
        lines, complete = _read_lines(self.chain_path)
        if not complete or len(lines) != seq:
            raise ChainError(
                f"{self.chain_path} has {len(lines)} lines, runs.db {seq} events; "
                "run verify() and repair by hand - the register does not append to a broken chain"
            )
        if seq:
            try:
                last = json.loads(lines[-1]).get("sha256")
            except (ValueError, AttributeError):
                last = None
            if last != sha:
                raise ChainError(f"last line of {self.chain_path} is not the register's head")

    # --- reading -------------------------------------------------------------------------

    def verify(self) -> ChainCheck:
        """Chain file against the ``events`` table, plus each stored result against its hash."""
        seq, head = self._head()
        check = verify_chain(self.chain_path, expected_lines=seq, expected_head=head)
        problems = list(check.problems)
        lines, _ = _read_lines(self.chain_path)
        for row in self.con.execute("SELECT seq, line FROM events ORDER BY seq"):
            n = int(row["seq"])
            if n <= len(lines) and lines[n - 1] != row["line"]:
                problems.append(f"line {n}: differs from runs.db")
        for row in self.con.execute("SELECT id, result_sha256, result_json FROM runs"):
            if sha256_text(row["result_json"]) != row["result_sha256"]:
                problems.append(f"run {row['id']}: stored result does not match its hash")
        return ChainCheck(check.lines, check.head, problems)

    def copy_chain(self, dest: Path) -> ChainCheck:
        """Copy the chain for a report after verifying it; never overwrites ``dest``."""
        check = self.verify()
        if not check.ok:
            raise ChainError("chain does not verify: " + "; ".join(check.problems))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.chain_path.open("rb") as src, dest.open("xb") as out:
            shutil.copyfileobj(src, out)
        return check

    def _filters(
        self, *, include_diagnostic: bool, statuses: Collection[str] | None
    ) -> tuple[str, list[Any]]:
        where: list[str] = ["1=1"]
        args: list[Any] = []
        if not include_diagnostic:
            where.append("diagnostic=0")
        if statuses is not None:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        return " AND ".join(where), args

    def trial_count(
        self, *, include_diagnostic: bool = True, statuses: Collection[str] | None = ("ok",)
    ) -> int:
        """Distinct trials (config x window x variant) for multiple-testing notes.

        Default: every trial that produced a result someone could look at, diagnostics
        included (ADR-0026: evaluations that can lead to candidates are trials). ``statuses=None``
        also counts trials that only ever aborted or were refused.
        """
        where, args = self._filters(include_diagnostic=include_diagnostic, statuses=statuses)
        sql = f"SELECT COUNT(DISTINCT trial_key) FROM runs WHERE {where}"
        return int(self.con.execute(sql, args).fetchone()[0])

    def config_count(
        self, *, include_diagnostic: bool = True, statuses: Collection[str] | None = ("ok",)
    ) -> int:
        """Distinct configurations across all data windows (same filters as trial_count)."""
        where, args = self._filters(include_diagnostic=include_diagnostic, statuses=statuses)
        sql = f"SELECT COUNT(DISTINCT config_hash) FROM runs WHERE {where}"
        return int(self.con.execute(sql, args).fetchone()[0])

    def list_runs(
        self,
        *,
        quarter: str | None = None,
        variant: str | None = None,
        status: str | None = None,
        diagnostic: bool | None = None,
        graded: bool | None = None,
        commit: str | None = None,
        trial: str | None = None,
    ) -> list[RunRow]:
        """Runs in recording order; every given filter must match."""
        where: list[str] = ["1=1"]
        args: list[Any] = []
        for column, value in (
            ("quarter", quarter),
            ("variant", variant),
            ("status", status),
            ("diagnostic", None if diagnostic is None else int(diagnostic)),
            ("graded", None if graded is None else int(graded)),
            ("code_commit", commit),
            ("trial_key", trial),
        ):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        rows = self.con.execute(
            "SELECT r.*, 1 + (SELECT COUNT(*) FROM repeats p WHERE p.run_id=r.id) AS n "
            f"FROM runs r WHERE {' AND '.join(where)} ORDER BY r.id",
            args,
        ).fetchall()
        return [
            RunRow(
                run_id=r["id"],
                created_at=r["created_at"],
                trial_key=r["trial_key"],
                config_hash=r["config_hash"],
                quarter=r["quarter"],
                variant=r["variant"],
                diagnostic=bool(r["diagnostic"]),
                commit=r["code_commit"],
                clean=bool(r["clean"]),
                dataset_sha256=r["dataset_sha256"],
                status=r["status"],
                error=r["error"],
                graded=bool(r["graded"]),
                repeats=r["n"],
            )
            for r in rows
        ]

    def result(self, run_id: int) -> QuarterResult:
        row = self.con.execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return QuarterResult.from_json(row["result_json"])

    def returns(self, run_id: int) -> dict[str, list[tuple[str, float]]]:
        """Depot -> (day, daily return) of one run, in day order."""
        out: dict[str, list[tuple[str, float]]] = {}
        for r in self.con.execute(
            "SELECT depot, day, ret FROM daily_returns WHERE run_id=? ORDER BY depot, day",
            (run_id,),
        ):
            out.setdefault(r["depot"], []).append((r["day"], r["ret"]))
        return out
