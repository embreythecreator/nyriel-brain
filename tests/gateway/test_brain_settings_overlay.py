import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.overlay import brain_settings


class _Adapter:
    _api_key = "ward-secret"

    def _check_auth(self, request):
        if request.headers.get("Authorization") == "Bearer ward-secret":
            return None
        return web.json_response({"error": "invalid_api_key"}, status=401)


def _create_app(adapter=None):
    app = web.Application()
    brain_settings.register_brain_settings_routes(app, adapter or _Adapter())
    return app


def _seal(fields, *, secret="ward-secret", now=None):
    iat = int(now or time.time())
    payload = {
        "action": brain_settings.SEAL_ACTION,
        "exp": iat + 60,
        "fields": sorted(fields),
        "iat": iat,
        "method": "PATCH",
        "nonce": f"nonce-{iat}-{','.join(sorted(fields))}",
        "path": "/v1/brain/settings",
    }
    payload_b64 = brain_settings._b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = brain_settings._seal_signature(secret, payload_b64)
    return f"{payload_b64}.{signature}"


@pytest.fixture(autouse=True)
def _clear_seal_nonces():
    brain_settings._USED_SEAL_NONCES.clear()
    yield
    brain_settings._USED_SEAL_NONCES.clear()


@pytest.mark.asyncio
async def test_safe_patch_applies_without_seal(monkeypatch):
    applied = []
    monkeypatch.setattr(brain_settings, "set_config_value", lambda key, value: applied.append((key, value)))

    async with TestClient(TestServer(_create_app())) as cli:
        response = await cli.patch(
            "/v1/brain/settings",
            headers={"Authorization": "Bearer ward-secret"},
            json={"patch": {"model.provider": "openai"}},
        )

        assert response.status == 200
        assert await response.json() == {"applied": ["model.provider"], "failed": {}}
        assert applied == [("model.provider", "openai")]


@pytest.mark.asyncio
async def test_dangerous_patch_requires_seal(monkeypatch):
    applied = []
    monkeypatch.setattr(brain_settings, "set_config_value", lambda key, value: applied.append((key, value)))

    async with TestClient(TestServer(_create_app())) as cli:
        response = await cli.patch(
            "/v1/brain/settings",
            headers={"Authorization": "Bearer ward-secret"},
            json={"patch": {"approvals.mode": "off"}},
        )

        body = await response.json()
        assert response.status == 403
        assert body["error"] == "seal_required"
        assert body["fields"] == ["approvals.mode"]
        assert body["reason"] == "missing"
        assert applied == []


@pytest.mark.asyncio
async def test_dangerous_patch_accepts_valid_seal(monkeypatch):
    applied = []
    monkeypatch.setattr(brain_settings, "set_config_value", lambda key, value: applied.append((key, value)))

    async with TestClient(TestServer(_create_app())) as cli:
        response = await cli.patch(
            "/v1/brain/settings",
            headers={
                "Authorization": "Bearer ward-secret",
                brain_settings.SEAL_HEADER: _seal(["approvals.mode"]),
            },
            json={"patch": {"approvals.mode": "off"}},
        )

        assert response.status == 200
        assert await response.json() == {"applied": ["approvals.mode"], "failed": {}}
        assert applied == [("approvals.mode", "off")]


@pytest.mark.asyncio
async def test_dangerous_seal_is_one_use(monkeypatch):
    monkeypatch.setattr(brain_settings, "set_config_value", lambda key, value: None)
    seal = _seal(["approvals.mode"])

    async with TestClient(TestServer(_create_app())) as cli:
        first = await cli.patch(
            "/v1/brain/settings",
            headers={"Authorization": "Bearer ward-secret", brain_settings.SEAL_HEADER: seal},
            json={"patch": {"approvals.mode": "off"}},
        )
        second = await cli.patch(
            "/v1/brain/settings",
            headers={"Authorization": "Bearer ward-secret", brain_settings.SEAL_HEADER: seal},
            json={"patch": {"approvals.mode": "on"}},
        )

        assert first.status == 200
        assert second.status == 403
        assert (await second.json())["reason"] == "replayed"


@pytest.mark.asyncio
async def test_get_redacts_secret_values(monkeypatch):
    monkeypatch.setattr(
        brain_settings,
        "load_config",
        lambda: {"model": {"api_key": "secret-value", "provider": "openai"}},
    )

    async with TestClient(TestServer(_create_app())) as cli:
        response = await cli.get("/v1/brain/settings", headers={"Authorization": "Bearer ward-secret"})

        assert response.status == 200
        assert await response.json() == {
            "config": {"model": {"api_key": "***REDACTED***", "provider": "openai"}}
        }
