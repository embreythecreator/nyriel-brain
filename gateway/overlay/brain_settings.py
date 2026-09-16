"""nyriel-brain overlay: Ward-authenticated HTTP settings route (SG1).

The Brain's settings (its ``config.yaml``) are file-based and only mutable via the
Brain's ``config set`` CLI. The Face's admin panel needs to read/write them over
HTTP, so this overlay exposes three routes on the gateway's existing aiohttp API
server:

    GET   /v1/brain/settings/schema   -> domain/dangerous descriptor + source commit
    GET   /v1/brain/settings          -> current config, secrets redacted
    PATCH /v1/brain/settings          -> validated partial write (atomic, via config set)

Every route reuses the adapter's ``_check_auth`` (Bearer == API_SERVER_KEY = the
Ward token) so this adds no new trust boundary. It is mounted by a single hook in
``api_server.py``'s ``connect()`` and never edits any vendored upstream file in
place, so monthly re-absorption sees zero conflicts.

Wire keys (model/terminal/...) are preserved verbatim; the Face rebrands display
labels only. Secrets live in ``.env`` (set_config_value routes *_API_KEY/_TOKEN
there), so config.yaml rarely holds raw secrets -- but GET redacts by key-name
anyway as defense in depth.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import subprocess
import time
from typing import Any, Dict

try:
    from aiohttp import web
except Exception:  # pragma: no cover - aiohttp is always present in the gateway
    web = None  # type: ignore

from nyriel_cli.config import (
    get_config_path,
    get_project_root,
    load_config,
    set_config_value,
)

# Dotted keys whose mutation can disable a safety check or run arbitrary code.
# Writing ANY of these requires a signed Seal (defense in depth).
# The Face proxy enforces the same set; this is the server-side half.
DANGEROUS_FIELDS = {
    "approvals.mode",          # turning the approval gate off
    "code_execution.enabled",  # arbitrary code execution
    "terminal.backend",        # swapping the shell backend (e.g. -> local host)
    "updates.auto_apply",      # silent self-update
    "security.redact_secrets", # disabling secret redaction
}
# ponytail: exact-key match + the two prefixes below. Widen if a new safety
# toggle lands; the Face projection's `dangerous` flags must stay in sync.
DANGEROUS_PREFIXES = ("code_execution.", "approvals.")

# Signed elevation token the Face attaches only when the admin session holds the
# elevated Seal capability. Absent or invalid -> dangerous writes are refused.
SEAL_HEADER = "X-Brain-Settings-Seal"
SEAL_ACTION = "brain_settings.patch"
SEAL_MAX_TTL_SECONDS = 120
SEAL_CLOCK_SKEW_SECONDS = 15

_SECRET_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key)", re.I
)
_USED_SEAL_NONCES: Dict[str, float] = {}


def _is_dangerous(dotted_key: str) -> bool:
    return dotted_key in DANGEROUS_FIELDS or dotted_key.startswith(DANGEROUS_PREFIXES)


def _redact(obj: Any) -> Any:
    """Mask values whose key name looks secret-ish. Recurses dicts/lists."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_RE.search(str(k)) and v not in (None, "", {}, []):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested patch into dotted leaf keys (scalar leaves only)."""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _scalar_str(value: Any) -> str:
    """set_config_value expects a string; render scalars the way the CLI would."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _source_commit() -> str:
    try:
        root = get_project_root()
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def _schema_descriptor() -> Dict[str, Any]:
    """Top-level domains parsed from cli-config.yaml.example + dangerous flags."""
    domains = []
    try:
        example = get_project_root() / "cli-config.yaml.example"
        for line in example.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([a-z][a-z0-9_]*):", line)
            if m:
                domains.append(m.group(1))
    except Exception:
        domains = sorted(load_config().keys())
    return {
        "source": "cli-config.yaml.example",
        "source_commit": _source_commit(),
        "config_path": str(get_config_path()),
        "domains": domains,
        "dangerous": sorted(DANGEROUS_FIELDS),
        "seal": {
            "action": SEAL_ACTION,
            "header": SEAL_HEADER,
            "max_ttl_seconds": SEAL_MAX_TTL_SECONDS,
        },
    }


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _seal_signature(secret: str, payload_b64: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def _seal_secret(adapter) -> str:
    return str(getattr(adapter, "_api_key", "") or "")


def _prune_used_seal_nonces(now: float) -> None:
    expired = [nonce for nonce, exp in _USED_SEAL_NONCES.items() if exp <= now]
    for nonce in expired:
        _USED_SEAL_NONCES.pop(nonce, None)


def _verify_settings_seal(request, adapter, dangerous_fields: list[str]) -> tuple[bool, str]:
    """Verify a signed, short-lived Seal for dangerous settings writes."""
    seal = request.headers.get(SEAL_HEADER, "").strip()
    if not seal:
        return False, "missing"

    secret = _seal_secret(adapter)
    if not secret:
        return False, "unconfigured"

    try:
        payload_b64, signature = seal.split(".", 1)
    except ValueError:
        return False, "malformed"

    expected_signature = _seal_signature(secret, payload_b64)
    if not hmac.compare_digest(signature, expected_signature):
        return False, "bad_signature"

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return False, "bad_payload"

    if not isinstance(payload, dict):
        return False, "bad_payload"

    now = time.time()
    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(exp, (int, float)) or not isinstance(iat, (int, float)):
        return False, "bad_time"
    if iat > now + SEAL_CLOCK_SKEW_SECONDS:
        return False, "issued_in_future"
    if exp <= now - SEAL_CLOCK_SKEW_SECONDS:
        return False, "expired"
    if exp - iat > SEAL_MAX_TTL_SECONDS:
        return False, "ttl_too_long"

    if payload.get("action") != SEAL_ACTION:
        return False, "bad_action"
    if payload.get("method") != "PATCH":
        return False, "bad_method"
    if payload.get("path") != request.path:
        return False, "bad_path"

    fields = payload.get("fields")
    if not isinstance(fields, list) or sorted(str(field) for field in fields) != sorted(dangerous_fields):
        return False, "bad_fields"

    nonce = str(payload.get("nonce") or "").strip()
    if not nonce:
        return False, "missing_nonce"

    _prune_used_seal_nonces(now)
    if nonce in _USED_SEAL_NONCES:
        return False, "replayed"

    _USED_SEAL_NONCES[nonce] = float(exp)
    return True, ""


def _settings_seal_error(reason: str, dangerous_fields: list[str]):
    return web.json_response(
        {
            "error": "seal_required",
            "message": "These fields require a valid Brain Settings Seal and were not written.",
            "fields": sorted(dangerous_fields),
            "reason": reason,
        },
        status=403,
    )


def register_brain_settings_routes(app, adapter) -> None:
    """Mount the three settings routes on the gateway's aiohttp app.

    `adapter` is the ApiServerAdapter instance; we reuse its `_check_auth`
    (the Ward/Bearer gate) so these routes share the API server's trust model.
    """
    if web is None:
        return

    async def _schema(request):
        if (err := adapter._check_auth(request)) is not None:
            return err
        return web.json_response(_schema_descriptor())

    async def _get(request):
        if (err := adapter._check_auth(request)) is not None:
            return err
        return web.json_response({"config": _redact(load_config())})

    async def _patch(request):
        if (err := adapter._check_auth(request)) is not None:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be an object"}, status=400)

        patch = body.get("patch", body)  # accept {"patch": {...}} or a bare object
        if not isinstance(patch, dict) or not patch:
            return web.json_response({"error": "empty patch"}, status=400)

        flat = _flatten(patch)
        dangerous_fields = sorted(k for k in flat if _is_dangerous(k))
        if dangerous_fields:
            verified, reason = _verify_settings_seal(request, adapter, dangerous_fields)
            if not verified:
                return _settings_seal_error(reason, dangerous_fields)

        applied, failed = [], {}
        for key, value in flat.items():
            try:
                set_config_value(key, value if isinstance(value, str) else _scalar_str(value))
                applied.append(key)
            except SystemExit:
                failed[key] = "rejected (managed or invalid)"
            except Exception as exc:  # surface, don't swallow
                failed[key] = str(exc)
        status = 200 if not failed else 207
        return web.json_response({"applied": applied, "failed": failed}, status=status)

    app.router.add_get("/v1/brain/settings/schema", _schema)
    app.router.add_get("/v1/brain/settings", _get)
    app.router.add_patch("/v1/brain/settings", _patch)
