"""convene_debate tool tests (WO-MOA/2 checklist 14 + Amendment A1)."""

import importlib
import json

import pytest

from agent.execution_policy import (
    PLAN_ALLOWED_TOOLS,
    ExecutionPolicyStore,
)
from nyriel_state import SessionDB

SID = "debate-tool-session"


@pytest.fixture
def cdt():
    """The LIVE tools.convene_debate_tool module, resolved per test.

    tests/agent/test_empty_tool_name_loop_dampening.py evicts every
    ``agent.*`` / ``tools.*`` / ``nyriel_*`` module from sys.modules, so
    anything bound at import time here could be a dead object by the time
    these tests run — and patching it would silently miss the instance the
    tool executor actually calls.
    """
    return importlib.import_module("tools.convene_debate_tool")


@pytest.fixture(autouse=True)
def _unbound_debate_backend(cdt):
    """Isolate the module-global debate backend (see build_moa_facade)."""
    previous = cdt._ACTIVE_DEBATE_FN
    cdt.register_debate_backend(None)
    try:
        yield
    finally:
        cdt.register_debate_backend(previous)


@pytest.fixture
def db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


@pytest.fixture
def store(db):
    return ExecutionPolicyStore(db)


def _saved_plan(monkeypatch, store, tmp_path) -> str:
    import agent.execution_policy as ep
    import agent.runtime_cwd as runtime_cwd
    import tools.save_plan_tool as spt

    store.enter_planning(SID, "seed task")
    monkeypatch.setattr(runtime_cwd, "resolve_agent_cwd", lambda: tmp_path)
    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    result = json.loads(spt.save_plan_tool("Debate Me", "# pool\n", SID))
    assert "error" not in result
    return result["short_id"]


def test_convene_debate_is_plan_allowed():
    assert "convene_debate" in PLAN_ALLOWED_TOOLS


def test_inline_prose_rejected_structurally(cdt, monkeypatch, store, tmp_path):
    out = json.loads(
        cdt.convene_debate_tool("debate whether we should use Postgres or not", SID)
    )
    assert "error" in out and "saved artifact reference" in out["error"]
    out = json.loads(cdt.convene_debate_tool("", SID))
    assert "error" in out


def test_unknown_ref_rejected(cdt, monkeypatch, store, tmp_path):
    import agent.execution_policy as ep

    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    _saved_plan(monkeypatch, store, tmp_path)
    out = json.loads(cdt.convene_debate_tool("deadbeef99", SID))
    assert "error" in out and "does not match" in out["error"]


def test_valid_ref_degrades_with_recorded_skip(cdt, monkeypatch, store, tmp_path):
    import agent.execution_policy as ep

    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    short_id = _saved_plan(monkeypatch, store, tmp_path)
    out = json.loads(cdt.convene_debate_tool(short_id, SID))
    assert out["debate"] == "skipped"
    assert out["reason"] == "not_implemented"
    ledger = store.load(SID).plan_path + ".verdict.jsonl"
    records = [json.loads(l) for l in open(ledger, encoding="utf-8")]
    assert records[-1]["event"] == "debate_skipped"
    assert records[-1]["artifact_ref"] == short_id
    assert records[-1]["required_in_plan"] is False


def test_disabled_flag_skips_with_disabled_reason(cdt, monkeypatch, store, tmp_path):
    import agent.execution_policy as ep
    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    monkeypatch.setattr(cdt, "_debate_flags", lambda: (False, True))
    short_id = _saved_plan(monkeypatch, store, tmp_path)
    out = json.loads(cdt.convene_debate_tool(short_id, SID))
    assert out["debate"] == "skipped" and out["reason"] == "disabled"
    ledger = store.load(SID).plan_path + ".verdict.jsonl"
    records = [json.loads(l) for l in open(ledger, encoding="utf-8")]
    assert records[-1]["reason"] == "disabled"
    assert records[-1]["required_in_plan"] is True


def test_bound_backend_rules_and_ledgers_transcript(cdt, monkeypatch, store, tmp_path):
    import agent.execution_policy as ep
    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    short_id = _saved_plan(monkeypatch, store, tmp_path)
    cdt.register_debate_backend(
        lambda ref, text: {
            "mode": "debate",
            "rounds": 2,
            "exit_reason": "rounds_exhausted",
            "stances": {"A": "concede"},
            "unresolved": {"B": "unanswered attack"},
            "retrieval_ref": "wordpack:abc",
            "ruling": "Ship it, but fix the cache key.",
            "transcript": "ROUND 1: ...",
        }
    )
    try:
        out = json.loads(cdt.convene_debate_tool(short_id, SID))
    finally:
        cdt.register_debate_backend(None)

    assert out["debate"] == "ruled"
    assert out["ruling"] == "Ship it, but fix the cache key."
    assert out["unresolved"] == ["B"]
    # Transcript is ledger/Forge material, not model context.
    assert "transcript" not in out
    records = [
        json.loads(l)
        for l in open(store.load(SID).plan_path + ".verdict.jsonl", encoding="utf-8")
    ]
    assert records[-1]["event"] == "debate"
    assert records[-1]["transcript"] == "ROUND 1: ..."
    assert records[-1]["retrieval_ref"] == "wordpack:abc"


def test_backend_failure_degrades_to_recorded_skip(cdt, monkeypatch, store, tmp_path):
    import agent.execution_policy as ep
    monkeypatch.setattr(ep, "ExecutionPolicyStore", lambda db_arg=None: store)
    short_id = _saved_plan(monkeypatch, store, tmp_path)

    def _boom(ref, text):
        raise RuntimeError("provider exploded")

    cdt.register_debate_backend(_boom)
    try:
        out = json.loads(cdt.convene_debate_tool(short_id, SID))
    finally:
        cdt.register_debate_backend(None)

    assert out["debate"] == "skipped" and out["reason"] == "fanout_failed"
    records = [
        json.loads(l)
        for l in open(store.load(SID).plan_path + ".verdict.jsonl", encoding="utf-8")
    ]
    assert records[-1]["event"] == "debate_skipped"


def test_plan_contract_mandates_convene_debate(db):
    from agent.plan_mode import handle_plan_command

    res = handle_plan_command("build a widget", SID, session_db=db)
    assert res.rewritten_message is not None
    assert "convene_debate" in res.rewritten_message
    assert "MANDATORY" in res.rewritten_message
