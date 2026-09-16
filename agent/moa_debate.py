"""Multi-round adversarial debate over a saved artifact (WO-MOA/2, R1/R4/R6).

The convergence half of the doctrine. ADHD's diverge phase forbids
cross-talk — blind branches map a wide space. Debate requires it: critics
must see each other's attacks to rebut or concede, because an argument
nobody answers is not a tested argument.

Protocol:
  round 1  artifact + pinned evidence -> each slot attacks the weakest
           assumption, emitting a categorical stance (attack|concede)
  gate     next_debate_action(): rounds ceiling > all-concede early exit >
           round budget > continue                                    (R4)
  round 2  artifact + ALL round-1 critiques -> rebut or concede
  ruling   aggregator reads the whole transcript and takes a position

Load-bearing details:

- The evidence pack is retrieved ONCE and pinned for every round and every
  slot (R7.2), so the cache key and the trace's ``retrieval_ref`` describe
  exactly what each critic saw.
- A slot that dies after round 1 leaves its attack standing. Silently
  dropping it biases the aggregator against whatever it attacked, so
  orphans are marked unresolved-not-conceded in the ruling prompt (R6).
- Abstention is not consensus: a slot whose reply will not parse yields no
  stance, and a round with zero stances never counts as all-concede.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.model_json import extract_json_payload
from agent.moa_vote import (
    DEBATE_CONTINUE,
    STANCE_ATTACK,
    STANCE_CONCEDE,
    format_unresolved_critiques,
    next_debate_action,
    tally,
)

logger = logging.getLogger(__name__)

MAX_ROUNDS_CEILING = 5

_STANCE_INSTRUCTION = (
    "Reply with a JSON object only, no prose before or after:\n"
    '{"stance": "attack" | "concede", "critique": "<one paragraph>"}\n'
    '"attack" means you have a substantive objection. "concede" means you '
    "have none worth raising — do not concede merely to be agreeable, and "
    "do not manufacture an objection to seem rigorous."
)

_ROUND1_PROMPT = (
    "You are one of several independent critics examining a SAVED artifact "
    "before it is committed to.\n"
    "Attack its weakest load-bearing assumption. Prefer the objection that "
    "would cost the most if it went unnoticed. Cite evidence note ids when "
    "the evidence bears on your point.\n\n"
    "{evidence}"
    "ARTIFACT:\n{artifact}\n\n"
    "{stance_instruction}"
)

_ROUND2_PROMPT = (
    "You are one of several independent critics. The previous round is "
    "complete and every critic's objection is below, including your own.\n"
    "Rebut the objections you believe are wrong, or concede. Change your "
    "position if another critic showed you were mistaken — conceding to a "
    "better argument is the point of this round, not a loss.\n\n"
    "{evidence}"
    "ARTIFACT:\n{artifact}\n\n"
    "CRITIQUES SO FAR:\n{critiques}\n\n"
    "{stance_instruction}"
)

_RULING_PROMPT = (
    "You are the aggregator. Independent critics have debated a saved "
    "artifact over {rounds} round(s); the full transcript is below.\n"
    "Rule on it. State which objections survived, which were answered, and "
    "what should actually change. Take a position — do not summarize the "
    "disagreement and leave the decision open.\n"
    "{unresolved}"
    "\nARTIFACT:\n{artifact}\n\n"
    "TRANSCRIPT:\n{transcript}"
)


def _slot_name(slot) -> str:
    slot = slot or {}
    return str(slot.get("name") or slot.get("wing") or slot.get("model") or "reference")


def _parse_stance(text) -> tuple:
    """(stance, critique) from one slot reply; (None, ...) when unparseable."""
    parsed = extract_json_payload(text, "{")
    if not isinstance(parsed, dict):
        return None, ""
    stance = str(parsed.get("stance") or "").strip().lower()
    critique = str(parsed.get("critique") or parsed.get("text") or "").strip()
    if stance not in (STANCE_ATTACK, STANCE_CONCEDE):
        return None, critique
    return stance, critique


def _run_round(slots: list, prompt: str, runner) -> list:
    """Fan one prompt across every slot in parallel. Never raises.

    One entry per slot, in slot order: ``{"label", "stance", "critique",
    "ok"}``. A slot that raises or returns junk lands with ``ok=False`` and
    ``stance=None`` — an abstention, deliberately NOT a concession.
    """
    if not slots:
        return []
    messages = [{"role": "user", "content": prompt}]

    def _one(slot):
        fallback = _slot_name(slot)
        try:
            label, text, _acct = runner(slot, list(messages))
        except Exception as exc:  # pragma: no cover - runner is never-raising
            logger.debug("Debate slot raised (abstains): %s", exc)
            return {"label": fallback, "stance": None, "critique": "", "ok": False}
        stance, critique = _parse_stance(text)
        return {
            "label": label or fallback,
            "stance": stance,
            "critique": critique,
            "ok": stance is not None,
        }

    with ThreadPoolExecutor(max_workers=min(len(slots), 8)) as pool:
        return list(pool.map(_one, slots))


def _render_critiques(entries: list) -> str:
    lines = [
        f"- {e['label']} [{e['stance']}]: {e['critique']}"
        for e in entries
        if e["ok"] and e["critique"]
    ]
    return "\n".join(lines) or "(no parseable critiques)"


def _orphaned_attacks(first_round: list, final_round: list) -> dict:
    """Round-1 attacks whose author never spoke again (R6)."""
    answered = {e["label"] for e in final_round if e["ok"]}
    return {
        e["label"]: e["critique"]
        for e in first_round
        if e["ok"] and e["stance"] == STANCE_ATTACK and e["label"] not in answered
    }


def run_debate(
    agent: Any,
    preset: dict,
    artifact_text: str,
    *,
    artifact_ref: str = "",
    runner=None,
    pack_builder=None,
) -> dict:
    """Run the protocol. Returns a trace-shaped result dict.

    Result keys double as the debate trace record (checklist 11):
    ``mode``, ``rounds``, ``artifact_ref``, ``retrieval_ref``, ``stances``,
    ``exit_reason``, ``unresolved``, ``ruling``.
    """
    if runner is None:
        from agent.moa_loop import _run_reference as runner  # noqa: N813
    if pack_builder is None:
        from agent.word_seam import build_pack_for_turn as pack_builder

    aggregator = (preset or {}).get("aggregator") or {}
    slots = [
        s for s in ((preset or {}).get("reference_models") or [])
        if s.get("enabled", True)
    ]
    if not aggregator or not slots:
        return {"error": "debate needs an aggregator and at least one reference slot"}
    if not (artifact_text or "").strip():
        return {"error": "debate needs a non-empty artifact"}

    max_rounds = max(
        1, min(int((preset or {}).get("debate_rounds", 2) or 2), MAX_ROUNDS_CEILING)
    )
    round_budget = (preset or {}).get("debate_round_budget")

    # Retrieve once, pin for every round and every slot (R7.2).
    pack = pack_builder(agent, artifact_text[:2000], "debate")
    evidence_block = ""
    try:
        from agent.moa_retrieval import render_pack

        rendered = render_pack(pack)
        if rendered:
            evidence_block = f"{rendered}\n\n"
    except Exception:  # pragma: no cover - evidence is advisory
        pass

    transcript_parts: list = []
    first_round: list = []
    prior: list = []
    rounds_run = 0
    spent = 0
    exit_reason = None

    while True:
        if rounds_run == 0:
            prompt = _ROUND1_PROMPT.format(
                evidence=evidence_block,
                artifact=artifact_text,
                stance_instruction=_STANCE_INSTRUCTION,
            )
        else:
            prompt = _ROUND2_PROMPT.format(
                evidence=evidence_block,
                artifact=artifact_text,
                critiques=_render_critiques(prior),
                stance_instruction=_STANCE_INSTRUCTION,
            )
        entries = _run_round(slots, prompt, runner)
        spent += len(slots)
        rounds_run += 1
        transcript_parts.append(f"ROUND {rounds_run}:\n{_render_critiques(entries)}")
        if rounds_run == 1:
            first_round = entries
        prior = entries

        exit_reason = next_debate_action(
            completed_rounds=rounds_run,
            max_rounds=max_rounds,
            last_round_stances=[e["stance"] for e in entries if e["ok"]],
            spent_slot_calls=spent,
            round_budget=round_budget,
            next_round_slots=len(slots),
        )
        if exit_reason != DEBATE_CONTINUE:
            break

    unresolved = _orphaned_attacks(first_round, prior) if rounds_run > 1 else {}
    outcome = tally([e["stance"] for e in prior if e["ok"]])

    unresolved_block = format_unresolved_critiques(unresolved)
    ruling_prompt = _RULING_PROMPT.format(
        rounds=rounds_run,
        unresolved=f"\n{unresolved_block}\n" if unresolved_block else "",
        artifact=artifact_text,
        transcript="\n\n".join(transcript_parts),
    )
    try:
        _label, ruling, _acct = runner(
            aggregator, [{"role": "user", "content": ruling_prompt}]
        )
    except Exception as exc:  # pragma: no cover - runner is never-raising
        logger.debug("Debate ruling failed: %s", exc)
        ruling = ""

    return {
        "mode": "debate",
        "artifact_ref": artifact_ref,
        "retrieval_ref": getattr(pack, "snapshot_ref", ""),
        "rounds": rounds_run,
        "exit_reason": exit_reason,
        "stances": {e["label"]: e["stance"] for e in prior},
        "outcome": outcome.winner,
        "agreement_ratio": outcome.agreement_ratio,
        "unresolved": unresolved,
        "transcript": "\n\n".join(transcript_parts),
        "ruling": ruling or "",
    }
