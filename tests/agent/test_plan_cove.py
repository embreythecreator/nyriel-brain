"""Word-grounded CoVe tests (WO-MOA/2 checklist 7, R7.3).

No LLM, no Word: the slot runner and the retriever are injected. What is
pinned is the pass's judgment posture — abstention on garbage, no_evidence
never inventing contradiction, and per-claim vote collection.
"""

import json

import agent.plan_verify as pv
from agent.moa_vote import blocking_contradictions, tally_claims
from agent.plan_cove import (
    _parse_verdicts,
    build_claim_votes,
    extract_claims,
    make_verify_fn,
)

_PRESET = {
    "aggregator": {"provider": "anthropic", "model": "claude-sonnet-5"},
    "reference_models": [
        {"provider": "openrouter", "model": "a", "enabled": True},
        {"provider": "openrouter", "model": "b", "enabled": True},
        {"provider": "openrouter", "model": "c", "enabled": False},
    ],
}

_PLAN = "# Plan\nThe gateway already speaks HTTP/2 and Word runs on :5055.\n"


def _runner(claim_reply, verdict_replies):
    """Fake _run_reference: first call extracts claims, rest vote."""
    calls = {"n": 0}

    def run(slot, messages, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        if i == 0:
            return ("agg", claim_reply, None)
        return (f"slot{i}", verdict_replies[(i - 1) % len(verdict_replies)], None)

    run.calls = calls
    return run


def _no_evidence_retriever(agent, query, **kwargs):
    return []


class TestParsing:
    def test_verdict_parsing_filters_junk(self):
        text = (
            '```json\n{"1": "supported", "2": "CONTRADICTED", '
            '"3": "maybe", "9": "supported"}\n```'
        )
        assert _parse_verdicts(text, 3) == {1: "supported", 2: "contradicted"}

    def test_unparseable_is_no_votes(self):
        assert _parse_verdicts("I think claim 1 is fine, honestly.", 2) == {}
        assert _parse_verdicts("", 2) == {}

    def test_claim_extraction_dedups_and_caps(self):
        reply = json.dumps(["a", "a", "b"] + [f"c{i}" for i in range(20)])
        claims = extract_claims(
            _PLAN,
            _PRESET["aggregator"],
            runner=lambda s, m, **k: ("agg", reply, None),
        )
        assert claims[:2] == ["a", "b"]
        assert len(claims) <= 8

    def test_empty_plan_extracts_nothing(self):
        called = []

        def run(s, m, **k):
            called.append(1)
            return ("agg", "[]", None)

        assert extract_claims("   ", _PRESET["aggregator"], runner=run) == []
        assert not called


class TestBuildClaimVotes:
    def test_each_enabled_slot_votes_on_every_claim(self):
        claim_reply = json.dumps(["gateway speaks HTTP/2", "Word runs on :5055"])
        verdict = json.dumps({"1": "contradicted", "2": "supported"})
        run = _runner(claim_reply, [verdict])
        votes = build_claim_votes(
            None, _PRESET, _PLAN, runner=run, retriever=_no_evidence_retriever
        )
        # 1 extraction call + 2 enabled slots (the disabled one never runs).
        assert run.calls["n"] == 3
        assert votes["gateway speaks HTTP/2"] == ["contradicted", "contradicted"]
        assert votes["Word runs on :5055"] == ["supported", "supported"]
        blocked = blocking_contradictions(tally_claims(votes))
        assert blocked == ["gateway speaks HTTP/2"]

    def test_garbage_slot_abstains_rather_than_votes(self):
        claim_reply = json.dumps(["c1"])
        run = _runner(claim_reply, ["sure, looks right to me"])
        votes = build_claim_votes(
            None, _PRESET, _PLAN, runner=run, retriever=_no_evidence_retriever
        )
        assert votes == {"c1": []}
        # A claim nobody voted on never blocks.
        assert blocking_contradictions(tally_claims(votes)) == []

    def test_no_slots_returns_nothing(self):
        preset = {"aggregator": _PRESET["aggregator"], "reference_models": []}
        assert build_claim_votes(
            None, preset, _PLAN, runner=lambda *a, **k: 1 / 0
        ) == {}

    def test_evidence_is_retrieved_per_claim(self):
        seen = []

        def retriever(agent, query, **kwargs):
            seen.append(query)
            return [{"id": "note:x", "snippet": f"about {query}"}]

        claim_reply = json.dumps(["c1", "c2"])
        run = _runner(
            claim_reply, [json.dumps({"1": "supported", "2": "no_evidence"})]
        )
        build_claim_votes(None, _PRESET, _PLAN, runner=run, retriever=retriever)
        assert seen == ["c1", "c2"]

    def test_no_evidence_majority_flags_but_never_blocks(self):
        claim_reply = json.dumps(["novel thing"])
        run = _runner(claim_reply, [json.dumps({"1": "no_evidence"})])
        votes = build_claim_votes(
            None, _PRESET, _PLAN, runner=run, retriever=_no_evidence_retriever
        )
        results = tally_claims(votes)
        assert results["novel thing"].winner == "no_evidence"
        assert blocking_contradictions(results) == []


class TestSeamBinding:
    def test_registered_backend_is_used_then_unbound(self, monkeypatch):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        assert pv.resolve_verify_fn() is pv.verify_plan_stub
        pv.register_verify_backend(lambda plan, sid="": {"c": ["contradicted"] * 3})
        try:
            d = pv.evaluate_plan("x", "s", mode=pv.GATE_BLOCKING)
            assert not d.allowed and d.blocked == ["c"]
        finally:
            pv.register_verify_backend(None)
        assert pv.resolve_verify_fn() is pv.verify_plan_stub

    def test_make_verify_fn_matches_seam_signature(self):
        fn = make_verify_fn(None, {"aggregator": {}, "reference_models": []})
        assert fn(_PLAN, "session") == {}


def test_make_verify_fn_exposes_last_evidence():
    """4.8: the bound seam publishes the evidence its verdicts rested on."""
    import agent.plan_cove as pc

    claim_reply = json.dumps(["c1"])
    run = _runner(claim_reply, [json.dumps({"1": "supported"})])

    def retriever(agent, query, **kwargs):
        return [{"id": "note:x", "snippet": f"about {query}"}]

    orig = pc.build_claim_votes
    pc.build_claim_votes = lambda a, p, c, s, **kw: orig(a, p, c, s, runner=run, retriever=retriever, **kw)
    try:
        fn = pc.make_verify_fn(None, _PRESET)
        assert fn(_PLAN, "sid") == {"c1": ["supported", "supported"]}
        assert fn.last_evidence == {"c1": [{"id": "note:x", "text": "about c1"}]}
    finally:
        pc.build_claim_votes = orig
