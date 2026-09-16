#!/usr/bin/env python3
"""Word→Brain session bridge.

Mirrors Word's notebook RAG chat sessions into the Brain session ledger
(~/.nyriel/state.db) with source='word', so session recall (Face, CLI,
session_search) sees Word conversations without being pointed at Word.

Idempotent: each Word message id lands in messages.platform_message_id and
already-mirrored messages are skipped on later runs. Append-only — Word's
LangGraph chats only ever append, so no edit/delete reconciliation.

Auth: WORD_WARD_TOKEN / OPEN_NOTEBOOK_WARD_TOKEN env, falling back to
~/Oblivion/word/.env so the launchd plist carries no secret.

Run one pass and exit; schedule via launchd (see
~/Library/LaunchAgents/com.nyriel.word-session-sync.plist).

ponytail: notebook chats only — source-chat sessions (chat pinned to a single
source) stay unmirrored until someone actually uses them.
"""

import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BRAIN_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_REPO))

from nyriel_state import SessionDB, _default_db_path  # noqa: E402

BASE_URL = os.environ.get("WORD_BASE_URL", "http://127.0.0.1:5055")
WORD_ENV = Path.home() / "Oblivion" / "word" / ".env"


def _token() -> str:
    tok = os.environ.get("WORD_WARD_TOKEN") or os.environ.get(
        "OPEN_NOTEBOOK_WARD_TOKEN"
    )
    if tok:
        return tok.strip()
    if WORD_ENV.exists():
        for line in WORD_ENV.read_text().splitlines():
            m = re.match(r"OPEN_NOTEBOOK_WARD_TOKEN\s*=\s*(.+)", line.strip())
            if m:
                return m.group(1).strip()
    return ""


TOKEN = _token()


def _get(path: str):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def main() -> int:
    try:
        health = _get("/health")
        if health.get("status") != "healthy":
            print(f"word not healthy: {health}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"word unreachable, nothing to sync: {exc}", file=sys.stderr)
        return 0  # organ down is normal, not an error worth alerting on

    state = SessionDB()
    ro = sqlite3.connect(f"file:{_default_db_path()}?mode=ro", uri=True)
    sessions_synced = messages_added = 0

    for nb in _get("/api/notebooks"):
        nb_id = urllib.parse.quote(nb["id"], safe=":")
        for sess in _get(f"/api/chat/sessions?notebook_id={nb_id}"):
            if not sess.get("message_count"):
                continue
            brain_id = "word:" + sess["id"].split(":", 1)[1]
            row = ro.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (brain_id,)
            ).fetchone()
            known = {
                r[0]
                for r in ro.execute(
                    "SELECT platform_message_id FROM messages "
                    "WHERE session_id = ? AND platform_message_id IS NOT NULL",
                    (brain_id,),
                )
            }
            # cheap skip: row exists and Word has no more messages than we hold
            if row and sess["message_count"] <= len(known):
                continue

            detail = _get(
                f"/api/chat/sessions/{urllib.parse.quote(sess['id'], safe=':')}"
            )
            created = _epoch(sess["created"])
            if not row:
                state.create_session(brain_id, "word")
                # backfill start time so a July Word chat doesn't sort as today
                with sqlite3.connect(_default_db_path(), timeout=30) as w:
                    w.execute(
                        "UPDATE sessions SET started_at = ? WHERE id = ?",
                        (created, brain_id),
                    )
            title = f"[Word · {nb['name']}] {sess.get('title') or 'Untitled'}"
            state.set_session_title(brain_id, title)

            new = 0
            for idx, msg in enumerate(detail.get("messages") or []):
                if msg["id"] in known:
                    continue
                role = "user" if msg.get("type") == "human" else "assistant"
                content = msg.get("content") or ""
                if not isinstance(content, str):
                    # LangChain content can be a list of parts; sqlite binds str only
                    content = json.dumps(content, ensure_ascii=False)
                state.append_message(
                    brain_id,
                    role,
                    content=content,
                    platform_message_id=msg["id"],
                    # ponytail: Word keeps no per-message wall time; created+idx
                    # preserves order and stays inside the session's real era
                    timestamp=created + idx,
                )
                new += 1
            if new:
                sessions_synced += 1
                messages_added += new

    ro.close()
    print(f"synced {messages_added} messages across {sessions_synced} sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
