"""Word memory plugin — MemoryProvider interface.

Long-term memory backed by Word, the angel's memory organ (headless Open
Notebook fork: Postgres+pgvector storage, Ward-authenticated REST API).

Layer mapping (WO-B2): session context stays Brain-local (``sync_turn`` is a
deliberate no-op) · persistent facts flow to Word — every built-in
MEMORY.md/USER.md write is mirrored into a dedicated notebook through a
durable write-behind queue, and the model gets explicit save/search/read
tools. Recall happens per turn via ``prefetch`` against Word's search API;
the MemoryManager sanitizes and fences all prefetch output centrally.

Word being down is never fatal: reads degrade to silence (with a 60s
re-probe backoff), writes wait in the local queue and replay when the organ
comes back.

Config (env vars, set up via `nyriel memory setup`):
  WORD_BASE_URL      — Word API endpoint (default: http://127.0.0.1:5055)
  WORD_WARD_TOKEN    — Ward bearer token (falls back to OPEN_NOTEBOOK_WARD_TOKEN;
                       optional — a local authless Word needs none)
  WORD_NOTEBOOK      — memory notebook name (default: Nyriel Memory)
  WORD_SEARCH_TYPE   — 'text' (default) or 'vector' (needs an embedding
                       model configured inside Word)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:5055"
_DEFAULT_NOTEBOOK = "Nyriel Memory"
# WO-POSTURE/1 1.7 — PLAN-posture and untrusted-origin captures land here;
# prefetch never reads it. Name assumed until D-4 rules.
_QUARANTINE_NOTEBOOK = "Nyriel Quarantine"
_HEALTH_BACKOFF_SECONDS = 60.0
_PREFETCH_LIMIT = 6
# ponytail: flat cap; ~2s × 25 ≈ a minute of retry before dead-lettering.
_MAX_WRITE_ATTEMPTS = 25
_SNIPPET_CHARS = 400
_ASYNC_SHUTDOWN = object()


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "word_memory_search",
    "description": (
        "Search Word long-term memory. scope 'memory' (default) searches the "
        "dedicated memory notebook; scope 'all' searches every notebook, "
        "including ingested research sources."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "scope": {
                "type": "string",
                "enum": ["memory", "all"],
                "description": "Search scope (default: memory).",
            },
        },
        "required": ["query"],
    },
}

SAVE_SCHEMA = {
    "name": "word_memory_save",
    "description": (
        "Persist a durable fact, preference, or decision to Word long-term "
        "memory. Survives across sessions; queued locally if Word is down."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the memory."},
            "content": {"type": "string", "description": "The fact to remember."},
        },
        "required": ["title", "content"],
    },
}

READ_SCHEMA = {
    "name": "word_memory_read",
    "description": "Fetch one full note from Word by its id (e.g. a search or prefetch hit).",
    "parameters": {
        "type": "object",
        "properties": {
            "note_id": {"type": "string", "description": "Note id, e.g. 'note:abc123'."},
        },
        "required": ["note_id"],
    },
}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class _Client:
    """Thin Ward-authenticated client for the Word REST API."""

    def __init__(self, base_url: str, token: str, notebook_name: str,
                 quarantine_name: str = _QUARANTINE_NOTEBOOK):
        self.base_url = re.sub(r"/+$", "", base_url)
        self.token = token.replace("Bearer ", "").strip()
        self.notebook_name = notebook_name
        self.quarantine_name = quarantine_name
        self._notebook_id: Optional[str] = None
        self._notebook_ids: Dict[str, str] = {}
        self._notebook_lock = threading.Lock()

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, *, json_body=None, timeout: float = 5.0) -> Any:
        import requests
        resp = requests.request(
            method.upper(), f"{self.base_url}{path}",
            json=json_body if method.upper() not in {"GET", "DELETE"} else None,
            headers=self._headers(),
            timeout=timeout,
        )
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text
        if not resp.ok:
            msg = payload.get("detail") if isinstance(payload, dict) else payload
            raise RuntimeError(f"Word {method} {path} failed ({resp.status_code}): {msg}")
        return payload

    def health(self) -> bool:
        try:
            return self.request("GET", "/health", timeout=3.0).get("status") == "healthy"
        except Exception:
            return False

    def ensure_notebook(self, name: Optional[str] = None) -> str:
        """Find-or-create a notebook by name (default: memory); cached."""
        name = name or self.notebook_name
        with self._notebook_lock:
            if name == self.notebook_name and self._notebook_id:
                return self._notebook_id  # legacy cache slot, still honored
            if name in self._notebook_ids:
                return self._notebook_ids[name]
            for nb in self.request("GET", "/api/notebooks"):
                if nb.get("name") == name:
                    self._notebook_ids[name] = nb["id"]
                    break
            else:
                created = self.request("POST", "/api/notebooks", json_body={
                    "name": name,
                    "description": (
                        "Nyriel Brain quarantine — unapproved evidence (plan-mode / untrusted origin)."
                        if name == self.quarantine_name else
                        "Nyriel Brain long-term memory (agent-distilled)."
                    ),
                })
                self._notebook_ids[name] = created["id"]
            if name == self.notebook_name:
                self._notebook_id = self._notebook_ids[name]
            return self._notebook_ids[name]

    def quarantine_notebook_id(self) -> Optional[str]:
        try:
            return self.ensure_notebook(self.quarantine_name)
        except Exception as exc:
            logger.debug("Word quarantine notebook unavailable: %s", exc)
            return None

    def search(self, query: str, *, search_type: str = "text", limit: int = _PREFETCH_LIMIT) -> list:
        payload = self.request("POST", "/api/search", json_body={
            "query": query,
            "type": search_type,
            "limit": limit,
            "search_sources": True,
            "search_notes": True,
        }, timeout=6.0)
        return list(payload.get("results") or [])

    def get_note(self, note_id: str) -> dict:
        from urllib.parse import quote
        return self.request("GET", f"/api/notes/{quote(str(note_id), safe=':')}")

    def create_note(self, title: str, content: str, *, quarantine: bool = False) -> dict:
        return self.request("POST", "/api/notes", json_body={
            "title": title,
            "content": content,
            "note_type": "ai",
            "notebook_id": self.ensure_notebook(self.quarantine_name if quarantine else None),
        }, timeout=8.0)

    def create_source(self, url: str, *, title: Optional[str] = None,
                      notebook_id: Optional[str] = None) -> dict:
        """Link-type source (Word fetches + embeds it asynchronously)."""
        return self.request("POST", "/api/sources/json", json_body={
            "type": "link",
            "url": url,
            "title": title,
            "notebooks": [notebook_id or self.ensure_notebook()],
            "embed": True,
            "async_processing": True,
            "provenance": "ingested-external",
        }, timeout=8.0)


# ---------------------------------------------------------------------------
# Durable write-behind queue
# ---------------------------------------------------------------------------

class _WriteQueue:
    """SQLite-backed async note writer. Survives crashes and Word downtime —
    pending rows replay on startup and retry with backoff until Word accepts
    them."""

    def __init__(self, client: _Client, db_path: Path):
        self._client = client
        self._db_path = db_path
        self._q: queue.Queue = queue.Queue()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()
        self._thread = threading.Thread(target=self._loop, name="word-writer", daemon=True)
        self._thread.start()
        after_id = 0
        while True:  # paged replay — a long outage can leave >200 rows
            rows = self._pending_rows(after_id)
            if not rows:
                break
            for row_id, title, content in rows:
                self._q.put((row_id, title, content))
            after_id = rows[-1][0]

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, content TEXT,
            created_at TEXT, last_error TEXT
        )""")
        # WO-POSTURE/1 1.1 — attempt counter + content hash on pending rows
        # (ALTER for pre-existing queues), a dead-letter table, and the
        # capture ledger that makes capture_note idempotent.
        for column in ("attempts INTEGER DEFAULT 0", "content_hash TEXT", "quarantine INTEGER DEFAULT 0"):
            try:
                conn.execute(f"ALTER TABLE pending ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # already migrated
        conn.execute("""CREATE TABLE IF NOT EXISTS dead (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            created_at TEXT, last_error TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS captured (
            hash TEXT PRIMARY KEY, vault_id TEXT, created_at TEXT,
            quarantined INTEGER DEFAULT 0, session_id TEXT
        )""")
        for column in ("quarantined INTEGER DEFAULT 0", "session_id TEXT"):
            try:
                conn.execute(f"ALTER TABLE captured ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

    def _pending_rows(self, after_id: int = 0, limit: int = 200) -> list:
        conn = self._get_conn()
        return conn.execute(
            "SELECT id, title, content FROM pending WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()

    # -- capture ledger ---------------------------------------------------

    def captured_lookup(self, content_hash: str):
        """(seen, vault_id) — seen with vault_id None means still queued."""
        row = self._get_conn().execute(
            "SELECT vault_id FROM captured WHERE hash = ?", (content_hash,)
        ).fetchone()
        return (row is not None, row[0] if row else None)

    def record_capture(self, content_hash: str, vault_id: Optional[str], *,
                       quarantined: bool = False, session_id: str = "") -> None:
        """First write fixes quarantined/session_id; later writes only learn the id."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO captured (hash, vault_id, created_at, quarantined, session_id) VALUES (?,?,?,?,?) "
            "ON CONFLICT(hash) DO UPDATE SET vault_id = COALESCE(excluded.vault_id, captured.vault_id)",
            (content_hash, vault_id, datetime.now(timezone.utc).isoformat(),
             int(bool(quarantined)), session_id or ""),
        )
        conn.commit()

    def quarantined_ids(self) -> set:
        """Word ids still under quarantine — the Brain-side truth. Word's
        note payload carries no notebook, so membership is tracked here."""
        rows = self._get_conn().execute(
            "SELECT vault_id FROM captured WHERE quarantined = 1 AND vault_id IS NOT NULL"
        ).fetchall()
        return {str(r[0]) for r in rows}

    def promote_session(self, session_id: str) -> int:
        """1.8 — lift quarantine for everything a session captured. Idempotent.
        ponytail: ledger-only; Word has no note-move endpoint and re-filing
        would churn vault_ids that plans pin. Physical notebook stays."""
        if not session_id:
            return 0
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE captured SET quarantined = 0 WHERE quarantined = 1 AND session_id = ?",
            (session_id,),
        )
        conn.commit()
        return int(cur.rowcount or 0)

    def pending_count(self) -> int:
        conn = self._get_conn()
        return int(conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0])

    def enqueue(self, title: str, content: str, *, content_hash: Optional[str] = None,
                quarantine: bool = False, session_id: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO pending (title, content, created_at, content_hash, quarantine) VALUES (?,?,?,?,?)",
            (title, content, now, content_hash, int(bool(quarantine))),
        )
        row_id = cur.lastrowid
        conn.commit()
        if content_hash:
            self.record_capture(content_hash, None, quarantined=quarantine, session_id=session_id)
        self._q.put((row_id, title, content))

    def _flush_row(self, row_id: int, title: str, content: str) -> None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT content_hash, quarantine FROM pending WHERE id = ?", (row_id,)
            ).fetchone()
            if row and row[1]:
                note = self._client.create_note(title, content, quarantine=True)
            else:
                note = self._client.create_note(title, content)
            if row and row[0] and isinstance(note, dict) and note.get("id"):
                self.record_capture(row[0], str(note["id"]))
            conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))
            conn.commit()
        except Exception as exc:
            attempts = int(conn.execute(
                "UPDATE pending SET last_error = ?, attempts = COALESCE(attempts, 0) + 1 "
                "WHERE id = ? RETURNING attempts", (str(exc), row_id),
            ).fetchone()[0])
            if attempts >= _MAX_WRITE_ATTEMPTS:
                # Dead-letter: keep the row for the operator, stop burning retries.
                conn.execute(
                    "INSERT INTO dead (id, title, content, created_at, last_error) "
                    "SELECT id, title, content, created_at, last_error FROM pending WHERE id = ?",
                    (row_id,),
                )
                conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))
                conn.commit()
                logger.error("Word note write dead-lettered after %d attempts: %s", attempts, exc)
                return
            conn.commit()
            logger.warning("Word note write failed (attempt %d, will retry): %s", attempts, exc)
            time.sleep(2)
            self._q.put((row_id, title, content))

    def _loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=5)
                if item is _ASYNC_SHUTDOWN:
                    break
                self._flush_row(*item)
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("Word writer error: %s", exc)

    def shutdown(self) -> None:
        self._q.put(_ASYNC_SHUTDOWN)
        self._thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Overlay formatter
# ---------------------------------------------------------------------------

def _compact(s: str, limit: int = _SNIPPET_CHARS) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def _build_overlay(items: List[Dict[str, Any]]) -> str:
    """Format hydrated search hits for prompt injection. Each item carries its
    Word provenance id so the model can follow up with word_memory_read."""
    lines = []
    for it in items:
        title = _compact(it.get("title") or "", 80) or "(untitled)"
        snippet = _compact(it.get("snippet") or "")
        if not snippet:
            continue
        lines.append(f"- {title} · {snippet} (id: {it.get('id', '?')})")
    if not lines:
        return ""
    return "[Word Long-Term Memory]\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Main plugin class
# ---------------------------------------------------------------------------

class WordMemoryProvider(MemoryProvider):
    """Word organ memory — durable queue, notebook-scoped recall, Ward auth."""

    def __init__(self):
        self._client: Optional[_Client] = None
        self._queue: Optional[_WriteQueue] = None
        self._session_id = ""
        self._policy_store = None
        self._search_type = "text"
        self._healthy = False
        self._last_failure = 0.0
        self._lock = threading.Lock()

    # ── Core identity ──────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "word"

    def is_available(self) -> bool:
        # Local-first organ: defaults work for a co-located Word; downtime is
        # handled at call time, not config time.
        return True

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "base_url", "description": "Word API endpoint", "default": _DEFAULT_BASE_URL, "env_var": "WORD_BASE_URL"},
            {"key": "token", "description": "Ward bearer token (blank for a local authless Word)", "secret": True, "env_var": "WORD_WARD_TOKEN"},
            {"key": "notebook", "description": "Memory notebook name", "default": _DEFAULT_NOTEBOOK, "env_var": "WORD_NOTEBOOK"},
            {"key": "search_type", "description": "Recall search mode", "default": "text", "choices": ["text", "vector"], "env_var": "WORD_SEARCH_TYPE"},
        ]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def initialize(self, session_id: str, **kwargs) -> None:
        base_url = os.environ.get("WORD_BASE_URL", _DEFAULT_BASE_URL)
        token = (os.environ.get("WORD_WARD_TOKEN")
                 or os.environ.get("OPEN_NOTEBOOK_WARD_TOKEN") or "")
        notebook = os.environ.get("WORD_NOTEBOOK", _DEFAULT_NOTEBOOK)
        self._search_type = os.environ.get("WORD_SEARCH_TYPE", "text")
        if self._search_type not in {"text", "vector"}:
            self._search_type = "text"

        self._client = _Client(base_url, token, notebook)
        self._session_id = session_id

        from nyriel_constants import get_nyriel_home
        self._queue = _WriteQueue(self._client, get_nyriel_home() / "word_queue.db")

        self._healthy = self._client.health()
        if self._healthy:
            try:
                self._client.ensure_notebook()
            except Exception as exc:
                logger.warning("Word notebook setup failed (non-fatal): %s", exc)
        else:
            self._last_failure = time.time()
            logger.warning(
                "Word organ unreachable at %s — recall degrades to silence, "
                "writes will queue locally.", base_url,
            )

    def _usable(self) -> bool:
        """Health gate with backoff: after a failure, skip Word calls for
        _HEALTH_BACKOFF_SECONDS, then re-probe."""
        if not self._client:
            return False
        with self._lock:
            if self._healthy:
                return True
            if time.time() - self._last_failure < _HEALTH_BACKOFF_SECONDS:
                return False
        healthy = self._client.health()
        with self._lock:
            self._healthy = healthy
            if not healthy:
                self._last_failure = time.time()
        return healthy

    def _mark_failed(self) -> None:
        with self._lock:
            self._healthy = False
            self._last_failure = time.time()

    def system_prompt_block(self) -> str:
        notebook = self._client.notebook_name if self._client else _DEFAULT_NOTEBOOK
        return (
            "# Word Long-Term Memory\n"
            f"Active. Durable facts live in the '{notebook}' notebook of the Word organ "
            "and persist across sessions.\n"
            "Use word_memory_save to persist facts/preferences/decisions, "
            "word_memory_search to recall (scope 'all' reaches ingested research too), "
            "word_memory_read to fetch a full note by id."
        )

    # ── Recall ─────────────────────────────────────────────────────────────

    def _hydrate(self, hit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Turn a search hit (id + relevance) into title/snippet/id/notebook."""
        item_id = str(hit.get("id") or hit.get("item_id") or "")
        if not item_id:
            return None
        title = hit.get("title") or ""
        content = hit.get("content") or hit.get("matches") or ""
        notebook_id = None
        if item_id.startswith("note:"):
            try:
                note = self._client.get_note(item_id)
                title = note.get("title") or title
                content = note.get("content") or content
                notebook_id = note.get("notebook_id")
            except Exception as exc:
                logger.debug("Word note hydrate failed for %s: %s", item_id, exc)
        if isinstance(content, list):
            content = " … ".join(str(c) for c in content)
        return {
            "id": item_id,
            "title": str(title),
            "snippet": _compact(content),
            "notebook_id": notebook_id,
            "relevance": hit.get("relevance"),
        }

    def _search_hydrated(self, query: str, *, limit: int = _PREFETCH_LIMIT) -> List[Dict[str, Any]]:
        hits = self._client.search(query, search_type=self._search_type, limit=limit)
        out = []
        for hit in hits:
            item = self._hydrate(hit)
            if item and item["snippet"]:
                out.append(item)
        return out

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        # 1.8 — /plan approve rewrites into the EXECUTING turn; this is the
        # first Word contact on that turn, so quarantine lifts here.
        try:
            from agent.execution_policy import PlanModeState
            if self._queue and self._policy_state(session_id) is PlanModeState.EXECUTING:
                self.promote_session(session_id)
        except Exception as exc:
            logger.debug("Word: promote-on-approve skipped: %s", exc)
        if not query or not self._usable():
            return ""
        try:
            # 1.7 — quarantine is Brain-side truth (Word notes carry no
            # notebook id), so the ledger decides what prefetch may surface.
            quarantined = self._queue.quarantined_ids() if self._queue else set()
            hits = [h for h in self._search_hydrated(query) if h.get("id") not in quarantined]
            return _build_overlay(hits)
        except Exception as exc:
            logger.debug("Word prefetch failed (non-fatal): %s", exc)
            self._mark_failed()
            return ""

    # ── Turn sync ──────────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Deliberate no-op: session context stays Brain-local (WO-B2 layer
        mapping). Durable facts reach Word via on_memory_write and the
        word_memory_save tool."""

    # ── Tools ──────────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, SAVE_SCHEMA, READ_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self._client:
            return tool_error("Word memory not initialized")
        try:
            return json.dumps(self._dispatch(tool_name, args))
        except Exception as exc:
            self._mark_failed()
            return tool_error(str(exc))

    def _dispatch(self, tool_name: str, args: dict) -> Any:
        if tool_name == "word_memory_search":
            query = args.get("query", "")
            if not query:
                return {"error": "query is required"}
            scope = args.get("scope", "memory")
            items = self._search_hydrated(query, limit=20 if scope == "memory" else 8)
            if scope == "memory":
                memory_nb = self._client.ensure_notebook()
                items = [i for i in items
                         if i.get("notebook_id") in (memory_nb, None)][:8]
            for i in items:
                i.pop("notebook_id", None)
            return {"results": items, "scope": scope}

        if tool_name == "word_memory_save":
            title = _compact(args.get("title", ""), 120)
            content = args.get("content", "")
            if not title or not content:
                return {"error": "title and content are required"}
            self._queue.enqueue(title, content)
            return {"status": "queued", "title": title,
                    "note": "Write is durable — it lands in Word now or replays when the organ is back."}

        if tool_name == "word_memory_read":
            note_id = args.get("note_id", "")
            if not note_id:
                return {"error": "note_id is required"}
            note = self._client.get_note(note_id)
            return {"id": note.get("id"), "title": note.get("title"),
                    "content": note.get("content")}

        return {"error": f"Unknown tool: {tool_name}"}

    # ── Capture (WO-POSTURE/1 1.1) ─────────────────────────────────────────

    def capture_note(
        self,
        title: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Idempotent write. Returns the Word note id when it landed
        synchronously (or landed earlier), None when queued or Word is down.

        Dedup key = sha256(title + content); metadata is appended as a
        provenance footer and does NOT affect identity, so re-reading the
        same page from another session makes no HTTP call.
        """
        if not title or not content or not self._queue:
            return None
        import hashlib
        content_hash = hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()
        seen, vault_id = self._queue.captured_lookup(content_hash)
        if seen:
            return vault_id
        body = content
        if metadata:
            body += "\n\n— capture: " + "; ".join(
                f"{k}={metadata[k]}" for k in sorted(metadata) if metadata[k] is not None
            )
        quarantine = bool((metadata or {}).get("quarantine"))
        session_id = str((metadata or {}).get("session_id") or self._session_id or "")
        if self._usable():
            try:
                note = self._client.create_note(title, body, quarantine=quarantine)
                vault_id = str(note.get("id") or "") or None
                self._queue.record_capture(content_hash, vault_id,
                                           quarantined=quarantine, session_id=session_id)
                origin = str((metadata or {}).get("origin") or "")
                if origin.startswith(("http://", "https://")):
                    # 1.4: URL-bearing captures also register a link source
                    # (Word fetches/embeds it). Best-effort; the note is the
                    # addressable record either way.
                    try:
                        self._client.create_source(origin, title=title)
                    except Exception as exc:
                        logger.debug("Word source registration skipped: %s", exc)
                return vault_id
            except Exception as exc:
                logger.debug("Word sync capture failed, queueing: %s", exc)
        self._queue.enqueue(title, body, content_hash=content_hash, quarantine=quarantine,
                            session_id=session_id)
        return None

    def promote_session(self, session_id: str) -> int:
        """Lift quarantine for a session's captures (1.8). Returns rows promoted."""
        if not self._queue:
            return 0
        promoted = self._queue.promote_session(session_id)
        if promoted:
            logger.info("Word: promoted %d quarantined capture(s) for session %s", promoted, session_id)
        return promoted

    def _policy_state(self, session_id: str):
        """Live plan-mode state for a session, or None. Plugins may import
        core; this reads the same SessionDB the tool executor enforces from."""
        if not session_id:
            return None
        try:
            from agent.execution_policy import ExecutionPolicyStore
            if self._policy_store is None:
                self._policy_store = ExecutionPolicyStore(None)
            return self._policy_store.load(session_id).state
        except Exception as exc:
            logger.debug("Word: policy state unavailable for %s: %s", session_id, exc)
            return None

    # ── Optional hooks ─────────────────────────────────────────────────────

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory writes (MEMORY.md/USER.md adds) into Word."""
        if action != "add" or not content or not self._queue:
            return
        title = f"[{target}] {_compact(content, 60)}"
        provenance = f"\n\n— mirrored from built-in {target} memory"
        if metadata and metadata.get("session_id"):
            provenance += f" (session {metadata['session_id']})"
        self._queue.enqueue(title, content + provenance)

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs,
    ) -> None:
        """Mirror a completed subagent delegation (task + result) into Word.

        WO-POSTURE/1 1.2 — the dispatch already exists in delegate_tool;
        Word simply never implemented the hook, so research never landed.
        """
        if not task or not result or not self._queue:
            return
        title = f"[delegation] {_compact(task, 60)}"
        provenance = "\n\n— provenance: origin=delegate_task"
        provenance += f"; child_session={child_session_id or '?'}"
        provenance += f"; parent_session={self._session_id or '?'}"
        # ponytail: posture/trust_tier/quarantine arrive once 1.7 threads the
        # ExecutionPolicy through; until then they are stamped unknown.
        for key in ("posture", "trust_tier", "quarantine"):
            provenance += f"; {key}={kwargs.get(key, 'unknown')}"
        self._queue.enqueue(title, f"## Task\n{task}\n\n## Result\n{result}{provenance}")

    def shutdown(self) -> None:
        if self._queue:
            self._queue.shutdown()


def register(ctx) -> None:
    """Register Word as a memory provider plugin."""
    ctx.register_memory_provider(WordMemoryProvider())
