"""Flesh config pack — static file, defaults DENY (WO-F/1 §4).

Phase 1 has exactly one angel mounted, so the "per-angel config pack" is a
JSON file the spine reads, not a grant system. Missing file, missing angel,
missing verb/sinew entry — all deny. Provider credentials live in env vars
(DOORDASH_*, UBER_*), never in this file and never in flesh.db.

~/.nyriel/flesh_config.json:
{
  "approval_ttl_seconds": 300,
  "angels": {
    "nyriel": {
      "permitted_verbs": ["move_object"],
      "permitted_sinews": ["doordash_drive"],
      "max_spend_usd_per_dispatch": 25,
      "max_spend_usd_per_day": 50
    }
  }
}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_APPROVAL_TTL_SECONDS = 300


def config_path() -> Path:
    override = os.environ.get("FLESH_CONFIG_PATH")
    if override:
        return Path(override)
    from nyriel_constants import get_nyriel_home

    return Path(get_nyriel_home()) / "flesh_config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def approval_ttl_seconds(cfg: Optional[dict] = None) -> float:
    cfg = cfg if cfg is not None else load_config()
    try:
        return float(cfg.get("approval_ttl_seconds", DEFAULT_APPROVAL_TTL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_TTL_SECONDS


def angel_pack(angel: str, cfg: Optional[dict] = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    angels = cfg.get("angels")
    if not isinstance(angels, dict):
        return {}
    pack = angels.get(angel)
    return pack if isinstance(pack, dict) else {}


def permission_error(
    angel: str,
    verb: str,
    sinew: str,
    spend_usd: float,
    daily_spent_usd: float,
) -> Optional[str]:
    """Return a deny reason, or None if this dispatch is permitted."""
    pack = angel_pack(angel)
    if not pack:
        return (
            f"Flesh config pack denies everything for angel '{angel}'. "
            f"Grant verbs/sinews in {config_path()} (defaults are deny)."
        )
    if verb not in (pack.get("permitted_verbs") or []):
        return f"Verb '{verb}' is not granted to angel '{angel}'."
    if sinew not in (pack.get("permitted_sinews") or []):
        return f"Sinew '{sinew}' is not granted to angel '{angel}'."
    per_dispatch = float(pack.get("max_spend_usd_per_dispatch", 0))
    if spend_usd > per_dispatch:
        return (
            f"Spend ${spend_usd:.2f} exceeds the per-dispatch ceiling "
            f"${per_dispatch:.2f} for angel '{angel}'."
        )
    per_day = float(pack.get("max_spend_usd_per_day", 0))
    if daily_spent_usd + spend_usd > per_day:
        return (
            f"Spend ${spend_usd:.2f} would exceed the daily ceiling "
            f"${per_day:.2f} (${daily_spent_usd:.2f} already dispatched today)."
        )
    return None


def day_start_epoch() -> float:
    """Local-midnight epoch for the daily aggregate window."""
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
