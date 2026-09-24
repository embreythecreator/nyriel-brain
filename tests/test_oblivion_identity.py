import pytest

from gateway.oblivion_identity import encode_for_tests, principal_for, verify_access_token

SECRET = "test-secret"
NOW = 1_000_000
CLAIMS = {"iss": "oblivion", "sub": "7", "gen": 0, "iat": NOW, "typ": "access", "exp": NOW + 60, "email": "a@b.c", "name": "Ada"}


def test_roundtrip():
    assert verify_access_token(encode_for_tests(CLAIMS, SECRET), SECRET, NOW)["sub"] == "7"


@pytest.mark.parametrize(
    "jwt, secret, now",
    [
        (encode_for_tests(CLAIMS, SECRET), "wrong", NOW),
        (encode_for_tests({**CLAIMS, "typ": "refresh"}, SECRET), SECRET, NOW),
        (encode_for_tests(CLAIMS, SECRET), SECRET, NOW + 61),
        (encode_for_tests(CLAIMS, SECRET)[:-2] + "xx", SECRET, NOW),
        (encode_for_tests({**CLAIMS, "iss": "other"}, SECRET), SECRET, NOW),
        ("not.a.jwt", SECRET, NOW),
        ("", SECRET, NOW),
        (encode_for_tests(CLAIMS, SECRET), "", NOW),
    ],
)
def test_rejects(jwt, secret, now):
    assert verify_access_token(jwt, secret, now) is None


def test_php_plugin_token_shape():
    # header segment must equal the plugin's: base64url of {"alg":"HS256","typ":"JWT"}
    assert encode_for_tests(CLAIMS, SECRET).startswith("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.")


def test_principal():
    assert principal_for({"sub": "1"}) == "wp-1"
    with pytest.raises(ValueError):
        principal_for({})
