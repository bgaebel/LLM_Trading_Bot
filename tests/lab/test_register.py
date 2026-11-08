"""Spec 016 AK8: trial register - dedup of repeats, append-only stores, checksum chain."""

import json
import sqlite3
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from llm_trading_bot.lab.model import (
    DayPoint,
    DepotResult,
    MarketQuality,
    Quarter,
    QuarterResult,
    Variant,
)
from llm_trading_bot.lab.paths import CHAIN_FILE, LAB_DIR_ENV, RUNS_FILE
from llm_trading_bot.lab.register import (
    GENESIS,
    UNKNOWN_COMMIT,
    ChainError,
    CodeState,
    GitRunner,
    Register,
    RegisterError,
    code_state,
    config_hash,
    daily_returns,
    head_from_files,
    trial_key,
    verify_chain,
)
from llm_trading_bot.paper.db import SchemaError

COMMIT = "a" * 40
OTHER = "b" * 40
DATA = "d" * 64


def result(label: str = "2022Q1", variant: Variant | None = None, **kw: object) -> QuarterResult:
    q = Quarter.parse(label)
    depot = DepotResult(
        "trend",
        [
            DayPoint("2022-01-01", 10_100.0, 5_000.0, {"BTC-EUR": 0.5}),
            DayPoint("2022-01-02", 9_999.0, 5_000.0, {"BTC-EUR": 0.5}),
        ],
        end_mid=9_999.0,
        end_liquidated=9_980.0,
    )
    out = QuarterResult(
        q,
        variant or Variant(),
        depots={"trend": depot},
        quality={"BTC-EUR": MarketQuality("BTC-EUR", 100, 99)},
        rounds=1440,
        seconds=3.5,
        dataset_sha256=DATA,
    )
    for name, value in kw.items():
        setattr(out, name, value)
    return out


def open_reg(tmp_path: Path, commit: str = COMMIT, clean: bool = True) -> Register:
    return Register.open(
        tmp_path / RUNS_FILE,
        tmp_path / CHAIN_FILE,
        code=CodeState(commit, clean),
        now=lambda: 1_700_000_000.0,
    )


def test_rerun_deduplicated(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        first = reg.record(result())
        # Same trial, data and commit: only the wall-clock runtime differs.
        again = reg.record(result(seconds=9.9))
        assert (first.merged, first.repeat) == (False, 1)
        assert (again.merged, again.repeat, again.run_id) == (True, 2, first.run_id)
        assert again.trial_key == first.trial_key
        runs = reg.list_runs()
        assert len(runs) == 1 and runs[0].repeats == 2
        assert reg.trial_count() == 1
        # The archive grew: another dataset, still the same trial (config x window x variant).
        other_data = reg.record(result(dataset_sha256="e" * 64))
        assert not other_data.merged and reg.trial_count() == 1 and len(reg.list_runs()) == 2
        assert reg.verify().ok and reg.verify().lines == 3
    # Another commit is another configuration, hence another trial.
    with open_reg(tmp_path, commit=OTHER) as reg:
        assert not reg.record(result()).merged
        assert reg.trial_count() == 2 and reg.config_count() == 2
        assert reg.verify().ok


def test_differing_repeat_kept_as_own_run(tmp_path: Path) -> None:
    with open_reg(tmp_path, clean=False) as reg:
        reg.record(result())
        changed = reg.record(result(rounds=1439))
        assert not changed.merged and changed.repeat == 1
        assert len(reg.list_runs()) == 2 and reg.trial_count() == 1


def test_chain_detects_loss(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        for label in ("2021Q2", "2021Q3", "2021Q4", "2022Q1"):
            reg.record(result(label))
        good = reg.verify()
        assert good.ok and good.lines == 4 and good.head != GENESIS
        chain = tmp_path / CHAIN_FILE
        lines = chain.read_text(encoding="utf-8").splitlines(keepends=True)

        def variant_of(name: str, content: list[str]) -> Path:
            path = tmp_path / name
            path.write_text("".join(content), encoding="utf-8", newline="")
            return path

        assert verify_chain(variant_of("same.jsonl", lines)).ok
        removed = variant_of("removed.jsonl", [lines[0], lines[2], lines[3]])
        assert not verify_chain(removed).ok
        changed_line = lines[1].replace('"2021Q3"', '"2023Q3"')
        changed = variant_of("changed.jsonl", [lines[0], changed_line, *lines[2:]])
        assert any("hash mismatch" in p for p in verify_chain(changed).problems)
        reordered = variant_of("reordered.jsonl", [lines[0], lines[2], lines[1], lines[3]])
        assert not verify_chain(reordered).ok
        cut = variant_of("cut.jsonl", lines[:3])
        assert verify_chain(cut).ok  # a cut-off tail is only visible against the register
        assert not verify_chain(cut, expected_lines=4, expected_head=good.head).ok
        half = variant_of("half.jsonl", [*lines[:3], lines[3][:20]])
        assert not verify_chain(half).ok

        # The real chain loses its last line: the register notices and refuses to build on it.
        chain.write_text("".join(lines[:3]), encoding="utf-8", newline="")
        assert not reg.verify().ok
        with pytest.raises(ChainError):
            reg.record(result("2022Q2"))
        assert len(reg.list_runs()) == 4  # nothing recorded by the refused attempt
        chain.write_text("".join(lines), encoding="utf-8", newline="")
        assert reg.verify().ok
        assert reg.record(result("2022Q2")).seq == 5


def test_aborted_quarter_recorded(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        rec = reg.record_aborted(Quarter.parse("2023Q2"), Variant(), "MemoryError in worker")
        (run,) = reg.list_runs(status="aborted")
        assert run.run_id == rec.run_id and run.error == "MemoryError in worker"
        assert run.quarter == "2023Q2" and not run.graded
        assert reg.result(rec.run_id).status == "aborted"
        # Not a result anyone could look at, but never forgotten.
        assert reg.trial_count() == 0 and reg.trial_count(statuses=None) == 1
        event = json.loads((tmp_path / CHAIN_FILE).read_text(encoding="utf-8"))["event"]
        assert (event["status"], event["error"]) == ("aborted", "MemoryError in worker")
        # The same quarter later succeeds: one trial, two runs.
        reg.record(result("2023Q2"))
        assert reg.trial_count(statuses=None) == 1 and len(reg.list_runs()) == 2


def test_register_is_append_only(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        reg.record(result())
        reg.record(result())
        for sql in (
            "UPDATE runs SET status='ok'",
            "DELETE FROM runs",
            "UPDATE repeats SET clean=1",
            "DELETE FROM repeats",
            "DELETE FROM daily_returns",
            "UPDATE events SET line=''",
            "DELETE FROM events",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                reg.con.execute(sql)
        assert reg.verify().ok


def test_daily_returns_stored(tmp_path: Path) -> None:
    res = result()
    returns = daily_returns(res.depots["trend"])
    assert [day for day, _ in returns] == ["2022-01-01", "2022-01-02"]
    assert [r for _, r in returns] == pytest.approx([0.01, 9_999.0 / 10_100.0 - 1])
    # Compounded, the daily returns give the depot's change over the quarter.
    assert (1 + returns[0][1]) * (1 + returns[1][1]) == pytest.approx(9_999.0 / 10_000.0)
    with open_reg(tmp_path) as reg:
        rec = reg.record(res)
        assert reg.returns(rec.run_id) == {"trend": daily_returns(res.depots["trend"])}
        assert reg.result(rec.run_id) == res


def test_config_hash_and_trial_key() -> None:
    base = config_hash(Variant(), COMMIT)
    shuffled = ["buyhold", "trend", "meanrev", "ensemble", "breakout"]
    assert base == config_hash(Variant(), COMMIT, depots=shuffled)
    assert base != config_hash(Variant(fee_rate=0.003), COMMIT)
    assert base != config_hash(Variant(), OTHER)
    assert base != config_hash(Variant(), COMMIT, depots=["trend"])
    assert base != config_hash(Variant(), COMMIT, strategy_version="v0.4.0")
    q1, q2 = Quarter.parse("2022Q1"), Quarter.parse("2022Q2")
    assert trial_key(base, q1, Variant()) != trial_key(base, q2, Variant())


def test_diagnostic_and_filters(tmp_path: Path) -> None:
    diag = Variant(name="gap-tolerance", gap_window=3, diagnostic=True)
    with open_reg(tmp_path) as reg:
        reg.record(result("2022Q1"))
        reg.record(result("2022Q2"))
        reg.record(result("2022Q1", diag))
        assert reg.trial_count() == 3
        assert reg.trial_count(include_diagnostic=False) == 2
        assert reg.config_count() == 2 and reg.config_count(include_diagnostic=False) == 1
        assert [r.variant for r in reg.list_runs(diagnostic=True)] == ["gap-tolerance"]
        assert [r.quarter for r in reg.list_runs(variant="base")] == ["2022Q1", "2022Q2"]
        assert len(reg.list_runs(quarter="2022Q1")) == 2
        assert len(reg.list_runs(commit=COMMIT, graded=True)) == 3
        assert reg.list_runs(status="sealed") == []


def test_invalid_status_refused(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg, pytest.raises(RegisterError):
        reg.record(result(status="OK; DROP"))


def test_copy_chain_never_overwrites(tmp_path: Path) -> None:
    with open_reg(tmp_path) as reg:
        reg.record(result())
        dest = tmp_path / "report" / "register.jsonl"
        assert reg.copy_chain(dest).ok
        assert dest.read_bytes() == (tmp_path / CHAIN_FILE).read_bytes()
        with pytest.raises(FileExistsError):
            reg.copy_chain(dest)


def test_other_schema_version_refused(tmp_path: Path) -> None:
    open_reg(tmp_path).close()
    con = sqlite3.connect(tmp_path / RUNS_FILE)
    con.execute("PRAGMA user_version=2")
    con.close()
    with pytest.raises(SchemaError):
        open_reg(tmp_path)


def test_default_paths_in_lab_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LAB_DIR_ENV, str(tmp_path / "lab"))
    with Register.open(code=CodeState(COMMIT, True)) as reg:
        reg.record(result())
    assert (tmp_path / "lab" / RUNS_FILE).is_file() and (tmp_path / "lab" / CHAIN_FILE).is_file()


def fake_git(head: str | Exception, status: str | Exception) -> "FakeGit":
    return FakeGit(head, status)


class FakeGit:
    def __init__(self, head: str | Exception, status: str | Exception) -> None:
        self.answers = {"rev-parse": head, "status": status}

    def __call__(self, args: Sequence[str]) -> str:
        answer = self.answers[args[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_code_state_from_git() -> None:
    failed = subprocess.CalledProcessError(128, ["git"])
    assert code_state(fake_git(COMMIT + "\n", "")) == CodeState(COMMIT, True)
    assert code_state(fake_git(COMMIT + "\n", " M src/x.py\n")) == CodeState(COMMIT, False)
    assert code_state(fake_git(COMMIT + "\n", "?? new.py\n")) == CodeState(COMMIT, False)
    assert code_state(fake_git(COMMIT, failed)) == CodeState(COMMIT, False)
    assert code_state(fake_git(failed, "")) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(fake_git(FileNotFoundError("git"), "")) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(fake_git("fatal: not a commit", "")) == CodeState(UNKNOWN_COMMIT, False)


def real_git(repo: Path) -> GitRunner:
    """A runner that starts git, as a caller outside src may supply (src never starts processes)."""

    def run(args: Sequence[str]) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        )
        return done.stdout

    return run


def test_outside_git_checkout_is_unknown_and_dirty(tmp_path: Path) -> None:
    # A real git in a folder that is no checkout (or no git installed at all).
    assert code_state(real_git(tmp_path)) == CodeState(UNKNOWN_COMMIT, False)
    assert code_state(repo=tmp_path) == CodeState(UNKNOWN_COMMIT, False)


def git_dir(root: Path, head: str, refs: dict[str, str], packed: str = "") -> Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    for name, sha in refs.items():
        (git / name).write_text(sha + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return git


def test_head_from_files(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    git_dir(loose, "ref: refs/heads/main", {"refs/heads/main": COMMIT})
    (loose / "src" / "pkg").mkdir(parents=True)
    assert head_from_files(loose / "src" / "pkg") == COMMIT
    # Without a runner the commit is known, cleanliness is not.
    assert code_state(repo=loose / "src") == CodeState(COMMIT, False)

    packed = tmp_path / "packed"
    git_dir(packed, "ref: refs/heads/main", {}, f"# pack-refs\n{OTHER} refs/heads/main\n")
    assert head_from_files(packed) == OTHER

    detached = tmp_path / "detached"
    git_dir(detached, COMMIT, {})
    assert head_from_files(detached) == COMMIT

    # A linked worktree: .git is a file, the branch ref lives in the common git dir.
    main = git_dir(tmp_path / "main", "ref: refs/heads/main", {"refs/heads/wt": OTHER})
    wt_git = main / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/wt\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    assert head_from_files(tmp_path / "wt") == OTHER

    broken = tmp_path / "broken"
    git_dir(broken, "ref: refs/heads/gone", {})
    assert head_from_files(broken) == UNKNOWN_COMMIT
    assert head_from_files(tmp_path / "nowhere") == UNKNOWN_COMMIT


def test_head_from_files_matches_git() -> None:
    repo = Path(__file__).resolve().parent
    try:
        expected = real_git(repo)(["rev-parse", "HEAD"]).strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git checkout or no git")
    assert head_from_files(repo) == expected


def test_commit_and_clean_flag_recorded(tmp_path: Path) -> None:
    with open_reg(tmp_path, commit=OTHER, clean=False) as reg:
        reg.record(result())
        (run,) = reg.list_runs()
        assert (run.commit, run.clean) == (OTHER, False)
