"""Sinew registry — name -> (verb, factory). Vary the limbs, not the spine.

Factories build configured Sinew instances from env vars. An unconfigured
sinew raises SinewError at build time with the missing env names — never a
silent fallback to another provider (WO-F/1 §6 silent-failure watch).
"""

from __future__ import annotations

import os

from plugins.flesh.sinews.base import Sinew, SinewError


def _build_doordash_drive() -> Sinew:
    from plugins.flesh.sinews.doordash_drive import DoorDashDriveSinew

    cfg = {
        "developer_id": os.environ.get("DOORDASH_DEVELOPER_ID", ""),
        "key_id": os.environ.get("DOORDASH_KEY_ID", ""),
        "signing_secret": os.environ.get("DOORDASH_SIGNING_SECRET", ""),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise SinewError(
            "doordash_drive is not configured; set env "
            + ", ".join(f"DOORDASH_{k.upper()}" for k in missing)
        )
    return DoorDashDriveSinew(cfg)


def _build_uber_direct() -> Sinew:
    from plugins.flesh.sinews.uber_direct import UberDirectSinew

    cfg = {
        "customer_id": os.environ.get("UBER_CUSTOMER_ID", ""),
        "client_id": os.environ.get("UBER_CLIENT_ID", ""),
        "client_secret": os.environ.get("UBER_CLIENT_SECRET", ""),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise SinewError(
            "uber_direct is not configured; set env "
            + ", ".join(f"UBER_{k.upper()}" for k in missing)
        )
    return UberDirectSinew(cfg)


def _build_mturk() -> Sinew:
    from plugins.flesh.sinews.mturk import MTurkSinew

    # AWS creds resolve via the standard boto3 chain; sandbox by default.
    return MTurkSinew({})


def _build_prodigi() -> Sinew:
    from plugins.flesh.sinews.prodigi import ProdigiSinew

    api_key = os.environ.get("PRODIGI_API_KEY", "")
    if not api_key:
        raise SinewError(
            "prodigi is not configured; set env PRODIGI_API_KEY "
            "(default host is the sandbox — set PRODIGI_API_BASE for live)"
        )
    return ProdigiSinew({
        "api_key": api_key,
        "api_base": os.environ.get("PRODIGI_API_BASE", ""),
    })


def _build_printful() -> Sinew:
    from plugins.flesh.sinews.printful import PrintfulSinew

    api_key = os.environ.get("PRINTFUL_API_KEY", "")
    if not api_key:
        raise SinewError(
            "printful is not configured; set env PRINTFUL_API_KEY "
            "(optional PRINTFUL_STORE_ID for account-level tokens)"
        )
    return PrintfulSinew({
        "api_key": api_key,
        "store_id": os.environ.get("PRINTFUL_STORE_ID", ""),
    })


def _build_uber_deeplink() -> Sinew:
    from plugins.flesh.sinews.transit import UberDeeplinkSinew

    return UberDeeplinkSinew({})


def _build_maps_handoff() -> Sinew:
    from plugins.flesh.sinews.transit import MapsHandoffSinew

    return MapsHandoffSinew({})


# name -> (verb, factory)
SINEW_FACTORIES = {
    "doordash_drive": ("move_object", _build_doordash_drive),
    "uber_direct": ("move_object", _build_uber_direct),
    "prodigi": ("make_object", _build_prodigi),
    "printful": ("make_object", _build_printful),
    "mturk": ("perform_digital_task", _build_mturk),
    "uber_deeplink": ("move_person", _build_uber_deeplink),
    "maps_handoff": ("move_person", _build_maps_handoff),
}


def build_sinew(name: str) -> Sinew:
    entry = SINEW_FACTORIES.get(name)
    if entry is None:
        raise SinewError(f"Unknown sinew '{name}'. Known: {sorted(SINEW_FACTORIES)}")
    return entry[1]()


def sinew_names_for_verb(verb: str, permitted: list) -> list:
    return [
        name
        for name, (sinew_verb, _) in SINEW_FACTORIES.items()
        if sinew_verb == verb and name in permitted
    ]
