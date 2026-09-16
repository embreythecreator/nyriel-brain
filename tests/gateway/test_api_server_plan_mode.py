"""Plan mode at the API server layer.

Covers the audit fixes: exact-token /plan interception (no /planning…
capture), the per-session busy-guard, and cross-request persistence — a
fresh request (fresh AIAgent) must still see the PLANNING policy saved by
an earlier request.
"""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    _derive_chat_session_id,
    cors_middleware,
    security_headers_middleware,
)
from agent.execution_policy import ExecutionPolicyStore, PlanModeState


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True, extra={}))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app


@pytest.fixture
def adapter():
    return _make_adapter()


# ── _apply_plan_mode unit surface ────────────────────────────────────

def test_planning_prefix_not_intercepted(adapter):
    """'/planning …' must pass through as a normal message, not enter
    plan mode with a mangled task (exact-token match only)."""
    reply, effective = adapter._apply_plan_mode("/planning session notes", "sess-px")
    assert reply is None
    assert effective == "/planning session notes"
    assert ExecutionPolicyStore().load("sess-px").state is PlanModeState.OFF


def test_posture_field_sets_and_clears_chat_flag(adapter):
    """WO-POSTURE/1 2.3: the client's resting posture is a session flag —
    chat|act only, junk ignored, plan mode outranks it."""
    from agent.execution_policy import ExecutionPosture

    store = ExecutionPolicyStore()
    adapter._apply_posture({"posture": "chat"}, "sess-posture")
    assert store.load("sess-posture").posture is ExecutionPosture.CHAT
    adapter._apply_posture({"posture": "nonsense"}, "sess-posture")
    adapter._apply_posture("not a body", "sess-posture")
    assert store.load("sess-posture").posture is ExecutionPosture.CHAT
    adapter._apply_plan_mode("/plan research it", "sess-posture")
    assert store.load("sess-posture").posture is ExecutionPosture.PLAN
    adapter._apply_posture({"posture": "act"}, "sess-posture")
    assert store.load("sess-posture").posture is ExecutionPosture.PLAN  # plan outranks
    adapter._apply_plan_mode("/plan off", "sess-posture")
    assert store.load("sess-posture").posture is ExecutionPosture.ACT


def test_plan_block_projects_pins_and_staleness(adapter, tmp_path):
    """WO-POSTURE/1 4.9: ids on the plan block, full table on the pins payload,
    stale when the pinned set drifts from its digest."""
    from dataclasses import replace

    from agent.execution_policy import compute_plan_digest
    from agent.plan_pins import evidence_digest, pins_from_evidence

    sid = "sess-pins-proj"
    store = ExecutionPolicyStore()
    store.enter_planning(sid, "research")
    plan = tmp_path / "plan.md"
    plan.write_text("# p\n")
    policy, err = store.record_revision(sid, path=str(plan), digest=compute_plan_digest(str(plan)))
    assert err is None
    assert adapter._plan_block(sid)["evidence_pins"] == [] and adapter._plan_block(sid)["evidence_stale"] is False
    pins = pins_from_evidence([{"arxiv_id": "2401.1", "claim": "c"}, {"vault_id": "note:1", "claim": "d"}])
    store.save(sid, replace(policy, evidence_pins=pins, evidence_digest=evidence_digest(pins)))
    block = adapter._plan_block(sid)
    assert block["evidence_pins"] == ["2401.1", "note:1"] and block["evidence_stale"] is False
    payload = adapter._plan_pins_payload(sid)
    assert [p["kind"] for p in payload["pins"]] == ["arxiv", "vault"] and payload["evidence_stale"] is False
    store.save(sid, replace(store.load(sid), evidence_digest="drifted"))
    assert adapter._plan_block(sid)["evidence_stale"] is True
    assert adapter._plan_pins_payload(sid)["evidence_stale"] is True


def test_busy_guard_rejects_plan_mutations(adapter):
    adapter._inflight_session_counts["sess-busy"] = 1
    reply, effective = adapter._apply_plan_mode("/plan do the thing", "sess-busy")
    assert reply is not None and "Agent is running" in reply
    assert effective == "/plan do the thing"
    assert ExecutionPolicyStore().load("sess-busy").state is PlanModeState.OFF

    # /plan status stays available mid-run.
    reply, _ = adapter._apply_plan_mode("/plan status", "sess-busy")
    assert reply is not None and "Plan mode" in reply


def test_session_busy_sees_streaming_runs(adapter):
    fake_agent = MagicMock()
    fake_agent.session_id = "sess-stream"
    adapter._active_run_agents["run_x"] = fake_agent
    assert adapter._session_busy("sess-stream") is True
    assert adapter._session_busy("sess-other") is False


# ── Cross-request enforcement at the aiohttp layer ───────────────────

@pytest.mark.asyncio
async def test_plan_state_persists_across_requests(adapter):
    """Request 1: /plan <task> enters PLANNING and fires the rewritten
    invocation. Request 2 (fresh agent) must still be planning: the user
    message arrives reminder-prefixed and the payload carries the plan
    block."""
    # Unique task text per run: the session id is derived from the first
    # user message, so a fixed string would collide with policy state left
    # by any earlier run against the same store.
    import uuid
    first_msg = f"/plan add a hello command {uuid.uuid4().hex[:8]}"
    sid = _derive_chat_session_id(None, first_msg)
    seen_messages = []

    def _fake_create_agent(**kwargs):
        agent = MagicMock()
        agent.session_id = kwargs.get("session_id") or sid
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        def _run(user_message, conversation_history, task_id):
            seen_messages.append(user_message)
            return {"final_response": "ok"}

        agent.run_conversation.side_effect = _run
        return agent

    app = _create_app(adapter)
    async with TestClient(TestServer(app)) as client:
        with patch.object(adapter, "_create_agent", side_effect=_fake_create_agent):
            r1 = await client.post("/v1/chat/completions", json={
                "model": "nyriel",
                "messages": [{"role": "user", "content": first_msg}],
            })
            assert r1.status == 200
            body1 = await r1.json()

            r2 = await client.post("/v1/chat/completions", json={
                "model": "nyriel",
                "messages": [
                    {"role": "user", "content": first_msg},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "also add tests"},
                ],
            })
            assert r2.status == 200
            body2 = await r2.json()

    # Request 1 fired the plan-mode invocation as the agent message.
    assert len(seen_messages) == 2
    assert "[PLAN MODE ON]" in seen_messages[0]
    # Request 2 built a FRESH agent yet still saw PLANNING: reminder prefix.
    assert seen_messages[1].startswith("[Plan mode active")
    assert "also add tests" in seen_messages[1]
    # The persisted policy row is the enforcement source for the executor.
    assert ExecutionPolicyStore().load(sid).state is PlanModeState.PLANNING
    # Both payloads surface the plan block to clients.
    assert body1.get("plan", {}).get("state") == "planning"
    assert body2.get("plan", {}).get("state") == "planning"
