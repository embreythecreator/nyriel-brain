#!/usr/bin/env python3
"""convene_debate — the plan-mode debate trigger (WO-MOA/2, checklist 14).

The tool call IS the categorical event the deterministic mode-picker keys
on: the injected plan contract mandates calling it at Phase-2 entry, and
Python composes everything downstream — no free-text judgment in mode
selection.

Structural guard (ruling 1): the signature accepts ONLY a saved artifact
reference — a plan short id — never inline prose. R1's precondition
(debate needs a fixed object to attack) is enforced by shape, not prompt.

Amendment A1 (two-flag degrade path):
  moa.debate_enabled          — fan-out is invokable at all (default true)
  moa.debate_required_in_plan — Phase-2 requires it to SUCCEED (default false)
Until the real multi-round fan-out lands in moa_loop, every invocation takes
the degrade path: a ``debate_skipped`` record is appended to the artifact's
verdict ledger (auditable, never silent — mirrors ``verify_skipped``) and
the model is told to proceed with single-context Phase-2 scoring. The real
fan-out replaces the body of ``_run_debate`` as a drop-in.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Only a short-id-shaped token (or full plan id) passes — anything with
# whitespace, punctuation prose, or length beyond an id is rejected.
_ARTIFACT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")


def _debate_flags() -> tuple:
    """(enabled, required_in_plan) from raw config, tolerant of absence."""
    try:
        from nyriel_cli.config import load_config

        moa_cfg = (load_config() or {}).get("moa") or {}
    except Exception:
        moa_cfg = {}
    enabled = bool(moa_cfg.get("debate_enabled", True))
    required = bool(moa_cfg.get("debate_required_in_plan", False))
    return enabled, required


# Bound debate backend, registered at agent init (the tool has a session,
# not an agent — same shape as plan_verify's verify backend).
_ACTIVE_DEBATE_FN = None


def register_debate_backend(fn) -> None:
    """Bind the real fan-out: ``fn(artifact_ref, artifact_text) -> dict``.

    Pass None to unbind and fall back to the recorded-skip degrade path.
    """
    global _ACTIVE_DEBATE_FN
    _ACTIVE_DEBATE_FN = fn


def make_debate_backend(agent, preset):
    """Bind agent+preset into the debate seam (see ``agent.moa_debate``)."""

    def _run(artifact_ref: str, artifact_text: str) -> dict:
        from agent.moa_debate import run_debate

        return run_debate(
            agent, preset, artifact_text, artifact_ref=artifact_ref
        )

    return _run


def _run_debate(artifact_ref: str, plan_path: str, session_id: str):
    """Debate fan-out seam. Returns (result_dict | None, skip_reason).

    Degrades — never raises — on every path the operator would rather see
    recorded than crashed through: no backend bound, artifact unreadable,
    or the fan-out itself failing.
    """
    if _ACTIVE_DEBATE_FN is None:
        return None, "not_implemented"
    try:
        from pathlib import Path

        artifact_text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return None, "artifact_unreadable"
    try:
        result = _ACTIVE_DEBATE_FN(artifact_ref, artifact_text)
    except Exception as exc:
        logger.warning("Debate fan-out failed (degrading to skip): %s", exc)
        return None, "fanout_failed"
    if not isinstance(result, dict) or result.get("error"):
        return None, "fanout_failed"
    return result, None


def convene_debate_tool(artifact_ref: str, session_id: str = "") -> str:
    ref = (artifact_ref or "").strip()
    if not ref or not _ARTIFACT_REF_RE.match(ref):
        return json.dumps({
            "error": (
                "convene_debate requires a saved artifact reference (the "
                "short id save_plan returned) — not inline prose. Save the "
                "idea pool or draft plan with save_plan first."
            )
        })

    from agent.execution_policy import ExecutionPolicyStore

    policy = ExecutionPolicyStore().load(session_id or "")
    known = {policy.short_id, policy.plan_id}
    if not policy.plan_id or ref not in known:
        return json.dumps({
            "error": (
                f"Artifact '{ref}' does not match the session's saved plan"
                f"{f' ({policy.short_id})' if policy.short_id else ''}. "
                "convene_debate only debates the saved artifact of THIS "
                "plan session."
            )
        })

    enabled, required = _debate_flags()
    if not enabled:
        skip_reason = "disabled"
        result = None
    else:
        result, skip_reason = _run_debate(ref, policy.plan_path, session_id)

    from agent.plan_verify import write_verdict_record

    if result is not None:
        # The full transcript is Forge feed and audit material, not model
        # context — it goes to the ledger; the model gets the ruling.
        write_verdict_record(
            policy.plan_path,
            {
                "event": "debate",
                "plan_id": policy.plan_id,
                "revision": policy.revision,
                **result,
            },
        )
        return json.dumps({
            "debate": "ruled",
            "artifact_ref": ref,
            "rounds": result.get("rounds"),
            "exit_reason": result.get("exit_reason"),
            "stances": result.get("stances"),
            "unresolved": list((result.get("unresolved") or {}).keys()),
            "retrieval_ref": result.get("retrieval_ref"),
            "ruling": result.get("ruling"),
            "note": (
                "Adversarial convergence complete. Fold the ruling into the "
                "plan — surviving objections must be answered or explicitly "
                "accepted, not dropped."
            ),
        })

    # Degrade path (A1): auditable, never silent.

    write_verdict_record(
        policy.plan_path,
        {
            "event": "debate_skipped",
            "reason": skip_reason,
            "artifact_ref": ref,
            "plan_id": policy.plan_id,
            "revision": policy.revision,
            "required_in_plan": required,
        },
    )
    return json.dumps({
        "debate": "skipped",
        "reason": skip_reason,
        "artifact_ref": ref,
        "note": (
            "Debate fan-out did not run. Proceed with single-context "
            "Phase-2 scoring (score, cluster, deepen per the adhd skill) "
            "and note the skip in the plan. The skip is recorded."
        ),
    })


CONVENE_DEBATE_SCHEMA = {
    "name": "convene_debate",
    "description": (
        "Convene the adversarial debate fan-out over a SAVED artifact at "
        "ADHD Phase-2 entry (plan mode only). Pass the short id that "
        "save_plan returned — inline prose is rejected. Reference critics "
        "attack the artifact, rebut each other, and the aggregator rules; "
        "if the fan-out cannot run, the skip is recorded and you proceed "
        "with single-context Phase-2 scoring."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "artifact_ref": {
                "type": "string",
                "description": (
                    "The saved artifact's short id (from save_plan). Not "
                    "prose, not plan content — the reference only."
                ),
            },
        },
        "required": ["artifact_ref"],
    },
}


# --- Registry ---
from tools.registry import registry

registry.register(
    name="convene_debate",
    toolset="plan",
    schema=CONVENE_DEBATE_SCHEMA,
    handler=lambda args, **kw: convene_debate_tool(
        artifact_ref=args.get("artifact_ref", ""),
        session_id=kw.get("session_id", "") or "",
    ),
    emoji="⚔️",
)
