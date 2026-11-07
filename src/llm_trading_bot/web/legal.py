"""Impressum and privacy policy as pages (Spec 014, ADR-0025).

The Impressum holds the operator's name and address, so it lives only on the server and is read
on every request; the privacy policy is ``web/datenschutz.py``. Both are written in a tiny markup
that ``render`` turns into HTML - escaped first, so nothing in the file can become markup.
"""

import os
import re
import stat
from html import escape
from pathlib import Path

MAX_IMPRESSUM_BYTES = 16 * 1024
"""More than any Impressum needs; a bigger file is a mistake, not a page."""

# AK4: the only thing that becomes a link. No scheme other than mailto:, no '?' (parameters),
# no quote or angle bracket - the match is safe inside an attribute as it is.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _inline(text: str) -> str:
    """One line of text: escaped, then e-mail addresses as mailto links."""
    escaped = escape(text, quote=True)
    return EMAIL.sub(lambda m: f'<a href="mailto:{m[0]}">{m[0]}</a>', escaped)


def _block(lines: list[str]) -> str:
    """A block of non-blank lines: headings stand alone, '- ' lines form a list, the rest a
    paragraph whose single line breaks are kept."""
    out: list[str] = []
    items: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    for line in lines:
        if line.startswith("## "):
            flush()
            out.append(f"<h2>{_inline(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            flush()
            out.append(f"<h1>{_inline(line[2:].strip())}</h1>")
        elif line.startswith("- "):
            if para:
                flush()
            items.append(_inline(line[2:].strip()))
        else:
            if items:
                flush()
            para.append(_inline(line))
    flush()
    return "".join(out)


def render(text: str) -> str:
    """The mini markup as HTML: blocks separated by blank lines; '# ' and '## ' headings, '- '
    list items, everything else a paragraph. A BOM is dropped, CRLF counts as LF."""
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line:
            current.append(line)
        elif current:
            blocks.append(_block(current))
            current = []
    if current:
        blocks.append(_block(current))
    return "".join(blocks)


def read_impressum(path: Path) -> str | None:
    """The Impressum, or None if it is missing or not fit to show (AK3).

    Opened without following a symlink and without blocking on a FIFO; only a regular file of
    at most MAX_IMPRESSUM_BYTES in UTF-8 counts. The reason is never shown to the visitor.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks: list[bytes] = []
        size = 0
        # One byte more than allowed tells "too big" apart from "exactly at the limit".
        while size <= MAX_IMPRESSUM_BYTES:
            chunk = os.read(fd, MAX_IMPRESSUM_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    if size > MAX_IMPRESSUM_BYTES:
        return None
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = text.removeprefix("﻿")
    # The upload refuses NUL as well (AK9): a file with one was not written by it.
    if not text.strip() or "\x00" in text:
        return None
    return text
