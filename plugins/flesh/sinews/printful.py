"""sinew-printful — Printful print-on-demand fulfillment (make_object), Class A.

The WO-M/1 live-order sinew (existing operator account, free tier). Bearer
private-token auth against the v1 API. The real-spend boundary is Printful's
two-step order flow: dispatch() creates a DRAFT order, then confirms it —
both inside dispatch(), which only ever runs after the T3 approval is
consumed. If the confirm step fails the draft stays unconfirmed in Printful
(no spend) while the approval is spent — the safe failure direction.
Endpoint shapes per Printful v1 docs; verify at first live run.
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

_BASE = "https://api.printful.com"

# Printful order status -> normalized lifecycle status.
_STATUS_MAP = {
    "draft": "assigned",
    "pending": "assigned",
    "onhold": "in_progress",
    "inprocess": "in_progress",
    "partial": "in_progress",
    "fulfilled": "completed",
    "canceled": "canceled",
    "failed": "failed",
}

# Stable string only — this reaches the quote digest.
_CANCELLATION_TERMS = (
    "Cancellable while the order is draft or pending; once fulfillment "
    "starts cancellation is refused."
)


class PrintfulSinew(Sinew):
    name = "printful"
    verb = "make_object"

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.config['api_key']}"}
        if self.config.get("store_id"):
            headers["X-PF-Store-Id"] = str(self.config["store_id"])
        return headers

    def _call(self, method: str, path: str, body: dict = None) -> dict:
        status, parsed = self.http.request(
            method, f"{_BASE}{path}", headers=self._headers(), json_body=body)
        if status >= 400:
            detail = parsed.get("result") if isinstance(parsed, dict) else parsed
            raise SinewError(f"Printful {method} {path} -> {status}: {detail}")
        result = parsed.get("result") if isinstance(parsed, dict) else None
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _items(payload: dict) -> list:
        if not payload.get("variant_id") and not payload.get("sku"):
            raise SinewError(
                "make_object needs payload.variant_id (Printful catalog variant) "
                "or payload.sku."
            )
        if not payload.get("file_url"):
            raise SinewError(
                "make_object needs payload.file_url (public print-ready asset)."
            )
        item = {
            "quantity": int(payload.get("copies", 1)),
            "files": [{"url": payload["file_url"]}],
        }
        if payload.get("variant_id"):
            item["variant_id"] = int(payload["variant_id"])
        else:
            item["sku"] = str(payload["sku"])
        return [item]

    @staticmethod
    def _recipient(dropoff: dict) -> dict:
        missing = [
            k for k in ("name", "address1", "city", "zip", "country_code")
            if not dropoff.get(k)
        ]
        if missing:
            raise SinewError("make_object dropoff needs: " + ", ".join(missing))
        recipient = {
            "name": dropoff["name"],
            "address1": dropoff["address1"],
            "city": dropoff["city"],
            "zip": dropoff["zip"],
            "country_code": str(dropoff["country_code"]).upper(),
        }
        if dropoff.get("address2"):
            recipient["address2"] = dropoff["address2"]
        if dropoff.get("state"):
            recipient["state_code"] = dropoff["state"]
        if dropoff.get("contact"):
            recipient["phone"] = dropoff["contact"]
        return recipient

    def _order_body(self, request: dict, external_id: str = "") -> dict:
        body = {
            "recipient": self._recipient(request.get("dropoff") or {}),
            "items": self._items(request.get("payload") or {}),
        }
        if external_id:
            body["external_id"] = external_id
        return body

    def quote(self, request: dict) -> Quote:
        result = self._call("POST", "/orders/estimate-costs", self._order_body(request))
        costs = result.get("costs") or {}
        if costs.get("currency") not in (None, "USD"):
            raise SinewError(
                f"Printful estimated in {costs.get('currency')}, not USD. "
                "Refusing to guess FX."
            )
        total = costs.get("total")
        if total is None:
            raise SinewError(f"Printful estimate returned no total: {result}")
        return Quote(
            sinew=self.name,
            price_usd=round(float(total), 2),
            eta_minutes=None,  # POD fulfills in days; price decides ranking
            provider_quote_id=None,
            expires_at=None,
            cancellation_terms=_CANCELLATION_TERMS,
            raw=result,
        )

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        draft = self._call(
            "POST", "/orders", self._order_body(request, external_id=idempotency_key))
        order_id = draft.get("id")
        if not order_id:
            raise SinewError(f"Printful draft order not created: {draft}")
        confirmed = self._call("POST", f"/orders/{order_id}/confirm")
        status = _STATUS_MAP.get(str(confirmed.get("status", "pending")), "assigned")
        return DispatchResult(
            provider_reference=str(order_id),
            status=status,
            tracking_url=str(confirmed.get("dashboard_url", "")),
            raw=confirmed,
        )

    def track(self, provider_reference: str) -> TrackResult:
        result = self._call("GET", f"/orders/{provider_reference}")
        status = _STATUS_MAP.get(str(result.get("status", "")), "in_progress")
        final = None
        if status == "completed":
            costs = result.get("costs") or {}
            if costs.get("total") is not None and costs.get("currency") in (None, "USD"):
                final = round(float(costs["total"]), 2)
        return TrackResult(status=status, final_cost_usd=final, detail=result)

    def cancel(self, provider_reference: str) -> CancelResult:
        try:
            result = self._call("DELETE", f"/orders/{provider_reference}")
        except SinewError as exc:
            return CancelResult(status="failed", raw={"error": str(exc)})
        return CancelResult(status="canceled", fee_usd=0.0, raw={"result": result})
