"""Flesh spine: approval lifecycle, consume-then-dispatch atomicity, digest
determinism, settle variance flagging. Run: pytest tests/plugins/flesh/"""

import os
import time

import pytest

os.environ.setdefault("FLESH_CONFIG_PATH", "/nonexistent")

from plugins.flesh.approval import FleshApprovalStore
from plugins.flesh.db import FleshDB
from plugins.flesh.digest import quote_digest


@pytest.fixture()
def db(tmp_path):
    return FleshDB(tmp_path / "flesh.db")


@pytest.fixture()
def approved(db):
    store = FleshApprovalStore(db)
    quote = {
        "sinew": "doordash_drive", "price_usd": 9.99, "eta_minutes": 30,
        "provider_quote_id": "x1", "expires_at": time.time() + 270,
        "cancellation_terms": "Free until pickup", "raw": {},
    }
    route = {"pickup": {"address": "A"}, "dropoff": {"address": "B"}}
    rec = store.create("sess1", verb="move_object", sinew="doordash_drive",
                       quote=quote, route_or_task=route)
    return store, rec


def test_dispatch_requires_approval(db, approved):
    _, rec = approved
    got, err = db.consume_and_create_dispatch(rec["approval_id"], rec["quote_digest"], "{}")
    assert got is None and "not approved" in err


def test_digest_binds_the_quote(db, approved):
    store, rec = approved
    _, err = store.approve("sess1", rec["approval_id"][:8])
    assert err is None
    got, err = db.consume_and_create_dispatch(rec["approval_id"], "tampered", "{}")
    assert got is None and "digest mismatch" in err.lower()


def test_consume_is_one_shot(db, approved):
    store, rec = approved
    store.approve("sess1", rec["approval_id"][:8])
    _, err = db.consume_and_create_dispatch(rec["approval_id"], rec["quote_digest"], "{}")
    assert err is None
    got, err = db.consume_and_create_dispatch(rec["approval_id"], rec["quote_digest"], "{}")
    assert got is None and "consumed" in err.lower()
    assert len(db.open_dispatches()) == 1


def test_expired_approval_refused(db):
    store = FleshApprovalStore(db)
    quote = {"sinew": "s", "price_usd": 1.0, "eta_minutes": None,
             "provider_quote_id": None, "expires_at": time.time() - 1,
             "cancellation_terms": "", "raw": {}}
    rec = store.create("sess1", verb="move_object", sinew="s", quote=quote, route_or_task={})
    _, err = store.approve("sess1", rec["approval_id"][:8])
    assert err is not None and "expired" in err.lower()


def test_deny_supersedes(db, approved):
    store, rec = approved
    _, err = store.deny("sess1", rec["approval_id"][:8])
    assert err is None
    assert store.pending_for_session("sess1") is None


def test_settle_variance_flags_beyond_15pct(db, approved):
    store, rec = approved
    store.approve("sess1", rec["approval_id"][:8])
    db.consume_and_create_dispatch(rec["approval_id"], rec["quote_digest"], "{}")
    db.update_dispatch(rec["approval_id"], status="completed")
    settle = db.insert_settle(rec["approval_id"], 12.50, {})  # +25% vs 9.99
    assert settle["flagged"] is True
    settle_ok = db.insert_settle("other", 0, {})  # no-op path
    assert settle_ok["flagged"] is False


def test_digest_normalizes_whitespace_and_price():
    route = {"pickup": {"address": "A"}}
    a = quote_digest("move_object", "dd", 9.99, route, "Free  until   pickup")
    b = quote_digest("move_object", "dd", 9.990001, route, "Free until pickup")
    assert a == b


def test_transit_deeplinks_compose():
    from plugins.flesh.sinews.transit import MapsHandoffSinew, UberDeeplinkSinew

    request = {"pickup": {"address": "1 Main St, Orlando FL"},
               "dropoff": {"address": "2 Oak Ave, Orlando FL"}}
    uber = UberDeeplinkSinew({}).quote(request)
    assert uber.price_usd == 0.0
    assert uber.raw["deeplink_url"].startswith("https://m.uber.com/ul/?action=setPickup")
    assert "dropoff%5Bformatted_address%5D=2+Oak+Ave" in uber.raw["deeplink_url"]
    maps = MapsHandoffSinew({}).quote(request)
    assert maps.raw["deeplink_url"].startswith("https://www.google.com/maps/dir/?")


def test_territory_gate():
    from plugins.flesh.territory import check_serviceable

    ok, zone = check_serviceable("waymo", {"lat": 33.45, "lon": -112.07})
    assert ok and zone == "Phoenix metro"
    ok, reason = check_serviceable("waymo", {"lat": 28.54, "lon": -81.38})  # Orlando
    assert not ok and "outside" in reason
    ok, reason = check_serviceable("waymo", {"address": "no coords"})
    assert not ok  # fail closed without lat/lon
    ok, _ = check_serviceable("uber_deeplink", {"address": "anywhere"})
    assert ok  # unrestricted sinews pass


def test_handoff_confirm_payload_carries_deeplink(db):
    from dataclasses import asdict

    from plugins.flesh.sinews.transit import UberDeeplinkSinew

    store = FleshApprovalStore(db)
    quote = UberDeeplinkSinew({}).quote({"dropoff": {"address": "2 Oak Ave"}})
    store.create("sessT", verb="move_person", sinew="uber_deeplink",
                 quote=asdict(quote), route_or_task={"dropoff": {"address": "2 Oak Ave"}})
    payload = store.confirm_payload("sessT")
    assert payload["is_deeplink"] is True
    assert payload["deeplink_url"].startswith("https://m.uber.com/ul/")
