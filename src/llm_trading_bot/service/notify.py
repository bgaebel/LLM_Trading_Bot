"""systemd notifications (``sd_notify``) without a third-party package (ADR-0017).

systemd passes a datagram socket in ``NOTIFY_SOCKET``; without it (local runs, Windows) every
call does nothing. A failing send never stops the service: the watchdog then restarts it, which
is the intended reaction to a service that cannot report anymore.
"""

import socket
from collections.abc import Mapping


class Notifier:
    def __init__(self, address: str | None) -> None:
        self.address = address or None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Notifier":
        return cls(env.get("NOTIFY_SOCKET"))

    def send(self, *lines: str) -> bool:
        if self.address is None or not hasattr(socket, "AF_UNIX"):
            return False
        # A leading "@" names a socket in the abstract namespace (Linux).
        target = "\0" + self.address[1:] if self.address.startswith("@") else self.address
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(target)
                sock.sendall("\n".join(lines).encode("utf-8"))
        except OSError:
            return False
        return True

    def ready(self, status: str) -> bool:
        return self.send("READY=1", f"STATUS={status}")

    def watchdog(self) -> bool:
        return self.send("WATCHDOG=1")

    def status(self, text: str) -> bool:
        return self.send(f"STATUS={text}")

    def stopping(self) -> bool:
        return self.send("STOPPING=1", "STATUS=stopping")
