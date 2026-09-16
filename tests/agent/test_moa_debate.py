"""Debate round-protocol tests (WO-MOA/2 R1/R4/R6, checklist 3/4/10).

No LLM, no Word: runner and pack builder are injected. Pinned behaviors are
the ones that go wrong silently — hostile critics terminating at the
ceiling, abstention never reading as consensus, orphaned attacks surviving
into the ruling, and one pinned evidence pack across every round.
"""

import json

from agent.moa_debate import _orphaned_attacks, _parse_stance, run_debate
from agent.moa_retrieval import build_retrieval_pack

_PRESET = {
    "aggregator": {"provider": "anthropic", "model": "agg"},
    "reference_models": [
        {"provider": "openrouter", "model": "a", "name": "A", "enabled": True},
        {"provider": "openrouter", "model": "b", "name": "B", "enabled": True},
        {"provider": "openrouter", "model": "c", "name": "C", "enabled": False},
    ],
    "debate_rounds": 2,
}

_ARTIFACT = "Idea pool:\n1. cache everything\n2. drop the server\n"


def _stance(stance, critique="because reasons"):
    return json.dumps({"stance": stance, "critique": critique})


def _pack_builder(calls):
    def build(agent, query, turn_kind, **kwargs):
        calls.append((query, turn_kind))
        return build_retrieval_pack([{"id": "note:a", "snippet": "prior art"}])

    return build


class _Runner:
    """Scripted slot runner: replies keyed by slot name, per round."""

    def __init__(self, script, ruling="RULED"):
        self.script = script  # list of {slot_name: reply} per round
        self.ruling = ruling
        self.prompts = []
        self._slot_calls = 0

    def __call__(self, slot, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        name = slot.get("name")
        if name is None:  # aggregator ruling call
            return ("agg", self.ruling, None)
        round_idx = self._slot_calls // 2  # 2 enabled slots per round
        self._slot_calls += 1
        table = self.script[min(round_idx, len(self.script) - 1)]
        reply = table.get(name, "")
        if isinstance(reply, Exception):
            raise reply
        return (name, reply, None)


class TestParsing:
    def test_valid_stances(self):
        assert _parse_stance(_stance("attack", "x")) == ("attack", "x")
        assert _parse_stance('```json\n{"stance":"CONCEDE"}\n```') == ("concede", "")

    def test_junk_yields_no_stance(self):
        assert _parse_stance("I disagree, broadly.") == (None, "")
        assert _parse_stance(json.dumps({"stance": "maybe"}))[0] is None
        assert _parse_stance("") == (None, "")


class TestOrphanDetection:
    def test_attacker_that_vanished_is_orphaned(self):
        first = [
            {"label": "A", "stance": "attack", "critique": "tenancy", "ok": True},
            {"label": "B", "stance": "concede", "critique": "", "ok": True},
        ]
        final = [
            {"label": "A", "stance": None, "critique": "", "ok": False},
            {"label": "B", "stance": "concede", "critique": "", "ok": True},
        ]
        assert _orphaned_attacks(first, final) == {"A": "tenancy"}

    def test_conceder_that_vanished_is_not_orphaned(self):
        first = [{"label": "A", "stance": "concede", "critique": "fine", "ok": True}]
        final = [{"label": "A", "stance": None, "critique": "", "ok": False}]
        assert _orphaned_attacks(first, final) == {}


class TestRunDebate:
    def test_two_rounds_then_ceiling(self):
        runner = _Runner(
            [
                {
                    "A": _stance("attack", "weak cache key"),
                    "B": _stance("attack", "no tenancy"),
                },
                {"A": _stance("concede"), "B": _stance("attack", "still broken")},
            ]
        )
        calls = []
        out = run_debate(
            None,
            _PRESET,
            _ARTIFACT,
            artifact_ref="ab12cd34",
            runner=runner,
            pack_builder=_pack_builder(calls),
        )
        assert out["rounds"] == 2
        assert out["exit_reason"] == "rounds_exhausted"
        assert out["ruling"] == "RULED"
        assert out["artifact_ref"] == "ab12cd34"
        assert out["retrieval_ref"].startswith("wordpack:")
        # Retrieved ONCE and pinned for both rounds (R7.2).
        assert len(calls) == 1 and calls[0][1] == "debate"
        # Round 2 prompt carried round 1's critiques (cross-talk is required).
        assert "weak cache key" in runner.prompts[2]

    def test_all_concede_exits_after_one_round(self):
        runner = _Runner([{"A": _stance("concede"), "B": _stance("concede")}])
        out = run_debate(
            None, _PRESET, _ARTIFACT, runner=runner, pack_builder=_pack_builder([])
        )
        assert out["rounds"] == 1
        assert out["exit_reason"] == "exit_early"
        assert out["outcome"] == "concede"
        assert out["agreement_ratio"] == 1.0

    def test_hostile_critics_terminate_at_ceiling(self):
        preset = {**_PRESET, "debate_rounds": 3}
        runner = _Runner([{"A": _stance("attack"), "B": _stance("attack")}])
        out = run_debate(
            None, preset, _ARTIFACT, runner=runner, pack_builder=_pack_builder([])
        )
        assert out["rounds"] == 3
        assert out["exit_reason"] == "rounds_exhausted"

    def test_budget_stops_before_next_round(self):
        preset = {**_PRESET, "debate_rounds": 4, "debate_round_budget": 3}
        runner = _Runner([{"A": _stance("attack"), "B": _stance("attack")}])
        out = run_debate(
            None, preset, _ARTIFACT, runner=runner, pack_builder=_pack_builder([])
        )
        # 2 slot-calls spent; another round needs 2 more > budget 3.
        assert out["rounds"] == 1
        assert out["exit_reason"] == "budget_exhausted"

    def test_all_abstain_is_not_consensus(self):
        runner = _Runner([{"A": "garbage", "B": "also garbage"}])
        out = run_debate(
            None, _PRESET, _ARTIFACT, runner=runner, pack_builder=_pack_builder([])
        )
        # Unparseable != concede: the debate ran its full rounds.
        assert out["rounds"] == 2
        assert out["exit_reason"] == "rounds_exhausted"
        assert out["outcome"] is None

    def test_dead_round2_slot_leaves_unresolved_attack_in_ruling(self):
        runner = _Runner(
            [
                {"A": _stance("attack", "cache key unstable"), "B": _stance("concede")},
                {"A": RuntimeError("slot died"), "B": _stance("concede")},
            ]
        )
        out = run_debate(
            None, _PRESET, _ARTIFACT, runner=runner, pack_builder=_pack_builder([])
        )
        assert out["unresolved"] == {"A": "cache key unstable"}
        ruling_prompt = runner.prompts[-1]
        assert "unresolved — weigh accordingly" in ruling_prompt
        assert "cache key unstable" in ruling_prompt

    def test_guard_rails(self):
        assert "error" in run_debate(None, _PRESET, "   ", runner=_Runner([]))
        assert "error" in run_debate(
            None,
            {"aggregator": {}, "reference_models": []},
            _ARTIFACT,
            runner=_Runner([]),
        )
