import pytest

from agent.errors import MoAPresetNotFoundError
from nyriel_cli.moa_config import (
    DEFAULT_MOA_AGGREGATOR,
    DEFAULT_MOA_PRESET_NAME,
    DEFAULT_MOA_REFERENCE_MODELS,
    build_moa_turn_prompt,
    decode_moa_turn,
    exact_moa_preset_name,
    normalize_moa_config,
    resolve_moa_preset,
    set_active_moa_preset,
)


def test_moa_slot_picker_excludes_unconfigured_providers(monkeypatch):
    from nyriel_cli import moa_cmd

    captured = {}
    monkeypatch.setattr(moa_cmd, "load_picker_context", lambda: object())

    def fake_build(_context, **kwargs):
        captured.update(kwargs)
        return {
            "providers": [
                {"slug": "moa", "models": ["default"]},
                {"slug": "opencode-go", "models": ["deepseek-v4-pro"]},
            ]
        }

    monkeypatch.setattr(moa_cmd, "build_models_payload", fake_build)

    assert [row["slug"] for row in moa_cmd._model_options()] == ["opencode-go"]
    assert captured["include_unconfigured"] is False


def _enabled_refs(refs):
    return [{**slot, "enabled": True} for slot in refs]


def test_normalize_moa_config_uses_default_named_preset():
    cfg = normalize_moa_config({})

    assert cfg["default_preset"] == DEFAULT_MOA_PRESET_NAME
    assert list(cfg["presets"]) == [DEFAULT_MOA_PRESET_NAME]
    assert cfg["reference_models"] == _enabled_refs(DEFAULT_MOA_REFERENCE_MODELS)
    assert cfg["aggregator"] == DEFAULT_MOA_AGGREGATOR






def test_normalize_moa_config_tolerates_non_numeric_values():
    """Non-numeric strings in hand-edited config.yaml must degrade to defaults
    instead of crashing normalize_moa_config with ValueError."""
    cfg = normalize_moa_config(
        {
            "presets": {
                "broken": {
                    "max_tokens": "notanumber",
                    "reference_temperature": "hot",
                    "aggregator_temperature": "",
                }
            }
        }
    )

    preset = cfg["presets"]["broken"]
    assert preset["max_tokens"] == 4096
    # Unparseable/blank temperatures degrade to None = "don't send the
    # parameter; provider default applies" (matching single-model behavior),
    # not to a hardcoded sampling value.
    assert preset["reference_temperature"] is None
    assert preset["aggregator_temperature"] is None


def test_normalize_moa_config_tolerates_non_list_reference_models():
    """A hand-edited scalar reference_models must degrade to defaults instead of
    crashing normalize_moa_config with TypeError (symmetric with the non-numeric
    scalar-field tolerance)."""
    cfg = normalize_moa_config(
        {"presets": {"broken": {"reference_models": 2}}}
    )
    assert cfg["presets"]["broken"]["reference_models"] == DEFAULT_MOA_REFERENCE_MODELS


def test_normalize_moa_config_wraps_bare_dict_reference_models():
    """A single reference slot written without the list wrapper is rescued."""
    cfg = normalize_moa_config(
        {"presets": {"p": {"reference_models": {"provider": "openai", "model": "gpt-4o"}}}}
    )
    assert cfg["presets"]["p"]["reference_models"] == [{"provider": "openai", "model": "gpt-4o"}]


def test_normalize_moa_config_preserves_council_slot_metadata():
    cfg = normalize_moa_config(
        {
            "reference_models": [
                {
                    "provider": "openrouter",
                    "model": "deepseek/deepseek-v4-pro",
                    "role": "code_reasoning",
                    "wing": "Code Reasoning Wing",
                    "purpose": "Debugging and structured execution.",
                }
            ],
            "aggregator": {
                "provider": "openrouter",
                "model": "anthropic/claude-fable-5",
                "role": "orchestrator",
                "name": "Crown",
            },
        }
    )

    ref = cfg["reference_models"][0]
    agg = cfg["aggregator"]
    assert ref["role"] == "code_reasoning"
    assert ref["wing"] == "Code Reasoning Wing"
    assert ref["purpose"] == "Debugging and structured execution."
    assert agg["role"] == "orchestrator"
    assert agg["name"] == "Crown"


def test_normalize_moa_config_preserves_slot_reasoning_effort():
    cfg = normalize_moa_config(
        {
            "presets": {
                "p": {
                    "reference_models": [
                        {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "LOW"},
                        {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": False},
                        {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "nonsense"},
                        {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "ultra"},
                    ],
                    "aggregator": {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
                }
            }
        }
    )

    preset = cfg["presets"]["p"]
    assert preset["reference_models"][0]["reasoning_effort"] == "low"
    assert preset["reference_models"][1]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in preset["reference_models"][2]
    assert preset["reference_models"][3]["reasoning_effort"] == "ultra"
    assert preset["aggregator"]["reasoning_effort"] == "xhigh"


def test_normalize_moa_config_coerces_numeric_strings():
    """Valid numeric strings (e.g. from YAML round-trip) must coerce correctly."""
    cfg = normalize_moa_config({"max_tokens": "8192", "reference_temperature": "0.9"})

    preset = cfg["presets"][DEFAULT_MOA_PRESET_NAME]
    assert preset["max_tokens"] == 8192
    assert preset["reference_temperature"] == 0.9


def test_normalize_moa_config_coerces_float_max_tokens():
    """max_tokens: 4096.0 (float from YAML) must coerce to int."""
    cfg = normalize_moa_config({"max_tokens": 4096.0})
    assert cfg["presets"][DEFAULT_MOA_PRESET_NAME]["max_tokens"] == 4096

    cfg2 = normalize_moa_config({"max_tokens": "4096.5"})
    assert cfg2["presets"][DEFAULT_MOA_PRESET_NAME]["max_tokens"] == 4096


def test_exact_preset_matching_is_not_fuzzy():
    config = {"presets": {"coding": {}, "review": {}}}

    assert exact_moa_preset_name(config, "coding") == "coding"
    assert exact_moa_preset_name(config, "cod") is None
    assert exact_moa_preset_name(config, "coding please fix this") is None


def test_exact_preset_matching_skips_disabled_presets():
    """A disabled preset must not match the implicit bare-name switch path.

    Regression for #55187: with ``enabled: false`` presets, a plain model
    switch whose name collides with a preset key (e.g. ``default``) silently
    pivoted the session onto the MoA virtual provider. The per-preset
    ``enabled`` opt-out must gate this implicit match.
    """
    config = {
        "presets": {
            "default": {"enabled": False},
            "klo": {"enabled": False},
        },
    }
    assert exact_moa_preset_name(config, "default") is None
    assert exact_moa_preset_name(config, "klo") is None






def test_resolve_missing_moa_preset_has_actionable_error():
    cfg = {
        "default_preset": "日常对话-高峰",
        "presets": {"日常对话-高峰": {}, "日常对话-非高峰": {}},
    }

    with pytest.raises(MoAPresetNotFoundError) as exc_info:
        resolve_moa_preset(cfg, "日常对话-高峰期")

    message = str(exc_info.value)
    assert "日常对话-高峰期" in message
    assert "日常对话-高峰" in message
    assert "日常对话-非高峰" in message
    assert "nyriel moa list" in message


def test_missing_moa_preset_is_non_retryable():
    from agent.error_classifier import FailoverReason, classify_api_error

    result = classify_api_error(
        MoAPresetNotFoundError("MoA preset 'old' was not found"),
        provider="moa",
        model="old",
    )

    assert result.reason == FailoverReason.model_not_found
    assert result.retryable is False
    assert result.should_fallback is False








def _preset(**extra):
    base = {
        "reference_models": [{"provider": "openrouter", "model": "anthropic/claude-opus-4.8"}],
        "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
    }
    base.update(extra)
    return {"default_preset": "p", "presets": {"p": base}}






# ── validate_moa_payload (write-boundary validation, #64156) ─────────────────
#
# normalize_moa_config is deliberately tolerant at READ time (hand-edited
# configs degrade to defaults). validate_moa_payload is the strict WRITE-time
# counterpart: it must flag exactly the payloads normalize would silently
# repair, so API save paths reject them instead of corrupting user config.


def _valid_preset_payload():
    return {
        "reference_models": [{"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"}],
        "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
    }




def test_validate_moa_payload_agrees_with_clean_slot():
    """Contract: a payload validate accepts must survive normalize UNCHANGED in
    its slots — validate and _clean_slot can never disagree (else a payload
    could pass validation and still be swapped for defaults)."""
    from nyriel_cli.moa_config import validate_moa_payload

    payload = {"presets": {"p": _valid_preset_payload()}}
    assert validate_moa_payload(payload) == []

    cfg = normalize_moa_config(payload)
    # Slots survive with only the canonical enabled=True default added — no
    # provider/model swap, no defaults substitution.
    assert cfg["presets"]["p"]["reference_models"] == _enabled_refs(payload["presets"]["p"]["reference_models"])
    assert cfg["presets"]["p"]["aggregator"] == payload["presets"]["p"]["aggregator"]


# ── Per-slot max_tokens ────────────────────────────────────────────────────






# --- fanout cadence normalization (every_n) ---








# --- privacy_filter normalization ---






