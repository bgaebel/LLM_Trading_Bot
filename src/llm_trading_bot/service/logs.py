"""Logging of the service: one line per round, trades and problems on stdout (journald).

ADR-0017: keys never reach a log line, not even inside an exception message or a traceback.
"""

import logging
import re
import time
from typing import TextIO

LOGGER = "llm_trading_bot.service"
# OpenAI keys (``sk-...``, ``sk-proj-...``); the rest of a line stays readable. Deliberately
# without a word boundary: a key glued to other text is caught too, at the price of the odd
# mangled word ("risk-free" -> "risk-***").
_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def redact(text: str) -> str:
    return _KEY.sub("sk-***", text)


class RedactingFormatter(logging.Formatter):
    """Redacts the finished line, so message, arguments and traceback are all covered."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level_name: str, stream: TextIO, *, journal: bool) -> logging.Logger:
    """``journal``: stdout goes to journald, which stamps every line itself."""
    level = logging.getLevelNamesMapping().get(level_name.strip().upper())
    if level is None or level_name.strip().upper() in {"NOTSET", "WARN", "FATAL"}:
        raise ValueError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR: {level_name!r}")
    logger = logging.getLogger(LOGGER)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    fmt = "%(levelname)s %(message)s" if journal else "%(asctime)s %(levelname)s %(message)s"
    formatter = RedactingFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
