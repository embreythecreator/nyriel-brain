"""Plane progress/status thread-routing regressions."""

from gateway.run import _resolve_progress_thread_id


def test_plane_progress_without_source_thread_uses_triggering_event():
    """Progress for a Plane reply must inherit the triggering event anchor."""
    assert (
        _resolve_progress_thread_id(
            "plane",
            source_thread_id=None,
            event_message_id="plane-event-123",
        )
        == "plane-event-123"
    )


def test_plane_progress_preserves_explicit_source_thread():
    assert (
        _resolve_progress_thread_id(
            "plane",
            source_thread_id="explicit-thread-root",
            event_message_id="plane-event-123",
        )
        == "explicit-thread-root"
    )
