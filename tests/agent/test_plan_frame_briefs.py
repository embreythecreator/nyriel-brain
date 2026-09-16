"""WO-POSTURE/1 4.5 — NeuroArxiv per frame, capped per D-8."""

import pytest

from agent.plan_mode import PLAN_MODE_INSTRUCTIONS
from nyriel_cli.model_council import (
    PLAN_FRAME_READ_CAPS,
    PLAN_FRAMES,
    plan_frame_brief,
)


def test_caps_cover_every_frame_and_contrarian_is_cheapest():
    assert set(PLAN_FRAME_READ_CAPS) == set(PLAN_FRAMES)
    assert PLAN_FRAME_READ_CAPS["contrarian"] == min(PLAN_FRAME_READ_CAPS.values())


def test_brief_names_skill_cap_and_transport():
    brief = plan_frame_brief("contrarian")
    assert "neuroarxiv" in brief and "web_extract" in brief and "at most 6 papers" in brief
    with pytest.raises(KeyError):
        plan_frame_brief("vibes")


def test_plan_instructions_embed_one_brief_per_frame():
    assert "{FRAME_BRIEFS}" not in PLAN_MODE_INSTRUCTIONS
    for frame in PLAN_FRAMES:
        assert f"Frame `{frame}`" in PLAN_MODE_INSTRUCTIONS
    assert "`frame` field set" in PLAN_MODE_INSTRUCTIONS
