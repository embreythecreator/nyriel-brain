"""WO-MOA/2: posture config keys normalize with behavior-preserving defaults.

Callers: pytest only. The keys are inert (moa_loop.py does not read them yet);
these tests pin the schema so the mode-aware fan-out lands against a stable
contract.
"""

from nyriel_cli.moa_config import (
    MOA_MODES,
    coerce_moa_mode,
    normalize_moa_config,
    resolve_moa_preset,
)


def test_default_preset_is_ensemble_with_inert_posture_keys():
    preset = resolve_moa_preset({}, None)
    assert preset["mode"] == "ensemble"
    assert preset["debate_rounds"] == 2
    assert preset["debate_round_budget"] is None
    assert preset["self_consistency_samples"] == 3
    assert preset["self_consistency_fallback"] == "error"


def test_mode_coercion_accepts_all_postures_and_rejects_junk():
    for mode in MOA_MODES:
        assert coerce_moa_mode(mode) == mode
    assert coerce_moa_mode("Debate") == "debate"
    assert coerce_moa_mode("self-consistency") == "self_consistency"
    assert coerce_moa_mode("SELF_CONSISTENCY") == "self_consistency"
    assert coerce_moa_mode("moa") == "ensemble"
    assert coerce_moa_mode(None) == "ensemble"
    assert coerce_moa_mode(42) == "ensemble"


def test_posture_keys_normalize_and_clamp_from_raw_config():
    cfg = normalize_moa_config(
        {
            "presets": {
                "default": {
                    "mode": "debate",
                    "debate_rounds": 0,
                    "debate_round_budget": "12",
                    "self_consistency_samples": 1,
                    "self_consistency_fallback": "ENSEMBLE",
                }
            }
        }
    )
    preset = cfg["presets"]["default"]
    assert preset["mode"] == "debate"
    assert preset["debate_rounds"] == 1  # clamped to >= 1
    assert preset["debate_round_budget"] == 12
    assert preset["self_consistency_samples"] == 2  # clamped to >= 2
    assert preset["self_consistency_fallback"] == "ensemble"


def test_sc_fallback_junk_defaults_to_error():
    cfg = normalize_moa_config(
        {"presets": {"default": {"self_consistency_fallback": "retry"}}}
    )
    assert cfg["presets"]["default"]["self_consistency_fallback"] == "error"
