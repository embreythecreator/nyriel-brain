"""Transcript appends wait out a transient compression lock.

A compaction holding the session's writer lock is not a disk fault: the
lease clears on its own. Before this, a turn that merely collided with a
compaction was discarded and the operator was told to check for a full
disk.
"""

from unittest.mock import MagicMock  # noqa: F401  (kept for parity with suite)

import pytest

from run_agent import AIAgent
from nyriel_state import SessionCompressionInProgressError


class _DB:
    """Refuses `fail_times` appends with a lock error, then accepts."""

    def __init__(self, fail_times: int, error=None):
        self.fail_times = fail_times
        self.calls = 0
        self.error = error or SessionCompressionInProgressError("locked")
        self.written = None

    def append_messages_batch(self, *, session_id, messages, compression_lock_holder):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        self.written = list(messages)


def _agent(db) -> AIAgent:
    a = AIAgent.__new__(AIAgent)  # no __init__: this path needs only these
    a._session_db = db
    a.session_id = "s-lock"
    a._active_compression_lock_holder = None
    return a


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    slept = []
    monkeypatch.setattr("run_agent.time.sleep", lambda s: slept.append(s))
    return slept


def test_transient_lock_is_waited_out(_no_real_sleeping):
    db = _DB(fail_times=3)
    _agent(db)._append_batch_with_compression_patience([{"role": "user"}])
    assert db.calls == 4  # 3 refusals, then success
    assert db.written == [{"role": "user"}]
    assert _no_real_sleeping == [0.25, 0.5, 1.0]  # backoff, not a busy spin


def test_first_attempt_success_does_not_sleep(_no_real_sleeping):
    db = _DB(fail_times=0)
    _agent(db)._append_batch_with_compression_patience([{"role": "user"}])
    assert db.calls == 1 and _no_real_sleeping == []


def test_patience_is_bounded_and_reraises(_no_real_sleeping):
    # A lock that outlives our patience must still fail the turn — blocking a
    # live turn indefinitely is worse than failing it.
    db = _DB(fail_times=99)
    with pytest.raises(SessionCompressionInProgressError):
        _agent(db)._append_batch_with_compression_patience([{"role": "user"}])
    assert db.calls == 6  # 1 + len(_COMPRESSION_LOCK_RETRY_DELAYS)
    assert sum(_no_real_sleeping) < 10  # bounded, not an 80s stall


def test_other_errors_are_not_retried(_no_real_sleeping):
    # A real disk/permission fault must fail fast, not stall behind backoff.
    db = _DB(fail_times=99, error=OSError("No space left on device"))
    with pytest.raises(OSError):
        _agent(db)._append_batch_with_compression_patience([{"role": "user"}])
    assert db.calls == 1 and _no_real_sleeping == []


class TestOperatorMessage:
    def _msg(self, error=None):
        return AIAgent._format_turn_completion_explanation(
            "session_persistence_failed", error
        )

    def test_lock_failure_does_not_blame_the_disk(self):
        msg = self._msg(SessionCompressionInProgressError("locked"))
        assert "compaction" in msg.lower()
        assert "Nothing is wrong with your disk" in msg
        assert "micro_compact" in msg

    def test_real_storage_failure_still_names_disk(self):
        assert "full disk" in self._msg(OSError("No space left on device"))

    def test_unknown_cause_falls_back_safely(self):
        assert "full disk" in self._msg(None)
