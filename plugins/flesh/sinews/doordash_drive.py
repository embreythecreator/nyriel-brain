"""sinew-doordash-drive — DoorDash Drive v2 courier dispatch (move_object).

Class A* (WO-F/1 §3.2): open sandbox, production access restricted — this
sinew proves the spine (M1) and becomes the Phase-2 depth sinew; the first
live dispatch is Uber Direct (M2).

Auth: short-lived JWT signed HS256 with the developer signing secret
(base64url-encoded), header dd-ver DD-JWT-V1. Stdlib only — no pyjwt.

Endpoint shapes verified against DoorDash Drive v2 docs at build time per
plan risk #1; sandbox behavior is exercised by the M1 round-trip.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from plugins.flesh.sinews.base import (
    CancelResult,
    DispatchResult,
    Quote,
    Sinew,
    SinewError,
    TrackResult,
)

_BASE = "https://openapi.doordash.com/drive/v2"

# DoorDash delivery_status -> normalized lifecycle status (WO-F/1 §2.2)
_STATUS_MAP = {
    "created": "assigned",
    "confirmed": "assigned",
    "enroute_to_pickup": "en_route",
    "arrived_at_pickup": "arrived",
    "picked_up": "in_progress",
    "enroute_to_dropoff": "in_progress",
    "arrived_at_dropoff": "arrived",
    "delivered": "completed",
    "cancelled": "canceled",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class DoorDashDriveSinew(Sinew):
    name = "doordash_drive"
    verb = "move_object"

    # -- auth --------------------------------------------------------------

    def _jwt(self) -> str:
        header = {"alg": "HS256", "typ": "JWT", "dd-ver": "DD-JWT-V1"}
        now = int(time.time())
        payload = {
            "aud": "doordash",
            "iss": self.config["developer_id"],
            "kid": self.config["key_id"],
            "iat": now,
            "exp": now + 300,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + _b64url(json.dumps(payload, separators=(",", ":")).encode())
        )
        secret = base64.urlsafe_b64decode(self.config["signing_secret"] + "==")
        sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
        return signing_input + "." + _b64url(sig)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._jwt()}"}

    def _call(self, method: str, path: str, body: dict = None) -> dict:
        status, parsed = self.http.request(
            method, _BASE + path, headers=self._headers(), json_body=body
        )
        if status >= 400:
            message = parsed.get("message") or parsed.get("field_errors") or parsed
            raise SinewError(f"DoorDash {method} {path} -> {status}: {message}")
        return parsed

    # -- lifecycle ---------------------------------------------------------

    def quote(self, request: dict) -> Quote:
        pickup = request.get("pickup") or {}
        dropoff = request.get("dropoff") or {}
        external_id = request.get("external_delivery_id") or f"flesh-{uuid.uuid4().hex[:12]}"
        body = {
            "external_delivery_id": external_id,
            "pickup_address": pickup.get("address", ""),
            "pickup_phone_number": pickup.get("contact", ""),
            "dropoff_address": dropoff.get("address", ""),
            "dropoff_phone_number": dropoff.get("contact", ""),
        }
        if pickup.get("notes"):
            body["pickup_instructions"] = pickup["notes"]
        if dropoff.get("notes"):
            body["dropoff_instructions"] = dropoff["notes"]
        payload = request.get("payload") or {}
        if payload.get("value_usd"):
            body["order_value"] = int(round(float(payload["value_usd"]) * 100))
        data = self._call("POST", "/quotes", body)
        fee_usd = float(data.get("fee", 0)) / 100.0
        # DD quotes are valid ~5 min; expires field name varies by API rev,
        # so pin our own conservative window when absent.
        expires_at = time.time() + 270
        eta = None
        if data.get("duration"):
            try:
                eta = int(data["duration"])
            except (TypeError, ValueError):
                eta = None
        return Quote(
            sinew=self.name,
            price_usd=fee_usd,
            eta_minutes=eta,
            provider_quote_id=data.get("external_delivery_id", external_id),
            expires_at=expires_at,
            # stable text only — this string is digest-bound
            cancellation_terms="Free cancellation until a dasher picks up the item.",
            raw=data,
        )

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        quote_id = request.get("provider_quote_id")
        if not quote_id:
            raise SinewError("doordash_drive dispatch requires provider_quote_id from the quote step.")
        data = self._call("POST", f"/quotes/{quote_id}/accept", {})
        return DispatchResult(
            provider_reference=data.get("external_delivery_id", quote_id),
            status=_STATUS_MAP.get(data.get("delivery_status", "created"), "assigned"),
            tracking_url=data.get("tracking_url", ""),
            raw=data,
        )

    def track(self, provider_reference: str) -> TrackResult:
        data = self._call("GET", f"/deliveries/{provider_reference}")
        status = _STATUS_MAP.get(data.get("delivery_status", ""), "in_progress")
        final_cost = None
        if status == "completed" and data.get("fee") is not None:
            final_cost = float(data["fee"]) / 100.0
        return TrackResult(status=status, final_cost_usd=final_cost, detail=data)

    def cancel(self, provider_reference: str) -> CancelResult:
        try:
            data = self._call("PUT", f"/deliveries/{provider_reference}/cancel", {})
        except SinewError as exc:
            return CancelResult(status="failed", raw={"error": str(exc)})
        return CancelResult(status="canceled", fee_usd=0.0, raw=data)
