"""Transit v0 — C-class handoff sinews for move_person (WO-F/1 §3.1).

No API dispatch exists for these providers: the confirm card's tap IS the
T3 event (it opens the composed deeplink; the provider's own UX takes over).
Flesh records intent only. Price is $0 from Flesh's side — the operator pays
inside the provider app.
"""

from __future__ import annotations

import urllib.parse

from plugins.flesh.sinews.base import HandoffSinew, Quote

_HANDOFF_TERMS = "Completed in the provider's app; Flesh records intent only and charges nothing."


class UberDeeplinkSinew(HandoffSinew):
    """Pre-filled Uber universal link (documented Trip Deeplink format).
    Being https://, it opens the Uber app via app-link association when
    installed, else falls back to m.uber.com."""

    name = "uber_deeplink"
    verb = "move_person"

    def compose_deeplink(self, request: dict) -> str:
        pickup = request.get("pickup") or {}
        dropoff = request.get("dropoff") or {}
        params = {"action": "setPickup"}
        if pickup.get("address"):
            params["pickup[formatted_address]"] = pickup["address"]
        else:
            params["pickup"] = "my_location"
        if dropoff.get("address"):
            params["dropoff[formatted_address]"] = dropoff["address"]
        return "https://m.uber.com/ul/?" + urllib.parse.urlencode(params)

    def quote(self, request: dict) -> Quote:
        return Quote(
            sinew=self.name,
            price_usd=0.0,
            eta_minutes=None,
            provider_quote_id=None,
            expires_at=None,
            cancellation_terms=_HANDOFF_TERMS,
            raw={"deeplink_url": self.compose_deeplink(request)},
        )


class MapsHandoffSinew(HandoffSinew):
    """Universal fallback — Google Maps directions. Zero cost, always available."""

    name = "maps_handoff"
    verb = "move_person"

    def compose_deeplink(self, request: dict) -> str:
        pickup = request.get("pickup") or {}
        dropoff = request.get("dropoff") or {}
        params = {"api": "1", "travelmode": "transit"}
        if pickup.get("address"):
            params["origin"] = pickup["address"]
        if dropoff.get("address"):
            params["destination"] = dropoff["address"]
        return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)

    def quote(self, request: dict) -> Quote:
        return Quote(
            sinew=self.name,
            price_usd=0.0,
            eta_minutes=None,
            provider_quote_id=None,
            expires_at=None,
            cancellation_terms=_HANDOFF_TERMS,
            raw={"deeplink_url": self.compose_deeplink(request)},
        )
