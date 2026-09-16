"""Tests for the Nous-nyriel-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"nyriel"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``nyriel-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "nyriel" tag namespace.

``is_nous_nyriel_non_agentic`` should only match the actual Nyriel
nyriel-3 / Nyriel-4 chat family.
"""

from __future__ import annotations

import pytest

from nyriel_cli.model_switch import (
    _NYRIEL_MODEL_WARNING,
    _check_nyriel_model_warning,
    is_nous_nyriel_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "embreythecreator/nyriel-3-Llama-3.1-70B",
        "embreythecreator/nyriel-3-Llama-3.1-405B",
        "nyriel-3",
        "nyriel-3",
        "nyriel-4",
        "nyriel-4-405b",
        "nyriel_4_70b",
        "openrouter/nyriel3:70b",
        "openrouter/embreythecreator/nyriel-4-405b",
        "embreythecreator/nyriel3",
        "nyriel-3.1",
    ],
)
def test_matches_real_nous_nyriel_chat_models(model_name: str) -> None:
    assert is_nous_nyriel_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Nyriel 3/4"
    )
    assert _check_nyriel_model_warning(model_name) == _NYRIEL_MODEL_WARNING


