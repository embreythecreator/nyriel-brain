"""WO-STAGE/HANDS-1: the stage-control channel (cloud angel drives the Oblivion app)."""
import importlib.util
import threading

import pytest

from gateway import stage_control as sc

# The WebSocket tests need aiohttp (the [messaging] extra the cloud image installs); a bare venv skips them.
needs_aiohttp = pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="aiohttp not installed")


@pytest.fixture(autouse=True)
def _no_result_grace(monkeypatch):
    """Short timeouts in these tests should not also wait the 5 s result grace."""
    monkeypatch.setattr(sc, "RESULT_GRACE_S", 0.0)


class FakeApp:
    """An attached app: records frames; optionally answers each command."""

    def __init__(self, control, principal, answer=None):
        self.control, self.principal, self.answer = control, principal, answer
        self.frames = []

    def send(self, frame):
        self.frames.append(frame)
        if self.answer is not None:
            self.control.complete(self.principal, {"type": "result", "id": frame["id"], **self.answer}, owner=self)


def _dispatch_in_thread(control, principal, argv, timeout_s, errors):
    def run():
        try:
            control.dispatch(principal, argv, timeout_s=timeout_s)
        except sc.StageControlError as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    return worker


def _wait_for_frame(app):
    for _ in range(2000):
        if app.frames:
            return
        threading.Event().wait(0.001)
    raise AssertionError("no command frame sent")


def test_dispatch_round_trip():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1", answer={"exit_code": 0, "stdout": "OK", "stderr": ""})
    control.attach("wp-1", app.send, owner=app)
    result = control.dispatch("wp-1", ["new-split", "down"], timeout_s=5)
    assert result["exit_code"] == 0 and result["stdout"] == "OK"
    assert app.frames[0]["type"] == "command"
    assert app.frames[0]["argv"] == ["new-split", "down"]
    assert app.frames[0]["timeout_s"] == 5


def test_unknown_principal_not_connected():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)
    with pytest.raises(sc.StageAppNotConnected):
        control.dispatch("wp-2", ["ping"])
    assert app.frames == []


def test_timeout_raises_and_late_result_ignored():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)
    with pytest.raises(sc.StageCommandTimeout):
        control.dispatch("wp-1", ["ping"], timeout_s=0.05)
    late = {"type": "result", "id": app.frames[0]["id"], "exit_code": 0}
    assert control.complete("wp-1", late, owner=app) is False


def test_disconnect_mid_command():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)
    errors = []
    worker = _dispatch_in_thread(control, "wp-1", ["send", "hi"], 5, errors)
    _wait_for_frame(app)
    assert control.detach("wp-1", owner=app) is True
    worker.join(2)
    assert len(errors) == 1 and isinstance(errors[0], sc.StageAppDisconnected)
    assert control.is_attached("wp-1") is False


def test_newer_connection_replaces_older():
    control = sc.StageControl()
    old, new = FakeApp(control, "wp-1"), FakeApp(control, "wp-1", answer={"exit_code": 0})
    control.attach("wp-1", old.send, owner=old)
    control.attach("wp-1", new.send, owner=new)
    assert control.dispatch("wp-1", ["ping"], timeout_s=5)["exit_code"] == 0
    assert old.frames == [] and len(new.frames) == 1
    # the replaced owner's teardown must not detach the newer app
    assert control.detach("wp-1", owner=old) is False
    assert control.is_attached("wp-1") is True


def test_replacement_fails_pending_commands_of_older():
    control = sc.StageControl()
    old, new = FakeApp(control, "wp-1"), FakeApp(control, "wp-1")
    control.attach("wp-1", old.send, owner=old)
    errors = []
    worker = _dispatch_in_thread(control, "wp-1", ["ping"], 5, errors)
    _wait_for_frame(old)
    control.attach("wp-1", new.send, owner=new)
    worker.join(2)
    assert len(errors) == 1 and isinstance(errors[0], sc.StageAppDisconnected)


def test_stranger_cannot_complete():
    control = sc.StageControl()
    app, stranger = FakeApp(control, "wp-1"), object()
    control.attach("wp-1", app.send, owner=app)
    errors = []
    worker = _dispatch_in_thread(control, "wp-1", ["ping"], 0.3, errors)
    _wait_for_frame(app)
    frame = {"type": "result", "id": app.frames[0]["id"], "exit_code": 0}
    assert control.complete("wp-2", frame, owner=app) is False
    assert control.complete("wp-1", frame, owner=stranger) is False
    worker.join(2)
    assert len(errors) == 1 and isinstance(errors[0], sc.StageCommandTimeout)


def test_refused_result_passes_through():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1", answer={"refused": "tier"})
    control.attach("wp-1", app.send, owner=app)
    assert control.dispatch("wp-1", ["send", "x"])["refused"] == "tier"


def test_send_failure_is_disconnect():
    control = sc.StageControl()

    def broken(frame):
        raise ConnectionError("closed")

    owner = object()
    control.attach("wp-1", broken, owner=owner)
    with pytest.raises(sc.StageAppDisconnected):
        control.dispatch("wp-1", ["ping"])


@pytest.mark.parametrize("argv", [[], "ping", ["ping", 3], None])
def test_bad_argv_rejected(argv):
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)
    with pytest.raises(ValueError):
        control.dispatch("wp-1", argv)
    assert app.frames == []


def test_timeout_clamped_to_60():
    control = sc.StageControl()
    app = FakeApp(control, "wp-1", answer={"exit_code": 0})
    control.attach("wp-1", app.send, owner=app)
    control.dispatch("wp-1", ["ping"], timeout_s=600)
    assert app.frames[0]["timeout_s"] == 60


def test_turn_principal_binding():
    assert sc.turn_principal() == ""
    token = sc.bind_turn_principal("wp-9")
    assert sc.turn_principal() == "wp-9"
    sc.reset_turn_principal(token)
    assert sc.turn_principal() == ""


def test_owner_principal_from_config():
    assert sc.owner_principal({"stage_control": {"owner_principal": " wp-1 "}}) == "wp-1"
    assert sc.owner_principal({}) == ""
    assert sc.owner_principal({"stage_control": "nope"}) == ""


# ---- API server: principal binding + route auth (Task 2) ----

import asyncio  # noqa: E402

from gateway.oblivion_identity import encode_for_tests  # noqa: E402
from gateway.platforms.api_server import APIServerAdapter  # noqa: E402

SECRET = "jwt-secret"
WARD = "ward-key-1234567890"
NOW = 4_000_000_000


class FakeRequest(dict):
    def __init__(self, authorization=""):
        super().__init__()
        self.headers = {"Authorization": authorization}
        self.remote = "127.0.0.1"
        self.path = "/v1/stage-control/ws"
        self.method = "GET"


def _server():
    server = APIServerAdapter.__new__(APIServerAdapter)
    server._api_key = WARD
    server._oblivion_jwt_secret = SECRET
    server._request_audit_log_suffix = lambda request: "test"
    return server


def _token(sub="1"):
    return encode_for_tests({"iss": "oblivion", "sub": sub, "gen": 0, "typ": "access", "iat": NOW - 10, "exp": NOW + 3600}, SECRET)


def test_check_auth_binds_token_principal():
    token = sc.bind_turn_principal("")
    try:
        assert _server()._check_auth(FakeRequest(f"Bearer {_token('42')}")) is None
        assert sc.turn_principal() == "wp-42"
    finally:
        sc.reset_turn_principal(token)


def test_check_auth_ward_key_binds_owner(monkeypatch):
    monkeypatch.setattr(sc, "owner_principal", lambda config=None: "wp-1")
    token = sc.bind_turn_principal("")
    try:
        assert _server()._check_auth(FakeRequest(f"Bearer {WARD}")) is None
        assert sc.turn_principal() == "wp-1"
    finally:
        sc.reset_turn_principal(token)


@needs_aiohttp
def test_stage_ws_rejects_ward_key():
    response = asyncio.run(_server()._handle_stage_control_ws(FakeRequest(f"Bearer {WARD}")))
    assert response.status == 403


@needs_aiohttp
def test_stage_ws_rejects_bad_auth():
    response = asyncio.run(_server()._handle_stage_control_ws(FakeRequest("Bearer nope")))
    assert response.status == 401


def test_stage_ws_route_registered():
    server = _server()
    rows = {(method, path) for method, path, _ in server._http_route_table()}
    assert ("GET", "/v1/stage-control/ws") in rows


# ---- tool `oblivion_app` (Task 3) ----

import json  # noqa: E402


@pytest.fixture
def isolated_control(monkeypatch):
    control = sc.StageControl()
    monkeypatch.setattr(sc, "get_stage_control", lambda: control)
    return control


def _bound(principal):
    return sc.bind_turn_principal(principal)


def test_tool_hidden_without_principal(isolated_control):
    from tools import stage_tool

    token = _bound("")
    try:
        assert stage_tool.check_app_attached() is False
    finally:
        sc.reset_turn_principal(token)


def test_tool_hidden_when_not_attached(isolated_control):
    from tools import stage_tool

    other = FakeApp(isolated_control, "wp-2")
    isolated_control.attach("wp-2", other.send, owner=other)
    token = _bound("wp-1")
    try:
        assert stage_tool.check_app_attached() is False
    finally:
        sc.reset_turn_principal(token)


def test_tool_offered_and_runs_when_attached(isolated_control):
    from tools import stage_tool

    app = FakeApp(isolated_control, "wp-1", answer={"exit_code": 0, "stdout": "surface:7", "stderr": ""})
    isolated_control.attach("wp-1", app.send, owner=app)
    token = _bound("wp-1")
    try:
        assert stage_tool.check_app_attached() is True
        out = json.loads(stage_tool.oblivion_app_tool({"argv": ["new-split", "down"]}))
        assert out == {"exit_code": 0, "stdout": "surface:7", "stderr": ""}
    finally:
        sc.reset_turn_principal(token)


def test_tool_reports_refusal(isolated_control):
    from tools import stage_tool

    app = FakeApp(isolated_control, "wp-1", answer={"refused": "tier"})
    isolated_control.attach("wp-1", app.send, owner=app)
    token = _bound("wp-1")
    try:
        out = json.loads(stage_tool.oblivion_app_tool({"argv": ["send", "echo hi"]}))
        assert out["refused"] == "tier" and "not retry" in out["note"]
    finally:
        sc.reset_turn_principal(token)


def test_tool_error_names_cause(isolated_control):
    from tools import stage_tool

    token = _bound("wp-1")
    try:
        assert "not connected" in json.loads(stage_tool.oblivion_app_tool({"argv": ["ping"]}))["error"]
        assert "argv" in json.loads(stage_tool.oblivion_app_tool({"argv": "ping"}))["error"]
    finally:
        sc.reset_turn_principal(token)


def test_tool_only_in_api_server_toolset():
    from toolsets import TOOLSETS

    homes = {name for name, spec in TOOLSETS.items() if "oblivion_app" in spec.get("tools", [])}
    # its own toolset, plus the API-server composite that lets the resolver recover it
    assert homes == {"nyriel-api-server", "stage_control"}


# ---- phase A2 review fixes ----


def _api_server_tool_names():
    """Build the tool list the way APIServerAdapter._create_agent does."""
    import model_tools
    from nyriel_cli.tools_config import _get_platform_tools

    enabled = sorted(_get_platform_tools({}, "api_server"))
    return {t["function"]["name"] for t in model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)}


def _names_for(principal):
    token = sc.bind_turn_principal(principal)
    try:
        return _api_server_tool_names()
    finally:
        sc.reset_turn_principal(token)


def test_stage_control_toolset_enabled_for_api_server_only():
    from nyriel_cli.tools_config import _get_platform_tools

    assert "stage_control" in _get_platform_tools({}, "api_server")
    assert "stage_control" not in _get_platform_tools({}, "cli")
    assert "stage_control" not in _get_platform_tools({}, "telegram")


def test_api_server_tool_list_follows_attach_and_principal(isolated_control):
    assert "oblivion_app" not in _names_for("wp-1")  # none attached
    app = FakeApp(isolated_control, "wp-1")
    isolated_control.attach("wp-1", app.send, owner=app)
    assert "oblivion_app" in _names_for("wp-1")  # attached → appears (no stale negative)
    assert "oblivion_app" not in _names_for("wp-2")  # wp-1's positive not offered to wp-2
    assert "oblivion_app" not in _names_for("")  # nor to an unbound turn
    isolated_control.detach("wp-1", owner=app)
    assert "oblivion_app" not in _names_for("wp-1")  # detached → gone (no stale positive)


def test_check_scope_bypasses_cache_when_stage_principal_bound():
    from tools.registry import CHECK_FN_CACHE_BYPASS, check_fn_cache_scope

    token = sc.bind_turn_principal("wp-1")
    try:
        assert check_fn_cache_scope() == CHECK_FN_CACHE_BYPASS
    finally:
        sc.reset_turn_principal(token)


def test_attach_returns_replaced_owner():
    control = sc.StageControl()
    old, new = object(), object()
    assert control.attach("wp-1", lambda f: None, owner=old) is None
    assert control.attach("wp-1", lambda f: None, owner=new) is old
    assert control.attach("wp-1", lambda f: None, owner=new) is None


@needs_aiohttp
def test_replaced_websocket_is_closed(monkeypatch):
    from aiohttp import WSMsgType, web
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(sc, "get_stage_control", lambda control=sc.StageControl(): control)
    server = _server()

    async def scenario():
        app = web.Application()
        app.router.add_get("/v1/stage-control/ws", server._handle_stage_control_ws)
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": f"Bearer {_token('5')}"}
            first = await client.ws_connect("/v1/stage-control/ws", headers=headers)
            for _ in range(200):  # wait for the first attach to land
                if sc.get_stage_control().is_attached("wp-5"):
                    break
                await asyncio.sleep(0.01)
            second = await client.ws_connect("/v1/stage-control/ws", headers=headers)
            msg = await asyncio.wait_for(first.receive(), 5)
            assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
            assert first.close_code == 4000
            assert not second.closed and sc.get_stage_control().is_attached("wp-5")
            await second.close()

    asyncio.run(scenario())


def test_result_inside_grace_is_not_a_timeout(monkeypatch):
    monkeypatch.setattr(sc, "RESULT_GRACE_S", 2.0)
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)

    def late_answer():
        _wait_for_frame(app)
        threading.Event().wait(0.3)  # past timeout_s=0.1, inside the grace
        control.complete("wp-1", {"type": "result", "id": app.frames[0]["id"], "exit_code": 0}, owner=app)

    threading.Thread(target=late_answer).start()
    assert control.dispatch("wp-1", ["ping"], timeout_s=0.1)["exit_code"] == 0


def test_result_at_the_boundary_wins(monkeypatch):
    """The wait times out at the same instant the result lands: report the result."""
    control = sc.StageControl()
    app = FakeApp(control, "wp-1")
    control.attach("wp-1", app.send, owner=app)

    class BoundaryEvent(threading.Event):
        def wait(self, timeout=None):
            control.complete("wp-1", {"type": "result", "id": app.frames[0]["id"], "exit_code": 7}, owner=app)
            return False

    real_pending = sc._Pending
    monkeypatch.setattr(sc, "_Pending", lambda: real_pending(event=BoundaryEvent()))
    assert control.dispatch("wp-1", ["ping"], timeout_s=1)["exit_code"] == 7
