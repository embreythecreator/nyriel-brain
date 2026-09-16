"""Tolerant JSON extraction from model replies (WO-MOA/2).

Shared by every posture that asks a model for categorical output: CoVe
verdicts, debate stances, self-consistency samples. One implementation so
the postures cannot drift apart on what counts as parseable — a reply the
debate protocol accepts must be a reply the verify pass would accept.

Returns None rather than raising: an unparseable reply means that model
abstains, never that the turn dies.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)


def extract_json_payload(text, opener: str):
    """Pull the first JSON array (``[``) or object (``{``) out of a reply.

    Tolerates code fences and surrounding prose by slicing from the first
    opener to the last matching closer. None on anything unparseable.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = _FENCE_RE.sub("", text.strip())
    closer = "]" if opener == "[" else "}"
    start = stripped.find(opener)
    end = stripped.rfind(closer)
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(stripped[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
