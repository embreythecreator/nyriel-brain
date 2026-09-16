"""Resolve NYRIEL_HOME for standalone skill scripts.

Skill scripts may run outside the Nyriel process (system Python, nix env,
CI) where ``nyriel_constants`` is not importable.  This module provides the
same ``get_nyriel_home()`` contract without requiring it on ``sys.path``.

When ``nyriel_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from nyriel_constants import get_nyriel_home as get_nyriel_home
except (ModuleNotFoundError, ImportError):

    def get_nyriel_home() -> Path:
        """Return the Nyriel home directory (default: ``~/.nyriel``)."""
        val = os.environ.get("NYRIEL_HOME", "").strip()
        return Path(val) if val else Path.home() / ".nyriel"
