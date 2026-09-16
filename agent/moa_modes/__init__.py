"""Inert mode-dispatch skeleton for the MoA postures (WO-MOA/2, Phase 1).

Every mode name resolves through one registry so the dispatch seam exists,
soaks, and is testable before any posture logic does — but ALL four modes
currently map to the ensemble passthrough sentinel: moa_loop.py behavior is
byte-identical whether or not this package is consulted.

Wiring order (per the ratified build appendix): the one-line
``resolve_mode()`` call in moa_loop.create() lands only after the golden
trace-replay gate exists to protect that diff. Until then this package has
no callers outside its tests.

The soak counter turns "shipped inert, let it soak" into a measurable
go/no-go: posture-logic PRs gate on a minimum count of live requests routed
per mode name, not a calendar guess.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any, Mapping

from nyriel_cli.moa_config import MOA_MODES, coerce_moa_mode

# Handler sentinel: "run the existing ensemble path unchanged". Real posture
# handlers replace these values one PR at a time, behind the golden gate.
ENSEMBLE_PASSTHROUGH = "ensemble_passthrough"

MODE_HANDLERS: dict[str, str] = {mode: ENSEMBLE_PASSTHROUGH for mode in MOA_MODES}

_soak_lock = threading.Lock()
_soak_counts: Counter = Counter()


def resolve_mode(preset: Mapping[str, Any] | None) -> str:
    """Resolve a preset's posture and record the routing for soak metrics.

    Junk/missing mode coerces to `ensemble` (same rule as config
    normalization), so a hand-edited config can never route to a handler
    that does not exist.
    """
    mode = coerce_moa_mode((preset or {}).get("mode"))
    with _soak_lock:
        _soak_counts[mode] += 1
    return mode


def soak_counts() -> dict[str, int]:
    """Live requests routed per mode name since process start."""
    with _soak_lock:
        return dict(_soak_counts)
