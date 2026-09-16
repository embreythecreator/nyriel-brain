"""Retrieval-pack core for MoA postures (WO-MOA/2, R7 — pure part).

Doctrine: retrieve-once, fan-out-many. The orchestrator performs a single
Word retrieval before the fan-out, builds one pack, and injects it into
every reference prompt — slots never query Word themselves. This module is
the pure core: pack construction, prefetch dedup, snapshot pinning, and the
categorical recall gate. The live Word client wiring (plugin-registry
accessor) stays outside so this logic is testable against frozen fixtures,
per the build appendix ("solve the shape problem without the flakiness of
a real client").

Dedup rule (checklist 16): the Word memory plugin already prefetches recall
into the turn per its own identity (note ids). The pack SUBTRACTS those ids
rather than re-retrieving — slots must never see the same evidence twice
under two labels.

Snapshot pinning (R7.2): ``snapshot_ref`` is a content hash over the pack's
(id, text) pairs, stable across debate rounds. It joins the cache key and
lands in traces as ``retrieval_ref`` so traces stay joinable to exactly
what the critics saw.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# Categorical recall gate (R7.5). Plan-lifecycle turns always retrieve;
# casual ensemble turns retrieve only when configured to.
RECALL_ALWAYS_TURNS = frozenset({"debate", "verify"})


def needs_recall(turn_kind: str, *, casual_recall_enabled: bool = False) -> bool:
    """Categorical gate: no free-text judgment, position decides.

    ``turn_kind`` is the resolved MoA mode for the turn. Debate and verify
    (plan-lifecycle) always retrieve; everything else follows the config
    flag (default off — retrieval costs latency and context budget).
    """
    kind = str(turn_kind or "").strip().lower()
    if kind in RECALL_ALWAYS_TURNS:
        return True
    return bool(casual_recall_enabled)


def _note_text(hit: Mapping[str, Any]) -> str:
    for key in ("content", "snippet", "text", "title"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass(frozen=True)
class RetrievalPack:
    """One pinned evidence pack, shared verbatim by every reference slot."""

    notes: tuple  # tuple of {"id": str, "text": str}
    snapshot_ref: str
    dropped_as_prefetched: int = 0

    @property
    def empty(self) -> bool:
        return not self.notes


def build_retrieval_pack(
    hits: Sequence[Mapping[str, Any]],
    *,
    prefetch_ids: Iterable[str] = (),
    max_notes: int = 12,
) -> RetrievalPack:
    """Build the pinned pack from raw Word search hits.

    - Dedup within the hit list by note id (first occurrence wins — search
      ranking order is preserved).
    - Subtract ids the plugin's per-turn prefetch already surfaced.
    - Cap at ``max_notes`` (ranking order).
    - ``snapshot_ref`` hashes the surviving (id, text) pairs in a sorted,
      order-independent form: the same evidence set always pins the same
      ref regardless of ranking shuffle.
    """
    prefetched = {str(p) for p in prefetch_ids}
    seen: set = set()
    notes: list = []
    dropped = 0
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        note_id = str(hit.get("id") or "").strip()
        text = _note_text(hit)
        if not note_id or not text:
            continue
        if note_id in prefetched:
            dropped += 1
            continue
        if note_id in seen:
            continue
        seen.add(note_id)
        notes.append({"id": note_id, "text": text})
        if len(notes) >= max_notes:
            break
    digest = hashlib.sha256(
        " ".join(
            f"{n['id']}:{n['text']}"
            for n in sorted(notes, key=lambda n: n["id"])
        ).encode("utf-8", "replace")
    ).hexdigest()
    return RetrievalPack(
        notes=tuple(notes),
        snapshot_ref=f"wordpack:{digest[:16]}",
        dropped_as_prefetched=dropped,
    )


def render_pack(pack: RetrievalPack) -> str:
    """Render the pack as the evidence block injected into every reference
    prompt. Empty pack renders empty (callers skip injection)."""
    if pack.empty:
        return ""
    lines = [
        "[Word evidence pack — identical for every reference; cite note ids "
        f"when you rely on them. snapshot {pack.snapshot_ref}]"
    ]
    for note in pack.notes:
        lines.append(f"- ({note['id']}) {note['text']}")
    return "\n".join(lines)
