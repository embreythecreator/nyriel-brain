"""WO-FACE/APPROVAL-1: approval cards on the /v1/chat/completions stream.

These drive the REAL ``_run_agent`` binding and the REAL command guard — only
the agent is faked — so a regression in the listener registration (the thing
that flips an api_server turn from unattended deny to a blocking ask) fails
here, unlike the /v1/runs tests that mock the agent away.
"""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tools.approval as approval_mod
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
SID = "nyriel-face:embrey:s-approval"
FLAGGED = "sudo systemctl restart nginx"


class _FakeAgent:
    """Runs one flagged terminal command through the real guard."""

    session_id = SID

    def __init__(self, stream_delta_callback=None, **_):
        self._say = stream_delta_callback or (lambda _t: None)

    def run_conversation(self, user_message, conversation_history, task_id):
        result = approval_mod.check_all_command_guards(FLAGGED, "local")
        text = "RAN" if result["approved"] else result["message"]
        self._say(text)
        return {"final_response": text, "messages": [], "api_calls": 1}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(approval_mod, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval_mod, "_get_approval_mode", lambda: "manual")
    monkeypatch.setenv("NYRIEL_EXEC_ASK", "1")
    for var in ("NYRIEL_CRON_SESSION", "NYRIEL_GATEWAY_SESSION", "NYRIEL_INTERACTIVE", "NYRIEL_YOLO_MODE"):
        monkeypatch.delenv(var, raising=False)
    a = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": KEY}))
    monkeypatch.setattr(a, "_create_agent", lambda **kw: _FakeAgent(**kw))
    return a


def _app(adapter):
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    app.router.add_post("/v1/sessions/{session_id}/approval", adapter._handle_session_approval)
    return app


def _chat(cli, *, card=True):
    headers = {**AUTH, "X-Nyriel-Session-Id": SID}
    if card:
        headers["X-Nyriel-Approvals"] = "card"
    return cli.post(
        "/v1/chat/completions",
        json={"model": "t", "stream": True, "messages": [{"role": "user", "content": "go"}]},
        headers=headers,
    )


async def _read_until(resp, needle, buf=""):
    while needle not in buf:
        chunk = await asyncio.wait_for(resp.content.readany(), timeout=10)
        assert chunk, f"stream ended before {needle!r}: {buf[-400:]}"
        buf += chunk.decode()
    return buf


def _event(body, name):
    for block in body.split("\n\n"):
        if block.startswith(f"event: {name}\n"):
            return json.loads(block.split("data: ", 1)[1])
    return None


def _approve(cli, rid, choice="once"):
    return cli.post(f"/v1/sessions/{SID}/approval", json={"choice": choice, "request_id": rid}, headers=AUTH)


@pytest.mark.asyncio
async def test_card_blocks_until_resolved_then_runs(adapter):
    async with TestClient(TestServer(_app(adapter))) as cli:
        resp = await _chat(cli)
        buf = await _read_until(resp, "event: nyriel.approval\n")
        card = _event(buf, "nyriel.approval")
        assert card["choices"] == ["once", "session", "always", "deny"]
        assert card["timeout_s"] > 0 and card["request_id"]

        assert (await _approve(cli, "stale-id")).status == 409
        assert (await _approve(cli, card["request_id"], "yes-please")).status == 400
        ok = await _approve(cli, card["request_id"])
        assert ok.status == 200 and (await ok.json())["resolved"] == 1

        buf = await _read_until(resp, "[DONE]", buf)
        assert _event(buf, "nyriel.approval.responded") == {
            "request_id": card["request_id"], "choice": "once", "resolved": True,
        }
        assert '"content": "RAN"' in buf
    assert SID not in adapter._chat_approval_streams
    assert SID not in approval_mod._gateway_notify_cbs


@pytest.mark.asyncio
async def test_deny_blocks_the_command(adapter):
    async with TestClient(TestServer(_app(adapter))) as cli:
        resp = await _chat(cli)
        buf = await _read_until(resp, "event: nyriel.approval\n")
        rid = _event(buf, "nyriel.approval")["request_id"]
        assert (await _approve(cli, rid, "deny")).status == 200
        buf = await _read_until(resp, "[DONE]", buf)
        assert _event(buf, "nyriel.approval.responded")["choice"] == "deny"
        assert "BLOCKED: Command denied by user" in buf


@pytest.mark.asyncio
async def test_lapse_settles_card_as_timeout(adapter, monkeypatch):
    monkeypatch.setattr(approval_mod, "_get_approval_timeout", lambda: 1)
    async with TestClient(TestServer(_app(adapter))) as cli:
        buf = await _read_until(await _chat(cli), "[DONE]")
        assert _event(buf, "nyriel.approval")["timeout_s"] == 1
        assert _event(buf, "nyriel.approval.responded")["choice"] == "timeout"
        assert "Silence is not consent" in buf


@pytest.mark.asyncio
async def test_without_header_keeps_unattended_deny(adapter):
    async with TestClient(TestServer(_app(adapter))) as cli:
        buf = await _read_until(await _chat(cli, card=False), "[DONE]")
        assert "event: nyriel.approval" not in buf
        assert "unattended platform" in buf


@pytest.mark.asyncio
async def test_resolve_requires_live_stream_and_same_principal(adapter):
    async with TestClient(TestServer(_app(adapter))) as cli:
        assert (await _approve(cli, "x")).status == 404
        # A stream opened by a 0blivion.io principal is not answerable with the Ward key.
        adapter._chat_approval_streams[SID] = {"principal": "wp-7", "answered": {}, "pending": set()}
        assert (await _approve(cli, "x")).status == 404
        adapter._chat_approval_streams.pop(SID)
        bad = await cli.post(f"/v1/sessions/{SID}/approval", json={"choice": "once"}, headers={"Authorization": "Bearer nope"})
        assert bad.status == 401
