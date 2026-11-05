#!/usr/bin/env python3
"""Lightweight TwinCAT 3 repository checks (no TwinCAT installation needed).

Checks:
  1. All TwinCAT XML files are well-formed and decode as declared (UTF-8).
  2. POU/DUT/GVL/interface (.TcIO): the Name attribute matches the file name.
  3. Every <Compile Include="..."> in *.plcproj points to an existing file.
  4. No generated artifacts are tracked or about to be added (_Boot, _CompileInfo, *.compileinfo, ...).

Usage: python scripts/check-twincat.py [root]   (exit code 1 on any finding)
"""
from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

XML_SUFFIXES = {
    ".tcpou", ".tcdut", ".tcgvl", ".tcio", ".tctto", ".tcvis",
    ".tctlo", ".tcgtlo", ".tcipo", ".tcvmo", ".tcrmo",
    ".plcproj", ".tsproj", ".xti", ".tspproj",
}
# Element under <TcPlcObject> whose Name must equal the file stem.
# .TcIO = interface (element <Itf>).
NAMED_OBJECTS = {".tcpou": "POU", ".tcdut": "DUT", ".tcgvl": "GVL", ".tcio": "Itf"}
FORBIDDEN_DIRS = {
    "_Boot", "_CompileInfo", "_Libraries", "_ModuleInstall", "_Deployment", "_Repository",
    ".TcGit", ".vs",
}
FORBIDDEN_SUFFIXES = {
    ".compileinfo", ".compiled-library", ".tclrs", ".~u", ".tpy", ".bak", ".orig", ".xtv",
}
FORBIDDEN_NAMES = {"LineIDs.dbg"}


def tracked_files(root: Path) -> list[Path]:
    """Tracked + untracked-but-not-ignored files; falls back to a directory walk outside a repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, check=True, capture_output=True,
        ).stdout.decode("utf-8")
        return [root / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def check_xml(path: Path, root_dir: Path, errors: list[str]) -> None:
    shown = path.relative_to(root_dir)
    raw = path.read_bytes()
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        errors.append(f"{shown}: not valid UTF-8 ({exc})")
        return
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append(f"{shown}: XML not well-formed ({exc})")
        return

    suffix = path.suffix.lower()
    if suffix in NAMED_OBJECTS:
        expected = NAMED_OBJECTS[suffix]
        obj = next((c for c in root if local_name(c.tag) == expected), None)
        if obj is None:
            errors.append(f"{shown}: missing <{expected}> element")
        elif obj.get("Name") != path.stem:
            errors.append(f"{shown}: Name='{obj.get('Name')}' does not match file name")

    if suffix == ".plcproj":
        for el in root.iter():
            if local_name(el.tag) == "Compile" and el.get("Include"):
                rel = Path(*PureWindowsPath(el.get("Include")).parts)
                if not (path.parent / rel).is_file():
                    errors.append(f"{shown}: Compile Include '{el.get('Include')}' not found")


def check_forbidden(path: Path, root: Path, errors: list[str]) -> None:
    rel = path.relative_to(root)
    if (
        FORBIDDEN_DIRS.intersection(rel.parts)
        or path.name in FORBIDDEN_NAMES
        or any(path.name.lower().endswith(s) for s in FORBIDDEN_SUFFIXES)
    ):
        errors.append(f"{rel}: generated file must not be committed (see .gitignore)")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    errors: list[str] = []
    xml_count = 0
    for path in tracked_files(root):
        if not path.is_file():
            continue  # deleted but still in index
        check_forbidden(path, root, errors)
        if path.suffix.lower() in XML_SUFFIXES:
            xml_count += 1
            check_xml(path, root, errors)

    for e in errors:
        print(f"ERROR {e}")
    print(f"check-twincat: {xml_count} XML files checked, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
