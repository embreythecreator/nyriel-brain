"""Plan-mode shared core: /plan command handling + conversation-event text.

One module drives every surface (gateway platforms, CLI REPL, API server) so
the command grammar and state transitions cannot drift apart. All plan-mode
text is injected as ordinary conversation content — the system prompt and
tool schemas never change with mode, keeping the prompt-cache prefix stable.

Runtime enforcement lives in agent/execution_policy.py + the tool executor;
this module is only the operator-facing control surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from agent.execution_policy import (
    ExecutionPolicy,
    ExecutionPolicyStore,
    PlanModeState,
)

logger = logging.getLogger(__name__)

PLAN_USAGE = (
    "Usage: /plan <task> — enter plan mode\n"
    "       /plan status — show plan-mode state\n"
    "       /plan approve <id> — approve the saved plan and execute\n"
    "       /plan off — leave plan mode without executing"
)

# Operational contract injected when entering plan mode. Deliberately short:
# the full plan-authoring craft lives in the `plan` skill, which the model
# can read with skill_view (allowed in plan mode) — injecting 300+ lines
# every cycle would bloat the transcript for no enforcement benefit.
from nyriel_cli.model_council import render_plan_frame_briefs

PLAN_MODE_INSTRUCTIONS = """[PLAN MODE ON]
You are in plan mode. Mutating tools (terminal, write_file, patch, browser,
etc.) are LOCKED at the runtime — calls will be denied. delegate_task is
available only for isolated, tool-less reasoning branches. Do not fight the
denials; plan.

Your job this cycle:
1. Explore the task with read-only tools (read_file, search_files,
   session_search, web_search, skills_list/skill_view).
2. The operator explicitly requires ADHD mode for every plan. Load the
   `adhd` skill with skill_view and run its full diverge/focus loop. Treat
   this as explicit invocation: do not apply the skill's pre-flight abort.
   Use delegate_task for its parallel isolated branches: ONE tasks[] entry
   per frame below with that entry's `frame` field set (this pins the
   branch to its council seat and its evidence contract). Children hold
   read-only tools (skill_view, web_search, web_extract, read_file,
   search_files) — never save_plan or delegation. Each branch's goal:
{FRAME_BRIEFS}
   MANDATORY at Phase-2 entry: save the diverge output (idea pool or draft
   plan) with save_plan, then call convene_debate with the returned short
   id — the adversarial convergence pass. This is not optional and not
   your judgment call. If the tool reports the debate was skipped, proceed
   with the skill's single-context Phase-2 scoring and note the skip.
3. Write a concrete, actionable markdown plan. For the authoring craft
   (bite-sized tasks, exact paths, complete code, verification steps),
   consult the `plan` skill via skill_view if needed. End EVERY rung
   (step heading) with a line `verification_evidence: [...]` choosing
   only from: tests, regression, static, artifact_exercised,
   temp_removed, no_stray_ports — act mode treats those as that rung's
   pass criteria.
4. Save it with save_plan(title, content) — the ONLY write available.
   It returns a short id.
5. Present the plan and tell the operator: approve with
   /plan approve <short-id>, request changes with a normal message, or
   abandon with /plan off.

Revisions: feedback keeps plan mode on; save an updated plan with
save_plan again (new revision, same id).""".replace("{FRAME_BRIEFS}", render_plan_frame_briefs())


def build_plan_invocation(task: str) -> str:
    task = (task or "").strip()
    if task:
        return f"{PLAN_MODE_INSTRUCTIONS}\n\nThe task to plan:\n{task}"
    return (
        f"{PLAN_MODE_INSTRUCTIONS}\n\nNo explicit task was given — infer "
        "the task to plan from the current conversation context."
    )


def build_plan_reminder(policy: ExecutionPolicy) -> str:
    """One-line prefix for ordinary messages while PLANNING/READY."""
    if policy.state is PlanModeState.READY:
        detail = f"plan {policy.short_id} rev {policy.revision} awaiting approval"
    else:
        detail = "no plan saved yet"
    return (
        f"[Plan mode active — {detail}. Mutating tools are locked; revise "
        "with save_plan; the operator approves with "
        f"/plan approve {policy.short_id or '<id>'} or exits with /plan off.]"
    )


def _pins_stale(policy: ExecutionPolicy) -> bool:
    """4.8 / D-9: pins present but their set no longer matches the digest."""
    try:
        from agent.plan_pins import evidence_digest
        pins = tuple(policy.evidence_pins or ())
        if not pins and not policy.evidence_digest:
            return False
        return evidence_digest(pins) != (policy.evidence_digest or "")
    except Exception:
        return False


def build_execute_instruction(policy: ExecutionPolicy) -> str:
    text = (
        f"[Plan {policy.short_id} rev {policy.revision} APPROVED — tools are "
        f"unlocked for this turn. Execute the plan at {policy.plan_path} "
        "now, following it step by step."
    )
    # 4.11 — the plan's own rung contract becomes the executor's pass criteria.
    criteria = ""
    try:
        from agent.plan_rungs import pass_criteria_block
        with open(policy.plan_path, "r", encoding="utf-8") as fh:
            criteria = pass_criteria_block(fh.read())
    except Exception:
        criteria = ""
    if criteria:
        text += "\n" + criteria + "\nA rung is done only when its evidence exists."
    return text + "]"


def format_plan_status(policy: ExecutionPolicy) -> str:
    if policy.state is PlanModeState.OFF:
        return "Plan mode: off."
    lines = [f"Plan mode: {policy.state.value}"]
    if policy.task:
        lines.append(f"Task: {policy.task}")
    if policy.plan_id:
        lines.append(
            f"Plan: {policy.short_id} rev {policy.revision} — {policy.plan_path}"
        )
        if policy.state is PlanModeState.READY:
            lines.append(f"Approve with /plan approve {policy.short_id}")
    return "\n".join(lines)


@dataclass(frozen=True)
class PlanCommandResult:
    """Either a direct reply (no agent run) or a rewritten agent message."""
    reply: Optional[str] = None
    rewritten_message: Optional[str] = None


def handle_plan_command(
    args: str,
    session_id: str,
    *,
    runtime_is_codex: bool = False,
    session_db=None,
) -> PlanCommandResult:
    store = ExecutionPolicyStore(session_db)
    args = (args or "").strip()
    lowered = args.lower()

    if lowered == "status":
        return PlanCommandResult(reply=format_plan_status(store.load(session_id)))

    if lowered in ("off", "exit", "cancel"):
        policy = store.load(session_id)
        if policy.state is PlanModeState.OFF:
            return PlanCommandResult(reply="Plan mode is not active.")
        store.finish(session_id)
        return PlanCommandResult(reply="Plan mode off — back to normal execution.")

    if lowered.startswith("approve"):
        rest = args[len("approve"):].strip()
        parts = rest.split()
        force = "--force" in parts
        parts = [p for p in parts if p != "--force"]
        short_id = parts[0] if parts else ""
        if not short_id:
            policy = store.load(session_id)
            hint = (
                f" The current plan is {policy.short_id}."
                if policy.short_id else ""
            )
            return PlanCommandResult(
                reply=f"Usage: /plan approve <id> [--force].{hint}"
            )
        # WO-MOA/2 verify gate: runs BEFORE the approve transition, and every
        # verdict (including a forced override or a kill-switch skip) is
        # persisted BEFORE that transition is honored — override the verdict,
        # never the record. Default config keeps the gate off: this block is
        # a no-op and approve behaves exactly as pre-WO.
        from agent.plan_verify import (
            GATE_OFF,
            evaluate_plan,
            gate_status,
            write_verdict_record,
        )

        mode, skip_reason = gate_status()
        pre = store.load(session_id)
        if (mode != GATE_OFF or skip_reason) and not pre.plan_path:
            # Configured gate with no readable plan artifact: the ledger is
            # keyed on the plan path, so there is nowhere to record a
            # verdict. Approve still proceeds (a transient policy-read
            # hiccup must not strand an operator), but it must not be
            # silent — the whole point of the gate is an auditable trail.
            logger.warning(
                "Plan verify gate (%s) skipped for session %s: no readable "
                "plan path on the loaded policy; approving unverified.",
                mode if mode != GATE_OFF else f"off/{skip_reason}",
                session_id,
            )
        if mode == GATE_OFF and skip_reason == "killswitch" and pre.plan_path:
            write_verdict_record(
                pre.plan_path,
                {
                    "event": "verify_skipped",
                    "reason": "killswitch",
                    "plan_id": pre.plan_id,
                    "revision": pre.revision,
                },
            )
        elif mode != GATE_OFF and pre.plan_path:
            plan_content = ""
            try:
                from pathlib import Path

                plan_content = Path(pre.plan_path).read_text(encoding="utf-8")
            except OSError:
                pass
            decision = evaluate_plan(
                plan_content, session_id, mode=mode, force=force,
                stale_pins=_pins_stale(pre),
            )
            write_verdict_record(
                pre.plan_path,
                {
                    "event": "verify",
                    "mode": mode,
                    "plan_id": pre.plan_id,
                    "revision": pre.revision,
                    "claims": decision.claims,
                    "blocked": decision.blocked,
                    "forced": decision.forced,
                    "force_approved": decision.forced,
                    "pins": [dict(p) for p in decision.pins],
                    "pins_stale": decision.stale,
                },
            )
            if decision.allowed and decision.pins:
                # 4.8 — pins bind to the exact revision the verifier read.
                _pinned, pin_err = store.record_pins(
                    session_id, decision.pins, expected_revision=pre.revision
                )
                if pin_err:
                    logger.warning("evidence pins not recorded for %s: %s", session_id, pin_err)
            if not decision.allowed:
                return PlanCommandResult(
                    reply=(
                        f"Plan {pre.short_id} NOT approved — verification "
                        f"found contradicted claims: "
                        f"{', '.join(decision.blocked)}.\n\n"
                        f"{decision.verdict_table()}\n\n"
                        "Revise the plan, or override with "
                        f"/plan approve {pre.short_id} --force (the override "
                        "is recorded)."
                    )
                )
        policy, err = store.approve(session_id, short_id)
        if err is not None:
            return PlanCommandResult(reply=err)
        return PlanCommandResult(
            rewritten_message=build_execute_instruction(policy)
        )

    if not args:
        return PlanCommandResult(reply=PLAN_USAGE)

    # /plan <task> — enter planning.
    if runtime_is_codex:
        return PlanCommandResult(
            reply=(
                "Plan mode cannot be enforced under the codex_app_server "
                "runtime (tools run inside Codex, bypassing the tool "
                "executor). Switch runtime before planning."
            )
        )
    policy = store.load(session_id)
    if policy.state in (PlanModeState.PLANNING, PlanModeState.READY):
        return PlanCommandResult(
            reply=(
                f"Plan mode is already active ({format_plan_status(policy)}). "
                "Send feedback as a normal message, or /plan off first."
            )
        )
    store.enter_planning(session_id, args)
    return PlanCommandResult(rewritten_message=build_plan_invocation(args))
