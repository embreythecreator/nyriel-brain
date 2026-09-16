"""WO-SESSION/YIELD-1: wake-class turns yield the session to operator waiters.

Seam tests over the turn prologue in ``run_agent.AIAgent.run_conversation``:
lease classification, the yield poll in the refresher thread, the soft
ceiling, and the ``yielded_to_operator`` result stamp gateway/wake.py keys on.
"""

from __future__ import annotations

import time

from run_agent import AIAgent, SESSION_TURN_YIELD_NOTICE


class _DB:
    def __init__(self, *, yield_requested=False):
        self.events = []
        self.acquire_kwargs = None
        self.yield_requested = yield_requested

    def get_session(self, session_id):
        return {"id": session_id}

    def acquire_session_turn_lease(self, session_id, holder, **kwargs):
        self.events.append(("acquire", session_id, holder))
        self.acquire_kwargs = kwargs
        return True

    def resolve_resume_session_id(self, session_id):
        return session_id

    def get_messages_as_conversation(self, session_id, **kwargs):
        return [{"role": "user", "content": "durable latest"}]

    def refresh_session_turn_lease(self, session_id, holder, **kwargs):
        return True

    def session_turn_yield_requested(self, session_id, holder):
        self.events.append(("yield_poll", session_id, holder))
        return self.yield_requested

    def release_session_turn_lease(self, session_id, holder):
        self.events.append(("release", session_id, holder))


def _agent_with_db(db, *, turn_kind=None, session_id="sess"):
    agent = AIAgent.__new__(AIAgent)
    agent.session_id = session_id
    agent.platform = "api_server"
    agent.model = "test-model"
    agent._session_db = db
    agent._session_db_created = True
    agent._persist_disabled = False
    agent._parent_session_id = None
    agent._relay_pending_turn_id = None
    agent._reset_activity_labels_after_turn = lambda: None
    agent._conversation_root_id = lambda: session_id
    agent.log_prefix = ""
    agent._vprint = lambda *a, **k: None
    agent.status_callback = None
    agent._interrupt_requested = False
    agent._interrupt_message = None
    agent._pending_redirect = None
    agent._execution_thread_id = None
    agent._interrupt_thread_signal_pending = False
    # Shrink the refresher cadence so the yield poll fires within the test.
    agent._session_turn_lease_refresh_interval = 0.05
    if turn_kind is not None:
        agent._session_turn_kind = turn_kind
    # Record soft/hard interrupts instead of running the real machinery.
    agent._interrupt_calls = []

    def _fake_interrupt(message=None, *, hard_cancel=False):
        agent._interrupt_calls.append((message, hard_cancel))
        agent._interrupt_requested = True
        agent._interrupt_message = message

    agent.interrupt = _fake_interrupt
    return agent


def _run(agent, monkeypatch, *, wait_for_interrupt=False):
    def fake_run(_agent, _message, _system, history, *_args, **_kwargs):
        if wait_for_interrupt:
            deadline = time.monotonic() + 3.0
            while not _agent._interrupt_requested and time.monotonic() < deadline:
                time.sleep(0.01)
            return {
                "final_response": "partial",
                "messages": history,
                "interrupted": True,
            }
        return {"final_response": "ok", "messages": history, "failed": False}

    monkeypatch.setattr("agent.conversation_loop.run_conversation", fake_run)
    return AIAgent.run_conversation(
        agent,
        "message",
        conversation_history=[{"role": "user", "content": "seed"}],
    )


def test_operator_turn_acquires_as_operator_and_requests_yield(monkeypatch):
    db = _DB()
    agent = _agent_with_db(db)  # no _session_turn_kind stamp → operator
    result = _run(agent, monkeypatch)
    assert result["final_response"] == "ok"
    assert db.acquire_kwargs["kind"] == "operator"
    assert db.acquire_kwargs["request_yield_on_wait"] is True
    assert "yielded_to_operator" not in result


def test_wake_turn_acquires_as_wake_and_never_requests_yield(monkeypatch):
    db = _DB()
    agent = _agent_with_db(db, turn_kind="wake")
    result = _run(agent, monkeypatch)
    assert db.acquire_kwargs["kind"] == "wake"
    assert db.acquire_kwargs["request_yield_on_wait"] is False
    assert "yielded_to_operator" not in result


def test_wake_turn_yields_softly_when_operator_requests(monkeypatch):
    db = _DB(yield_requested=True)
    agent = _agent_with_db(db, turn_kind="wake")
    result = _run(agent, monkeypatch, wait_for_interrupt=True)
    assert result.get("yielded_to_operator") is True
    assert result.get("interrupted") is True
    # Exactly one SOFT interrupt carrying the yield notice.
    softs = [c for c in agent._interrupt_calls if c == (SESSION_TURN_YIELD_NOTICE, False)]
    assert softs, agent._interrupt_calls
    assert not any(hard for _msg, hard in agent._interrupt_calls)
    assert any(event[0] == "yield_poll" for event in db.events)
    assert db.events[-1][0] == "release"


def test_wake_turn_yields_at_soft_ceiling_without_a_request(monkeypatch):
    monkeypatch.setenv("NYRIEL_WAKE_TURN_SOFT_CEILING_S", "0.05")
    db = _DB(yield_requested=False)
    agent = _agent_with_db(db, turn_kind="wake")
    result = _run(agent, monkeypatch, wait_for_interrupt=True)
    assert result.get("yielded_to_operator") is True
    assert (SESSION_TURN_YIELD_NOTICE, False) in agent._interrupt_calls


def test_operator_turn_never_yield_polls(monkeypatch):
    db = _DB(yield_requested=True)
    agent = _agent_with_db(db)  # operator
    result = _run(agent, monkeypatch)
    assert result["final_response"] == "ok"
    assert not any(event[0] == "yield_poll" for event in db.events)
    assert not agent._interrupt_calls
