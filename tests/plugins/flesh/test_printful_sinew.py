"""Printful sinew: estimate parsing, the draft-then-confirm real-spend
boundary, status mapping, cancel. Mocked HTTP only — no live calls.
Run: pytest tests/plugins/flesh/"""

import os

import pytest

os.environ.setdefault("FLESH_CONFIG_PATH", "/nonexistent")

from plugins.flesh.sinews.base import SinewError
from plugins.flesh.sinews.printful import PrintfulSinew


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json_body=None, timeout=30):
        self.calls.append({"method": method, "url": url,
                           "headers": headers, "body": json_body})
        return self.responses.pop(0)


_REQUEST = {
    "dropoff": {
        "name": "Test Operator", "address1": "1 Main St", "city": "Orlando",
        "state": "FL", "zip": "32801", "country_code": "US",
    },
    "payload": {"variant_id": 1349, "copies": 1,
                "file_url": "https://example.test/poster.png"},
}


def _sinew(responses):
    return PrintfulSinew({"api_key": "test-token"}, http_client=FakeHttp(responses))


def test_quote_parses_costs_total():
    sinew = _sinew([(200, {"code": 200, "result": {
        "costs": {"currency": "USD", "subtotal": "21.25", "shipping": "4.69",
                  "tax": "0.00", "total": "25.94"}}})])
    quote = sinew.quote(dict(_REQUEST))
    assert quote.price_usd == 25.94
    assert sinew.http.calls[0]["url"].endswith("/orders/estimate-costs")
    assert sinew.http.calls[0]["headers"]["Authorization"] == "Bearer test-token"


def test_quote_refuses_non_usd():
    sinew = _sinew([(200, {"code": 200, "result": {
        "costs": {"currency": "EUR", "total": "23.00"}}})])
    with pytest.raises(SinewError, match="not USD"):
        sinew.quote(dict(_REQUEST))


def test_dispatch_is_draft_then_confirm():
    sinew = _sinew([
        (200, {"code": 200, "result": {"id": 987, "status": "draft"}}),
        (200, {"code": 200, "result": {"id": 987, "status": "pending",
                                       "dashboard_url": "https://pf.test/987"}}),
    ])
    result = sinew.dispatch(dict(_REQUEST), idempotency_key="appr-9")
    assert result.provider_reference == "987"
    assert result.status == "assigned"
    create, confirm = sinew.http.calls
    assert create["url"].endswith("/orders")
    assert create["body"]["external_id"] == "appr-9"
    assert confirm["url"].endswith("/orders/987/confirm")


def test_failed_draft_never_confirms():
    sinew = _sinew([(400, {"code": 400, "result": "Bad file url"})])
    with pytest.raises(SinewError, match="Bad file url"):
        sinew.dispatch(dict(_REQUEST), idempotency_key="appr-10")
    assert len(sinew.http.calls) == 1  # confirm never attempted


def test_track_maps_fulfilled_with_final_cost():
    sinew = _sinew([(200, {"code": 200, "result": {
        "id": 987, "status": "fulfilled",
        "costs": {"currency": "USD", "total": "25.94"}}})])
    track = sinew.track("987")
    assert track.status == "completed"
    assert track.final_cost_usd == 25.94


def test_cancel_deletes_order():
    sinew = _sinew([(200, {"code": 200, "result": {"id": 987, "status": "canceled"}})])
    result = sinew.cancel("987")
    assert result.status == "canceled"
    assert sinew.http.calls[0]["method"] == "DELETE"


def test_factory_names_missing_env(monkeypatch):
    from plugins.flesh.sinews import build_sinew

    monkeypatch.delenv("PRINTFUL_API_KEY", raising=False)
    with pytest.raises(SinewError, match="PRINTFUL_API_KEY"):
        build_sinew("printful")
