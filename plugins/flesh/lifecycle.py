"""Flesh → Limb Rail telemetry.

Appends one JSON line per lifecycle event to ~/.nyriel/logs/limb_rail.jsonl
— the same file the gateway's Face action-result writer uses, but a different
producer (do not merge with _record_limb_rail_action_results). Discipline
inherited from that writer: never log free-text content — addresses, contacts,
and task specs are logged as lengths only.

ponytail: 3-line path helper duplicated from api_server rather than a shared
module; extract agent/limb_rail.py if a third producer appears.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_FIELDS = (
    "approval_id", "dispatch_id", "verb", "sinew", "status", "spend_usd",
    "variance_pct", "flagged", "fee_usd", "eta_minutes", "session_id",
    "requesting_angel", "error_type", "source",
)


def _rail_path() -> Path:
    from nyriel_constants import get_nyriel_home

    return Path(get_nyriel_home()) / "logs" / "limb_rail.jsonl"


def emit(event: str, **fields) -> None:
    """Append one flesh event row. Best-effort — telemetry never breaks a dispatch."""
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "organ": "flesh",
        "event": event,
    }
    for key, value in fields.items():
        if key in _SAFE_FIELDS:
            row[key] = value
        else:
            # lengths-only for anything not on the safe-scalar allowlist
            row[f"{key}_len"] = len(str(value)) if value is not None else None
    try:
        path = _rail_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        logger.exception("flesh limb-rail append failed")
