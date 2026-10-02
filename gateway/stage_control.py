"""Stage-control channel (WO-STAGE/HANDS-1): a cloud angel drives the Oblivion app.

The Oblivion app on a user's Mac dials OUT to ``GET /v1/stage-control/ws`` with its
0blivion.io access token; this module holds that socket per principal and turns a
tool call into one ``command`` frame and its matching ``result``. The app runs the
argv through its own bundled ``oblivion`` CLI after its own tier check, so nothing
here decides what is allowed — it only routes, exactly once, to the right app.

Sibling of ``browser_control_broker`` (same rules, not an extension of it: that
broker's capability contract is frozen to browser verbs):

- At most one attached app per principal. A newer connection replaces the older;
  the older's pending commands fail with :class:`StageAppDisconnected`.
- Only the current owner may complete or detach; a replaced owner's teardown is a
  no-op. Completion is single-shot; late results after timeout/detach are ignored.
- Unknown principal, timeout and disconnect raise distinct errors. Nothing is
  retried — a ``send`` may already have landed.
"""

from __future__ import annotations

import secrets
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

#: Protocol version the app announces in its ``hello`` frame.
STAGE_CONTROL_PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 60.0
#: Extra wait past the command timeout for the app's result to travel back.
RESULT_GRACE_S = 5.0


class StageControlError(Exception):
    """Base error for stage-control dispatch."""


class StageAppNotConnected(StageControlError):
    """No Oblivion app is attached for this principal."""


class StageCommandTimeout(StageControlError):
    """The app did not answer in time (the command may still have run)."""


class StageAppDisconnected(StageControlError):
    """The app went away (or was replaced) while the command was pending."""


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[dict] = None
    disconnected: bool = False


@dataclass
class _App:
    owner: Any
    send: Callable[[dict], None]
    hello: dict
    pending: Dict[str, _Pending] = field(default_factory=dict)


class StageControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._apps: Dict[str, _App] = {}

    def attach(self, principal: str, send: Callable[[dict], None], *, owner: Any, hello: Optional[dict] = None) -> Any:
        """Attach ``owner`` for ``principal``. Returns the replaced owner (or None) so the
        transport can close it — the older app then knows to reconnect."""
        if not principal:
            raise ValueError("principal required")
        with self._lock:
            old = self._apps.get(principal)
            self._apps[principal] = _App(owner=owner, send=send, hello=dict(hello or {}))
            if old is not None:
                self._fail_all_locked(old)
        return old.owner if old is not None and old.owner is not owner else None

    def set_hello(self, principal: str, hello: dict, *, owner: Any) -> None:
        with self._lock:
            app = self._apps.get(principal)
            if app is not None and app.owner is owner:
                app.hello = dict(hello)

    def detach(self, principal: str, *, owner: Any) -> bool:
        with self._lock:
            app = self._apps.get(principal)
            if app is None or app.owner is not owner:
                return False
            del self._apps[principal]
            self._fail_all_locked(app)
        return True

    def is_attached(self, principal: str) -> bool:
        if not principal:
            return False
        with self._lock:
            return principal in self._apps

    def hello(self, principal: str) -> dict:
        with self._lock:
            app = self._apps.get(principal)
            return dict(app.hello) if app else {}

    def dispatch(self, principal: str, argv: Any, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
        try:
            timeout = float(timeout_s)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        if timeout <= 0:
            timeout = DEFAULT_TIMEOUT_S
        timeout = min(timeout, MAX_TIMEOUT_S)

        command_id = secrets.token_hex(16)
        pending = _Pending()
        with self._lock:
            app = self._apps.get(principal) if principal else None
            if app is None:
                raise StageAppNotConnected(f"no Oblivion app connected for {principal or 'this caller'}")
            app.pending[command_id] = pending
        wire_timeout = int(timeout) if timeout == int(timeout) else timeout
        frame = {"type": "command", "id": command_id, "argv": list(argv), "timeout_s": wire_timeout}
        try:
            app.send(frame)  # outside the lock: a sender may complete synchronously
        except Exception as exc:
            with self._lock:
                app.pending.pop(command_id, None)
            raise StageAppDisconnected(f"Oblivion app connection failed: {exc}") from exc

        # The app enforces `timeout` itself; the grace covers the result's trip back.
        pending.event.wait(timeout + RESULT_GRACE_S)
        with self._lock:
            # Decide under the lock that `complete` and detach also take: a result
            # that landed at the boundary wins over both timeout and disconnect.
            app.pending.pop(command_id, None)
            result, disconnected = pending.result, pending.disconnected
        if result is not None:
            return result
        if disconnected:
            raise StageAppDisconnected("Oblivion app disconnected before answering")
        raise StageCommandTimeout(f"Oblivion app did not answer within {timeout:g}s (the command may have run)")

    def complete(self, principal: str, frame: dict, *, owner: Any) -> bool:
        command_id = frame.get("id") if isinstance(frame, dict) else None
        if not isinstance(command_id, str):
            return False
        with self._lock:
            app = self._apps.get(principal)
            if app is None or app.owner is not owner:
                return False
            pending = app.pending.pop(command_id, None)
            if pending is None or pending.result is not None or pending.disconnected:
                return False
            pending.result = dict(frame)
            pending.event.set()
        return True

    @staticmethod
    def _fail_all_locked(app: _App) -> None:
        for pending in list(app.pending.values()):
            pending.disconnected = True
            pending.event.set()
        app.pending.clear()


_GLOBAL = StageControl()


def get_stage_control() -> StageControl:
    return _GLOBAL


# The principal whose app a turn may reach. Bound by the API server from the
# verified caller (0blivion.io token → its own principal; shared Face key → the
# configured owner) and carried into tool threads with the rest of the context.
_TURN_PRINCIPAL: ContextVar[str] = ContextVar("stage_control_turn_principal", default="")


def bind_turn_principal(principal: str) -> Token:
    return _TURN_PRINCIPAL.set(principal or "")


def reset_turn_principal(token: Token) -> None:
    _TURN_PRINCIPAL.reset(token)


def turn_principal() -> str:
    return _TURN_PRINCIPAL.get()


def owner_principal(config: Optional[dict] = None) -> str:
    """``stage_control.owner_principal`` from config.yaml (the shared Face key's app), or ``""``."""
    if config is None:
        try:
            from nyriel_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception:
            return ""
    section = config.get("stage_control") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return ""
    return str(section.get("owner_principal") or "").strip()
