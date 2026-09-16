"""Vote kernel tests (WO-MOA/2 Phase 2). Pure inputs, no mocks, no LLM."""

from itertools import product

from agent.moa_vote import (
    DEBATE_BUDGET_EXHAUSTED,
    DEBATE_CONTINUE,
    DEBATE_EXIT_EARLY,
    DEBATE_ROUNDS_EXHAUSTED,
    STANCE_ATTACK,
    STANCE_CONCEDE,
    VERDICT_CONTRADICTED,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
    blocking_contradictions,
    next_debate_action,
    tally,
    tally_claims,
)


class TestTally:
    def test_simple_majority(self):
        r = tally(["A", "A", "B"])
        assert r.winner == "A"
        assert r.total == 3
        assert r.counts == {"A": 2, "B": 1}
        assert abs(r.agreement_ratio - 2 / 3) < 1e-9
        assert not r.unanimous

    def test_unanimous(self):
        r = tally(["x", "x", "x"])
        assert r.winner == "x" and r.unanimous and r.agreement_ratio == 1.0

    def test_empty_votes(self):
        r = tally([])
        assert r.winner is None and r.total == 0 and r.agreement_ratio == 0.0
        assert not r.unanimous

    def test_tie_breaks_to_first_seen(self):
        assert tally(["B", "A", "A", "B"]).winner == "B"
        assert tally(["A", "B", "B", "A"]).winner == "A"

    def test_invariants_exhaustive_small_domain(self):
        # Every vote list up to length 4 over a 3-value domain.
        domain = ("a", "b", "c")
        for n in range(1, 5):
            for votes in product(domain, repeat=n):
                r = tally(votes)
                assert r.total == n
                assert 0.0 < r.agreement_ratio <= 1.0
                assert sum(r.counts.values()) == n
                assert r.winner in votes
                assert r.counts[r.winner] == max(r.counts.values())
                # Determinism: same input, same result.
                assert tally(votes) == r


class TestNextDebateAction:
    def test_rounds_exhausted_is_hard_stop(self):
        # Even an all-attack round cannot extend past max_rounds.
        assert (
            next_debate_action(
                completed_rounds=2,
                max_rounds=2,
                last_round_stances=[STANCE_ATTACK, STANCE_ATTACK],
            )
            == DEBATE_ROUNDS_EXHAUSTED
        )

    def test_all_concede_exits_early(self):
        assert (
            next_debate_action(
                completed_rounds=1,
                max_rounds=2,
                last_round_stances=[STANCE_CONCEDE, STANCE_CONCEDE, STANCE_CONCEDE],
            )
            == DEBATE_EXIT_EARLY
        )

    def test_empty_stances_do_not_exit_early(self):
        # A round that produced no stances (all slots died) must not read as
        # consensus.
        assert (
            next_debate_action(
                completed_rounds=1, max_rounds=2, last_round_stances=[]
            )
            == DEBATE_CONTINUE
        )

    def test_budget_blocks_next_round(self):
        assert (
            next_debate_action(
                completed_rounds=1,
                max_rounds=3,
                last_round_stances=[STANCE_ATTACK],
                spent_slot_calls=5,
                round_budget=8,
                next_round_slots=5,
            )
            == DEBATE_BUDGET_EXHAUSTED
        )

    def test_budget_exactly_sufficient_continues(self):
        assert (
            next_debate_action(
                completed_rounds=1,
                max_rounds=3,
                last_round_stances=[STANCE_ATTACK],
                spent_slot_calls=5,
                round_budget=10,
                next_round_slots=5,
            )
            == DEBATE_CONTINUE
        )

    def test_none_budget_is_unbudgeted(self):
        assert (
            next_debate_action(
                completed_rounds=1,
                max_rounds=3,
                last_round_stances=[STANCE_ATTACK],
                spent_slot_calls=10_000,
                round_budget=None,
                next_round_slots=10_000,
            )
            == DEBATE_CONTINUE
        )

    def test_mixed_stances_continue(self):
        assert (
            next_debate_action(
                completed_rounds=1,
                max_rounds=2,
                last_round_stances=[STANCE_ATTACK, STANCE_CONCEDE],
            )
            == DEBATE_CONTINUE
        )

    def test_hostile_always_attack_terminates_at_ceiling(self):
        # Contract acceptance shape: an adversarial stub that never concedes
        # must be forcibly terminated by the round ceiling.
        completed = 0
        max_rounds = 4
        actions = []
        while True:
            action = next_debate_action(
                completed_rounds=completed,
                max_rounds=max_rounds,
                last_round_stances=[STANCE_ATTACK] * 3,
            )
            actions.append(action)
            if action != DEBATE_CONTINUE:
                break
            completed += 1
        assert actions[-1] == DEBATE_ROUNDS_EXHAUSTED
        assert completed == max_rounds  # ran exactly max_rounds rounds


class TestVerifyTallies:
    def test_only_contradicted_blocks(self):
        results = tally_claims(
            {
                "c1": [VERDICT_SUPPORTED, VERDICT_SUPPORTED, VERDICT_CONTRADICTED],
                "c2": [VERDICT_CONTRADICTED, VERDICT_CONTRADICTED, VERDICT_SUPPORTED],
                "c3": [VERDICT_NO_EVIDENCE, VERDICT_NO_EVIDENCE, VERDICT_NO_EVIDENCE],
            }
        )
        blocked = blocking_contradictions(results)
        assert blocked == ["c2"]
        # no_evidence flags in the table (winner) but never blocks.
        assert results["c3"].winner == VERDICT_NO_EVIDENCE

    def test_no_claims_no_blocks(self):
        assert blocking_contradictions(tally_claims({})) == []
