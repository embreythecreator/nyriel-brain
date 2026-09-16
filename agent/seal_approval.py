"""SealApprovalStore — the T1/T2/T3 gate for Face-executed space actions.

WO-FACE/CHAT-1 P0-1. Until this existed, `space_action.js` validated `seal_tier`
and then hard-refused everything above T1 with
`refusalReason: "seal-tier-approval-not-available-v1"` — the most rigorous
permission model in the product had no surface, so it could only ever say no.

Modeled on plugins/flesh/approval.py, which says of itself: "the token record is
deliberately shaped as a Seal T3 grant prototype — when the real seal protocol
lands, this store migrates by rename, not rearchitecture." This is that landing.
Persistence follows agent/execution_policy.py instead of flesh.db: one JSON blob
per session in state.db, because Seal grants are session-scoped by definition
("approve for this session") while Flesh approvals are cross-session records.

State:  (none) -> pending -> approved -> consumed
                          -> denied            (operator said no)
                          -> expired           (TTL lapsed, NOT deleted)

Two invariants worth stating because both are load-bearing and neither is
obvious:

1. **A session grant binds the action-shape digest, not the tool name.** Binding
   a name would let a later, differently-dangerous call ride through on a shared
   name — "approve file_write once" must not authorize every future file_write.

2. **Nothing is ever deleted, only terminated.** An expired card that vanished
   would be indistinguishable from one that was never sent, so expiry writes a
   `NOT TAKEN` ledger line. Deny writes a symmetric refusal record: the operator
   must be able to prove later that they said no to *this exact* action, which a
   store that only records approvals cannot do.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Long enough to read a card, short enough that a walk-away does not leave a
# live grant sitting armed.
DEFAULT_GRANT_TTL_SECONDS = 300.0
SESSION_GRANT_TTL_SECONDS = 3600.0
LEDGER_MAX_ENTRIES = 200

TIER_LABELS = {
    1: "read",
    2: "bounded write",
    3: "approval-gated",
}

_SESSION_LOCKS: Dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = _SESSION_LOCKS[session_id] = threading.Lock()
        return lock


def _stable(value):
    """Order-independent, float-normalized shape for hashing."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def action_digest(kind: str, code: str, seal_tier: int, scope: Optional[dict] = None) -> str:
    """Canonical digest of what is being approved.

    Binds the FULL action: kind, the literal code, the declared tier, and any
    scope. Whitespace is collapsed so cosmetic reformatting cannot mint a new
    digest, but nothing else is normalized — a single changed character in the
    code is a different action and must invalidate the approval. Volatile fields
    (action id, timestamps, nonce) must never reach this function.
    """
    payload = {
        "kind": kind or "",
        "code": " ".join((code or "").split()),
        "seal_tier": int(seal_tier),
        "scope": _stable(scope or {}),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _empty_grant_state() -> Dict[str, Any]:
    return {
        "nonce": 0,
        "pending": None,
        "grants": [],
        "ledger": [],
        "approved_count": 0,
        "last_approved_at": 0.0,
    }


class SealApprovalStore:
    def __init__(self, session_db=None):
        self._db = session_db

    def _get_db(self):
        if self._db is None:
            from agent.execution_policy import _default_session_db
            self._db = _default_session_db()
        return self._db

    # -- persistence -------------------------------------------------------

    def _load(self, session_id: str) -> Dict[str, Any]:
        if not session_id:
            return _empty_grant_state()
        # Retry a locked DB rather than defaulting. Defaulting here would drop a
        # pending grant on a hiccup, and a dropped grant reads to the operator as
        # a card that silently vanished — the exact failure this store exists to
        # make impossible.
        for attempt in range(3):
            try:
                data = self._get_db().get_seal_grant(session_id)
                break
            except Exception:
                if attempt == 2:
                    logger.warning(
                        "seal grant load failed after retries; treating as empty "
                        "(tier>=2 actions will be re-requested, never auto-approved)",
                        exc_info=True,
                    )
                    return _empty_grant_state()
                time.sleep(0.05 * (attempt + 1))
        if not isinstance(data, dict):
            return _empty_grant_state()
        merged = _empty_grant_state()
        merged.update(data)
        return merged

    def _save(self, session_id: str, state: Dict[str, Any]) -> bool:
        state["ledger"] = state.get("ledger", [])[-LEDGER_MAX_ENTRIES:]
        return self._get_db().set_seal_grant(session_id, state)

    def _record(self, state: Dict[str, Any], outcome: str, rec: Dict[str, Any]) -> None:
        state.setdefault("ledger", []).append({
            "at": time.time(),
            "outcome": outcome,
            "digest": rec.get("digest", ""),
            "short_digest": (rec.get("digest") or "")[:6],
            "verb": rec.get("verb", ""),
            "seal_tier": rec.get("seal_tier"),
            "action_id": rec.get("action_id", ""),
        })

    # -- expiry ------------------------------------------------------------

    def _expire_if_due(self, session_id: str, state: Dict[str, Any], now: float) -> bool:
        """Terminate a lapsed pending grant. Returns True if state changed.

        Expiry is a state transition with a record, never a deletion.
        """
        changed = False
        pending = state.get("pending")
        if pending and float(pending.get("expires_at", 0)) <= now:
            self._record(state, "expired", pending)
            state["pending"] = None
            changed = True
        live = [g for g in state.get("grants", []) if float(g.get("expires_at", 0)) > now]
        if len(live) != len(state.get("grants", [])):
            state["grants"] = live
            changed = True
        return changed

    # -- request -----------------------------------------------------------

    def request(
        self,
        session_id: str,
        *,
        action_id: str,
        kind: str,
        code: str,
        seal_tier: int,
        verb: str,
        effect: str,
        scope: Optional[dict] = None,
        prose: str = "",
        ttl_seconds: float = DEFAULT_GRANT_TTL_SECONDS,
    ) -> Tuple[Dict[str, Any], bool]:
        """Open a pending grant, or report that a standing grant already covers it.

        Returns ``(record, already_granted)``. When ``already_granted`` is True the
        caller may execute immediately — a session grant matching this exact
        action shape is live — and no card is shown.
        """
        digest = action_digest(kind, code, seal_tier, scope)
        with _session_lock(session_id):
            state = self._load(session_id)
            now = time.time()
            self._expire_if_due(session_id, state, now)

            for grant in list(state.get("grants", [])):
                if grant.get("digest") == digest and float(grant.get("expires_at", 0)) > now:
                    # A once-grant is what "Approve once" leaves behind, and it
                    # is spent here. Without this consumption the operator gets
                    # asked again for the action they just approved: the grant's
                    # rewritten message tells the agent to proceed, the agent
                    # re-emits the same block, and the loop never closes.
                    single_use = bool(grant.get("once"))
                    if single_use:
                        state["grants"] = [g for g in state.get("grants", []) if g is not grant]
                    else:
                        state["approved_count"] = int(state.get("approved_count", 0)) + 1
                        state["last_approved_at"] = now
                    self._record(
                        state,
                        "consumed-once-grant" if single_use else "auto-approved-by-session-grant",
                        {"digest": digest, "verb": verb, "seal_tier": seal_tier,
                         "action_id": action_id},
                    )
                    self._save(session_id, state)
                    return {"digest": digest, "status": "approved"}, True

            state["nonce"] = int(state.get("nonce", 0)) + 1
            rec = {
                "grant_id": uuid.uuid4().hex,
                "action_id": action_id,
                "nonce": state["nonce"],
                "kind": kind,
                "seal_tier": int(seal_tier),
                "verb": verb,
                "effect": effect,
                "scope": scope or {},
                "prose": prose,
                "digest": digest,
                "created_at": now,
                "expires_at": now + float(ttl_seconds),
                "status": "pending",
            }
            # A newer request supersedes an unanswered older one — but the older
            # one still gets a record, so a card that disappeared is explainable.
            previous = state.get("pending")
            if previous:
                self._record(state, "superseded", previous)
            state["pending"] = rec
            self._save(session_id, state)
            return rec, False

    # -- operator actions --------------------------------------------------

    def approve(
        self, session_id: str, *, nonce: int, digest: str, for_session: bool = False
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """pending -> approved. Re-verifies BOTH nonce and digest.

        The nonce check is the real guard, not the client's remount keying: it is
        what makes a click that lands after a newer request rendered inert on the
        server. Verifying the digest alone would let that stale click approve the
        *new* action if the two happened to share a shape.
        """
        with _session_lock(session_id):
            state = self._load(session_id)
            now = time.time()
            self._expire_if_due(session_id, state, now)
            pending = state.get("pending")

            if pending is None:
                self._save(session_id, state)
                return None, "No Seal approval is pending in this session."
            if int(pending.get("nonce", -1)) != int(nonce):
                self._save(session_id, state)
                return None, (
                    "That approval is stale — a newer request replaced it. "
                    "Re-read the current card before approving."
                )
            if pending.get("digest") != digest:
                self._save(session_id, state)
                return None, (
                    "Action no longer matches the digest it was presented under — "
                    "refusing to arm."
                )

            pending["status"] = "approved"
            pending["approved_at"] = now
            state["approved_count"] = int(state.get("approved_count", 0)) + 1
            state["last_approved_at"] = now
            self._record(state, "approved-for-session" if for_session else "approved", pending)

            # Both verbs leave a live grant behind; only its lifetime differs.
            # "Approve once" writes a single-use grant that request() spends on
            # the next emission of this exact shape — that is what lets the
            # agent actually perform the action it was just granted, instead of
            # bouncing off a fresh card. A standing grant survives every
            # matching emission until its hour is up or /seal revoke drops it.
            state.setdefault("grants", []).append({
                "digest": pending["digest"],
                "verb": pending.get("verb", ""),
                "seal_tier": pending.get("seal_tier"),
                "expires_at": now + (SESSION_GRANT_TTL_SECONDS if for_session else DEFAULT_GRANT_TTL_SECONDS),
                "once": not for_session,
            })

            state["pending"] = None
            self._save(session_id, state)
            return pending, None

    def deny(
        self, session_id: str, *, nonce: int
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """pending -> denied, with a durable refusal record carrying the digest."""
        with _session_lock(session_id):
            state = self._load(session_id)
            self._expire_if_due(session_id, state, time.time())
            pending = state.get("pending")
            if pending is None:
                self._save(session_id, state)
                return None, "No Seal approval is pending in this session."
            if int(pending.get("nonce", -1)) != int(nonce):
                self._save(session_id, state)
                return None, "That approval is stale — a newer request replaced it."
            pending["status"] = "denied"
            pending["denied_at"] = time.time()
            self._record(state, "denied", pending)
            state["pending"] = None
            self._save(session_id, state)
            return pending, None

    def revoke_session_grants(self, session_id: str) -> int:
        """Drop every standing grant. The one place session grants are revocable."""
        with _session_lock(session_id):
            state = self._load(session_id)
            count = len(state.get("grants", []))
            for grant in state.get("grants", []):
                self._record(state, "revoked", grant)
            state["grants"] = []
            self._save(session_id, state)
            return count

    # -- read surfaces -----------------------------------------------------

    def confirm_payload(self, session_id: str) -> Optional[Dict[str, Any]]:
        """The seal_confirm block riding the completion payload -> Face card.

        Same always-present-key contract as plan and flesh_confirm: None clears
        the card. The split between digest-bound fields and model-authored prose
        is deliberate and must survive into the renderer — an attacker who
        prompt-injects persuasive copy writes in `prose`, which is presented as a
        caption, never in the fields the digest actually binds.
        """
        with _session_lock(session_id):
            state = self._load(session_id)
            if self._expire_if_due(session_id, state, time.time()):
                self._save(session_id, state)
            pending = state.get("pending")
            if not pending:
                return None

            digest = pending.get("digest", "")
            return {
                "grant_id": pending.get("grant_id", ""),
                "action_id": pending.get("action_id", ""),
                "nonce": pending.get("nonce"),
                "seal_tier": pending.get("seal_tier"),
                "tier_label": TIER_LABELS.get(int(pending.get("seal_tier") or 0), "unknown"),
                "status": pending.get("status", "pending"),
                # Digest-bound: renders as primary content.
                "verified_fields": {
                    "verb": pending.get("verb", ""),
                    "effect": pending.get("effect", ""),
                    "scope": pending.get("scope") or {},
                },
                # Model-authored: renders as a demoted caption.
                "prose": pending.get("prose", ""),
                "digest": digest,
                "short_digest": digest[:6],
                "expires_at": pending.get("expires_at"),
                "ttl_seconds": max(
                    0, int(float(pending.get("expires_at", 0)) - time.time())
                ),
                # Anti-fatigue: makes reflex-clicking visible in the moment it
                # is happening. Nothing in the comparison set does this.
                "approval_streak": int(state.get("approved_count", 0)),
                "seconds_since_last_approval": (
                    int(time.time() - float(state["last_approved_at"]))
                    if state.get("last_approved_at") else None
                ),
                "session_grant_count": len([g for g in state.get("grants", []) if not g.get("once")]),
            }

    def live_grant_digests(self, session_id: str) -> List[str]:
        """Digests with a live grant right now. Read-only; spends nothing."""
        state = self._load(session_id)
        now = time.time()
        return [
            str(g.get("digest", ""))
            for g in state.get("grants", [])
            if float(g.get("expires_at", 0)) > now
        ]

    def ledger(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Durable outcome record. `expired` rows are the NOT TAKEN lines."""
        state = self._load(session_id)
        return list(state.get("ledger", []))[-int(limit):]

    def status_summary(self, session_id: str) -> str:
        payload = self.confirm_payload(session_id)
        if payload is None:
            state = self._load(session_id)
            # Once-grants are in flight, not standing — counting them here would
            # tell the operator they have blanket approvals they do not have.
            grants = len([g for g in state.get("grants", []) if not g.get("once")])
            return (
                f"Seal: no approval pending in this session. "
                f"{grants} standing session grant(s)."
            )
        return (
            f"Seal T{payload['seal_tier']} ({payload['tier_label']}) pending: "
            f"{payload['verified_fields']['verb']} — digest {payload['short_digest']}, "
            f"expires in {payload['ttl_seconds']}s. "
            f"Approval #{payload['approval_streak'] + 1} this session."
        )


# -- Emission-time gate ------------------------------------------------------
#
# The Face must ask CODE, never text. The trap this avoids is stamping a grant
# token into the space-action envelope and letting the Face execute when it sees
# one: the envelope is model-authored, so the model would simply write its own
# token and self-approve. Any scheme where the Face trusts a field *inside* the
# block is broken by construction.
#
# So the Brain gates at emission. It parses the blocks out of its own final
# response — code the model does not execute — computes each digest, and puts
# the ids it will allow on the completion payload as `seal_authorized`, beside
# `seal_confirm`. The model cannot forge that list: an id only lands in it when
# the digest computed from the block's own kind/code/tier/scope has a live
# grant. Reuse an approved id with different code and the digest changes, so it
# is not listed.
#
# Gating here rather than reacting to a Face refusal also keeps the Face's
# refusal out of load-bearing control flow (which would invert Brain-as-model),
# and digests the payload before it round-trips through a renderer.

# Fences must sit alone on their own lines — same rule face_policy.py states to
# the model, and the same one space_action.js parses by.
SPACE_ACTION_BLOCK_PATTERN = re.compile(
    r"^[ \t]*```space-action[ \t]*\r?\n(.*?)\r?\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

EFFECT_DISPLAY_LIMIT = 240


def extract_space_action_envelopes(text: str) -> List[Dict[str, Any]]:
    """Parse the fenced space-action envelopes out of an assistant message.

    A malformed block is skipped, not guessed at. The Face refuses it as
    `malformed-json` on its own side, so inventing a shape here would only
    manufacture a grant for an action that can never run.
    """
    found: List[Dict[str, Any]] = []
    for match in SPACE_ACTION_BLOCK_PATTERN.finditer(text or ""):
        try:
            env = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(env, dict):
            found.append(env)
    return found


def gate_space_actions(
    session_id: str, text: str, store: Optional["SealApprovalStore"] = None
) -> List[str]:
    """-> the action ids the Face may execute from this message.

    T1 is authorized outright: it is the read tier and has never been gated.
    Tier >= 2 is authorized only when a live grant covers its digest; otherwise
    the first such block opens a pending grant and the card goes up.

    Only one grant is opened per message. A second unapproved action would
    supersede the first before the operator ever saw it, and superseding a card
    the operator is mid-read is exactly the silent disappearance this store
    exists to prevent. The rest wait for the turn after.
    """
    envelopes = extract_space_action_envelopes(text)
    if not envelopes:
        return []

    store = store or SealApprovalStore()
    authorized: List[str] = []
    opened = False

    for env in envelopes:
        action_id = str(env.get("id") or "")
        if not action_id:
            continue

        try:
            seal_tier = 1 if env.get("seal_tier") is None else int(env["seal_tier"])
        except (TypeError, ValueError):
            continue  # Face refuses invalid-seal-tier; nothing to grant.

        if seal_tier < 2:
            authorized.append(action_id)
            continue

        if opened:
            continue

        kind = str(env.get("kind") or "")
        payload = env.get("payload")
        code = str((payload or {}).get("code") or "") if isinstance(payload, dict) else ""
        scope = env.get("scope") if isinstance(env.get("scope"), dict) else {}

        try:
            # verb and effect are derived from the digest-bound material, never
            # read from model-supplied naming fields. The card presents them as
            # verified, so they must actually be projections of what the digest
            # binds — otherwise the model gets to write in the ledger column.
            _rec, already_granted = store.request(
                session_id,
                action_id=action_id,
                kind=kind,
                code=code,
                seal_tier=seal_tier,
                verb=kind,
                effect=" ".join(code.split())[:EFFECT_DISPLAY_LIMIT],
                scope=scope,
                # Model-authored, presented as a demoted caption.
                prose=str(env.get("note") or env.get("prose") or ""),
            )
        except Exception:
            # Fail closed: no id in the list means the Face awaits rather than
            # executes. A gate that authorizes on error is not a gate.
            logger.warning("seal gate failed for action %s; withholding", action_id, exc_info=True)
            continue

        if already_granted:
            authorized.append(action_id)
        else:
            opened = True

    return authorized
