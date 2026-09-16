"""Serviceability gate — interim Word stand-in (tracked doctrine debt).

WO-F/1 says Word gates serviceability; Word has no such concept yet, and
M4 needs one function: is this location inside this sinew's service area?
zones.json holds named bounding boxes per sinew; a sinew with no entry is
unrestricted. When Word grows a serviceability domain, this module becomes
a thin HTTP client — callers don't change.

ponytail: bounding boxes, not polygons; a metro-area gate doesn't need
point-in-polygon math.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

_ZONES_PATH = Path(__file__).parent / "zones.json"


def _zones() -> dict:
    try:
        return json.loads(_ZONES_PATH.read_text()).get("sinews", {})
    except (OSError, ValueError):
        return {}


def check_serviceable(sinew: str, location: Optional[dict]) -> Tuple[bool, str]:
    """(ok, reason). Restricted sinews require lat/lon to verify — fail closed."""
    zones = _zones().get(sinew)
    if not zones:
        return True, ""
    location = location or {}
    lat, lon = location.get("lat"), location.get("lon")
    if lat is None or lon is None:
        return False, (
            f"{sinew} is territory-gated and the location has no lat/lon to "
            "verify against its service areas — falling back."
        )
    for zone in zones:
        min_lat, min_lon, max_lat, max_lon = zone["bbox"]
        if min_lat <= float(lat) <= max_lat and min_lon <= float(lon) <= max_lon:
            return True, zone.get("name", "")
    return False, f"Location is outside every {sinew} service area."
