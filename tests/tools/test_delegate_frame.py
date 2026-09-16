"""WO-POSTURE/1 4.2 — per-task plan ``frame`` on delegate_task."""

from unittest.mock import patch

import pytest

from nyriel_cli.model_council import PLAN_FRAMES
from tools.delegate_tool import DELEGATE_TASK_SCHEMA, _resolve_frame_credentials


def test_schema_frame_enum_is_the_plan_frame_roster():
    props = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
    assert tuple(props["frame"]["enum"]) == PLAN_FRAMES


def test_unknown_frame_raises_keyerror():
    with pytest.raises(KeyError):
        _resolve_frame_credentials("vibes")


def test_frame_credentials_pin_the_seat_model():
    slot = {"frame": "contrarian", "role": "writing", "provider": "openrouter", "model": "seat-model"}
    runtime = {"provider": "openrouter", "base_url": "https://or/v1", "api_key": "k", "api_mode": "chat_completions"}
    with patch("nyriel_cli.model_council.resolve_plan_frame_slot", return_value=slot), patch(
        "nyriel_cli.runtime_provider.resolve_runtime_provider", return_value=runtime
    ):
        creds = _resolve_frame_credentials("contrarian")
    assert creds == {
        "model": "seat-model",
        "provider": "openrouter",
        "base_url": "https://or/v1",
        "api_key": "k",
        "api_mode": "chat_completions",
    }


def test_seat_without_key_is_a_valueerror():
    slot = {"frame": "contrarian", "role": "writing", "provider": "openrouter", "model": "m"}
    with patch("nyriel_cli.model_council.resolve_plan_frame_slot", return_value=slot), patch(
        "nyriel_cli.runtime_provider.resolve_runtime_provider", return_value={"api_key": ""}
    ):
        with pytest.raises(ValueError):
            _resolve_frame_credentials("contrarian")
