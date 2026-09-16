"""Registry-mediated access to the Word organ for MoA retrieval (WO-MOA/2).

Layering rule (checklist 16): ``moa_loop``/``moa_retrieval`` are core; the
Word client lives in a plugin (``plugins/memory/word``). Core must never
import the plugin — that inverts the plugin architecture. Everything here
goes through the agent's own MemoryManager registry, which is where the
plugin registered itself, so this module has ZERO plugin imports.

Single-client rule (R7.1): retrieval reuses the provider the agent already
holds. WO-BODY/1 flagged two uncoordinated Word clients as a structural
gap; minting a third here (a direct HTTP client, or a per-slot path) is
forbidden.

Degradation: Word being absent, unconfigured, or down is normal (local-first
organ). Every failure yields an empty hit list — a MoA turn continues with
an empty evidence pack rather than dying on recall.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

WORD_PROVIDER_NAME = "word"


def get_word_provider(agent: Any):
    """The agent's registered Word memory provider, or None.

    None means: no memory manager, Word not installed/registered, or the
    provider reports itself unavailable. Callers treat all three the same.
    """
    manager = getattr(agent, "_memory_manager", None)
    if manager is None:
        return None
    try:
        provider = manager.get_provider(WORD_PROVIDER_NAME)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Word provider lookup failed: %s", exc)
        return None
    if provider is None:
        return None
    try:
        if not provider.is_available():
            return None
    except Exception:  # pragma: no cover - availability probe is advisory
        return None
    return provider


def retrieve(agent: Any, query: str, *, limit: int = 8) -> list:
    """One Word search for the whole fan-out. Returns hydrated hit dicts.

    Hit shape is the provider's own (``id``/``title``/``snippet``/…), which
    is what ``moa_retrieval.build_retrieval_pack`` consumes. Returns [] on
    every failure path — never raises into a turn.
    """
    query = (query or "").strip()
    if not query:
        return []
    provider = get_word_provider(agent)
    if provider is None:
        return []
    search = getattr(provider, "_search_hydrated", None)
    if not callable(search):
        logger.debug("Word provider exposes no hydrated search; skipping recall")
        return []
    try:
        hits = search(query, limit=limit)
    except Exception as exc:
        logger.debug("Word retrieval failed (non-fatal): %s", exc)
        return []
    return [h for h in (hits or []) if isinstance(h, dict)]


def capture(agent: Any, title: str, content: str, *, metadata: dict = None):
    """Write-half twin of ``retrieve`` (WO-POSTURE/1 1.3).

    Duck-typed ``capture_note`` probe on the registered provider; returns the
    vault id (str) when Word acknowledged the write, None on every other
    path — queued, unavailable, or provider without a write half. Never
    raises into a turn.
    """
    if not (title or "").strip() or not (content or "").strip():
        return None
    provider = get_word_provider(agent)
    if provider is None:
        return None
    write = getattr(provider, "capture_note", None)
    if not callable(write):
        logger.debug("Word provider exposes no capture_note; skipping capture")
        return None
    try:
        vault_id = write(title, content, metadata=metadata)
    except Exception as exc:
        logger.debug("Word capture failed (non-fatal): %s", exc)
        return None
    return str(vault_id) if vault_id else None


def build_pack_for_turn(
    agent: Any,
    query: str,
    turn_kind: str,
    *,
    casual_recall_enabled: bool = False,
    limit: int = 8,
    max_notes: int = 12,
):
    """Retrieve-once composition: gate → one search → dedup → pinned pack.

    The single entry point debate/verify call before fanning out. Returns a
    ``RetrievalPack`` (empty when the gate says no recall, or when Word is
    unreachable). Keeping this here rather than in ``moa_retrieval`` leaves
    that module pure and fixture-testable; this one owns the agent contact.
    """
    from agent.moa_retrieval import build_retrieval_pack, needs_recall

    if not needs_recall(turn_kind, casual_recall_enabled=casual_recall_enabled):
        return build_retrieval_pack([])
    hits = retrieve(agent, query, limit=limit)
    return build_retrieval_pack(
        hits, prefetch_ids=prefetched_ids(agent), max_notes=max_notes
    )


def prefetched_ids(agent: Any) -> set:
    """Note ids the plugin's per-turn prefetch already put in this turn.

    The retrieval pack SUBTRACTS these (checklist 16) so reference slots
    never see the same evidence twice under two labels. Read from whatever
    the agent recorded for the turn; an empty set simply means "nothing to
    subtract", which is safe (at worst a note appears once in the pack and
    once in the prefetch overlay — the same as today's behavior).
    """
    raw = getattr(agent, "_word_prefetch_ids", None)
    if not raw:
        return set()
    try:
        return {str(x) for x in raw}
    except TypeError:  # pragma: no cover - defensive
        return set()
