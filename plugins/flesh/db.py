"""flesh.db — plugin-owned SQLite substrate for Flesh dispatch state.

Own file, NOT state.db: the poller needs an all-open-dispatches scan across
sessions, spend ceilings need SUM() aggregates, and idempotency is a PRIMARY
KEY constraint — relational queries state.db's per-session JSON-blob shape
cannot express. Precedent: kanban.db, word_queue.db. A courier is en route
whether or not the Brain is running; everything here survives restarts.

dispatch_id == approval_id: the PK uniqueness constraint IS the idempotency
guarantee (consume-then-dispatch, WO-F/1 decision 4c). No separate key table.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flesh_approvals (
    approval_id        TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    verb               TEXT NOT NULL,
    sinew              TEXT NOT NULL,
    quote_digest       TEXT NOT NULL,
    quote_json         TEXT NOT NULL,
    spend_usd          REAL NOT NULL,
    granting_principal TEXT NOT NULL DEFAULT 'operator',
    requesting_angel   TEXT NOT NULL DEFAULT 'nyriel',
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at         REAL NOT NULL,
    expires_at         REAL NOT NULL,
    consumed_at        REAL
);
CREATE TABLE IF NOT EXISTS flesh_dispatches (
    dispatch_id         TEXT PRIMARY KEY,
    approval_id         TEXT NOT NULL,
    verb                TEXT NOT NULL,
    sinew               TEXT NOT NULL,
    provider_reference  TEXT,
    request_json        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'assigned',
    status_history_json TEXT NOT NULL DEFAULT '[]',
    quoted_price_usd    REAL NOT NULL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    last_polled_at      REAL,
    error               TEXT
);
CREATE TABLE IF NOT EXISTS flesh_settles (
    dispatch_id      TEXT PRIMARY KEY,
    final_cost_usd   REAL NOT NULL,
    quoted_price_usd REAL NOT NULL,
    variance_pct     REAL NOT NULL,
    flagged          INTEGER NOT NULL DEFAULT 0,
    settled_at       REAL NOT NULL,
    raw_settlement_json TEXT NOT NULL DEFAULT '{}'
);
"""

TERMINAL = ("completed", "canceled", "failed")
SETTLE_VARIANCE_FLAG = 0.15  # WO-F/1 §2.2 default


def _default_db_path() -> Path:
    from nyriel_constants import get_nyriel_home

    return Path(get_nyriel_home()) / "flesh.db"


class FleshDB:
    """Per-call connections (WAL) — cheap, and no cross-thread handle sharing
    between tool handlers and the poller thread."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = Path(db_path) if db_path else _default_db_path()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    # -- approvals ---------------------------------------------------------

    def insert_approval(self, rec: dict) -> None:
        """Insert a pending approval, superseding any prior pending one for
        the same session (one confirm card at a time, like plan mode)."""
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE flesh_approvals SET status='superseded' "
                "WHERE session_id=? AND status IN ('pending','approved') AND consumed_at IS NULL",
                (rec["session_id"],),
            )
            conn.execute(
                "INSERT INTO flesh_approvals (approval_id, session_id, verb, sinew,"
                " quote_digest, quote_json, spend_usd, granting_principal,"
                " requesting_angel, status, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    rec["approval_id"], rec["session_id"], rec["verb"], rec["sinew"],
                    rec["quote_digest"], rec["quote_json"], rec["spend_usd"],
                    rec.get("granting_principal", "operator"),
                    rec.get("requesting_angel", "nyriel"),
                    now, rec["expires_at"],
                ),
            )

    def get_approval(self, approval_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flesh_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            return dict(row) if row else None

    def pending_approval_for_session(self, session_id: str) -> Optional[dict]:
        """Latest actionable (pending/approved, unconsumed, unexpired) approval."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flesh_approvals WHERE session_id=? AND"
                " status IN ('pending','approved') AND consumed_at IS NULL AND expires_at > ?"
                " ORDER BY created_at DESC LIMIT 1",
                (session_id, time.time()),
            ).fetchone()
            return dict(row) if row else None

    def find_approval_by_short_id(self, session_id: str, short_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flesh_approvals WHERE session_id=? AND approval_id LIKE ?"
                " ORDER BY created_at DESC LIMIT 1",
                (session_id, short_id + "%"),
            ).fetchone()
            return dict(row) if row else None

    def set_approval_status(self, approval_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE flesh_approvals SET status=? WHERE approval_id=?",
                (status, approval_id),
            )

    def expire_stale_approvals(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE flesh_approvals SET status='expired' "
                "WHERE status IN ('pending','approved') AND consumed_at IS NULL AND expires_at <= ?",
                (time.time(),),
            )
            return cur.rowcount

    # -- consume-then-dispatch (the T3 boundary) ---------------------------

    def consume_and_create_dispatch(
        self, approval_id: str, quote_digest: str, request_json: str
    ):
        """Atomically consume an approved approval and create its dispatch row.

        Returns (approval_record, None) or (None, error_string). Runs in one
        BEGIN IMMEDIATE transaction: no code path — tool call, internal call,
        retry after a crash — can dispatch twice or without a valid token.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM flesh_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None, f"Unknown approval_id '{approval_id}'."
            rec = dict(row)
            if rec["consumed_at"] is not None:
                conn.execute("ROLLBACK")
                return None, (
                    f"Approval {approval_id[:8]} was already consumed. If no dispatch "
                    "completed, that is the safe failure direction — re-quote."
                )
            if rec["status"] != "approved":
                conn.execute("ROLLBACK")
                return None, (
                    f"Approval {approval_id[:8]} is '{rec['status']}', not approved. "
                    "The operator approves with /flesh approve."
                )
            if rec["expires_at"] <= now:
                conn.execute("ROLLBACK")
                return None, f"Approval {approval_id[:8]} expired — re-quote."
            if rec["quote_digest"] != quote_digest:
                conn.execute("ROLLBACK")
                return None, (
                    "Quote digest mismatch: the quote changed after approval. "
                    "Re-quote and re-present the confirm card."
                )
            conn.execute(
                "UPDATE flesh_approvals SET consumed_at=? WHERE approval_id=?",
                (now, approval_id),
            )
            conn.execute(
                "INSERT INTO flesh_dispatches (dispatch_id, approval_id, verb, sinew,"
                " request_json, status, status_history_json, quoted_price_usd,"
                " created_at, updated_at) VALUES (?,?,?,?,?,'assigned','[]',?,?,?)",
                (
                    approval_id, approval_id, rec["verb"], rec["sinew"],
                    request_json, rec["spend_usd"], now, now,
                ),
            )
            conn.execute("COMMIT")
            return rec, None
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return None, f"Dispatch for approval {approval_id[:8]} already exists (idempotent)."
        finally:
            conn.close()

    # -- dispatches --------------------------------------------------------

    def get_dispatch(self, dispatch_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flesh_dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            return dict(row) if row else None

    def open_dispatches(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM flesh_dispatches WHERE status NOT IN (?,?,?)", TERMINAL
            ).fetchall()
            return [dict(r) for r in rows]

    def update_dispatch(
        self,
        dispatch_id: str,
        *,
        status: Optional[str] = None,
        provider_reference: Optional[str] = None,
        error: Optional[str] = None,
        source: str = "poll",
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, status_history_json FROM flesh_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return
            history = json.loads(row["status_history_json"] or "[]")
            sets, vals = ["updated_at=?", "last_polled_at=?"], [now, now]
            if status and status != row["status"]:
                history.append({"status": status, "ts": now, "source": source})
                sets.append("status=?")
                vals.append(status)
                sets.append("status_history_json=?")
                vals.append(json.dumps(history))
            if provider_reference is not None:
                sets.append("provider_reference=?")
                vals.append(provider_reference)
            if error is not None:
                sets.append("error=?")
                vals.append(error)
            vals.append(dispatch_id)
            conn.execute(
                f"UPDATE flesh_dispatches SET {', '.join(sets)} WHERE dispatch_id=?", vals
            )
            conn.execute("COMMIT")

    # -- settles + ceilings ------------------------------------------------

    def insert_settle(self, dispatch_id: str, final_cost_usd: float, raw: dict) -> dict:
        d = self.get_dispatch(dispatch_id)
        quoted = float(d["quoted_price_usd"]) if d else 0.0
        variance = ((final_cost_usd - quoted) / quoted) if quoted else 0.0
        flagged = 1 if abs(variance) > SETTLE_VARIANCE_FLAG else 0
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO flesh_settles (dispatch_id, final_cost_usd,"
                " quoted_price_usd, variance_pct, flagged, settled_at, raw_settlement_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    dispatch_id, final_cost_usd, quoted, variance, flagged,
                    time.time(), json.dumps(raw)[:4000],
                ),
            )
        return {"variance_pct": variance, "flagged": bool(flagged)}

    def spend_since(self, since_epoch: float) -> float:
        """Aggregate dispatched spend (quoted) since a timestamp — daily ceiling."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(quoted_price_usd),0) AS s FROM flesh_dispatches"
                " WHERE created_at > ? AND status != 'failed'",
                (since_epoch,),
            ).fetchone()
            return float(row["s"])
