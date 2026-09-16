"""Evidence pins for deep-research plans (WO-POSTURE/1 4.7). Pure.

A pin is a small, hashable record tying a plan claim to a piece of evidence
the Brain actually read — an arXiv id, a Word vault id, or a URL. The plan's
``evidence_digest`` is the order-independent hash of its pin ids, so a plan
whose evidence set changed underneath it can be recognised as stale (4.8:
stale → treated as ``contradicted`` per D-9).

Everything here is total: junk in, empty/deterministic out, never raise.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

PIN_KINDS = ("arxiv", "vault", "url")


def _kind_for(ref: str) -> str:
    if ref.startswith(("note:", "source:")):
        return "vault"
    if ref.startswith(("http://", "https://")):
        return "url"
    return "arxiv"


def mint_pin(ref: Any, claim: Any = "", *, title: Any = "", notebook: Any = "") -> dict | None:
    """One pin, or None when ``ref`` is empty. ``id`` is the identity."""
    ref = str(ref or "").strip()
    if not ref:
        return None
    return {
        "id": ref,
        "kind": _kind_for(ref),
        "claim": str(claim or "").strip(),
        "title": str(title or "").strip(),
        "notebook": str(notebook or "").strip(),
    }


def pins_from_evidence(evidence: Any) -> tuple:
    """Frame-output ``evidence[]`` (``{arxiv_id|vault_id|url, claim}``) → pins.

    Deduplicated by id, first claim wins, input order preserved. Anything
    that is not a dict with a usable ref is dropped silently.
    """
    if not isinstance(evidence, (list, tuple)):
        return ()
    out: dict[str, dict] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        ref = item.get("arxiv_id") or item.get("vault_id") or item.get("url") or item.get("id")
        pin = mint_pin(ref, item.get("claim"), title=item.get("title"), notebook=item.get("notebook"))
        if pin and pin["id"] not in out:
            out[pin["id"]] = pin
    return tuple(out.values())


def coerce_pins(raw: Any) -> tuple:
    """Total coercion for persisted state: a list of pin dicts or ids → pins."""
    if isinstance(raw, dict) or isinstance(raw, (str, bytes)) or raw is None:
        return ()
    try:
        items = list(raw)
    except TypeError:
        return ()
    out: dict[str, dict] = {}
    for item in items:
        pin = None
        if isinstance(item, dict):
            pin = mint_pin(item.get("id"), item.get("claim"), title=item.get("title"), notebook=item.get("notebook"))
        elif isinstance(item, str):
            pin = mint_pin(item)
        if pin and pin["id"] not in out:
            out[pin["id"]] = pin
    return tuple(out.values())


def evidence_digest(pins: Iterable[Any]) -> str:
    """Order-independent sha256 over pin ids; '' for no pins."""
    ids = sorted({str(p.get("id")) for p in (pins or ()) if isinstance(p, dict) and p.get("id")})
    if not ids:
        return ""
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
