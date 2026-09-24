"""Verify 0blivion.io identity access tokens (WO-IDENTITY/1) with the shared OBLIVION_JWT_SECRET.

HS256 over base64url(header).base64url(payload); stdlib only. Mirrors oblivion-identity.php
and the Face's server/lib/auth/oblivion_identity.js.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

ISSUER = "oblivion"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign(signing_input: str, secret: str) -> str:
    return _b64url(hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest())


def encode_for_tests(claims: dict, secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{header}.{payload}.{_sign(f'{header}.{payload}', secret)}"


def verify_access_token(jwt: str, secret: str, now: int | None = None) -> dict | None:
    if not secret or not isinstance(jwt, str):
        return None
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    header, payload, signature = parts
    try:
        expected = _sign(f"{header}.{payload}", secret)
    except (UnicodeEncodeError, ValueError):
        return None
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8", "replace")):
        return None
    try:
        claims = json.loads(_unb64url(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("iss") != ISSUER or claims.get("typ") != "access":
        return None
    try:
        exp = int(claims.get("exp"))
    except (TypeError, ValueError):
        return None
    if exp <= (int(time.time()) if now is None else now):
        return None
    return claims


def principal_for(claims: dict) -> str:
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise ValueError("token has no subject")
    return f"wp-{sub}"
