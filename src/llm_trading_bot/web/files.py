"""Private files for the web UI: written atomically and readable by the owner only."""

import os
import sys
import tempfile
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` atomically with mode 600 (POSIX); a crash leaves the old file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if sys.platform != "win32":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
