import json

import pytest

from gateway.oblivion_identity import encode_for_tests
from gateway.platforms.api_server import APIServerAdapter as APIServer

SECRET = "jwt-secret"
WARD = "ward-key-1234567890"
NOW = 4_000_000_000  # far future so exp checks pass with real time.time()


class FakeRequest(dict):
    def __init__(self, authorization="", session_id=""):
        super().__init__()
        self.headers = {"Authorization": authorization}
        if session_id:
            self.headers["X-Nyriel-Session-Id"] = session_id
        self.remote = "127.0.0.1"
        self.path = "/v1/chat/completions"
        self.method = "POST"


def make_server(jwt_secret=SECRET, ward=WARD):
    server = APIServer.__new__(APIServer)
    server._api_key = ward
    server._oblivion_jwt_secret = jwt_secret
    server._request_audit_log_suffix = lambda request: "test"
    return server


def token(sub="1", **extra):
    return encode_for_tests({"iss": "oblivion", "sub": sub, "gen": 0, "typ": "access", "iat": NOW - 10, "exp": NOW + 3600, **extra}, SECRET)


def test_ward_key_still_ok():
    assert make_server()._check_auth(FakeRequest(f"Bearer {WARD}")) is None


def test_jwt_ok_and_principal_pinned():
    request = FakeRequest(f"Bearer {token('42')}")
    assert make_server()._check_auth(request) is None
    assert request["oblivion_principal"] == "wp-42"


def test_jwt_rejected_when_secret_unset():
    response = make_server(jwt_secret="")._check_auth(FakeRequest(f"Bearer {token()}"))
    assert response is not None and response.status == 401


def test_bad_jwt_401():
    response = make_server()._check_auth(FakeRequest("Bearer not.a.jwt"))
    assert response is not None and response.status == 401


def test_not_entitled_402(monkeypatch):
    server = make_server()
    monkeypatch.setattr(server, "_oblivion_entitled", lambda claims: False)
    response = server._check_auth(FakeRequest(f"Bearer {token()}"))
    assert response is not None and response.status == 402
    assert json.loads(response.text)["error"]["code"] == "oblivion_not_entitled"


def test_session_id_pinned_to_principal():
    server = make_server()
    request = FakeRequest(f"Bearer {token('7')}", session_id="nyriel-face:admin:s-abc")
    assert server._check_auth(request) is None
    assert server._oblivion_scoped_session_id(request, "nyriel-face:admin:s-abc") == "oblivion:wp-7:s-abc"
    assert server._oblivion_scoped_session_id(FakeRequest(f"Bearer {WARD}"), "nyriel-face:admin:s-abc") == "nyriel-face:admin:s-abc"
