"""Flesh confirm control surface: /flesh command grammar (mirrors plan_mode).

One module drives every surface so the grammar cannot drift. Runtime T3
enforcement does NOT live here — the approval consume happens inside the
flesh_dispatch tool handler (plugins/flesh/handlers.py), so no surface can
dispatch without a valid token regardless of how it invoked the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FLESH_USAGE = (
    "Usage: /flesh status — show the pending Flesh approval\n"
    "       /flesh approve <id> — grant the T3 seal for the quoted dispatch\n"
    "       /flesh deny <id> — reject the quote; the angel re-quotes\n"
    "       /flesh off — dismiss the pending approval"
)


@dataclass(frozen=True)
class FleshCommandResult:
    """Either a direct reply (no agent run) or a rewritten agent message."""

    reply: Optional[str] = None
    rewritten_message: Optional[str] = None


def handle_flesh_command(args: str, session_id: str) -> FleshCommandResult:
    try:
        from plugins.flesh.approval import FleshApprovalStore
    except Exception:
        return FleshCommandResult(
            reply="Flesh is not enabled (plugins.enabled must include 'flesh')."
        )
    store = FleshApprovalStore()
    args = (args or "").strip()
    lowered = args.lower()

    if lowered in ("", "status"):
        return FleshCommandResult(reply=store.status_summary(session_id))

    if lowered.startswith("approve"):
        short_id = args[len("approve"):].strip()
        if not short_id:
            return FleshCommandResult(reply=f"Usage: /flesh approve <id>.\n{store.status_summary(session_id)}")
        rec, err = store.approve(session_id, short_id)
        if err is not None:
            return FleshCommandResult(reply=err)
        # Handoff (deeplink) sinews: the tap IS the dispatch — the operator
        # completes the trip in the provider app. Consume and record intent
        # here; no agent turn or provider API call exists to make.
        payload = store.confirm_payload(session_id)
        if payload is None:
            import json as _json

            payload = {"is_deeplink": bool(
                (_json.loads(rec["quote_json"]).get("quote", {}).get("raw") or {}).get("deeplink_url")
            )}
        if payload.get("is_deeplink"):
            consumed, cerr = store.db().consume_and_create_dispatch(
                rec["approval_id"], rec["quote_digest"], rec["quote_json"]
            )
            if cerr is not None:
                return FleshCommandResult(reply=cerr)
            store.db().update_dispatch(
                rec["approval_id"], status="completed",
                provider_reference=payload.get("deeplink_url", "handoff"),
                source="handoff",
            )
            from plugins.flesh import lifecycle

            lifecycle.emit("handoff_dispatched", dispatch_id=rec["approval_id"],
                           verb=rec["verb"], sinew=rec["sinew"])
            return FleshCommandResult(
                reply=(
                    f"Handoff recorded ({rec['sinew']}). The link opened in a new "
                    "tab — complete the trip in the provider's app."
                )
            )
        return FleshCommandResult(
            rewritten_message=(
                f"[Flesh approval {rec['approval_id'][:8]} GRANTED by the operator "
                f"(T3 seal, ${rec['spend_usd']:.2f} via {rec['sinew']}). Dispatch now: "
                f"call flesh_dispatch with approval_id \"{rec['approval_id']}\" and "
                f"quote_digest \"{rec['quote_digest']}\". Do not alter any quote "
                "parameter — the digest binds the exact quote that was approved.]"
            )
        )

    if lowered.startswith("deny"):
        short_id = args[len("deny"):].strip()
        if not short_id:
            return FleshCommandResult(reply=f"Usage: /flesh deny <id>.\n{store.status_summary(session_id)}")
        rec, err = store.deny(session_id, short_id)
        if err is not None:
            return FleshCommandResult(reply=err)
        return FleshCommandResult(
            rewritten_message=(
                f"[Flesh approval {rec['approval_id'][:8]} DENIED by the operator. "
                "Do NOT dispatch. Re-compose: offer the next-ranked sinew or adjust "
                "the request (cheaper/slower option), then flesh_quote again and "
                "present a fresh confirm card.]"
            )
        )

    if lowered in ("off", "exit", "cancel"):
        rec = store.pending_for_session(session_id)
        if rec is None:
            return FleshCommandResult(reply="No Flesh approval is pending.")
        store.deny(session_id, rec["approval_id"][:8])
        return FleshCommandResult(reply="Flesh approval dismissed — nothing will be dispatched.")

    return FleshCommandResult(reply=FLESH_USAGE)
