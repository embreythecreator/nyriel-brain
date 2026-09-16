"""Focused tests for API server session-control endpoints."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from nyriel_state import SessionDB


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    return adapter


@pytest.fixture
def auth_adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "sk-test"}))
    adapter._session_db = session_db
    return adapter


def _create_session_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get("/v1/capabilities", adapter._handle_capabilities)
    app.router.add_get("/api/sessions", adapter._handle_list_sessions)
    app.router.add_post("/api/sessions", adapter._handle_create_session)
    app.router.add_get(
        "/api/sessions/{session_id}/plan-artifact",
        adapter._handle_get_plan_artifact,
    )
    app.router.add_put(
        "/api/sessions/{session_id}/plan-artifact",
        adapter._handle_put_plan_artifact,
    )
    app.router.add_get("/api/sessions/{session_id}", adapter._handle_get_session)
    app.router.add_patch("/api/sessions/{session_id}", adapter._handle_patch_session)
    app.router.add_delete("/api/sessions/{session_id}", adapter._handle_delete_session)
    app.router.add_get("/api/sessions/{session_id}/messages", adapter._handle_session_messages)
    app.router.add_post("/api/sessions/{session_id}/fork", adapter._handle_fork_session)
    app.router.add_post("/api/sessions/{session_id}/chat", adapter._handle_session_chat)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", adapter._handle_session_chat_stream)
    return app


@pytest.mark.asyncio
async def test_plan_artifact_read_and_revision_safe_save(adapter, session_db, tmp_path):
    from agent.execution_policy import ExecutionPolicyStore, compute_plan_digest

    session_id = session_db.create_session("artifact-session", "api_server")
    plan_path = tmp_path / ".nyriel" / "plans" / "2026-07-23-side-panel.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("# Side Panel\n\n- [ ] Build it\n")
    policy_store = ExecutionPolicyStore(session_db)
    policy_store.enter_planning(session_id, "Build the artifact viewer")
    ready, err = policy_store.record_revision(
        session_id,
        path=str(plan_path),
        digest=compute_plan_digest(str(plan_path)),
    )
    assert err is None
    adapter._execution_policy_store = lambda: policy_store

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        get_resp = await cli.get(f"/api/sessions/{session_id}/plan-artifact")
        assert get_resp.status == 200
        initial = await get_resp.json()
        assert initial["plan"]["short_id"] == ready.short_id
        assert initial["artifact"] == {
            "id": ready.plan_id,
            "type": "plan",
            "title": "Side Panel",
            "content": "# Side Panel\n\n- [ ] Build it\n",
            "path": str(plan_path),
            "revision": 1,
            "short_id": ready.short_id,
        }

        put_resp = await cli.put(
            f"/api/sessions/{session_id}/plan-artifact",
            json={
                "content": "# Side Panel\n\n- [x] Build it\n",
                "expected_revision": 1,
            },
        )
        assert put_resp.status == 200
        saved = await put_resp.json()
        assert saved["artifact"]["revision"] == 2
        assert saved["artifact"]["content"].endswith("- [x] Build it\n")
        assert saved["plan"]["revision"] == 2

        stale_resp = await cli.put(
            f"/api/sessions/{session_id}/plan-artifact",
            json={"content": "# Stale overwrite\n", "expected_revision": 1},
        )
        assert stale_resp.status == 409

    current = policy_store.load(session_id)
    assert current.revision == 2
    assert current.digest == compute_plan_digest(str(plan_path))
    assert plan_path.read_text() == "# Side Panel\n\n- [x] Build it\n"


@pytest.mark.asyncio
async def test_plan_artifact_endpoint_rejects_non_ready_or_missing_session(adapter, session_db):
    session_id = session_db.create_session("planning-session", "api_server")
    from agent.execution_policy import ExecutionPolicyStore

    policy_store = ExecutionPolicyStore(session_db)
    policy_store.enter_planning(session_id, "Still planning")
    adapter._execution_policy_store = lambda: policy_store

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        planning = await cli.get(f"/api/sessions/{session_id}/plan-artifact")
        assert planning.status == 200
        payload = await planning.json()
        assert payload["plan"]["state"] == "planning"
        assert payload["artifact"] is None

        missing = await cli.get("/api/sessions/not-there/plan-artifact")
        assert missing.status == 404


@pytest.mark.asyncio
async def test_plan_artifact_endpoints_require_auth(auth_adapter, session_db):
    session_id = session_db.create_session("private-plan", "api_server")
    app = _create_session_app(auth_adapter)
    async with TestClient(TestServer(app)) as cli:
        read_resp = await cli.get(f"/api/sessions/{session_id}/plan-artifact")
        write_resp = await cli.put(
            f"/api/sessions/{session_id}/plan-artifact",
            json={"content": "# Nope\n", "expected_revision": 1},
        )
    assert read_resp.status == 401
    assert write_resp.status == 401


@pytest.mark.asyncio
async def test_capabilities_advertises_session_control_surface(adapter):
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/v1/capabilities")
        assert resp.status == 200
        data = await resp.json()

    features = data["features"]
    assert features["session_resources"] is True
    assert features["session_chat"] is True
    assert features["session_chat_streaming"] is True
    assert features["session_fork"] is True
    assert features["session_plan_artifact"] is True
    assert features["run_steer"] is True

    assert features["admin_config_rw"] is False
    assert features["memory_write_api"] is False
    assert features["skills_api"] is True
    assert features["realtime_voice"] is False
    assert data["endpoints"]["sessions"] == {"method": "GET", "path": "/api/sessions"}
    assert data["endpoints"]["session_chat_stream"] == {
        "method": "POST",
        "path": "/api/sessions/{session_id}/chat/stream",
    }
    assert data["endpoints"]["session_plan_artifact_read"] == {
        "method": "GET",
        "path": "/api/sessions/{session_id}/plan-artifact",
    }
    assert data["endpoints"]["session_plan_artifact_update"] == {
        "method": "PUT",
        "path": "/api/sessions/{session_id}/plan-artifact",
    }
    assert data["endpoints"]["run_steer"] == {
        "method": "POST",
        "path": "/v1/runs/{run_id}/steer",
    }


@pytest.mark.asyncio
async def test_session_messages_default_to_latest_bounded_page(adapter, session_db):
    session_id = session_db.create_session("bounded-messages", "api_server")
    session_db.replace_messages(
        session_id,
        [{"role": "user", "content": f"msg {i}"} for i in range(501)],
    )

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get(f"/api/sessions/{session_id}/messages")
        assert resp.status == 200
        payload = await resp.json()

        explicit_resp = await cli.get(
            f"/api/sessions/{session_id}/messages?limit=2&offset=1"
        )
        assert explicit_resp.status == 200
        explicit = await explicit_resp.json()

    assert payload["pagination"] == {
        "limit": 500,
        "offset": 0,
        "order": "latest",
        "returned": 500,
    }
    assert payload["data"][0]["content"] == "msg 1"
    assert payload["data"][-1]["content"] == "msg 500"
    assert [message["content"] for message in explicit["data"]] == [
        "msg 1",
        "msg 2",
    ]



@pytest.mark.asyncio
async def test_run_agent_binds_api_session_context_for_tool_env(adapter, monkeypatch):
    """API-server request sessions should reach tools and terminal subprocess env."""
    monkeypatch.setenv("NYRIEL_SESSION_ID", "stale-session")
    observed = {}

    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self, session_id: str):
            self.session_id = session_id

        def run_conversation(self, user_message, conversation_history, task_id):
            from gateway.session_context import get_session_env
            from tools.environments.local import _make_run_env

            observed["task_id"] = task_id
            observed["context_session_id"] = get_session_env("NYRIEL_SESSION_ID")
            observed["context_platform"] = get_session_env("NYRIEL_SESSION_PLATFORM")
            observed["context_session_key"] = get_session_env("NYRIEL_SESSION_KEY")
            observed["child_session_id"] = _make_run_env({}).get("NYRIEL_SESSION_ID")
            return {"final_response": "ok"}

    def fake_create_agent(**kwargs):
        return FakeAgent(kwargs["session_id"])

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    result, usage = await adapter._run_agent(
        user_message="hello",
        conversation_history=[],
        session_id="request-session",
        gateway_session_key="request-key",
    )

    assert result["session_id"] == "request-session"
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["total_tokens"] == 0
    assert "runtime" not in usage
    assert observed == {
        "task_id": "request-session",
        "context_session_id": "request-session",
        "context_platform": "api_server",
        "context_session_key": "request-key",
        "child_session_id": "request-session",
    }


@pytest.mark.asyncio
async def test_run_agent_registers_active_run_id_for_steering(adapter, monkeypatch):
    observed = {}

    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self, session_id: str):
            self.session_id = session_id

        def steer(self, text: str) -> bool:
            observed["steer_text"] = text
            return True

        def run_conversation(self, user_message, conversation_history, task_id):
            observed["registered"] = adapter._active_run_agents.get("run_steer_test") is self
            observed["task_id"] = task_id
            return {"final_response": "ok"}

    def fake_create_agent(**kwargs):
        return FakeAgent(kwargs["session_id"])

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    result, usage = await adapter._run_agent(
        user_message="hello",
        conversation_history=[],
        session_id="request-session",
        active_run_id="run_steer_test",
    )

    assert result["session_id"] == "request-session"
    assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert observed == {"registered": True, "task_id": "request-session"}
    assert "run_steer_test" not in adapter._active_run_agents


@pytest.mark.asyncio
async def test_session_chat_stream_disconnect_keeps_control_refs_until_executor_finishes(
    adapter, session_db
):
    """Disconnects must interrupt the live run without dropping its control refs early."""
    session_id = session_db.create_session("disconnect-stream-session", "api_server")
    run_started = threading.Event()
    interrupt_called = threading.Event()
    allow_finish = threading.Event()
    write_calls = {"count": 0}

    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self, stream_delta_callback):
            self._stream_delta_callback = stream_delta_callback
            self.session_id = session_id

        def interrupt(self, _message=None):
            interrupt_called.set()

        def run_conversation(self, user_message, conversation_history, task_id):
            del user_message, conversation_history, task_id
            run_started.set()
            self._stream_delta_callback("hello")
            allow_finish.wait(timeout=5)
            return {"final_response": "done", "session_id": session_id}

    class DisconnectingStreamResponse:
        async def prepare(self, request):
            del request

        async def write(self, payload):
            del payload
            write_calls["count"] += 1
            if write_calls["count"] >= 3:
                raise ConnectionResetError("simulated client disconnect")

    request = MagicMock()
    request.headers = {}
    request.match_info = {"session_id": session_id}

    def _create_agent(**kwargs):
        return FakeAgent(kwargs["stream_delta_callback"])

    with patch.object(
        adapter,
        "_get_existing_session_or_404",
        return_value=({"id": session_id}, None),
    ), patch.object(
        adapter,
        "_read_json_body",
        return_value=({"message": "stream please"}, None),
    ), patch.object(
        adapter,
        "_create_agent",
        side_effect=_create_agent,
    ), patch(
        "gateway.platforms.api_server.web.StreamResponse",
        return_value=DisconnectingStreamResponse(),
    ):
        handler_task = asyncio.create_task(adapter._handle_session_chat_stream(request))

        for _ in range(60):
            if run_started.is_set():
                break
            await asyncio.sleep(0.05)

        assert run_started.is_set()
        run_id = next(iter(adapter._run_statuses))

        for _ in range(40):
            if interrupt_called.is_set():
                break
            await asyncio.sleep(0.05)

        assert interrupt_called.is_set()
        assert run_id in adapter._active_run_agents
        # Not in _active_run_tasks: session-stream turns are counted via
        # _inflight_agent_runs; a task entry would double-count them in the
        # shutdown drain (active_agent_work_count).
        assert run_id not in adapter._active_run_tasks
        assert not handler_task.done()

        allow_finish.set()
        await handler_task

    assert run_id not in adapter._active_run_agents


@pytest.mark.asyncio
async def test_session_chat_stream_run_completed_carries_turn_transcript(adapter, session_db):
    """run.completed must include the full interleaved turn transcript so a
    client that lost intermediate (pre-tool-call) assistant text from the live
    delta stream can reconcile without a separate /messages fetch. Refs #34703.
    """
    import json as _json

    session_id = session_db.create_session("transcript-session", "api_server")

    async def fake_run(**kwargs):
        # Stream the intermediate planning text the way a real turn would.
        kwargs["stream_delta_callback"]("Let me search for that:")
        kwargs["stream_delta_callback"]("Here is the summary.")
        result = {
            "final_response": "Here is the summary.",
            "session_id": session_id,
            "messages": [
                {"role": "user", "content": "search then summarize"},
                {
                    "role": "assistant",
                    "content": "Let me search for that:",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "content": "results", "tool_call_id": "call_1", "tool_name": "web_search"},
                {"role": "assistant", "content": "Here is the summary."},
            ],
        }
        return result, {"total_tokens": 6}

    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", side_effect=fake_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat/stream",
                json={"message": "search then summarize"},
            )
            assert resp.status == 200
            body = await resp.text()

    # Pull the run.completed event payload out of the SSE body.
    run_completed_payload = None
    for block in body.split("\n\n"):
        if "event: run.completed" in block:
            for line in block.splitlines():
                if line.startswith("data: "):
                    run_completed_payload = _json.loads(line[len("data: "):])
            break
    assert run_completed_payload is not None, body
    messages = run_completed_payload.get("messages")
    assert isinstance(messages, list) and messages, run_completed_payload

    # The colon-ended intermediate text that preceded the tool call must be present.
    contents = [m.get("content") for m in messages]
    assert "Let me search for that:" in contents
    assert "Here is the summary." in contents
    # No prior-turn user message should leak into the per-turn slice.
    assert all(m.get("role") in ("assistant", "tool") for m in messages)
    # The tool call is preserved alongside the intermediate text.
    assert any(m.get("tool_calls") for m in messages)


# ---------------------------------------------------------------------------
# Session-persisted model threading + provider-auth failure surfacing
# (salvaged from PR #57947 by @FvanW and PR #59941 by @kaishi00)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_chat_resolves_stored_model_route_alias(session_db, monkeypatch):
    """A session-persisted model that matches a model_routes alias must go
    through the route path (so route provider/credentials apply) and NOT be
    passed as a raw session_model (idea from PR #59941 by @kaishi00)."""
    adapter = APIServerAdapter(
        PlatformConfig(
            enabled=True,
            extra={"model_routes": {"alias": {"model": "route/model", "provider": "openrouter"}}},
        )
    )
    adapter._session_db = session_db
    session_id = session_db.create_session("route-pinned-session", "api_server", model="alias")

    mock_run = AsyncMock(return_value=({"final_response": "ok", "session_id": session_id}, {"total_tokens": 1}))
    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", mock_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "hi"},
            )
            assert resp.status == 200

    _, kwargs = mock_run.call_args
    assert kwargs["route"] == {"model": "route/model", "provider": "openrouter"}
    assert kwargs["session_model"] is None


@pytest.mark.asyncio
async def test_session_chat_treats_pre_existing_poisoned_row_as_no_model(session_db):
    """A session row created before the alias-leak fix may still have the
    virtual model alias (e.g. "nyriel-brain") persisted literally as its
    model. Reading that back must NOT thread it through as a raw
    session_model override — it must fall through to the global default,
    exactly like a row that never had a model at all (#session-model-
    alias-leak)."""
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    session_id = session_db.create_session(
        "poisoned-session", "api_server", model=adapter._model_name
    )

    mock_run = AsyncMock(return_value=({"final_response": "ok", "session_id": session_id}, {"total_tokens": 1}))
    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", mock_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "hi"},
            )
            assert resp.status == 200

    _, kwargs = mock_run.call_args
    assert kwargs["session_model"] is None


@pytest.mark.asyncio
async def test_session_chat_stream_treats_pre_existing_poisoned_row_as_no_model(session_db):
    """Streaming twin of the above: the SSE chat path must apply the same
    guard against a pre-existing poisoned session row."""
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    session_id = session_db.create_session(
        "poisoned-stream-session", "api_server", model=adapter._model_name
    )

    async def fake_run(**kwargs):
        return {"final_response": "ok", "session_id": session_id}, {"total_tokens": 1}

    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", side_effect=fake_run) as mock_run:
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat/stream",
                json={"message": "hi"},
            )
            assert resp.status == 200
            # Drain the SSE body: the 200 lands before the streaming task
            # invokes _run_agent, so asserting on call_args without reading
            # the body races the handler (flaked on loaded CI runners).
            await resp.text()

    _, kwargs = mock_run.call_args
    assert kwargs["session_model"] is None


def _register_session_model_route(app, adapter):
    app.router.add_post("/api/sessions/{session_id}/model", adapter._handle_session_model_lock)


def _patch_api_server_runtime(monkeypatch):
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_key": "sk-global",
            "base_url": "https://openrouter.example/v1",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "global/model")
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    monkeypatch.setattr(
        "gateway.run.GatewayRunner._load_reasoning_config",
        staticmethod(lambda model="": {}),
    )
    monkeypatch.setattr(
        "gateway.run.GatewayRunner._load_fallback_model",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr("gateway.run._current_max_iterations", lambda: 90)
    monkeypatch.setattr("nyriel_cli.tools_config._get_platform_tools", lambda *_: set())
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs_for_provider",
        lambda provider: {
            "provider": provider,
            "api_key": f"sk-{provider}",
            "base_url": f"https://{provider}.example/v1",
            "api_mode": "chat_completions",
        },
    )


@pytest.mark.asyncio
async def test_create_session_respects_browser_source_and_model_lock(adapter, session_db):
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/api/sessions",
            json={
                "id": "browser-lock-session",
                "source": "nyriel_browser",
                "provider": "nous",
                "model": "x-ai/grok-4.5",
                "require_model_lock": True,
                "title": "Browser lock",
                "system_prompt": "browser prompt",
            },
        )
        assert resp.status == 201, await resp.text()
        payload = await resp.json()

    assert payload["session"]["source"] == "nyriel_browser"
    assert payload["session"]["model"] == "x-ai/grok-4.5"
    row = session_db.get_session("browser-lock-session")
    assert row["source"] == "nyriel_browser"
    assert row["model"] == "x-ai/grok-4.5"
    import json as _json
    model_config = row.get("model_config")
    if isinstance(model_config, str):
        model_config = _json.loads(model_config)
    assert model_config["browser_model_lock"]["provider"] == "nous"
    assert model_config["browser_model_lock"]["model"] == "x-ai/grok-4.5"
    assert model_config["browser_model_lock"]["confirmed"] is True


@pytest.mark.asyncio
async def test_session_model_lock_endpoint_then_chat_reuses_persisted_lock_and_provider_credentials(
    adapter,
    session_db,
    monkeypatch,
):
    session_id = session_db.create_session(
        "endpoint-lock-chat",
        "api_server",
        model="gpt-5.5",
        system_prompt="Conversation started:\nModel: gpt-5.5\nProvider: openai-codex\n",
    )
    captured = {}

    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session_id = kwargs["session_id"]
            self.provider = kwargs.get("provider") or ""
            self.model = kwargs.get("model") or ""

        def run_conversation(self, user_message, conversation_history, task_id):
            return {"final_response": "locked", "session_id": self.session_id}

    _patch_api_server_runtime(monkeypatch)
    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    monkeypatch.setattr(
        adapter,
        "_session_model_override_for",
        lambda *_: {
            "model": "session/override-model",
            "provider": "openai-codex",
            "api_key": "sk-session-override",
            "base_url": "https://override.example/v1",
            "api_mode": "codex_responses",
        },
    )

    app = _create_session_app(adapter)
    _register_session_model_route(app, adapter)
    with patch.object(adapter, "_resolve_route", return_value=None):
        async with TestClient(TestServer(app)) as cli:
            lock_resp = await cli.post(
                f"/api/sessions/{session_id}/model",
                json={
                    "provider": "nous",
                    "model": "x-ai/grok-4.5",
                    "require_model_lock": True,
                },
            )
            assert lock_resp.status == 200, await lock_resp.text()

            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "use the stored lock"},
            )
            assert resp.status == 200, await resp.text()
            payload = await resp.json()

    assert captured["provider"] == "nous"
    assert captured["model"] == "x-ai/grok-4.5"
    assert captured["api_key"] == "sk-nous"
    assert captured["base_url"] == "https://nous.example/v1"
    assert payload["runtime"]["provider"] == "nous"
    assert payload["runtime"]["model"] == "x-ai/grok-4.5"
    assert payload["runtime"]["requested"] == {
        "provider": "nous",
        "model": "x-ai/grok-4.5",
    }
    assert payload["runtime"]["route_source"] == "session_model_lock"


@pytest.mark.asyncio
async def test_session_model_lock_endpoint_then_chat_stream_reuses_persisted_lock(
    adapter,
    session_db,
):
    session_id = session_db.create_session("endpoint-lock-stream", "api_server")
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        kwargs["stream_delta_callback"]("hi")
        return (
            {
                "final_response": "hi",
                "session_id": session_id,
                "runtime": {
                    "provider": "nous",
                    "model": "x-ai/grok-4.5",
                    "requested": {"provider": "nous", "model": "x-ai/grok-4.5"},
                    "route_source": "session_model_lock",
                },
            },
            {
                "total_tokens": 1,
                "runtime": {
                    "provider": "nous",
                    "model": "x-ai/grok-4.5",
                    "requested": {"provider": "nous", "model": "x-ai/grok-4.5"},
                    "route_source": "session_model_lock",
                },
            },
        )

    app = _create_session_app(adapter)
    _register_session_model_route(app, adapter)
    with patch.object(adapter, "_resolve_route", return_value=None), patch.object(
        adapter,
        "_run_agent",
        side_effect=fake_run,
    ):
        async with TestClient(TestServer(app)) as cli:
            lock_resp = await cli.post(
                f"/api/sessions/{session_id}/model",
                json={
                    "provider": "nous",
                    "model": "x-ai/grok-4.5",
                    "require_model_lock": True,
                },
            )
            assert lock_resp.status == 200, await lock_resp.text()

            resp = await cli.post(
                f"/api/sessions/{session_id}/chat/stream",
                json={"message": "stream with stored lock"},
            )
            assert resp.status == 200, await resp.text()
            body = await resp.text()

    assert captured["route"] == {"provider": "nous", "model": "x-ai/grok-4.5"}
    assert captured["requested_runtime"]["provider"] == "nous"
    assert captured["requested_runtime"]["model"] == "x-ai/grok-4.5"
    assert captured["route_source"] == "session_model_lock"
    assert "x-ai/grok-4.5" in body


@pytest.mark.asyncio
async def test_run_agent_reports_actual_agent_runtime_not_requested_metadata(adapter, monkeypatch):
    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self):
            self.session_id = "runtime-session"
            self.provider = "actual-provider"
            self.model = "actual-model"
            self._nyriel_api_runtime = {
                "provider": "requested-provider",
                "model": "requested-model",
                "route_source": "raw_request",
            }

        def run_conversation(self, user_message, conversation_history, task_id):
            return {"final_response": "ok", "session_id": self.session_id}

    monkeypatch.setattr(adapter, "_create_agent", lambda **kwargs: FakeAgent())

    result, usage = await adapter._run_agent(
        user_message="hello",
        conversation_history=[],
        session_id="runtime-session",
        route={"provider": "requested-provider", "model": "requested-model"},
        requested_runtime={
            "provider": "requested-provider",
            "model": "requested-model",
        },
        route_source="session_model_lock",
    )

    assert result["runtime"]["provider"] == "actual-provider"
    assert result["runtime"]["model"] == "actual-model"
    assert result["runtime"]["requested"] == {
        "provider": "requested-provider",
        "model": "requested-model",
    }
    assert usage["runtime"]["provider"] == "actual-provider"
    assert usage["runtime"]["model"] == "actual-model"


@pytest.mark.asyncio
async def test_confirmed_runtime_lock_rejects_actual_runtime_mismatch(adapter, monkeypatch):
    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0
        session_id = "mismatch-session"
        provider = "fallback-provider"
        model = "fallback-model"

        def run_conversation(self, user_message, conversation_history, task_id):
            return {"final_response": "wrong runtime", "session_id": self.session_id}

    monkeypatch.setattr(adapter, "_create_agent", lambda **kwargs: FakeAgent())

    with pytest.raises(RuntimeError, match="confirmed model lock runtime mismatch"):
        await adapter._run_agent(
            user_message="hello",
            conversation_history=[],
            session_id="mismatch-session",
            route={"provider": "nous", "model": "x-ai/grok-4.5"},
            requested_runtime={"provider": "nous", "model": "x-ai/grok-4.5"},
            route_source="session_model_lock",
            confirmed_runtime_lock=True,
        )


def test_confirmed_runtime_lock_disables_global_fallback_model(adapter, monkeypatch):
    _patch_api_server_runtime(monkeypatch)
    monkeypatch.setattr(
        "gateway.run.GatewayRunner._load_fallback_model",
        staticmethod(lambda: "openrouter/fallback-model"),
    )
    captured = {}

    class FakeAgent:
        provider = "nous"
        model = "x-ai/grok-4.5"

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)

    adapter._create_agent(
        session_id="locked-session",
        route={"provider": "nous", "model": "x-ai/grok-4.5"},
        confirmed_runtime_lock=True,
    )

    assert captured["fallback_model"] is None


@pytest.mark.asyncio
async def test_unconfirmed_request_does_not_replace_confirmed_session_lock(adapter, session_db):
    session_id = session_db.create_session("one-off-override", "api_server")
    session_db.update_session_runtime_lock(
        session_id,
        provider="nous",
        model="x-ai/grok-4.5",
        route_source="raw_request",
        confirmed=True,
    )
    mock_run = AsyncMock(
        return_value=(
            {
                "final_response": "ok",
                "session_id": session_id,
                "runtime": {"provider": "openrouter", "model": "anthropic/claude-sonnet"},
            },
            {"total_tokens": 1},
        )
    )
    app = _create_session_app(adapter)
    with patch.object(adapter, "_resolve_route", return_value=None), patch.object(
        adapter,
        "_run_agent",
        mock_run,
    ):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "message": "one turn only",
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet",
                },
            )
            assert resp.status == 200, await resp.text()

    import json as _json

    row = session_db.get_session(session_id)
    config = row["model_config"]
    if isinstance(config, str):
        config = _json.loads(config)
    assert config["browser_model_lock"]["provider"] == "nous"
    assert config["browser_model_lock"]["model"] == "x-ai/grok-4.5"
    assert config["browser_model_lock"]["confirmed"] is True


@pytest.mark.asyncio
async def test_require_model_lock_hard_fails_when_global_default_would_be_used(adapter, session_db, monkeypatch):
    session_id = session_db.create_session("lock-fail-session", "api_server")
    monkeypatch.setattr(adapter, "_model_name", "gpt-5.5")
    app = _create_session_app(adapter)
    with patch.object(adapter, "_resolve_route", return_value=None), patch.object(adapter, "_run_agent", new_callable=AsyncMock) as mock_run:
        async with TestClient(TestServer(app)) as cli:
            # empty model + require_model_lock must not silently fall through
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "message": "hello",
                    "provider": "nous",
                    "model": "",
                    "require_model_lock": True,
                },
            )
            assert resp.status in (400, 409), await resp.text()
            body = await resp.json()
            assert body["error"]["code"] in {"model_lock_unavailable", "invalid_model_lock", "missing_model"}
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_patch_session_persists_pinned_and_archived(adapter, session_db):
    """PATCH must accept the durable pin/archive flags and round-trip them.

    These were rejected as unsupported fields, so every pin the desktop made
    400'd silently (the client swallows the error) and the pin only ever lived
    in that one app's localStorage. The auto-archive sweep reads
    `sessions.pinned` server-side, so an unpersisted pin does not protect the
    chat it was supposed to keep.
    """
    session_id = session_db.create_session("pin-session", "api_server")
    app = _create_session_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.patch(f"/api/sessions/{session_id}", json={"pinned": True})
        assert resp.status == 200, await resp.text()
        assert (await resp.json())["session"]["pinned"] is True

        # The flag is durable, not just echoed back from the request body.
        assert bool(session_db.get_session(session_id)["pinned"]) is True

        resp = await cli.get(f"/api/sessions/{session_id}")
        assert (await resp.json())["session"]["pinned"] is True

        resp = await cli.patch(f"/api/sessions/{session_id}", json={"pinned": False})
        assert (await resp.json())["session"]["pinned"] is False
        assert bool(session_db.get_session(session_id)["pinned"]) is False

        resp = await cli.patch(f"/api/sessions/{session_id}", json={"archived": True})
        assert (await resp.json())["session"]["archived"] is True
        assert bool(session_db.get_session(session_id)["archived"]) is True


@pytest.mark.asyncio
async def test_patch_session_rejects_non_boolean_pinned(adapter, session_db):
    session_id = session_db.create_session("pin-type-session", "api_server")
    app = _create_session_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.patch(f"/api/sessions/{session_id}", json={"pinned": "yes"})
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "invalid_session_field"


@pytest.mark.asyncio
async def test_patch_session_still_rejects_unknown_fields(adapter, session_db):
    session_id = session_db.create_session("unknown-field-session", "api_server")
    app = _create_session_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.patch(f"/api/sessions/{session_id}", json={"nonsense": 1})
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "unsupported_session_field"


@pytest.mark.asyncio
async def test_fork_preserves_reply_and_roundtrips_lineage(adapter, session_db, tmp_path):
    import json
    session_db.create_session("branch-root", "api_server", cwd=str(tmp_path), system_prompt="Keep context")
    session_db.append_message("branch-root", "user", "_____user\nFirst")
    session_db.append_message("branch-root", "assistant", "First reply")
    session_db.append_message("branch-root", "user", "_____user\nAgain")
    session_db.append_message("branch-root", "assistant", "Old reply")
    original = session_db.get_messages("branch-root")
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        body = {"id": "branch-child", "fork_message_id": "user-again", "anchor": {"role": "user", "content": "Again", "occurrence": 1}, "exclude_anchor": True}
        response = await cli.post("/api/sessions/branch-root/fork", json=body)
        assert response.status == 201, await response.text()
        child = (await response.json())["session"]
        assert child["parent_session_id"] == "branch-root"
        assert child["fork_message_id"] == "user-again"
        assert [row["content"] for row in session_db.get_messages("branch-child")] == ["_____user\nFirst", "First reply"]
        assert session_db.get_messages("branch-root") == original
        session_db.append_message("branch-child", "user", "_____user\nAgain")
        session_db.append_message("branch-child", "assistant", "New reply")
        response = await cli.post("/api/sessions/branch-child/fork", json={**body, "id": "branch-third"})
        assert response.status == 201, await response.text()
        assert (await response.json())["session"]["parent_session_id"] == "branch-root"
        listed = (await (await cli.get("/api/sessions")).json())["data"]
        assert {row["id"] for row in listed} >= {"branch-root", "branch-child", "branch-third"}
        assert next(row for row in listed if row["id"] == "branch-child")["fork_message_id"] == "user-again"
    assert session_db.get_session("branch-child")["cwd"] == str(tmp_path)
    assert session_db.get_session("branch-child")["system_prompt"] == "Keep context"
    assert json.loads(session_db.get_session("branch-third")["model_config"])["_branched_from"] == "branch-root"
    exported = [session_db.export_session(id) for id in ("branch-root", "branch-child")]
    restored = SessionDB(tmp_path / "restored.db")
    try:
        result = restored.import_sessions(exported)
        assert not result["errors"], result
        assert restored.get_session("branch-child")["fork_message_id"] == "user-again"
        assert restored.get_session("branch-child")["parent_session_id"] == "branch-root"
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_fork_rejects_stale_anchor_and_busy_without_partial_child(adapter, session_db):
    session_db.create_session("root", "api_server")
    session_db.append_message("root", "user", "hello")
    session_db.append_message("root", "assistant", "preserve me")
    before = session_db.get_session("root")
    async with TestClient(TestServer(_create_session_app(adapter))) as cli:
        body = {"id": "child", "fork_message_id": "missing", "anchor": {"role": "user", "content": "missing"}}
        response = await cli.post("/api/sessions/root/fork", json=body)
        assert response.status == 400
        assert session_db.get_session("child") is None
        assert session_db.get_session("root")["ended_at"] == before["ended_at"]
        adapter._inflight_session_counts["root"] = 1
        response = await cli.post("/api/sessions/root/fork", json={"id": "busy-child"})
        assert response.status == 409
        assert session_db.get_session("busy-child") is None


def test_fork_message_copy_failure_rolls_back_everything(session_db, monkeypatch):
    session_db.create_session("root", "api_server")
    session_db.append_message("root", "user", "hello")
    def fail(*args, **kwargs):
        raise RuntimeError("simulated disk failure")
    monkeypatch.setattr(session_db, "_insert_message_rows", fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        session_db.fork_session("root", "child")
    assert session_db.get_session("child") is None
    assert session_db.get_session("root")["end_reason"] is None
    assert len(session_db.get_messages("root")) == 1


def test_fork_anchor_keeps_tool_pairs_and_duplicate_occurrence(session_db):
    session_db.create_session("root", "api_server")
    session_db.append_message("root", "user", "same")
    session_db.append_message("root", "assistant", "working", tool_calls=[{"id": "call-1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}])
    session_db.append_message("root", "tool", "output", tool_call_id="call-1")
    session_db.append_message("root", "assistant", "done")
    session_db.append_message("root", "user", "same")
    session_db.append_message("root", "assistant", "second")
    session_db.fork_session("root", "tools", anchor={"role": "assistant", "content": "working"}, fork_message_id="assistant-working")
    assert [m["role"] for m in session_db.get_messages("tools")] == ["user", "assistant", "tool"]
    session_db.fork_session("root", "duplicate", anchor={"role": "user", "content": "same", "occurrence": 2}, fork_message_id="second-same", exclude_anchor=True)
    assert [m["content"] for m in session_db.get_messages("duplicate")] == ["same", "working", "output", "done"]


def test_fork_column_migrates_existing_database_and_create_roundtrips(tmp_path):
    import sqlite3
    path = tmp_path / "older.db"
    db = SessionDB(path)
    db.create_session("original", "api_server")
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE sessions DROP COLUMN fork_message_id")
    db = SessionDB(path)
    try:
        assert db.get_session("original")["fork_message_id"] is None
        db.create_session("child", "api_server", parent_session_id="original", fork_message_id="stable-point")
        assert db.get_session("child")["fork_message_id"] == "stable-point"
        assert db.export_session("child")["fork_message_id"] == "stable-point"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_fork_cross_process_lease_conflict_is_409(adapter, session_db):
    import time
    session_db.create_session("root", "api_server")
    session_db.append_message("root", "user", "hello")
    def lease(conn):
        key = session_db._session_turn_lease_key_on_conn(conn, "root")
        conn.execute("INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) VALUES (?, ?, ?, ?)", (key, "test-live-owner", time.time(), time.time()+60))
    session_db._execute_write(lease)
    async with TestClient(TestServer(_create_session_app(adapter))) as cli:
        response = await cli.post("/api/sessions/root/fork", json={"id":"child"})
        assert response.status == 409, await response.text()
    assert session_db.get_session("child") is None
    assert session_db.get_session("root")["ended_at"] is None
