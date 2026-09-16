#!/usr/bin/env python3
"""Publish a message to the Face onscreen chat via the dashboard event bus.

Usage: python3 scripts/face_notify.py "Your message"

Connects to the control server's /api/pub WebSocket on the ``face-inbox``
channel and sends one ``face.notify`` frame; the Face's /api/brain_inbox SSE
bridge relays it into the onscreen chat thread. Live-only delivery: if no
Face is subscribed when the frame is published, it is dropped.

Meant to be called by the Brain itself (cron jobs, code_execution) or from
a shell. Exit 0 on publish, 1 on any failure.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

CHANNEL = "face-inbox"


def _dashboard_token() -> str:
    home = Path(os.environ.get("NYRIEL_HOME", "").strip() or Path.home() / ".nyriel")
    try:
        for line in (home / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("NYRIEL_DASHBOARD_SESSION_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


async def _publish(text: str) -> None:
    import urllib.parse

    import aiohttp

    token = _dashboard_token()
    if not token:
        raise RuntimeError("NYRIEL_DASHBOARD_SESSION_TOKEN not found in ~/.nyriel/.env")

    base = os.environ.get("NYRIEL_DASHBOARD_URL", "http://127.0.0.1:9119").rstrip("/")
    qs = urllib.parse.urlencode({"channel": CHANNEL, "token": token})
    url = f"{base.replace('http', 'ws', 1)}/api/pub?{qs}"
    frame = json.dumps({"type": "face.notify", "text": text, "ts": time.time()})

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_str(frame)
            # ponytail: fire-and-forget — the bus has no ack; a clean close
            # after send_str means the server accepted the frame.


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print("usage: face_notify.py <message>", file=sys.stderr)
        return 1
    try:
        asyncio.run(_publish(text))
    except Exception as exc:  # noqa: BLE001 — CLI boundary, report and exit
        print(f"face_notify failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
