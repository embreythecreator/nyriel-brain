"""Background tracker — the restart-resume guarantee.

A courier is en route whether or not the Brain is running. On plugin load
this daemon thread walks flesh.db for open dispatches and keeps polling
until they reach a terminal state, settling completed ones. Word's
_WriteQueue background thread is the precedent.

ponytail: fixed 60s interval, single thread; per-sinew rate policy when a
second high-tempo sinew exists.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60

_started = threading.Lock()
_thread = None


def poll_once() -> int:
    """One pass over open dispatches. Returns how many were polled."""
    from plugins.flesh.db import FleshDB
    from plugins.flesh.handlers import _poll_one

    db = FleshDB()
    db.expire_stale_approvals()
    open_dispatches = db.open_dispatches()
    for dispatch in open_dispatches:
        try:
            _poll_one(db, dispatch)
        except Exception:
            logger.exception("flesh poll failed for %s", dispatch.get("dispatch_id"))
    return len(open_dispatches)


def _loop(stop: threading.Event) -> None:
    while not stop.wait(POLL_INTERVAL_SECONDS):
        try:
            poll_once()
        except Exception:
            logger.exception("flesh poller pass failed")


def start() -> None:
    """Idempotent daemon-thread start; also runs one immediate resume pass."""
    global _thread
    with _started:
        if _thread is not None and _thread.is_alive():
            return
        try:
            resumed = poll_once()
            if resumed:
                logger.info("flesh poller resumed %d open dispatch(es)", resumed)
        except Exception:
            logger.exception("flesh poller resume pass failed")
        stop = threading.Event()
        _thread = threading.Thread(target=_loop, args=(stop,), daemon=True, name="flesh-poller")
        _thread.start()
