"""Seal approval control surface: /seal command grammar (mirrors flesh_mode).

One module drives every surface so the grammar cannot drift. The runtime gate is
NOT here — SealApprovalStore.approve re-verifies the (nonce, digest) pair, and
that verification is the enforcement boundary regardless of which surface issued
the command.

The Face card posts `/seal approve <nonce> <digest>`; the CLI can use the same
grammar. Both land here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SEAL_USAGE = (
    "Usage: /seal status — show the pending Seal grant and standing grants\n"
    "       /seal approve <nonce> <digest> — grant this action once\n"
    "       /seal session <nonce> <digest> — grant every action of this exact shape\n"
    "       /seal deny <nonce> — refuse; a durable refusal record is written\n"
    "       /seal revoke — drop all standing session grants\n"
    "       /seal ledger — show recent approve/deny/expire outcomes"
)


@dataclass(frozen=True)
class SealCommandResult:
    """Either a direct reply (no agent run) or a rewritten agent message."""

    reply: Optional[str] = None
    rewritten_message: Optional[str] = None


def _parse_nonce_digest(rest: str) -> Tuple[Optional[int], str, Optional[str]]:
    """-> (nonce, digest, error). Digest is optional (deny does not need it)."""
    parts = (rest or "").split()
    if not parts:
        return None, "", "Missing <nonce>."
    try:
        nonce = int(parts[0])
    except ValueError:
        return None, "", f"'{parts[0]}' is not a nonce. Read the card's current nonce."
    return nonce, (parts[1] if len(parts) > 1 else ""), None


def handle_seal_command(args: str, session_id: str) -> SealCommandResult:
    try:
        from agent.seal_approval import SealApprovalStore
    except Exception:
        return SealCommandResult(reply="Seal approval store is unavailable.")

    store = SealApprovalStore()
    args = (args or "").strip()
    lowered = args.lower()

    if lowered in ("", "status"):
        return SealCommandResult(reply=store.status_summary(session_id))

    if lowered == "ledger":
        rows = store.ledger(session_id, limit=12)
        if not rows:
            return SealCommandResult(reply="Seal ledger is empty for this session.")
        lines = [
            f"  {r.get('outcome','?'):<32} {r.get('short_digest','------')}  "
            f"{r.get('verb','')}"
            for r in rows
        ]
        return SealCommandResult(reply="Seal ledger (most recent last):\n" + "\n".join(lines))

    if lowered in ("revoke", "revoke-session", "off"):
        count = store.revoke_session_grants(session_id)
        return SealCommandResult(
            reply=(
                f"Revoked {count} standing session grant(s). "
                "Every future action of any shape will ask again."
            )
        )

    for verb in ("approve", "session", "deny"):
        if not lowered.startswith(verb):
            continue

        nonce, digest, err = _parse_nonce_digest(args[len(verb):])
        if err is not None:
            return SealCommandResult(reply=f"{err}\n{store.status_summary(session_id)}")

        if verb == "deny":
            rec, derr = store.deny(session_id, nonce=nonce)
            if derr is not None:
                return SealCommandResult(reply=derr)
            # Rewritten rather than a plain reply so the agent learns it was
            # refused and stops, instead of silently retrying the same action.
            return SealCommandResult(
                rewritten_message=(
                    f"[Seal grant {rec.get('digest','')[:6]} DENIED by the operator "
                    f"(T{rec.get('seal_tier')}). Do NOT retry this action and do not "
                    "re-emit an equivalent one. Explain what you were going to do, "
                    "then ask what the operator would prefer instead.]"
                )
            )

        if not digest:
            return SealCommandResult(
                reply=f"Usage: /seal {verb} <nonce> <digest>.\n{store.status_summary(session_id)}"
            )

        rec, aerr = store.approve(
            session_id, nonce=nonce, digest=digest, for_session=(verb == "session")
        )
        if aerr is not None:
            return SealCommandResult(reply=aerr)

        scope_note = (
            " Every future action with this exact shape is now pre-approved for "
            "this session; a different action still asks."
            if verb == "session"
            else " This grant covers exactly one execution."
        )
        return SealCommandResult(
            rewritten_message=(
                f"[Seal grant {rec['digest'][:6]} GRANTED by the operator "
                f"(T{rec['seal_tier']} {rec.get('verb','')}).{scope_note} "
                "Proceed with exactly the action that was approved — the digest binds "
                "it, so any change to the code, tier, or scope voids this grant and "
                "will be refused.]"
            )
        )

    return SealCommandResult(reply=SEAL_USAGE)
