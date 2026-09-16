"""Rung contract for deep-research plans (WO-POSTURE/1 4.11). Pure.

The deepen phase ends every rung (plan step) with a line

    verification_evidence: [tests, static, artifact_exercised]

drawn from the fixed enum below. Act mode reads those as the pass criteria
for that rung — nothing else counts as "done".
"""

from __future__ import annotations

import re
from typing import Any

VERIFICATION_EVIDENCE: tuple[str, ...] = (
    "tests",
    "regression",
    "static",
    "artifact_exercised",
    "temp_removed",
    "no_stray_ports",
)

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_EVIDENCE = re.compile(r"^\s*(?:[-*]\s*)?`?verification_evidence`?\s*:\s*\[?([^\]\n]*)\]?\s*`?\s*$", re.I)


def parse_rung_evidence(content: Any) -> list[dict]:
    """Every rung (last heading seen) with its declared evidence.

    Unknown evidence words are dropped, not raised; a rung with a
    ``verification_evidence`` line but nothing valid reports ``[]`` so the
    executor can refuse to call it done.
    """
    if not isinstance(content, str) or not content.strip():
        return []
    rungs: list[dict] = []
    current = "(preamble)"
    for line in content.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(2).strip()
            continue
        match = _EVIDENCE.match(line)
        if not match:
            continue
        words = [w.strip().strip("`'\"").lower() for w in match.group(1).split(",")]
        evidence = [w for w in words if w in VERIFICATION_EVIDENCE]
        rungs.append({"rung": current, "evidence": evidence})
    return rungs


def pass_criteria_block(content: Any) -> str:
    """Executor-facing summary, '' when the plan declares no rungs."""
    rungs = parse_rung_evidence(content)
    if not rungs:
        return ""
    lines = ["Pass criteria per rung (verification_evidence declared by the plan):"]
    for rung in rungs:
        needed = ", ".join(rung["evidence"]) if rung["evidence"] else "NONE VALID — do not mark done"
        lines.append(f"- {rung['rung']}: {needed}")
    return "\n".join(lines)
