"""Word-grounded chain-of-verification for saved plans (WO-MOA/2 R7.3).

The real satisfier of ``plan_verify``'s verify seam. Shape:

    plan content
      -> extract atomic claims              (1 aggregator call)
      -> per claim, retrieve Word evidence  (word_seam, registry-mediated)
      -> each reference slot votes on ALL claims at once, categorically
      -> Python tallies per claim           (moa_vote)

Vote economics: slots are asked to judge every claim in ONE call, so the
fan-out costs ``len(slots)`` calls, not ``len(claims) x len(slots)``. Each
claim still collects one vote per slot, which is exactly the per-claim
self-consistency the contract asks for (R3 composition note).

Failure posture: this pass advises a gate that can block approval, so it
never fabricates confidence. A slot that dies, times out, or returns
unparseable output simply does not vote; a claim nobody voted on tallies to
no winner and therefore never blocks. Missing Word evidence yields
``no_evidence``, which flags but never refuses (operator ruling).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.model_json import extract_json_payload
from agent.moa_vote import (
    VERDICT_CONTRADICTED,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
)

logger = logging.getLogger(__name__)

MAX_CLAIMS = 8
EVIDENCE_PER_CLAIM = 4
_VALID_VERDICTS = {VERDICT_SUPPORTED, VERDICT_CONTRADICTED, VERDICT_NO_EVIDENCE}

_CLAIM_PROMPT = (
    "Extract the load-bearing factual claims this plan RESTS ON — assertions "
    "about how the system currently works, what exists, or what was already "
    "decided. Ignore proposed actions, intentions, and opinions: those are "
    "the plan, not its foundations.\n"
    "Output a JSON array of at most {max_claims} short claim strings. No "
    "prose before or after.\n\n"
    "PLAN:\n{plan}"
)

_VERDICT_PROMPT = (
    "You are verifying a plan's factual foundations against retrieved "
    "evidence from the operator's knowledge base.\n"
    "For EACH numbered claim, emit exactly one verdict:\n"
    "  supported    — the evidence affirms the claim\n"
    "  contradicted — the evidence directly conflicts with the claim\n"
    "  no_evidence  — the evidence neither affirms nor conflicts\n"
    "Judge ONLY against the evidence shown. Absence of evidence is "
    "no_evidence, never contradicted. Do not infer, do not use outside "
    "knowledge.\n"
    "Output a JSON object mapping claim number (as a string) to verdict. No "
    "prose before or after.\n\n"
    "{claims_block}"
)


def _extract_json(text: str, opener: str):
    """Shared tolerant JSON extraction (see ``agent.model_json``).

    Kept as a module-local alias so the postures share one notion of
    "parseable" — a reply debate accepts is a reply verify accepts.
    """
    return extract_json_payload(text, opener)


def extract_claims(plan_content: str, aggregator_slot: dict, *, runner) -> list:
    """One call: plan -> list of atomic factual claims (capped)."""
    if not (plan_content or "").strip():
        return []
    prompt = _CLAIM_PROMPT.format(max_claims=MAX_CLAIMS, plan=plan_content)
    _label, text, _acct = runner(
        aggregator_slot, [{"role": "user", "content": prompt}]
    )
    parsed = _extract_json(text, "[")
    if not isinstance(parsed, list):
        logger.debug("Claim extraction returned unparseable output; no claims")
        return []
    claims = []
    for item in parsed:
        if isinstance(item, dict):
            claim = str(item.get("text") or item.get("claim") or "").strip()
        else:
            claim = str(item).strip()
        if claim and claim not in claims:
            claims.append(claim)
        if len(claims) >= MAX_CLAIMS:
            break
    return claims


def _claims_block(claims: list, evidence: dict) -> str:
    lines = []
    for idx, claim in enumerate(claims, start=1):
        lines.append(f"CLAIM {idx}: {claim}")
        notes = evidence.get(claim) or []
        if notes:
            for note in notes:
                lines.append(f"  evidence ({note['id']}): {note['text']}")
        else:
            lines.append("  evidence: (none retrieved)")
        lines.append("")
    return "\n".join(lines)


def _parse_verdicts(text: str, claim_count: int) -> dict:
    """{claim_index -> verdict} for the verdicts this slot returned validly."""
    parsed = _extract_json(text, "{")
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for key, value in parsed.items():
        try:
            idx = int(str(key).strip().lstrip("#"))
        except (TypeError, ValueError):
            continue
        verdict = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if 1 <= idx <= claim_count and verdict in _VALID_VERDICTS:
            out[idx] = verdict
    return out


def build_claim_votes(
    agent: Any,
    preset: dict,
    plan_content: str,
    session_id: str = "",
    *,
    runner=None,
    retriever=None,
    evidence_out: dict | None = None,
) -> dict:
    """Run the full CoVe pass. Returns ``{claim: [verdict, ...]}``.

    ``evidence_out`` (4.8): when given, filled with ``{claim: [note, …]}`` —
    the retrieved evidence each verdict rested on, so the gate can mint pins.

    Satisfies ``plan_verify``'s verify seam once bound to an agent+preset.
    ``runner``/``retriever`` are injectable for tests; production defaults
    reuse the MoA reference primitive and the registry-mediated Word seam —
    no new clients (R7.1).
    """
    if runner is None:
        from agent.moa_loop import _run_reference as runner  # noqa: N813
    if retriever is None:
        from agent.word_seam import retrieve as retriever

    aggregator = (preset or {}).get("aggregator") or {}
    slots = [
        s for s in ((preset or {}).get("reference_models") or [])
        if s.get("enabled", True)
    ]
    if not aggregator or not slots:
        logger.debug("CoVe: no aggregator or no reference slots; nothing to verify")
        return {}

    claims = extract_claims(plan_content, aggregator, runner=runner)
    if not claims:
        return {}

    evidence = {}
    for claim in claims:
        hits = retriever(agent, claim, limit=EVIDENCE_PER_CLAIM) or []
        notes = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            text = str(
                hit.get("snippet") or hit.get("content") or hit.get("title") or ""
            ).strip()
            if text:
                notes.append({"id": str(hit.get("id") or "?"), "text": text})
        evidence[claim] = notes
    if evidence_out is not None:
        evidence_out.clear()
        evidence_out.update(evidence)

    prompt = _VERDICT_PROMPT.format(claims_block=_claims_block(claims, evidence))
    votes = {claim: [] for claim in claims}
    for slot in slots:
        try:
            _label, text, _acct = runner(
                slot, [{"role": "user", "content": prompt}]
            )
        except Exception as exc:  # pragma: no cover - runner is never-raising
            logger.debug("CoVe slot failed (abstains): %s", exc)
            continue
        for idx, verdict in _parse_verdicts(text, len(claims)).items():
            votes[claims[idx - 1]].append(verdict)
    return votes


def make_verify_fn(agent: Any, preset: dict):
    """Bind agent+preset into ``plan_verify``'s ``(plan, session_id)`` seam."""

    def _verify(plan_content: str, session_id: str = "") -> dict:
        _verify.last_evidence = {}
        return build_claim_votes(
            agent, preset, plan_content, session_id, evidence_out=_verify.last_evidence
        )

    _verify.last_evidence = {}  # 4.8: evidence behind the last verdicts
    return _verify
