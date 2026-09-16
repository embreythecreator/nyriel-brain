"""Prodigi sinew: quote parsing + currency guard, digest stability, dispatch
shape, stage mapping, cancel outcomes. Mocked HTTP only — no live calls.
Run: pytest tests/plugins/flesh/"""

import os

import pytest

os.environ.setdefault("FLESH_CONFIG_PATH", "/nonexistent")

from plugins.flesh.digest import quote_digest
from plugins.flesh.sinews.base import SinewError
from plugins.flesh.sinews.prodigi import ProdigiSinew


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json_body=None, timeout=30):
        self.calls.append({"method": method, "url": url, "body": json_body})
        return self.responses.pop(0)


def _quote_response(items="18.50", shipping="7.95", currency="USD"):
    return (200, {
        "outcome": "Created",
        "quotes": [{
            "shipmentMethod": "Standard",
            "costSummary": {
                "items": {"amount": items, "currency": currency},
                "shipping": {"amount": shipping, "currency": currency},
            },
        }],
    })


_REQUEST = {
    "dropoff": {
        "name": "Test Operator", "address1": "1 Main St", "city": "Orlando",
        "state": "FL", "zip": "32801", "country_code": "US",
    },
    "payload": {"sku": "GLOBAL-PAP-18X24", "copies": 1,
                "file_url": "https://example.test/poster.png"},
}


def _sinew(responses):
    return ProdigiSinew({"api_key": "test-key"}, http_client=FakeHttp(responses))


def test_quote_sums_items_and_shipping_and_binds_digest():
    quote = _sinew([_quote_response()]).quote(dict(_REQUEST))
    assert quote.price_usd == 26.45
    assert quote.provider_quote_id is None and quote.expires_at is None
    a = quote_digest("make_object", quote.sinew, quote.price_usd,
                     _REQUEST, quote.cancellation_terms)
    again = _sinew([_quote_response()]).quote(dict(_REQUEST))
    b = quote_digest("make_object", again.sinew, again.price_usd,
                     _REQUEST, again.cancellation_terms)
    assert a == b  # identical quotes -> identical digest, re-quotable safely


def test_quote_refuses_non_usd():
    with pytest.raises(SinewError, match="not USD"):
        _sinew([_quote_response(currency="GBP")]).quote(dict(_REQUEST))


def test_quote_hits_sandbox_by_default():
    sinew = _sinew([_quote_response()])
    sinew.quote(dict(_REQUEST))
    assert sinew.http.calls[0]["url"].startswith("https://api.sandbox.prodigi.com")


def test_dispatch_builds_recipient_and_reference():
    sinew = _sinew([(200, {"outcome": "Created", "order": {
        "id": "ord_123", "status": {"stage": "InProgress"}}})])
    result = sinew.dispatch(dict(_REQUEST), idempotency_key="appr-1")
    assert result.provider_reference == "ord_123"
    assert result.status == "in_progress"
    body = sinew.http.calls[0]["body"]
    assert body["merchantReference"] == "appr-1"
    assert body["recipient"]["address"]["postalOrZipCode"] == "32801"
    assert body["items"][0]["assets"][0]["url"] == "https://example.test/poster.png"


def test_dispatch_requires_file_url():
    request = {"dropoff": _REQUEST["dropoff"], "payload": {"sku": "GLOBAL-PAP-18X24"}}
    with pytest.raises(SinewError, match="file_url"):
        _sinew([]).dispatch(request, idempotency_key="appr-2")


def test_track_maps_stages_and_sums_usd_charges():
    sinew = _sinew([(200, {"order": {
        "id": "ord_123", "status": {"stage": "Complete"},
        "charges": [
            {"totalCost": {"amount": "20.00", "currency": "USD"}},
            {"totalCost": {"amount": "6.45", "currency": "USD"}},
        ],
    }})])
    track = sinew.track("ord_123")
    assert track.status == "completed"
    assert track.final_cost_usd == 26.45


def test_cancel_reads_outcome():
    ok = _sinew([(200, {"outcome": "Cancelled"})]).cancel("ord_123")
    assert ok.status == "canceled"
    refused = _sinew([(200, {"outcome": "FailedToCancel"})]).cancel("ord_123")
    assert refused.status == "failed"


def test_factory_names_missing_env(monkeypatch):
    from plugins.flesh.sinews import build_sinew

    monkeypatch.delenv("PRODIGI_API_KEY", raising=False)
    with pytest.raises(SinewError, match="PRODIGI_API_KEY"):
        build_sinew("prodigi")
