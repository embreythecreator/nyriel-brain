"""Inert dispatch skeleton tests (WO-MOA/2 Phase 1)."""

from agent.moa_modes import (
    ENSEMBLE_PASSTHROUGH,
    MODE_HANDLERS,
    resolve_mode,
    soak_counts,
)
from nyriel_cli.moa_config import MOA_MODES


def test_every_mode_routes_to_ensemble_passthrough():
    assert set(MODE_HANDLERS) == set(MOA_MODES)
    assert all(h == ENSEMBLE_PASSTHROUGH for h in MODE_HANDLERS.values())


def test_resolve_mode_coerces_and_defaults():
    assert resolve_mode({"mode": "debate"}) == "debate"
    assert resolve_mode({"mode": "Self-Consistency"}) == "self_consistency"
    assert resolve_mode({"mode": "garbage"}) == "ensemble"
    assert resolve_mode({}) == "ensemble"
    assert resolve_mode(None) == "ensemble"
    # Every resolvable mode has a registered handler — junk can never route
    # off the registry.
    assert resolve_mode({"mode": "verify"}) in MODE_HANDLERS


def test_soak_counter_records_routing():
    before = soak_counts().get("debate", 0)
    resolve_mode({"mode": "debate"})
    resolve_mode({"mode": "debate"})
    assert soak_counts()["debate"] == before + 2
