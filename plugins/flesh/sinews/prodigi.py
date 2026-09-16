"""sinew-prodigi — Prodigi print-on-demand fulfillment (make_object), Class A.

The WO-M/1 dry-run sinew. X-API-Key auth against the v4 print API. The
default base is the SANDBOX host, so real spend requires an explicit
PRODIGI_API_BASE override — the safe direction by default. Prodigi quotes
are indicative (no provider quote id, no TTL); endpoint shapes per Prodigi
v4 docs; verify field names at first sandbox run (plan-risk pattern from
uber_direct).
"""

from __future__ import annotations

from plugins.flesh.sinews.base import (
    CancelResult,
    DispatchResult,
    Quote,
    Sinew,
    SinewError,
    TrackResult,
)

_SANDBOX_BASE = "https://api.sandbox.prodigi.com/v4.0"

# Prodigi order.status.stage -> normalized lifecycle status.
_STAGE_MAP = {
    "Draft": "assigned",
    "AwaitingPayment": "assigned",
    "InProgress": "in_progress",
    "Complete": "completed",
    "Cancelled": "canceled",
}

# Stable string only — this reaches the quote digest.
_CANCELLATION_TERMS = (
    "Cancellable via order action until production starts; outcome is "
    "reported per order and not guaranteed once in progress."
)


class ProdigiSinew(Sinew):
    name = "prodigi"
    verb = "make_object"

    def _base(self) -> str:
        return (self.config.get("api_base") or _SANDBOX_BASE).rstrip("/")

    def _call(self, method: str, path: str, body: dict = None) -> dict:
        status, parsed = self.http.request(
            method,
            f"{self._base()}{path}",
            headers={"X-API-Key": self.config["api_key"]},
            json_body=body,
        )
        if status >= 400:
            raise SinewError(f"Prodigi {method} {path} -> {status}: {parsed}")
        return parsed

    @staticmethod
    def _payload(request: dict) -> dict:
        payload = request.get("payload") or {}
        if not payload.get("sku"):
            raise SinewError("make_object needs payload.sku (e.g. GLOBAL-PAP-18X24).")
        return payload

    @staticmethod
    def _usd(cost: dict, label: str) -> float:
        cost = cost or {}
        if cost.get("currency") != "USD":
            raise SinewError(
                f"Prodigi quoted {label} in {cost.get('currency') or 'no currency'}, "
                "not USD. Refusing to guess FX."
            )
        return float(cost["amount"])

    @staticmethod
    def _recipient(dropoff: dict) -> dict:
        missing = [
            k for k in ("name", "address1", "city", "zip", "country_code")
            if not dropoff.get(k)
        ]
        if missing:
            raise SinewError("make_object dropoff needs: " + ", ".join(missing))
        address = {
            "line1": dropoff["address1"],
            "townOrCity": dropoff["city"],
            "postalOrZipCode": dropoff["zip"],
            "countryCode": str(dropoff["country_code"]).upper(),
        }
        if dropoff.get("address2"):
            address["line2"] = dropoff["address2"]
        if dropoff.get("state"):
            address["stateOrCounty"] = dropoff["state"]
        return {"name": dropoff["name"], "address": address}

    def quote(self, request: dict) -> Quote:
        payload = self._payload(request)
        dropoff = request.get("dropoff") or {}
        data = self._call("POST", "/quotes", {
            "shippingMethod": payload.get("shipping_method", "standard"),
            "destinationCountryCode": str(dropoff.get("country_code") or "US").upper(),
            "currencyCode": "USD",
            "items": [{
                "sku": payload["sku"],
                "copies": int(payload.get("copies", 1)),
                "attributes": payload.get("attributes") or {},
                "assets": [{"printArea": "default"}],
            }],
        })
        quotes = data.get("quotes") or []
        if not quotes:
            raise SinewError(f"Prodigi returned no quotes: {data.get('outcome', data)}")
        summary = quotes[0].get("costSummary") or {}
        price = self._usd(summary.get("items"), "items") + self._usd(
            summary.get("shipping"), "shipping")
        return Quote(
            sinew=self.name,
            price_usd=round(price, 2),
            eta_minutes=None,  # POD fulfills in days; price decides ranking
            provider_quote_id=None,
            expires_at=None,
            cancellation_terms=_CANCELLATION_TERMS,
            raw=data,
        )

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        payload = self._payload(request)
        if not payload.get("file_url"):
            raise SinewError(
                "make_object dispatch needs payload.file_url (public print-ready asset)."
            )
        data = self._call("POST", "/orders", {
            "merchantReference": idempotency_key,
            "shippingMethod": payload.get("shipping_method", "standard"),
            "recipient": self._recipient(request.get("dropoff") or {}),
            "items": [{
                "sku": payload["sku"],
                "copies": int(payload.get("copies", 1)),
                "sizing": payload.get("sizing", "fillPrintArea"),
                "attributes": payload.get("attributes") or {},
                "assets": [{"printArea": "default", "url": payload["file_url"]}],
            }],
        })
        order = data.get("order") or {}
        if not order.get("id"):
            raise SinewError(f"Prodigi order not created: {data.get('outcome', data)}")
        stage = (order.get("status") or {}).get("stage", "InProgress")
        return DispatchResult(
            provider_reference=order["id"],
            status=_STAGE_MAP.get(stage, "in_progress"),
            raw=data,
        )

    def track(self, provider_reference: str) -> TrackResult:
        data = self._call("GET", f"/orders/{provider_reference}")
        order = data.get("order") or {}
        stage = (order.get("status") or {}).get("stage", "")
        status = _STAGE_MAP.get(stage, "in_progress")
        final = None
        if status == "completed":
            # Sum USD charges; non-USD or absent charges fall back to the
            # quoted price at settle (variance flag still applies).
            total = 0.0
            for charge in order.get("charges") or []:
                cost = charge.get("totalCost") or {}
                if cost.get("currency") == "USD":
                    try:
                        total += float(cost.get("amount", 0))
                    except (TypeError, ValueError):
                        pass
            final = round(total, 2) if total else None
        return TrackResult(status=status, final_cost_usd=final, detail=order)

    def cancel(self, provider_reference: str) -> CancelResult:
        try:
            data = self._call(
                "POST", f"/orders/{provider_reference}/actions/cancel", {})
        except SinewError as exc:
            return CancelResult(status="failed", raw={"error": str(exc)})
        outcome = str(data.get("outcome", "")).lower()
        if "cancel" in outcome and "failed" not in outcome:
            return CancelResult(status="canceled", fee_usd=0.0, raw=data)
        return CancelResult(status="failed", raw=data)
