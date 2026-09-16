"""FleshApprovalStore — the T3 confirm machinery (WO-F/1 §4).

Modeled on agent/execution_policy.py's ExecutionPolicyStore, but persisted in
flesh.db (relational, cross-session) instead of a per-session JSON blob. The
token record is deliberately shaped as a Seal T3 grant prototype — when the
real seal protocol lands, this store migrates by rename, not rearchitecture.

State: pending -> approved (/flesh approve, digest re-verified)
               -> consumed (flesh_dispatch, atomic consume-then-dispatch in db.py)
       pending|approved -> superseded (/flesh deny, or a newer quote)
       pending|approved -> expired (TTL = min(own TTL, provider quote TTL))

SQLite transactions in FleshDB do the cross-process serialization that
ExecutionPolicyStore needed in-process locks for.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional, Tuple

from plugins.flesh import config as flesh_config
from plugins.flesh import lifecycle
from plugins.flesh.db import FleshDB
from plugins.flesh.digest import quote_digest


class FleshApprovalStore:
    def __init__(self, db: Optional[FleshDB] = None):
        self._db = db

    def db(self) -> FleshDB:
        if self._db is None:
            self._db = FleshDB()
        return self._db

    # -- create ------------------------------------------------------------

    def create(
        self,
        session_id: str,
        *,
        verb: str,
        sinew: str,
        quote: dict,
        route_or_task: dict,
        requesting_angel: str = "nyriel",
        granting_principal: str = "operator",
    ) -> dict:
        """Create a pending approval for a ranked quote. Returns the record.

        quote: {price_usd, eta_minutes, provider_quote_id, expires_at,
                cancellation_terms, ...} — the Quote dataclass as a dict.
        """
        now = time.time()
        ttl = flesh_config.approval_ttl_seconds()
        own_expiry = now + ttl
        provider_expiry = quote.get("expires_at")
        expires_at = min(own_expiry, provider_expiry) if provider_expiry else own_expiry
        digest = quote_digest(
            verb, sinew, quote.get("price_usd", 0.0),
            route_or_task, quote.get("cancellation_terms", ""),
        )
        rec = {
            "approval_id": uuid.uuid4().hex,
            "session_id": session_id,
            "verb": verb,
            "sinew": sinew,
            "quote_digest": digest,
            "quote_json": json.dumps(
                {"quote": quote, "route_or_task": route_or_task}, ensure_ascii=False
            ),
            "spend_usd": round(float(quote.get("price_usd", 0.0)), 2),
            "granting_principal": granting_principal,
            "requesting_angel": requesting_angel,
            "expires_at": expires_at,
        }
        self.db().insert_approval(rec)
        lifecycle.emit(
            "approval_created",
            approval_id=rec["approval_id"], verb=verb, sinew=sinew,
            spend_usd=rec["spend_usd"], session_id=session_id,
        )
        rec["status"] = "pending"
        return rec

    # -- operator actions (/flesh approve|deny) ----------------------------

    def approve(self, session_id: str, short_id: str) -> Tuple[Optional[dict], Optional[str]]:
        """pending -> approved. Re-verifies the digest against the stored
        quote snapshot (the plan-mode file-digest analog)."""
        rec = self.db().find_approval_by_short_id(session_id, short_id)
        if rec is None:
            return None, f"No Flesh approval matches id '{short_id}' in this session."
        if rec["consumed_at"] is not None:
            return None, f"Approval {short_id} was already dispatched."
        if rec["status"] not in ("pending",):
            return None, f"Approval {short_id} is '{rec['status']}' — re-quote to get a fresh card."
        if rec["expires_at"] <= time.time():
            self.db().set_approval_status(rec["approval_id"], "expired")
            return None, (
                f"Approval {short_id} expired (quotes are short-lived). "
                "Re-quote and re-present the confirm card."
            )
        stored = json.loads(rec["quote_json"])
        recomputed = quote_digest(
            rec["verb"], rec["sinew"],
            stored.get("quote", {}).get("price_usd", 0.0),
            stored.get("route_or_task", {}),
            stored.get("quote", {}).get("cancellation_terms", ""),
        )
        if recomputed != rec["quote_digest"]:
            return None, (
                "Stored quote no longer matches its digest — refusing to arm. Re-quote."
            )
        self.db().set_approval_status(rec["approval_id"], "approved")
        lifecycle.emit(
            "approved", approval_id=rec["approval_id"], verb=rec["verb"],
            sinew=rec["sinew"], spend_usd=rec["spend_usd"], session_id=session_id,
        )
        rec["status"] = "approved"
        return rec, None

    def deny(self, session_id: str, short_id: str) -> Tuple[Optional[dict], Optional[str]]:
        """Deny feeds back into compose — the caller re-quotes; not a dead end."""
        rec = self.db().find_approval_by_short_id(session_id, short_id)
        if rec is None:
            return None, f"No Flesh approval matches id '{short_id}' in this session."
        if rec["consumed_at"] is not None:
            return None, f"Approval {short_id} was already dispatched — use flesh_cancel instead."
        self.db().set_approval_status(rec["approval_id"], "superseded")
        lifecycle.emit(
            "denied", approval_id=rec["approval_id"], verb=rec["verb"],
            sinew=rec["sinew"], session_id=session_id,
        )
        rec["status"] = "superseded"
        return rec, None

    # -- read surfaces -----------------------------------------------------

    def pending_for_session(self, session_id: str) -> Optional[dict]:
        return self.db().pending_approval_for_session(session_id)

    def confirm_payload(self, session_id: str) -> Optional[dict]:
        """The flesh_confirm block riding the completion payload → Face card."""
        rec = self.pending_for_session(session_id)
        if rec is None:
            return None
        stored = json.loads(rec["quote_json"])
        quote = stored.get("quote", {})
        route_or_task = stored.get("route_or_task", {})
        return {
            "approval_id": rec["approval_id"],
            "short_id": rec["approval_id"][:8],
            "status": rec["status"],
            "verb": rec["verb"],
            "sinew": rec["sinew"],
            "price_usd": rec["spend_usd"],
            "eta_minutes": quote.get("eta_minutes"),
            "cancellation_terms": quote.get("cancellation_terms", ""),
            "route_or_task": route_or_task,
            "expires_at": rec["expires_at"],
            "is_deeplink": bool((quote.get("raw") or {}).get("deeplink_url")),
            "deeplink_url": (quote.get("raw") or {}).get("deeplink_url", ""),
        }

    def status_summary(self, session_id: str) -> str:
        rec = self.pending_for_session(session_id)
        if rec is None:
            return "Flesh: no approval pending in this session."
        return (
            f"Flesh approval {rec['approval_id'][:8]} [{rec['status']}]: "
            f"{rec['verb']} via {rec['sinew']} for ${rec['spend_usd']:.2f}, "
            f"expires in {max(0, int(rec['expires_at'] - time.time()))}s. "
            f"Approve with /flesh approve {rec['approval_id'][:8]}."
        )
