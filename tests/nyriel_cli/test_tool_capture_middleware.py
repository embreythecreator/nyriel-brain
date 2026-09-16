"""WO-POSTURE/1 1.5 — evidence-tool results are captured into Word at the seam.

No plugin import, no HTTP: a fake provider is registered the way the real
plugin registers itself; the frame is exercised through the public
run_tool_execution_middleware entry point with zero plugin middleware.
"""

import json
from types import SimpleNamespace

import pytest

from nyriel_cli import middleware


class _Provider:
    name = "word"

    def __init__(self, result="note:1", raises=False):
        self.result, self.raises, self.writes = result, raises, []

    def is_available(self):
        return True

    def capture_note(self, title, content, *, metadata=None):
        self.writes.append((title, content, metadata))
        if self.raises:
            raise RuntimeError("word down")
        return self.result


def _agent(provider):
    manager = SimpleNamespace(get_provider=lambda name: provider if name == "word" else None)
    return SimpleNamespace(_memory_manager=manager, session_id="sess-1")


@pytest.fixture(autouse=True)
def _no_plugin_middleware(monkeypatch):
    monkeypatch.setattr(middleware, "_get_middleware_callbacks", lambda kind: [])


BIG = "x" * 300
EXTRACT = json.dumps({"results": [{"url": "https://arxiv.org/abs/1", "title": "Paper", "content": BIG}]})


def test_web_extract_result_is_captured_and_carries_vault_id():
    p = _Provider()
    out = middleware.run_tool_execution_middleware(
        "web_extract", {"urls": ["https://arxiv.org/abs/1"]}, lambda a: EXTRACT, capture_agent=_agent(p)
    )
    assert json.loads(out)["vault_id"] == "note:1"
    title, content, meta = p.writes[0]
    assert title == "Paper" and content == EXTRACT
    assert meta["origin"] == "https://arxiv.org/abs/1" and meta["session_id"] == "sess-1"
    assert meta["node_type"] == "source" and meta["tool"] == "web_extract"
    # web origin is untrusted → quarantined even in ACT posture
    assert meta["posture"] == "act" and meta["trust_tier"] == "T3" and meta["quarantine"] is True


def test_plain_text_read_file_gets_trailing_vault_line():
    p = _Provider()
    out = middleware.run_tool_execution_middleware(
        "read_file", {"path": "/repo/a.py"}, lambda a: BIG, capture_agent=_agent(p)
    )
    assert out == f"{BIG}\n\n[word vault_id=note:1]"
    assert p.writes[0][0] == "read_file: /repo/a.py"


@pytest.mark.parametrize("result", [
    json.dumps({"success": False, "error": "boom", "pad": BIG}),
    "short",
])
def test_failed_or_tiny_results_are_not_captured(result):
    p = _Provider()
    out = middleware.run_tool_execution_middleware(
        "web_extract", {}, lambda a: result, capture_agent=_agent(p)
    )
    assert out == result and p.writes == []


def test_non_allowlisted_tool_and_missing_agent_pass_through():
    p = _Provider()
    assert middleware.run_tool_execution_middleware("write_file", {}, lambda a: BIG, capture_agent=_agent(p)) == BIG
    assert middleware.run_tool_execution_middleware("read_file", {}, lambda a: BIG) == BIG
    assert p.writes == []


def test_capture_failure_or_queued_write_never_alters_tool_result():
    raising = _Provider(raises=True)
    assert middleware.run_tool_execution_middleware("read_file", {}, lambda a: BIG, capture_agent=_agent(raising)) == BIG
    queued = _Provider(result=None)
    assert middleware.run_tool_execution_middleware("read_file", {}, lambda a: BIG, capture_agent=_agent(queued)) == BIG
    assert len(queued.writes) == 1


def test_capture_agent_never_reaches_plugin_callbacks(monkeypatch):
    seen = {}

    def cb(**kw):
        seen.update(kw)
        return kw["next_call"](kw["args"]) if "next_call" in kw else None

    monkeypatch.setattr(middleware, "_get_middleware_callbacks", lambda kind: [cb])
    middleware.run_tool_execution_middleware("read_file", {"path": "p"}, lambda a: BIG, capture_agent=_agent(_Provider()))
    assert "capture_agent" not in seen


def test_oversized_result_is_elided_to_handle_plus_head():
    p = _Provider()
    huge = "y" * (middleware.ELIDE_CHARS + 1)
    out = json.loads(middleware.run_tool_execution_middleware(
        "read_file", {"path": "/repo/big.log"}, lambda a: huge, capture_agent=_agent(p)
    ))
    assert out["vault_id"] == "note:1" and out["head"] == "y" * 400
    assert out["elided_chars"] == len(huge) - 400 and "word_memory_read" in out["note"]
    assert p.writes[0][1] == huge  # Word got the whole body


def test_oversized_result_stays_intact_when_capture_only_queued():
    huge = "y" * (middleware.ELIDE_CHARS + 1)
    out = middleware.run_tool_execution_middleware(
        "read_file", {"path": "/repo/big.log"}, lambda a: huge, capture_agent=_agent(_Provider(result=None))
    )
    assert out == huge


def test_plan_posture_quarantines_even_local_reads():
    from agent.execution_policy import ExecutionPolicy, PlanModeState

    p = _Provider()
    agent = _agent(p)
    agent._execution_policy = ExecutionPolicy(state=PlanModeState.PLANNING)
    middleware.run_tool_execution_middleware("read_file", {"path": "/repo/a.py"}, lambda a: BIG, capture_agent=agent)
    meta = p.writes[0][2]
    assert meta["posture"] == "plan" and meta["trust_tier"] == "T1" and meta["quarantine"] is True


def test_act_posture_local_read_is_not_quarantined():
    p = _Provider()
    middleware.run_tool_execution_middleware("read_file", {"path": "/repo/a.py"}, lambda a: BIG, capture_agent=_agent(p))
    meta = p.writes[0][2]
    assert meta["posture"] == "act" and meta["quarantine"] is False


def test_save_plan_becomes_artifact_node_citing_pins(tmp_path):
    """4.10: successful save_plan → Word artifact note with cites; word_note_id on the policy."""
    from dataclasses import replace

    from agent.execution_policy import ExecutionPolicy, ExecutionPolicyStore, PlanModeState
    from agent.plan_pins import pins_from_evidence
    from nyriel_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    store = ExecutionPolicyStore(db)
    pins = pins_from_evidence([{"arxiv_id": "2401.1", "claim": "c"}])
    policy = ExecutionPolicy(state=PlanModeState.READY, plan_id="p" * 32, revision=1, evidence_pins=pins)
    store.save("sess-plan", policy)
    p = _Provider(result="note:plan")
    agent = _agent(p)
    agent._session_db = db
    agent.session_id = "sess-plan"
    agent._execution_policy = policy
    result = json.dumps({"plan_id": "p" * 32, "short_id": "pppppppp", "revision": 1, "path": "/x.md", "digest": "d"})
    out = json.loads(middleware.run_tool_execution_middleware(
        "save_plan", {"title": "Side panel", "content": "# Side panel\n"}, lambda a: result, capture_agent=agent
    ))
    assert out["vault_id"] == "note:plan"
    title, content, meta = p.writes[0]
    assert title.startswith("[plan] Side panel") and content == "# Side panel\n"
    assert meta["node_type"] == "artifact" and meta["cites"] == "2401.1" and meta["quarantine"] is False
    assert store.load("sess-plan").word_note_id == "note:plan"


def test_failed_save_plan_and_word_down_leave_result_alone():
    err = json.dumps({"error": "save_plan requires a non-empty title."})
    assert middleware.run_tool_execution_middleware("save_plan", {}, lambda a: err, capture_agent=_agent(_Provider())) == err
    ok = json.dumps({"plan_id": "p" * 32, "short_id": "pppppppp", "revision": 1})
    assert middleware.run_tool_execution_middleware("save_plan", {"title": "t", "content": "c"}, lambda a: ok, capture_agent=_agent(_Provider(result=None))) == ok
