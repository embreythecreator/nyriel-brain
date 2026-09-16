"""Session-scoped execution policy — the runtime boundary behind plan mode.

Postures are an axis, not a mode zoo: v1 ships ACT (default, everything
allowed) and PLAN (read-only exploration + reasoning-only delegation +
save_plan). The policy lives in
SessionDB keyed by session_id because gateway/API surfaces build a fresh
AIAgent per request — state on the agent object would silently fail open.

Enforcement is a NON-halting deny: the tool executor synthesizes an error
result for the denied call and the turn continues (unlike guardrail blocks,
which halt the turn). See policy_deny_message().

State machine:

    OFF -> PLANNING -> READY(plan_id, revision, digest)
        -> EXECUTING(one armed turn) -> OFF
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MAX_PLAN_CONTENT_CHARS = 512_000


class ExecutionPosture(str, Enum):
    ACT = "act"
    PLAN = "plan"
    # WO-POSTURE/1 2.1 — CHAT is a session flag (set by the chat entry), not
    # a lock state: for tool policy it behaves exactly like ACT; the MoA chat
    # preset keys off it. Plan mode always outranks it.
    CHAT = "chat"
    # Future axes (observe, sandbox) extend this enum; never collapse to bool.


class PlanModeState(str, Enum):
    OFF = "off"
    PLANNING = "planning"
    READY = "ready"
    EXECUTING = "executing"


# Tools usable while planning. Mirrors the read-only set in
# agent/tool_dispatch_helpers._PARALLEL_SAFE_TOOLS (minus HA/vision, which
# have no planning value) plus todo/clarify bookkeeping and the plan-mode
# artifact tool. Everything absent is denied — default-deny, no exceptions
# for terminal/exec (shell cannot be classified safely; blocked outright).
PLAN_ALLOWED_TOOLS = frozenset({
    "read_file",
    "search_files",
    "session_search",
    "skill_view",
    "skills_list",
    "web_extract",
    "web_search",
    "todo",
    "clarify",
    "delegate_task",
    "save_plan",
    # WO-MOA/2: the Phase-2 debate trigger — pure side-call fan-out over a
    # saved artifact, no mutation surface (degrades to a recorded skip).
    "convene_debate",
})

# WO-POSTURE/1 4.3 — plan-mode CHILDREN (delegate_task fan-out) may read and
# search but never delegate, debate, or ask: pure reasoning over evidence.
PLAN_CHILD_ALLOWED_TOOLS = PLAN_ALLOWED_TOOLS - frozenset({
    "delegate_task", "convene_debate", "todo", "clarify",
    "save_plan",  # the parent owns the artifact; children return frame output
})


@dataclass(frozen=True)
class ExecutionPolicy:
    state: PlanModeState = PlanModeState.OFF
    task: str = ""
    plan_id: str = ""
    revision: int = 0
    digest: str = ""
    plan_path: str = ""
    title: str = ""
    armed: bool = False
    turn_id: str = ""
    updated_at: float = field(default_factory=time.time)
    chat: bool = False
    plan_child: bool = False  # 4.3: this session is a plan-mode delegate child
    # 4.7: evidence the READY plan rests on; cleared on every revision so a
    # re-verify must re-pin. evidence_digest is order-independent over ids.
    evidence_pins: tuple = ()
    evidence_digest: str = ""
    # 4.10: the Word artifact node for the current READY revision ('' until
    # the write-behind lands; the workspace file stays the digest cache).
    word_note_id: str = ""

    @property
    def posture(self) -> ExecutionPosture:
        if self.state in (PlanModeState.PLANNING, PlanModeState.READY):
            return ExecutionPosture.PLAN
        if self.chat and self.state is PlanModeState.OFF:
            return ExecutionPosture.CHAT
        return ExecutionPosture.ACT

    @property
    def tools_unrestricted(self) -> bool:
        """ACT and CHAT share the same (open) tool policy."""
        return self.posture in (ExecutionPosture.ACT, ExecutionPosture.CHAT)

    @property
    def short_id(self) -> str:
        return self.plan_id[:8]

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "task": self.task,
            "plan_id": self.plan_id,
            "revision": self.revision,
            "digest": self.digest,
            "plan_path": self.plan_path,
            "title": self.title,
            "armed": self.armed,
            "turn_id": self.turn_id,
            "updated_at": self.updated_at,
            "chat": self.chat,
            "plan_child": self.plan_child,
            "evidence_pins": list(self.evidence_pins),
            "evidence_digest": self.evidence_digest,
            "word_note_id": self.word_note_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionPolicy":
        try:
            return cls(
                state=PlanModeState(str(data.get("state", "off"))),
                task=str(data.get("task", "")),
                plan_id=str(data.get("plan_id", "")),
                revision=int(data.get("revision", 0)),
                digest=str(data.get("digest", "")),
                plan_path=str(data.get("plan_path", "")),
                title=str(data.get("title", "")),
                armed=bool(data.get("armed", False)),
                turn_id=str(data.get("turn_id", "")),
                updated_at=float(data.get("updated_at", 0.0)),
                chat=bool(data.get("chat", False)),
                plan_child=bool(data.get("plan_child", False)),
                # Totally coerced (never raise): junk pins must not brick the
                # session — see §2 #13 / the fail-open regression test.
                evidence_pins=_coerce_pins(data.get("evidence_pins")),
                evidence_digest=str(data.get("evidence_digest") or "")
                if isinstance(data.get("evidence_digest"), (str, bytes)) else "",
                word_note_id=str(data.get("word_note_id") or "")
                if isinstance(data.get("word_note_id"), str) else "",
            )
        except (TypeError, ValueError):
            # Malformed persisted state defaults to ACT — plan mode is
            # opt-in; a broken row must not brick normal sessions.
            return cls()


def _coerce_pins(raw):
    try:
        from agent.plan_pins import coerce_pins
        return coerce_pins(raw)
    except Exception:  # pragma: no cover - total by contract
        return ()


def policy_deny_message(
    policy: Optional["ExecutionPolicy"], tool_name: str
) -> Optional[str]:
    """Return a deny message if this tool call violates the policy, else None.

    Name-only in v1. Any future arg-sensitive posture (path-scoped writes,
    exec classifiers) must be checked inside the tool-execution middleware,
    after argument transformations — not here.
    """
    if policy is None or policy.tools_unrestricted:
        if tool_name == "save_plan":
            return (
                "save_plan is only available in plan mode. "
                "The operator enters it with /plan <task>."
            )
        return None
    allowed = PLAN_CHILD_ALLOWED_TOOLS if policy.plan_child else PLAN_ALLOWED_TOOLS
    if tool_name in allowed:
        return None
    hint = (
        f"/plan approve {policy.short_id}" if policy.short_id else "/plan approve"
    )
    return (
        f"Plan mode: the '{tool_name}' tool is locked. Continue with "
        "read-only tools, save your plan with save_plan(title, content), "
        f"and present it. The operator unlocks execution with {hint}."
    )


def compute_plan_digest(path: str) -> Optional[str]:
    """sha256 of a plan file, or None if unreadable."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _atomic_write_plan(path: Path, content: bytes) -> None:
    """Replace one existing plan file without exposing a partial write."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        try:
            temporary.chmod(path.stat().st_mode)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


# Read-modify-write transitions (approve, armed-consume, revision) are not
# atomic at the DB layer; serialize them per session so two concurrent
# requests in one process cannot both pass a state check before either
# saves (double-approve TOCTOU).
# ponytail: in-process locks only — CLI + gateway racing the SAME session
# from two processes would need a conditional UPDATE in SessionDB.
_SESSION_LOCKS: dict = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(session_id: str) -> "threading.Lock":
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = _SESSION_LOCKS[session_id] = threading.Lock()
        return lock


PLAN_MODE_REQUIRED_ERROR = "save_plan requires plan mode (/plan <task>)."
TRANSIENT_POLICY_ERROR = (
    "Plan state is temporarily unreadable (database busy). Retry the save."
)


# Bare `SessionDB()` opens a fresh sqlite connection, re-runs the whole schema
# DDL plus a full-table `UPDATE messages SET active = 1 WHERE active IS NULL`
# — tens of milliseconds against a large state.db, and it was being paid on
# every single message. Memoize per resolved DB path so multiplex
# `/p/<profile>/` scopes still get their own handle instead of the default
# profile's (which the import-time DEFAULT_DB_PATH binding silently gave them).
_DEFAULT_DBS: dict = {}
_DEFAULT_DBS_GUARD = threading.Lock()


def _default_session_db():
    from nyriel_state import SessionDB

    try:
        from nyriel_constants import get_nyriel_home
        db_path = Path(get_nyriel_home()) / "state.db"
    except Exception:
        db_path = None

    key = str(db_path or "")
    with _DEFAULT_DBS_GUARD:
        db = _DEFAULT_DBS.get(key)
        if db is None:
            db = _DEFAULT_DBS[key] = SessionDB(db_path) if db_path else SessionDB()
        return db


class ExecutionPolicyStore:
    """Load/save + state transitions over SessionDB.

    Every method is failure-tolerant on load (defaults to OFF/ACT) but
    enforcement is unconditional once a PLAN-posture policy loads.
    """

    def __init__(self, session_db=None):
        self._db = session_db

    def _get_db(self):
        if self._db is None:
            self._db = _default_session_db()
        return self._db

    def load(self, session_id: str) -> ExecutionPolicy:
        return self._load_with_status(session_id)[0]

    def _load_with_status(self, session_id: str) -> Tuple[ExecutionPolicy, bool]:
        """Load, reporting whether the default came from a read failure.

        Callers that destroy something on a "not in plan mode" answer need to
        tell an actual OFF policy apart from a locked-DB hiccup — the default
        policy looks identical either way.
        """
        if not session_id:
            return ExecutionPolicy(), False
        # Retry transient read failures (locked DB under multi-process
        # contention) before defaulting to ACT — a planning session that
        # falls back on a hiccup would silently run UNLOCKED.
        for attempt in range(3):
            try:
                data = self._get_db().get_execution_policy(session_id)
                break
            except Exception:
                if attempt == 2:
                    logger.warning(
                        "execution policy load failed after retries; "
                        "defaulting to ACT (plan mode NOT enforced this turn)",
                        exc_info=True,
                    )
                    return ExecutionPolicy(), True
                time.sleep(0.05 * (attempt + 1))
        if not data:
            return ExecutionPolicy(), False
        return ExecutionPolicy.from_dict(data), False

    def save(
        self, session_id: str, policy: ExecutionPolicy, *, expected_revision: int = None
    ) -> bool:
        """Persist a policy. With ``expected_revision`` the write is a
        compare-and-swap and returns False when another process moved first."""
        policy = replace(policy, updated_at=time.time())
        return self._get_db().set_execution_policy(
            session_id, policy.to_dict(), expected_revision=expected_revision
        )

    def load_for_turn(self, session_id: str, turn_id: str = "") -> ExecutionPolicy:
        """Turn-start load with one-use consume and crash reconciliation.

        EXECUTING + armed  -> consume: this turn runs unlocked (ACT posture),
                              armed flips off so it cannot be reused.
        EXECUTING + unarmed -> stale (approved turn already ran, or crashed
                              mid-execution): reset to OFF.
        """
        with _session_lock(session_id):
            policy = self.load(session_id)
            if policy.state is not PlanModeState.EXECUTING:
                return policy
            if policy.armed:
                consumed = replace(policy, armed=False, turn_id=turn_id or "")
                try:
                    self.save(session_id, consumed)
                except Exception:
                    logger.exception("execution policy consume failed")
                return consumed
            self.finish(session_id)
            return ExecutionPolicy()

    def enter_planning(self, session_id: str, task: str) -> ExecutionPolicy:
        # The chat flag rides through plan mode (2.1): plan outranks it while
        # active, and the session returns to chat when the plan finishes.
        policy = ExecutionPolicy(
            state=PlanModeState.PLANNING, task=task.strip(), chat=self.load(session_id).chat
        )
        self.save(session_id, policy)
        return policy

    def record_revision(
        self, session_id: str, *, path: str, digest: str, title: str = ""
    ) -> Tuple[Optional[ExecutionPolicy], Optional[str]]:
        """Register a saved plan revision. PLANNING/READY -> READY."""
        with _session_lock(session_id):
            policy, read_failed = self._load_with_status(session_id)
            if read_failed:
                return None, TRANSIENT_POLICY_ERROR
            if policy.state not in (PlanModeState.PLANNING, PlanModeState.READY):
                return None, PLAN_MODE_REQUIRED_ERROR
            base_revision = policy.revision
            policy = replace(
                policy,
                state=PlanModeState.READY,
                plan_id=policy.plan_id or uuid.uuid4().hex,
                revision=policy.revision + 1,
                digest=digest,
                plan_path=path,
                # The author's own title. Scanning the body for an H1 picks up
                # `#` comments inside fenced code blocks.
                title=title.strip() or policy.title,
                evidence_pins=(),  # 4.7: a new revision must be re-pinned
                evidence_digest="",
            )
            # The in-process lock cannot see a CLI turn writing the same
            # session, so this write is conditional on the revision we read.
            if not self.save(session_id, policy, expected_revision=base_revision):
                return None, (
                    "Another session updated this plan while it was being "
                    "saved. Re-read the plan and save again."
                )
            return policy, None

    def replace_plan_content(
        self,
        session_id: str,
        *,
        content: str,
        expected_revision: int,
    ) -> Tuple[Optional[ExecutionPolicy], Optional[str]]:
        """Replace the active READY artifact and register its new digest."""
        if not isinstance(content, str) or not content.strip():
            return None, "Plan content must be a non-empty string."
        if len(content) > MAX_PLAN_CONTENT_CHARS:
            return None, f"Plan content exceeds {MAX_PLAN_CONTENT_CHARS} characters."
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            return None, "expected_revision must be an integer."

        with _session_lock(session_id):
            policy = self.load(session_id)
            if policy.state is not PlanModeState.READY or not policy.plan_path:
                return None, "No ready plan artifact is available to edit."
            if policy.revision != expected_revision:
                return None, (
                    "Plan revision conflict: expected "
                    f"{expected_revision}, current revision is {policy.revision}."
                )

            path = Path(policy.plan_path)
            try:
                previous_content = path.read_bytes()
            except OSError:
                return None, f"Plan file missing or unreadable: {policy.plan_path}"

            actual_digest = hashlib.sha256(previous_content).hexdigest()
            if actual_digest != policy.digest:
                return None, (
                    "Plan file changed on disk since it was saved (digest mismatch). "
                    "Ask for a fresh revision before editing."
                )

            next_content = content.encode("utf-8")
            next_digest = hashlib.sha256(next_content).hexdigest()
            try:
                _atomic_write_plan(path, next_content)
            except OSError as exc:
                return None, f"Could not write plan file: {exc}"

            updated = replace(
                policy,
                revision=policy.revision + 1,
                digest=next_digest,
                evidence_pins=(),  # 4.7: edited content invalidates its pins
                evidence_digest="",
            )
            try:
                self.save(session_id, updated)
            except Exception:
                try:
                    _atomic_write_plan(path, previous_content)
                except OSError:
                    logger.exception("plan artifact rollback failed")
                raise
            return updated, None

    def approve(
        self, session_id: str, short_id: str
    ) -> Tuple[Optional[ExecutionPolicy], Optional[str]]:
        """READY -> EXECUTING(armed). Validates short-id and plan digest."""
        with _session_lock(session_id):
            policy = self.load(session_id)
            if policy.state is not PlanModeState.READY:
                return None, (
                    f"No plan is awaiting approval (state: {policy.state.value})."
                )
            if not short_id or short_id != policy.short_id:
                return None, (
                    f"Unknown plan id '{short_id}'. The current plan is "
                    f"{policy.short_id} (rev {policy.revision})."
                )
            actual = compute_plan_digest(policy.plan_path)
            if actual is None:
                return None, f"Plan file missing or unreadable: {policy.plan_path}"
            if actual != policy.digest:
                return None, (
                    "Plan file changed on disk since it was saved (digest "
                    "mismatch). Ask for a fresh revision, then approve that."
                )
            policy = replace(policy, state=PlanModeState.EXECUTING, armed=True)
            self.save(session_id, policy)
            return policy, None

    def record_pins(
        self, session_id: str, pins, expected_revision: int
    ) -> Tuple[Optional[ExecutionPolicy], Optional[str]]:
        """4.8 — attach evidence pins to the READY revision they verified.
        Conditional on the revision the verifier read (CAS), so a save_plan
        that raced the verify pass never inherits its pins."""
        from agent.plan_pins import coerce_pins, evidence_digest

        pins = coerce_pins(list(pins or ()))
        with _session_lock(session_id):
            policy, read_failed = self._load_with_status(session_id)
            if read_failed:
                return None, TRANSIENT_POLICY_ERROR
            if policy.state is not PlanModeState.READY:
                return None, "No ready plan to pin evidence to."
            if policy.revision != expected_revision:
                return None, (
                    f"Plan revision conflict: pins verified revision "
                    f"{expected_revision}, current is {policy.revision}."
                )
            updated = replace(
                policy, evidence_pins=pins, evidence_digest=evidence_digest(pins)
            )
            if not self.save(session_id, updated, expected_revision=expected_revision):
                return None, "Plan revision changed while recording pins."
            return updated, None

    def set_chat(self, session_id: str, on: bool) -> ExecutionPolicy:
        """Flip the session's chat flag (2.1). Never touches plan state."""
        policy = replace(self.load(session_id), chat=bool(on), updated_at=time.time())
        self.save(session_id, policy)
        return policy

    def finish(self, session_id: str) -> None:
        try:
            if self.load(session_id).chat:
                self.save(session_id, ExecutionPolicy(chat=True))
                return
            self._get_db().clear_execution_policy(session_id)
        except Exception:
            logger.exception("execution policy clear failed")

    def migrate(self, old_session_id: str, new_session_id: str) -> None:
        """Carry an active policy across a session-id rotation (compression).

        Without this, compression mid-PLANNING orphans the policy row and
        the next turn silently loads OFF/ACT — fail-open while the operator
        believes plan mode is on.
        """
        if not old_session_id or not new_session_id:
            return
        if old_session_id == new_session_id:
            return
        with _session_lock(old_session_id):
            policy = self.load(old_session_id)
            if policy.state is PlanModeState.OFF:
                return
            try:
                self.save(new_session_id, policy)
                self.finish(old_session_id)
            except Exception:
                logger.exception("execution policy migration failed")
