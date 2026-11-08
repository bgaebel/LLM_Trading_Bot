"""Capture real Bitvavo responses for the lab golden tests (Spec 016 AK3).

Fetches, at each requested round time ``k*60 + 5`` s (the service's schedule), exactly the
requests the live bot sends: ``GET /{market}/candles?interval=X&limit=500`` for 1m/15m/1h/4h of
BTC-EUR and ETH-EUR (newest first, forming bar included) and ``GET /ticker/book``. The response
bodies are stored verbatim as text, so the fake transport replays what Bitvavo sent byte for byte.

Public endpoints only, no keys, GET only (ADR-0004). 9 requests per capture; the script slows down
below 200 remaining weight points and stops at the first 429 (that would ban the IP 15 minutes).

Usage (from the repository root, run once by hand; the result is committed):

    uv run python tests/lab/fixtures/capture.py 2026-11-08T09:59 2026-11-08T10:00 ...

Each argument is a UTC minute; the capture starts at second 5 of it. Output:
``tests/lab/fixtures/bitvavo_captures.json.xz`` (lzma: the captures repeat most rows, and xz's
large dictionary stores the repeats almost for free, unlike gzip's 32 KB window).
"""

import calendar
import json
import lzma
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.bitvavo.com/v2"
MARKETS = ("BTC-EUR", "ETH-EUR")
INTERVALS = ("1m", "15m", "1h", "4h")
LIMIT = 500
OFFSET = 5
SLOW_BELOW = 200
OUT = Path(__file__).with_name("bitvavo_captures.json.xz")


def paths() -> list[str]:
    """Candles in the order build_snapshots asks for them, then the book (quotes last)."""
    out = [f"/{m}/candles?interval={i}&limit={LIMIT}" for m in MARKETS for i in INTERVALS]
    return [*out, "/ticker/book"]


def fetch(path: str) -> tuple[str, float, int | None]:
    request = urllib.request.Request(
        API + path,
        headers={"User-Agent": "llm-trading-bot-lab-capture/0.1", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read().decode("utf-8")
            remaining = response.headers.get("bitvavo-ratelimit-remaining")
    except urllib.error.HTTPError as exc:
        # Never retry: a second 429 only prolongs the ban.
        raise SystemExit(f"HTTP {exc.code} for {path} - stopping") from None
    return body, time.time(), int(remaining) if remaining is not None else None


def capture(at: int) -> dict[str, Any]:
    wait = at - time.time()
    if wait < 0:
        raise SystemExit(f"round time {at} already passed")
    time.sleep(wait)
    responses: dict[str, str] = {}
    arrived: dict[str, float] = {}
    for path in paths():
        body, when, remaining = fetch(path)
        responses[path], arrived[path] = body, round(when, 3)
        if remaining is not None and remaining < SLOW_BELOW:
            time.sleep(5)
    late = max(arrived.values()) - at
    if late >= 60 - OFFSET:
        # A response from the next minute could hold a bar the round time cannot know.
        raise SystemExit(f"capture at {at} took {late:.1f}s - too slow, discard it")
    return {"at": at, "arrived": arrived, "responses": responses}


def main(argv: list[str]) -> int:
    times = [calendar.timegm(time.strptime(a, "%Y-%m-%dT%H:%M")) + OFFSET for a in argv]
    if not times or times != sorted(times):
        raise SystemExit("give ascending UTC minutes like 2026-11-08T09:59")
    captures = []
    for at in times:
        captures.append(capture(at))
        doc = {
            "source": API,
            "note": "verbatim public Bitvavo responses, see tests/lab/fixtures/capture.py",
            "captures": captures,
        }
        # Written after every capture, so a later failure keeps the earlier ones.
        OUT.write_bytes(lzma.compress(json.dumps(doc, indent=0).encode("utf-8"), preset=9))
        print(f"captured {at}, wrote {OUT} ({OUT.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
