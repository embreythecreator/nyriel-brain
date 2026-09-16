"""sinew-uber-direct — Uber Direct courier dispatch (move_object), Class A.

The M2 live-dispatch sinew. OAuth2 client-credentials; quotes carry short
validity windows (minutes), which is exactly what exercises the
approval-TTL-equals-quote-TTL rule hardest. Endpoint shapes per Uber Direct
docs; verify param names at first live run (plan risk #1).
"""

from __future__ import annotations

import time

from plugins.flesh.sinews.base import (
    CancelResult,
    DispatchResult,
    Quote,
    Sinew,
    SinewError,
    TrackResult,
)

_AUTH_URL = "https://auth.uber.com/oauth/v2/token"
_BASE = "https://api.uber.com/v1/customers"

_STATUS_MAP = {
    "pending": "assigned",
    "pickup": "en_route",
    "pickup_complete": "in_progress",
    "dropoff": "in_progress",
    "delivered": "completed",
    "canceled": "canceled",
    "returned": "failed",
}


class UberDirectSinew(Sinew):
    name = "uber_direct"
    verb = "move_object"

    _token: str = ""
    _token_expiry: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode({
            "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"],
            "grant_type": "client_credentials",
            "scope": "eats.deliveries",
        }).encode()
        req = urllib.request.Request(
            _AUTH_URL, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            import json as _json

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read())
        except Exception as exc:
            raise SinewError(f"Uber OAuth failed: {exc}") from exc
        self._token = data.get("access_token", "")
        self._token_expiry = time.time() + float(data.get("expires_in", 0))
        if not self._token:
            raise SinewError("Uber OAuth returned no access_token.")
        return self._token

    def _call(self, method: str, path: str, body: dict = None) -> dict:
        url = f"{_BASE}/{self.config['customer_id']}{path}"
        status, parsed = self.http.request(
            method, url,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json_body=body,
        )
        if status >= 400:
            raise SinewError(f"Uber {method} {path} -> {status}: {parsed.get('message', parsed)}")
        return parsed

    def quote(self, request: dict) -> Quote:
        pickup = request.get("pickup") or {}
        dropoff = request.get("dropoff") or {}
        data = self._call("POST", "/delivery_quotes", {
            "pickup_address": pickup.get("address", ""),
            "dropoff_address": dropoff.get("address", ""),
        })
        expires_at = None
        # Uber returns ISO `expires` — normalize; fall back to a tight window.
        raw_expires = data.get("expires")
        if raw_expires:
            try:
                import datetime

                expires_at = datetime.datetime.fromisoformat(
                    str(raw_expires).replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                expires_at = None
        if expires_at is None:
            expires_at = time.time() + 120
        eta = None
        if data.get("duration"):
            try:
                eta = int(data["duration"])
            except (TypeError, ValueError):
                eta = None
        return Quote(
            sinew=self.name,
            price_usd=float(data.get("fee", 0)) / 100.0,
            eta_minutes=eta,
            provider_quote_id=data.get("id"),
            expires_at=expires_at,
            cancellation_terms="Free cancellation before courier pickup; fee may apply after.",
            raw=data,
        )

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        pickup = request.get("pickup") or {}
        dropoff = request.get("dropoff") or {}
        payload = request.get("payload") or {}
        body = {
            "quote_id": request.get("provider_quote_id"),
            "pickup_address": pickup.get("address", ""),
            "pickup_name": pickup.get("contact_name", "Pickup"),
            "pickup_phone_number": pickup.get("contact", ""),
            "dropoff_address": dropoff.get("address", ""),
            "dropoff_name": dropoff.get("contact_name", "Dropoff"),
            "dropoff_phone_number": dropoff.get("contact", ""),
            "manifest_items": [{
                "name": payload.get("description", "Item"),
                "quantity": 1,
                "size": payload.get("size", "small"),
            }],
            "idempotency_key": idempotency_key,
        }
        data = self._call("POST", "/deliveries", body)
        return DispatchResult(
            provider_reference=data.get("id", ""),
            status=_STATUS_MAP.get(data.get("status", "pending"), "assigned"),
            tracking_url=data.get("tracking_url", ""),
            raw=data,
        )

    def track(self, provider_reference: str) -> TrackResult:
        data = self._call("GET", f"/deliveries/{provider_reference}")
        status = _STATUS_MAP.get(data.get("status", ""), "in_progress")
        final_cost = None
        if status == "completed" and data.get("fee") is not None:
            final_cost = float(data["fee"]) / 100.0
        return TrackResult(status=status, final_cost_usd=final_cost, detail=data)

    def cancel(self, provider_reference: str) -> CancelResult:
        try:
            data = self._call("POST", f"/deliveries/{provider_reference}/cancel", {})
        except SinewError as exc:
            return CancelResult(status="failed", raw={"error": str(exc)})
        return CancelResult(
            status="canceled",
            fee_usd=float(data.get("cancellation_fee", 0)) / 100.0,
            raw=data,
        )
