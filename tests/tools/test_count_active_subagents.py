"""WO-PLAN/BUSY-1: session-scoped active-subagent count for busy messages."""

from tools import delegate_tool as dt


def test_count_owned_and_fallback():
    with dt._active_subagents_lock:
        saved = dict(dt._active_subagents)
        dt._active_subagents.clear()
    try:
        with dt._active_subagents_lock:
            dt._active_subagents["a"] = {"subagent_id": "a", "owner_session_id": "s1"}
            dt._active_subagents["b"] = {"subagent_id": "b", "owner_session_id": "s1"}
            dt._active_subagents["c"] = {"subagent_id": "c", "owner_session_id": "s2"}
        assert dt.count_active_subagents_for_session("s1") == 2
        assert dt.count_active_subagents_for_session("s2") == 1
        # Owned records exist, so an unknown session attributes nothing.
        assert dt.count_active_subagents_for_session("s3") == 0
        assert dt.count_active_subagents_for_session("") == 0

        # No ownership stamped anywhere -> global fallback count.
        with dt._active_subagents_lock:
            dt._active_subagents.clear()
            dt._active_subagents["x"] = {"subagent_id": "x"}
        assert dt.count_active_subagents_for_session("s1") == 1
    finally:
        with dt._active_subagents_lock:
            dt._active_subagents.clear()
            dt._active_subagents.update(saved)
