"""Categorical vote kernel for the MoA postures (WO-MOA/2, Phase 2).

Pure and LLM-free: no imports from moa_loop, no model calls, no I/O. Every
non-ensemble posture counts votes through here so the counting logic exists
exactly once. The contract forbids LLM-judged prose agreement — votes are
hashable categorical values (strings/enums), full stop.

The three consumer call sites this API was shaped against (all in
moa_loop.py once the mode-aware fan-out lands):

    # self_consistency: N samples of the same model, exact-match vote
    result = tally(samples)                     # -> winner, agreement_ratio

    # debate: per-round continuation + final stance resolution
    action = next_debate_action(
        completed_rounds=1, max_rounds=preset["debate_rounds"],
        last_round_stances=stances, spent_slot_calls=spent,
        round_budget=preset["debate_round_budget"], next_round_slots=n_slots)
    outcome = tally(final_round_stances)

    # verify: per-claim verdicts over supported|contradicted|no_evidence
    results = tally_claims({claim_id: verdict_votes, ...})
    blocked = blocking_contradictions(results)  # only `contradicted` blocks

Ensemble never touches this module, so byte-identical default behavior is
structural, not tested-for.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Hashable, Iterable, Mapping, Sequence

# Debate stances (round-1 critiques emit exactly one).
STANCE_ATTACK = "attack"
STANCE_CONCEDE = "concede"

# Verify verdicts. Ruling: only `contradicted` blocks; `no_evidence` flags
# but never refuses — genuinely new work legitimately rests on claims Word
# cannot corroborate.
VERDICT_SUPPORTED = "supported"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_NO_EVIDENCE = "no_evidence"

# next_debate_action() outcomes.
DEBATE_CONTINUE = "continue"
DEBATE_EXIT_EARLY = "exit_early"
DEBATE_ROUNDS_EXHAUSTED = "rounds_exhausted"
DEBATE_BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class VoteResult:
    """Outcome of one categorical tally.

    ``winner`` is None only for an empty vote set. Ties break toward the
    value that FIRST reached the winning count in input order — a
    deterministic rule so identical inputs always produce identical results
    (no dict-ordering or hash-seed dependence).
    """

    winner: Hashable | None
    counts: dict
    total: int
    agreement_ratio: float

    @property
    def unanimous(self) -> bool:
        return self.total > 0 and self.agreement_ratio == 1.0


def tally(votes: Iterable[Hashable]) -> VoteResult:
    """Exact-match count over categorical votes.

    agreement_ratio = winner_count / total (0.0 for an empty vote set).
    """
    seq = list(votes)
    if not seq:
        return VoteResult(winner=None, counts={}, total=0, agreement_ratio=0.0)
    counts = Counter(seq)
    top = max(counts.values())
    # Deterministic tie-break: first vote (in input order) whose value holds
    # the top count.
    winner = next(v for v in seq if counts[v] == top)
    return VoteResult(
        winner=winner,
        counts=dict(counts),
        total=len(seq),
        agreement_ratio=top / len(seq),
    )


def next_debate_action(
    *,
    completed_rounds: int,
    max_rounds: int,
    last_round_stances: Sequence[str],
    spent_slot_calls: int = 0,
    round_budget: int | None = None,
    next_round_slots: int = 0,
) -> str:
    """Decide whether another debate round runs. Pure — testable with canned
    stance sequences, no model in the loop.

    Precedence (checked in order):
    1. rounds_exhausted — the hard stop at ``debate_rounds``.
    2. exit_early — every stance in the last round conceded (non-empty).
       No disagreement, no debate: this is both the cost gate and the
       cognitively correct behavior (R4).
    3. budget_exhausted — running the next round would exceed
       ``round_budget`` total slot-calls. None = unbudgeted.
    4. continue.
    """
    if completed_rounds >= max_rounds:
        return DEBATE_ROUNDS_EXHAUSTED
    stances = list(last_round_stances)
    if stances and all(s == STANCE_CONCEDE for s in stances):
        return DEBATE_EXIT_EARLY
    if round_budget is not None and spent_slot_calls + next_round_slots > round_budget:
        return DEBATE_BUDGET_EXHAUSTED
    return DEBATE_CONTINUE


def tally_claims(
    claim_votes: Mapping[Hashable, Iterable[Hashable]],
) -> dict:
    """Per-claim tallies for the verify posture: {claim_id: VoteResult}."""
    return {claim: tally(votes) for claim, votes in claim_votes.items()}


def format_unresolved_critiques(
    orphaned: Mapping[str, str],
) -> str:
    """Render round-1 critiques whose author died before round 2 (R6).

    In debate, a dead round-2 slot leaves its round-1 attack standing
    unrebutted, silently biasing the aggregator against whatever it
    attacked. The guidance block marks each orphan so the aggregator weighs
    it as unanswered, not unanswerable. ``orphaned`` maps slot label →
    its round-1 critique text. Empty input renders empty.
    """
    if not orphaned:
        return ""
    lines = [
        "[Unrebutted critiques — these reference slots failed before the "
        "rebuttal round; their attacks below are unresolved, NOT conceded. "
        "Weigh accordingly.]"
    ]
    for label, critique in orphaned.items():
        lines.append(f"- {label} (unresolved — weigh accordingly): {critique}")
    return "\n".join(lines)


def blocking_contradictions(claim_results: Mapping[Hashable, VoteResult]) -> list:
    """Claim ids whose winning verdict is `contradicted` — the ONLY verdict
    that blocks approval. `no_evidence` majorities flag in the verdict table
    but never appear here.
    """
    return [
        claim
        for claim, result in claim_results.items()
        if result.winner == VERDICT_CONTRADICTED
    ]
