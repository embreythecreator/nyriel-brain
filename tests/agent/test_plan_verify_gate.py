"""Verify-on-approve gate tests (WO-MOA/2 Phase 4, stub-first).

Covers the three ruled behaviors: only `contradicted` blocks; --force
overrides loudly with the record written before the transition; the env
kill switch darkens the gate auditable-not-silent.
"""

import importlib
import json

import pytest

from agent.execution_policy import ExecutionPolicyStore, PlanModeState
from agent.plan_mode import handle_plan_command
from nyriel_state import SessionDB

SID = "verify-gate-session"


@pytest.fixture
def pv():
    """The LIVE agent.plan_verify module, resolved per test.

    tests/agent/test_empty_tool_name_loop_dampening.py deletes every
    ``agent.*`` / ``tools.*`` / ``nyriel_*`` entry from sys.modules to
    force a fresh import. Anything this file bound at import time would
    afterwards be a dead module object, so monkeypatching it would silently
    miss the instance ``plan_mode`` actually calls. Resolve at test time.
    """
    return importlib.import_module("agent.plan_verify")


@pytest.fixture(autouse=True)
def _unbound_verify_backend(pv):
    """Isolate the module-global verify backend.

    ``build_moa_facade`` registers an agent-bound backend as a module global,
    so any earlier test in the session that builds a MoA facade would
    otherwise shadow the stub these tests patch. Save/restore rather than
    just clearing, so this file cannot leak either.
    """
    previous = pv._ACTIVE_VERIFY_FN
    pv.register_verify_backend(None)
    try:
        yield
    finally:
        pv.register_verify_backend(previous)


@pytest.fixture
def db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


@pytest.fixture
def store(db):
    return ExecutionPolicyStore(db)


def _saved_plan(monkeypatch, db, store, tmp_path) -> str:
    """Enter planning and save a plan; returns the plan short id."""
    import agent.execution_policy as ep
    import agent.runtime_cwd as runtime_cwd
    import tools.save_plan_tool as spt

    store.enter_planning(SID, "seed task")
    monkeypatch.setattr(runtime_cwd, "resolve_agent_cwd", lambda: tmp_path)
    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    result = json.loads(spt.save_plan_tool("Gate Test", "# body\n", SID))
    assert "error" not in result
    return store.load(SID).short_id


_CONTRADICTED = {
    "the sky is green": ["contradicted", "contradicted", "supported"],
    "new feature has no precedent": ["no_evidence", "no_evidence", "no_evidence"],
}


class TestModule:
    def test_gate_defaults_off(self, pv, monkeypatch):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        assert pv.gate_mode({}) == pv.GATE_OFF
        assert pv.gate_mode({"plan": {"verify_gate": "junk"}}) == pv.GATE_OFF

    def test_gate_modes_resolve(self, pv, monkeypatch):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        assert pv.gate_mode({"plan": {"verify_gate": "shadow"}}) == pv.GATE_SHADOW
        assert pv.gate_mode({"plan": {"verify_gate": "BLOCKING"}}) == pv.GATE_BLOCKING

    def test_kill_switch_darkens_with_reason(self, pv, monkeypatch):
        monkeypatch.setenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, "1")
        mode, reason = pv.gate_status({"plan": {"verify_gate": "blocking"}})
        assert mode == pv.GATE_OFF and reason == "killswitch"
        # Switch on but gate not configured: no skip reason (nothing skipped).
        mode, reason = pv.gate_status({})
        assert mode == pv.GATE_OFF and reason is None

    def test_only_contradicted_blocks(self, pv):
        d = pv.evaluate_plan(
            "x", SID, mode=pv.GATE_BLOCKING, verify_fn=lambda c, s: _CONTRADICTED
        )
        assert not d.allowed
        assert d.blocked == ["the sky is green"]
        # no_evidence majority flags in the table but never blocks.
        assert d.claims["new feature has no precedent"]["winner"] == "no_evidence"

    def test_no_evidence_alone_never_blocks(self, pv):
        d = pv.evaluate_plan(
            "x",
            SID,
            mode=pv.GATE_BLOCKING,
            verify_fn=lambda c, s: {"novel": ["no_evidence", "no_evidence"]},
        )
        assert d.allowed and d.blocked == []

    def test_shadow_never_blocks(self, pv):
        d = pv.evaluate_plan(
            "x", SID, mode=pv.GATE_SHADOW, verify_fn=lambda c, s: _CONTRADICTED
        )
        assert d.allowed and d.blocked == ["the sky is green"]

    def test_force_allows_and_marks(self, pv):
        d = pv.evaluate_plan(
            "x",
            SID,
            mode=pv.GATE_BLOCKING,
            force=True,
            verify_fn=lambda c, s: _CONTRADICTED,
        )
        assert d.allowed and d.forced

    def test_pins_minted_from_seam_evidence_and_stale_blocks(self, pv):
        """4.8: evidence exposed by the seam becomes pins; stale pins count as
        a contradicted claim (D-9); --force keeps the full table."""
        def fn(c, s):
            return {"gateway speaks HTTP/2": ["supported", "supported"]}
        fn.last_evidence = {"gateway speaks HTTP/2": [{"id": "note:a", "text": "t"}, {"id": "?", "text": "x"}]}
        d = pv.evaluate_plan("x", SID, mode=pv.GATE_BLOCKING, verify_fn=fn)
        assert d.allowed and [p["id"] for p in d.pins] == ["note:a"] and d.pins[0]["claim"] == "gateway speaks HTTP/2"
        stale = pv.evaluate_plan("x", SID, mode=pv.GATE_BLOCKING, verify_fn=fn, stale_pins=True)
        assert not stale.allowed and pv.STALE_PINS_CLAIM in stale.blocked and stale.stale
        forced = pv.evaluate_plan("x", SID, mode=pv.GATE_BLOCKING, verify_fn=fn, stale_pins=True, force=True)
        assert forced.allowed and forced.forced and pv.STALE_PINS_CLAIM in forced.claims
        plain = pv.evaluate_plan("x", SID, mode=pv.GATE_BLOCKING, verify_fn=lambda c, s: {"k": ["supported"]})
        assert plain.pins == () and not plain.stale

    def test_verdict_record_appends_jsonl(self, pv, tmp_path):
        plan = tmp_path / "p.md"
        plan.write_text("x")
        ledger = pv.write_verdict_record(str(plan), {"event": "verify", "n": 1})
        pv.write_verdict_record(str(plan), {"event": "verify", "n": 2})
        lines = [json.loads(l) for l in open(ledger, encoding="utf-8")]
        assert [r["n"] for r in lines] == [1, 2]
        assert all("ts" in r for r in lines)


class TestApproveWiring:
    def test_default_config_round_trip_unchanged(
        self, pv, monkeypatch, db, store, tmp_path
    ):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        monkeypatch.setattr(pv, "_configured_mode", lambda config=None: pv.GATE_OFF)
        short_id = _saved_plan(monkeypatch, db, store, tmp_path)
        res = handle_plan_command(f"approve {short_id}", SID, session_db=db)
        assert res.rewritten_message is not None and "APPROVED" in res.rewritten_message

    def test_blocking_contradiction_refuses_and_persists(
        self, pv, monkeypatch, db, store, tmp_path
    ):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        monkeypatch.setattr(
            pv, "_configured_mode", lambda config=None: pv.GATE_BLOCKING
        )
        monkeypatch.setattr(pv, "verify_plan_stub", lambda c, s: _CONTRADICTED)
        short_id = _saved_plan(monkeypatch, db, store, tmp_path)
        res = handle_plan_command(f"approve {short_id}", SID, session_db=db)
        assert res.reply is not None and "NOT approved" in res.reply
        assert "the sky is green" in res.reply
        assert "--force" in res.reply
        # State is still READY — the transition was refused.
        assert store.load(SID).state is PlanModeState.READY
        # The refusal itself is on the ledger.
        ledger = store.load(SID).plan_path + ".verdict.jsonl"
        records = [json.loads(l) for l in open(ledger, encoding="utf-8")]
        assert records[-1]["blocked"] == ["the sky is green"]
        assert records[-1]["force_approved"] is False

    def test_force_approves_and_records_override_first(
        self, pv, monkeypatch, db, store, tmp_path
    ):
        monkeypatch.delenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, raising=False)
        monkeypatch.setattr(
            pv, "_configured_mode", lambda config=None: pv.GATE_BLOCKING
        )
        monkeypatch.setattr(pv, "verify_plan_stub", lambda c, s: _CONTRADICTED)
        short_id = _saved_plan(monkeypatch, db, store, tmp_path)
        res = handle_plan_command(f"approve {short_id} --force", SID, session_db=db)
        assert res.rewritten_message is not None and "APPROVED" in res.rewritten_message
        # plan_path is cleared from live policy after approve; recover ledger
        # from the plans dir.
        plans = list((tmp_path / ".nyriel" / "plans").glob("*.md.verdict.jsonl"))
        assert len(plans) == 1
        records = [json.loads(l) for l in open(plans[0], encoding="utf-8")]
        assert records[-1]["force_approved"] is True
        assert records[-1]["claims"]["the sky is green"]["winner"] == "contradicted"

    def test_killswitch_skip_is_recorded_not_silent(
        self, pv, monkeypatch, db, store, tmp_path
    ):
        monkeypatch.setenv(pv.VERIFY_GATE_KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(
            pv, "_configured_mode", lambda config=None: pv.GATE_BLOCKING
        )
        short_id = _saved_plan(monkeypatch, db, store, tmp_path)
        res = handle_plan_command(f"approve {short_id}", SID, session_db=db)
        assert res.rewritten_message is not None  # approve proceeded
        plans = list((tmp_path / ".nyriel" / "plans").glob("*.md.verdict.jsonl"))
        assert len(plans) == 1
        records = [json.loads(l) for l in open(plans[0], encoding="utf-8")]
        assert records[-1]["event"] == "verify_skipped"
        assert records[-1]["reason"] == "killswitch"
