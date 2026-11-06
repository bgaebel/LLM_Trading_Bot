"""Spec 008 AK6: sd_notify over a real Unix datagram socket; without NOTIFY_SOCKET nothing."""

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from llm_trading_bot.service.notify import Notifier
from llm_trading_bot.service.run import PING_SECONDS
from service_fakes import harness

unix_only = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "AF_UNIX"), reason="Unix datagram sockets"
)


Receiver = tuple[Path, socket.socket]


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[Receiver]:
    if sys.platform == "win32":
        pytest.skip("Unix datagram sockets")
    # Short path: Unix socket paths are limited to ~100 bytes.
    path = Path("/tmp") / f"notify-{os.getpid()}-{tmp_path.name[-20:]}.sock"
    path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    sock.settimeout(2)
    yield path, sock
    sock.close()
    path.unlink(missing_ok=True)


@unix_only
def test_messages_reach_the_notify_socket(receiver: Receiver) -> None:
    path, sock = receiver
    notifier = Notifier.from_env({"NOTIFY_SOCKET": str(path)})
    assert notifier.ready("running")
    assert sock.recv(1024) == b"READY=1\nSTATUS=running"
    assert notifier.watchdog()
    assert sock.recv(1024) == b"WATCHDOG=1"
    assert notifier.stopping()
    assert sock.recv(1024) == b"STOPPING=1\nSTATUS=stopping"


@unix_only
def test_a_vanished_socket_never_raises(tmp_path: Path) -> None:
    assert Notifier(str(tmp_path / "gone.sock")).watchdog() is False


def test_without_notify_socket_nothing_is_sent() -> None:
    notifier = Notifier.from_env({})
    assert notifier.address is None
    assert notifier.ready("x") is False and notifier.watchdog() is False


def test_ready_comes_after_the_db_is_open_and_then_regular_watchdog_pings(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, limit=3)
    h.service.run()
    sent = h.notifier.sent
    ready = next(i for i, m in enumerate(sent) if m[0] == "READY=1")
    assert ready == 0 and h.db.exists()
    # Three rounds a minute apart: never more than PING_SECONDS without a ping while waiting.
    assert h.notifier.lines().count("WATCHDOG=1") >= 3 * (60 // PING_SECONDS) - 1
    assert any(line.startswith("STATUS=running, last round ok at ") for line in h.notifier.lines())
