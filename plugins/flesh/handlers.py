"""Flesh tool handlers. T3 enforcement lives INSIDE _handle_flesh_dispatch —
proximity is how safeguards erode, so the approval consume happens here, in
the same atomic transaction that creates the dispatch row, no matter which
code path called the handler.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from tools.registry import tool_error, tool_result

from plugins.flesh import config as flesh_config
from plugins.flesh import lifecycle
from plugins.flesh.approval import FleshApprovalStore
from plugins.flesh.db import FleshDB
from plugins.flesh.sinews import build_sinew, sinew_names_for_verb
from plugins.flesh.sinews.base import VERBS, SinewError

_DEFAULT_ANGEL = "nyriel"


def _check_flesh_available() -> bool:
    """Tools stay listed but refuse dispatch until a config pack grants
    something (defaults deny — WO-F/1 §4)."""
    try:
        return bool(flesh_config.load_config().get("angels"))
    except Exception:
        return False


def _angel(cfg: dict) -> str:
    return str(cfg.get("default_angel") or _DEFAULT_ANGEL)


def _handle_flesh_quote(args: dict, **kw) -> str:
    session_id = str(kw.get("session_id") or "")
    verb = str(args.get("verb") or "").strip()
    if verb not in VERBS:
        return tool_error(f"verb must be one of {list(VERBS)}")
    cfg = flesh_config.load_config()
    angel = _angel(cfg)
    pack = flesh_config.angel_pack(angel, cfg)
    permitted = pack.get("permitted_sinews") or []
    if verb not in (pack.get("permitted_verbs") or []):
        return tool_error(f"Verb '{verb}' is not granted to angel '{angel}' (config pack denies).")

    preferred = str(args.get("preferred_sinew") or "").strip()
    names = [preferred] if preferred else sinew_names_for_verb(verb, permitted)
    if preferred and preferred not in permitted:
        return tool_error(f"Sinew '{preferred}' is not granted to angel '{angel}'.")
    if not names:
        return tool_error(f"No permitted sinews serve verb '{verb}'. Grant one in the config pack.")

    route_or_task = {
        key: args[key]
        for key in ("pickup", "dropoff", "task", "payload")
        if isinstance(args.get(key), dict)
    }
    quotes, failures = [], []
    # Territory gate (interim Word stand-in): drop sinews whose service area
    # excludes the pickup; the remaining ranked sinews are the fallback.
    from plugins.flesh import territory

    serviceable = []
    for name in names:
        ok, reason = territory.check_serviceable(name, args.get("pickup") or {})
        if ok:
            serviceable.append(name)
        else:
            failures.append(f"{name}: {reason}")
    names = serviceable
    if not names:
        return tool_error("No sinew serves this location. " + " | ".join(failures))
    for name in names:
        try:
            sinew = build_sinew(name)
            quotes.append(sinew.quote(dict(route_or_task)))
        except SinewError as exc:
            failures.append(f"{name}: {exc}")
    if not quotes:
        return tool_error("All sinews failed to quote. " + " | ".join(failures))

    # ponytail: ranking is one sort key (price, then ETA); ranker module when
    # angel-preference policy actually exists.
    quotes.sort(key=lambda q: (q.price_usd, q.eta_minutes if q.eta_minutes is not None else 1e9))
    best = quotes[0]

    daily_spent = FleshDB().spend_since(flesh_config.day_start_epoch())
    deny = flesh_config.permission_error(angel, verb, best.sinew, best.price_usd, daily_spent)
    if deny:
        return tool_error(deny)

    rec = FleshApprovalStore().create(
        session_id,
        verb=verb,
        sinew=best.sinew,
        quote=asdict(best),
        route_or_task=route_or_task,
        requesting_angel=angel,
    )
    lifecycle.emit("quoted", verb=verb, sinew=best.sinew, spend_usd=best.price_usd,
                   session_id=session_id)
    lines = [
        f"Best quote: {best.sinew} ${best.price_usd:.2f}"
        + (f", ETA {best.eta_minutes} min" if best.eta_minutes else ""),
        f"Cancellation: {best.cancellation_terms}",
        f"T3 approval {rec['approval_id'][:8]} is pending (expires with the quote).",
        "Present the confirm card details to the operator. Dispatch is LOCKED until "
        f"they approve — /flesh approve {rec['approval_id'][:8]} (or the Face card). "
        "After approval, call flesh_dispatch with approval_id and quote_digest.",
        f"approval_id: {rec['approval_id']}",
        f"quote_digest: {rec['quote_digest']}",
    ]
    if len(quotes) > 1:
        lines.append("Also quoted: " + "; ".join(
            f"{q.sinew} ${q.price_usd:.2f}" for q in quotes[1:]))
    if failures:
        lines.append("Sinew failures: " + " | ".join(failures))
    return tool_result("\n".join(lines))


def _handle_flesh_dispatch(args: dict, **kw) -> str:
    approval_id = str(args.get("approval_id") or "").strip()
    quote_digest = str(args.get("quote_digest") or "").strip()
    if not approval_id or not quote_digest:
        return tool_error("approval_id and quote_digest are both required.")

    db = FleshDB()
    rec, err = db.consume_and_create_dispatch(approval_id, quote_digest, request_json="{}")
    if err:
        return tool_error(err)

    stored = json.loads(rec["quote_json"])
    request = dict(stored.get("route_or_task") or {})
    request["provider_quote_id"] = (stored.get("quote") or {}).get("provider_quote_id")
    try:
        sinew = build_sinew(rec["sinew"])
        result = sinew.dispatch(request, idempotency_key=approval_id)
    except SinewError as exc:
        # Consumed approval + failed dispatch = visible, recoverable, safe
        # failure direction. Never re-arm the approval automatically.
        db.update_dispatch(approval_id, status="failed", error=str(exc), source="dispatch")
        lifecycle.emit("dispatch_failed", approval_id=approval_id, verb=rec["verb"],
                       sinew=rec["sinew"], error_type=type(exc).__name__)
        return tool_error(
            f"Dispatch failed after consume: {exc}. The approval is spent (by design); "
            "re-quote to try again."
        )
    db.update_dispatch(
        approval_id,
        status=result.status,
        provider_reference=result.provider_reference,
        source="dispatch",
    )
    lifecycle.emit("dispatched", dispatch_id=approval_id, verb=rec["verb"],
                   sinew=rec["sinew"], status=result.status, spend_usd=rec["spend_usd"])
    lines = [
        f"Dispatched via {rec['sinew']}: {result.status}",
        f"dispatch_id: {approval_id}",
    ]
    if result.tracking_url:
        lines.append(f"Tracking: {result.tracking_url}")
    return tool_result("\n".join(lines))


def _poll_one(db: FleshDB, dispatch: dict) -> str:
    """Track one dispatch, persist, settle on completion. Returns a summary line."""
    dispatch_id = dispatch["dispatch_id"]
    if not dispatch.get("provider_reference"):
        return f"{dispatch_id[:8]}: no provider reference (dispatch may have failed)"
    try:
        sinew = build_sinew(dispatch["sinew"])
        track = sinew.track(dispatch["provider_reference"])
    except SinewError as exc:
        db.update_dispatch(dispatch_id, error=str(exc))
        return f"{dispatch_id[:8]}: poll failed ({exc})"
    db.update_dispatch(dispatch_id, status=track.status)
    line = f"{dispatch_id[:8]} [{dispatch['sinew']}]: {track.status}"
    if track.status == "completed":
        final = track.final_cost_usd
        final = float(final) if final is not None else float(dispatch["quoted_price_usd"])
        settle = db.insert_settle(dispatch_id, final, track.detail)
        lifecycle.emit("settled", dispatch_id=dispatch_id, sinew=dispatch["sinew"],
                       spend_usd=final, variance_pct=round(settle["variance_pct"], 4),
                       flagged=settle["flagged"])
        line += f" — settled ${final:.2f} (variance {settle['variance_pct']:+.1%}"
        line += ", FLAGGED)" if settle["flagged"] else ")"
    else:
        lifecycle.emit("status", dispatch_id=dispatch_id, sinew=dispatch["sinew"],
                       status=track.status)
    return line


def _handle_flesh_status(args: dict, **kw) -> str:
    db = FleshDB()
    db.expire_stale_approvals()
    dispatch_id = str(args.get("dispatch_id") or "").strip()
    if dispatch_id:
        dispatch = db.get_dispatch(dispatch_id)
        if dispatch is None:
            return tool_error(f"Unknown dispatch_id '{dispatch_id}'.")
        return tool_result(_poll_one(db, dispatch))
    open_dispatches = db.open_dispatches()
    if not open_dispatches:
        return tool_result("No open dispatches.")
    return tool_result("\n".join(_poll_one(db, d) for d in open_dispatches))


def _handle_flesh_cancel(args: dict, **kw) -> str:
    dispatch_id = str(args.get("dispatch_id") or "").strip()
    if not dispatch_id:
        return tool_error("dispatch_id is required.")
    db = FleshDB()
    dispatch = db.get_dispatch(dispatch_id)
    if dispatch is None:
        return tool_error(f"Unknown dispatch_id '{dispatch_id}'.")
    if dispatch["status"] in ("completed", "canceled", "failed"):
        return tool_error(f"Dispatch is already {dispatch['status']}.")
    try:
        sinew = build_sinew(dispatch["sinew"])
        result = sinew.cancel(dispatch["provider_reference"] or "")
    except SinewError as exc:
        return tool_error(f"Cancel failed: {exc}")
    if result.status != "canceled":
        return tool_error(f"Provider refused the cancellation: {result.raw}")
    db.update_dispatch(dispatch_id, status="canceled", source="cancel")
    # WO-F/1 §4: a fee-incurring cancellation is T3. Providers report the fee
    # after the fact; a nonzero fee is flagged loudly on the rail.
    lifecycle.emit("canceled", dispatch_id=dispatch_id, sinew=dispatch["sinew"],
                   fee_usd=result.fee_usd, flagged=result.fee_usd > 0)
    fee_note = f" (cancellation fee ${result.fee_usd:.2f})" if result.fee_usd else ""
    return tool_result(f"Dispatch {dispatch_id[:8]} canceled{fee_note}.")
