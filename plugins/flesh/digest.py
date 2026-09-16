"""Canonical quote digest — what "approve exactly this, not a mutation" means.

The digest binds the FULL quote: verb, sinew, price, route-or-task, and
cancellation terms. Any re-quote produces a new digest and kills the old
approval. Volatile provider fields (timestamps, request ids) must never
reach this function — sinews populate cancellation_terms from stable fields
only, and normalization here collapses whitespace as a second guard.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, float):
        return round(value, 2)
    return value


def quote_digest(
    verb: str,
    sinew: str,
    price_usd: float,
    route_or_task: Optional[dict],
    cancellation_terms: str,
) -> str:
    payload = {
        "verb": verb,
        "sinew": sinew,
        "price_usd": round(float(price_usd), 2),
        "route_or_task": _stable(route_or_task or {}),
        "cancellation_terms": " ".join((cancellation_terms or "").split()),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
