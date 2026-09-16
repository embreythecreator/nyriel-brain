import logging

from nyriel_cli.model_council import (
    FALLBACK_ROLE,
    MODEL_COUNCIL_ROLES,
    ORCHESTRATOR_ROLE,
    PLAN_FRAMES,
    WING_ROLES,
    default_model_council_config,
    fallback_chain_for_role,
    model_council_moa_preset,
    resolve_model_council_config,
    resolve_plan_frame_slot,
    validate_model_council_config,
    route_model_role,
)


def test_default_council_is_crown_plus_six_wings_and_fallback():
    cfg = default_model_council_config()

    assert ORCHESTRATOR_ROLE in cfg["roles"]
    assert FALLBACK_ROLE in cfg["roles"]
    assert len(WING_ROLES) == 6
    assert set(cfg["roles"]) == set(MODEL_COUNCIL_ROLES)
    assert cfg["roles"][ORCHESTRATOR_ROLE]["name"] == "Crown"


def test_moa_preset_uses_six_wings_under_crown():
    preset = model_council_moa_preset()

    assert len(preset["reference_models"]) == 6
    assert [slot["role"] for slot in preset["reference_models"]] == list(WING_ROLES)
    assert preset["aggregator"]["role"] == ORCHESTRATOR_ROLE
    assert preset["aggregator"]["name"] == "Crown"


def test_env_overrides_role_models_and_providers():
    cfg = resolve_model_council_config(
        environ={
            "NYRIEL_ORCHESTRATOR_MODEL": "custom-fable",
            "NYRIEL_ORCHESTRATOR_PROVIDER": "custom:crown",
            "NYRIEL_REASONING_MODEL": "openai/gpt-test",
            "NYRIEL_REASONING_PROVIDER": "openrouter",
            "NYRIEL_MODEL_COUNCIL_ENABLED": "false",
        }
    )

    assert cfg["enabled"] is False
    assert cfg["roles"]["orchestrator"]["provider"] == "custom:crown"
    assert cfg["roles"]["orchestrator"]["model"] == "custom-fable"
    assert cfg["roles"]["reasoning"]["provider"] == "openrouter"
    assert cfg["roles"]["reasoning"]["model"] == "openai/gpt-test"


def test_config_overrides_are_merged_per_role():
    cfg = resolve_model_council_config(
        {
            "roles": {
                "writing": {
                    "provider": "anthropic",
                    "model": "claude-opus-custom",
                }
            }
        },
        environ={},
    )

    assert cfg["roles"]["writing"]["provider"] == "anthropic"
    assert cfg["roles"]["writing"]["model"] == "claude-opus-custom"
    assert cfg["roles"]["writing"]["name"] == "Writing Wing"


def test_router_examples_from_work_order():
    cases = [
        ("Write a cinematic scene in Nyriel's voice.", "writing"),
        ("Debug this TypeScript function.", "code_reasoning"),
        ("Analyze this screenshot.", "multimodal"),
        ("Summarize this 200-page document.", "long_context_agent"),
        ("Translate and explain this Chinese paragraph.", "chinese_generalist"),
        ("Design the architecture for my agent stack.", "reasoning"),
    ]

    for task, expected_role in cases:
        assert route_model_role(task, environ={})["selectedRole"] == expected_role


def test_router_detects_cjk_text():
    decision = route_model_role("解释这段文字的语气", environ={})

    assert decision["selectedRole"] == "chinese_generalist"
    assert "CJK" in decision["reason"]


def test_disabled_council_keeps_old_behavior_available():
    decision = route_model_role("", {"enabled": False}, environ={})

    assert decision["enabled"] is False
    assert decision["selectedRole"] == ""
    assert decision["selectedModel"] == ""


def test_fallback_chain_is_specialist_then_fallback_then_crown():
    chain = fallback_chain_for_role("code_reasoning", environ={})

    assert [slot["role"] for slot in chain] == [
        "code_reasoning",
        "fallback",
        "orchestrator",
    ]


def test_router_log_hides_reason_unless_debug(caplog):
    caplog.set_level(logging.INFO, logger="nyriel_cli.model_council")

    route_model_role("Debug this TypeScript function.", {"debug": False}, environ={})
    assert '"reason": ""' in caplog.text

    caplog.clear()
    route_model_role("Debug this TypeScript function.", {"debug": True}, environ={})
    assert "Task mentions code" in caplog.text


def test_plan_frames_each_resolve_to_a_wing_seat_never_the_crown():
    """WO-POSTURE/1 4.1: five frames, each pinned to a wing; unknown frame raises."""
    import pytest

    assert len(PLAN_FRAMES) == 5 and len(set(PLAN_FRAMES)) == 5
    crown = default_model_council_config()["roles"][ORCHESTRATOR_ROLE]["model"]
    for frame in PLAN_FRAMES:
        slot = resolve_plan_frame_slot(frame)
        assert slot["frame"] == frame and slot["role"] in WING_ROLES
        assert slot["provider"] and slot["model"] and slot["model"] != crown
    with pytest.raises(KeyError):
        resolve_plan_frame_slot("vibes")
    assert validate_model_council_config() == []


def test_plan_frame_slot_follows_role_overrides():
    slot = resolve_plan_frame_slot(
        "contrarian", {"roles": {"writing": {"model": "x-ai/grok-4.6"}}}
    )
    assert slot["role"] == "writing" and slot["model"] == "x-ai/grok-4.6"
