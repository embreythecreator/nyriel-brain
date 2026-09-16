"""Verify-on-approve gate for plan mode (WO-MOA/2, Phase 4 — stub-first).

/plan approve runs a verification pass over the saved plan before the
approve transition is honored. Rulings encoded here:

- Only a `contradicted` majority blocks. `no_evidence` flags in the verdict
  table but never refuses — new work legitimately rests on claims Word
  cannot corroborate.
- `--force` overrides the verdict, never the record: the full overridden
  verdict table plus ``force_approved: true`` is persisted BEFORE the
  approve transition executes.
- A global kill switch (env var, checked before anything else) darkens the
  whole gate across every plan; each skip is persisted as a
  ``verify_skipped`` record so the omission is auditable, not silent.

Gate modes (config key ``plan.verify_gate``, default ``off``):
    off      — pre-WO behavior, no verify call, nothing written (upgrading
               with no config change stays byte-identical).
    shadow   — verify runs and its would-be verdict is persisted on every
               approve, but never blocks. Measures claim-extraction quality
               before the gate is load-bearing.
    blocking — a `contradicted` majority refuses approval unless --force.

The verify implementation is an explicit seam: ``verify_plan_stub`` is the
first satisfier (fixed all-supported verdict); the real Word-grounded CoVe
pass (R7.3) replaces it as a drop-in with the same signature.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.moa_vote import blocking_contradictions, tally_claims

logger = logging.getLogger(__name__)

VERIFY_GATE_KILL_SWITCH_ENV = "NYRIEL_PLAN_VERIFY_GATE_OFF"

GATE_OFF = "off"
GATE_SHADOW = "shadow"
GATE_BLOCKING = "blocking"
_GATE_MODES = (GATE_OFF, GATE_SHADOW, GATE_BLOCKING)


def _kill_switch_on() -> bool:
    return str(os.environ.get(VERIFY_GATE_KILL_SWITCH_ENV, "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def gate_status(config: Mapping[str, Any] | None = None) -> tuple[str, str | None]:
    """(effective_mode, skip_reason).

    skip_reason is "killswitch" when the env switch darkened a
    configured-on gate — the caller persists a ``verify_skipped`` record so
    the omission is auditable (ruling: a dark kill switch must never be
    silent). None otherwise.
    """
    configured = _configured_mode(config)
    if _kill_switch_on():
        return GATE_OFF, ("killswitch" if configured != GATE_OFF else None)
    return configured, None


def gate_mode(config: Mapping[str, Any] | None = None) -> str:
    """Resolve the effective gate mode. Kill switch wins over everything.

    The env kill switch exists apart from --force: --force is a per-plan,
    loud, recorded override; the switch is what the operator flips when
    verify itself is misbehaving across every plan.
    """
    return gate_status(config)[0]


def _configured_mode(config: Mapping[str, Any] | None = None) -> str:
    if config is None:
        try:
            from nyriel_cli.config import load_config

            config = load_config() or {}
        except Exception:  # pragma: no cover - config unreadable → gate dark
            return GATE_OFF
    plan_cfg = config.get("plan") if isinstance(config, Mapping) else None
    raw = (plan_cfg or {}).get("verify_gate") if isinstance(plan_cfg, Mapping) else None
    text = str(raw or "").strip().lower()
    return text if text in _GATE_MODES else GATE_OFF


def verify_plan_stub(plan_content: str, session_id: str) -> dict[str, list[str]]:
    """Stub satisfier of the verify seam: one claim, unanimously supported.

    Signature contract (the real R7.3 implementation is a drop-in):
    takes the saved plan's content + session id, returns
    ``{claim_text: [verdict, ...]}`` with verdicts drawn from
    supported|contradicted|no_evidence.
    """
    return {"plan content is present": ["supported", "supported", "supported"]}


# The bound real backend, if one has been registered. Kept as a module
# global so ``handle_plan_command`` (which has a session, not an agent) can
# reach an agent-bound verifier without threading an agent through every
# plan-command caller.
_ACTIVE_VERIFY_FN = None


def register_verify_backend(fn) -> None:
    """Bind the real verify pass (e.g. ``plan_cove.make_verify_fn(agent, preset)``).

    Pass None to unbind and fall back to the stub. Idempotent; last writer
    wins, which matches the single-agent-per-process shape of a plan session.
    """
    global _ACTIVE_VERIFY_FN
    _ACTIVE_VERIFY_FN = fn


def resolve_verify_fn():
    """The active verify satisfier: the registered backend, else the stub."""
    return _ACTIVE_VERIFY_FN or verify_plan_stub


@dataclass(frozen=True)
class GateDecision:
    """Outcome of one gate evaluation, ready for persistence and display."""

    mode: str
    claims: dict  # {claim: {"winner": str, "counts": {verdict: n}}}
    blocked: list  # claim ids whose winning verdict is `contradicted`
    allowed: bool
    forced: bool = False
    pins: tuple = ()  # 4.8: evidence pins minted from the verify evidence
    stale: bool = False  # 4.8: pre-existing pins no longer match their digest

    def verdict_table(self) -> str:
        """Operator-facing per-claim verdict table (markdown)."""
        lines = ["| claim | verdict | votes |", "|---|---|---|"]
        for claim, row in self.claims.items():
            votes = ", ".join(f"{v}:{n}" for v, n in sorted(row["counts"].items()))
            lines.append(f"| {claim} | {row['winner']} | {votes} |")
        return "\n".join(lines)


def evaluate_plan(
    plan_content: str,
    session_id: str,
    *,
    mode: str,
    force: bool = False,
    verify_fn: Callable[[str, str], dict[str, list[str]]] | None = None,
    stale_pins: bool = False,
) -> GateDecision:
    """Run the verify seam and decide. Pure given ``verify_fn``.

    ``verify_fn=None`` resolves the active satisfier at call time (the
    registered backend, else the stub), so swapping it is a registration or
    a plain attribute assignment — never an edit-in-place.
    """
    fn = verify_fn if verify_fn is not None else resolve_verify_fn()
    claim_votes = fn(plan_content, session_id)
    results = tally_claims(claim_votes)
    blocked = blocking_contradictions(results)
    claims = {
        str(claim): {"winner": r.winner, "counts": dict(r.counts)}
        for claim, r in results.items()
    }
    # 4.8 — pins from whatever evidence the verify seam exposed (the CoVe
    # backend sets ``last_evidence``; the stub and plain lambdas set nothing).
    pins = _pins_from_seam(fn)
    if stale_pins:
        # D-9: a plan whose evidence set drifted is treated as contradicted.
        claims[STALE_PINS_CLAIM] = {"winner": "contradicted", "counts": {"contradicted": 1}}
        blocked = [*blocked, STALE_PINS_CLAIM]
    if mode == GATE_BLOCKING and blocked and not force:
        allowed = False
    else:
        allowed = True
    return GateDecision(
        mode=mode,
        claims=claims,
        blocked=[str(c) for c in blocked],
        allowed=allowed,
        forced=bool(force and blocked and mode == GATE_BLOCKING),
        pins=pins,
        stale=bool(stale_pins),
    )


STALE_PINS_CLAIM = "evidence pins are stale (digest mismatch)"


def _pins_from_seam(fn) -> tuple:
    evidence = getattr(fn, "last_evidence", None)
    if not isinstance(evidence, dict) or not evidence:
        return ()
    try:
        from agent.plan_pins import pins_from_evidence
    except Exception:  # pragma: no cover
        return ()
    items = []
    for claim, notes in evidence.items():
        for note in notes or ():
            if isinstance(note, dict) and note.get("id") and note.get("id") != "?":
                items.append({"vault_id": str(note["id"]), "claim": str(claim)})
    return pins_from_evidence(items)


def write_verdict_record(plan_path: str, payload: Mapping[str, Any]) -> str | None:
    """Append one JSON record to the plan's verdict ledger.

    Append-only, written BEFORE any state transition the record justifies —
    "override the verdict, never the record". Returns the ledger path, or
    None when the write failed (logged loudly; the caller decides whether a
    blocked write should also block the transition).
    """
    if not plan_path:
        return None
    ledger = f"{plan_path}.verdict.jsonl"
    record = {"ts": time.time(), **payload}
    try:
        Path(ledger).parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return ledger
    except OSError as exc:
        logger.error("Plan verdict ledger write failed (%s): %s", ledger, exc)
        return None
