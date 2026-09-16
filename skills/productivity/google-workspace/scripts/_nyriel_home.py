"""Resolve NYRIEL_HOME for standalone skill scripts.

Skill scripts may run outside the Nyriel process (e.g. system Python,
nix env, CI) where ``nyriel_constants`` is not importable.  This module
provides the same ``get_nyriel_home()`` and ``display_nyriel_home()``
contracts as ``nyriel_constants`` without requiring it on ``sys.path``.

When ``nyriel_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``nyriel_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``NYRIEL_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from nyriel_constants import display_nyriel_home as display_nyriel_home
    from nyriel_constants import get_nyriel_home as get_nyriel_home
except (ModuleNotFoundError, ImportError):

    def get_nyriel_home() -> Path:
        """Return the Nyriel home directory (default: ~/.nyriel).

        Mirrors ``nyriel_constants.get_nyriel_home()``."""
        val = os.environ.get("NYRIEL_HOME", "").strip()
        return Path(val) if val else Path.home() / ".nyriel"

    def display_nyriel_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``nyriel_constants.display_nyriel_home()``."""
        home = get_nyriel_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)
