"""OpenAI-format tool schemas for the Flesh organ (module-level dicts,
mirroring plugins/spotify/tools.py's SCHEMA constants)."""

_LOCATION = {
    "type": "object",
    "properties": {
        "address": {"type": "string"},
        "contact": {"type": "string", "description": "Phone number for the courier"},
        "notes": {"type": "string"},
    },
}

FLESH_QUOTE_SCHEMA = {
    "name": "flesh_quote",
    "description": (
        "Quote a physical-world action across permitted provider Sinews "
        "(T1, read-only, no money moves). Creates a pending T3 approval for "
        "the best-ranked quote; the operator must tap Approve on the Face "
        "confirm card (or type /flesh approve <id>) before any dispatch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "verb": {
                "type": "string",
                "enum": [
                    "move_person",
                    "move_object",
                    "make_object",
                    "perform_physical_task",
                    "perform_digital_task",
                ],
            },
            "pickup": _LOCATION,
            "dropoff": _LOCATION,
            "task": {
                "type": "object",
                "description": "For perform_* verbs: spec, acceptance criteria, duration bound, rate_usd",
            },
            "payload": {
                "type": "object",
                "description": (
                    "For move_object: description, value_usd, size. "
                    "For make_object (print fulfillment): sku (or variant_id "
                    "for printful), copies, file_url (public print-ready "
                    "asset), description; dropoff carries the recipient with "
                    "structured fields name, address1, city, state, zip, "
                    "country_code"
                ),
            },
            "preferred_sinew": {
                "type": "string",
                "description": "Pin one provider explicitly; otherwise all permitted sinews are quoted and ranked",
            },
        },
        "required": ["verb"],
    },
}

FLESH_DISPATCH_SCHEMA = {
    "name": "flesh_dispatch",
    "description": (
        "Dispatch a quoted Flesh action. REQUIRES a T3-approved approval_id "
        "bound to this exact quote_digest — only issued after the operator "
        "approves via /flesh approve. There is no code path that dispatches "
        "without a valid, unexpired, unconsumed approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "approval_id": {"type": "string"},
            "quote_digest": {"type": "string"},
        },
        "required": ["approval_id", "quote_digest"],
    },
}

FLESH_STATUS_SCHEMA = {
    "name": "flesh_status",
    "description": (
        "Poll dispatch status (T1). With dispatch_id, polls that dispatch; "
        "without, polls all open dispatches. Updates persisted state and "
        "settles completed dispatches (variance-flagged beyond 15%)."
    ),
    "parameters": {
        "type": "object",
        "properties": {"dispatch_id": {"type": "string"}},
    },
}

FLESH_CANCEL_SCHEMA = {
    "name": "flesh_cancel",
    "description": (
        "Cancel an open dispatch via its provider. Surface the cancellation "
        "terms from the confirm card first; a fee-incurring cancellation is "
        "flagged on the Limb Rail."
    ),
    "parameters": {
        "type": "object",
        "properties": {"dispatch_id": {"type": "string"}},
        "required": ["dispatch_id"],
    },
}
