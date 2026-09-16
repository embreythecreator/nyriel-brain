"""Typed artifact-event extraction for streaming API clients."""

import json

from gateway.platforms.api_server import _artifact_event_from_tool_result


def test_save_plan_completion_becomes_typed_editable_plan_artifact():
    event = _artifact_event_from_tool_result(
        "save_plan",
        {"title": "Side panel", "content": "# Side panel\n\n- [ ] Build\n"},
        json.dumps({
            "plan_id": "d8f1ad9b1234",
            "short_id": "d8f1ad9b",
            "revision": 1,
            "path": "/work/.nyriel/plans/side-panel.md",
            "digest": "abc",
        }),
    )

    assert event == {
        "id": "d8f1ad9b1234",
        "type": "plan",
        "title": "Side panel",
        "content": "# Side panel\n\n- [ ] Build\n",
        "path": "/work/.nyriel/plans/side-panel.md",
        "revision": 1,
        "short_id": "d8f1ad9b",
        "editable": True,
    }


def test_write_file_completion_becomes_generic_text_artifact():
    event = _artifact_event_from_tool_result(
        "write_file",
        {"path": "notes/release.md", "content": "# Release\n"},
        json.dumps({
            "success": True,
            "resolved_path": "/work/notes/release.md",
            "files_modified": ["/work/notes/release.md"],
        }),
    )

    assert event["id"] == "/work/notes/release.md"
    assert event["type"] == "markdown"
    assert event["title"] == "release.md"
    assert event["content"] == "# Release\n"
    assert event["editable"] is False


def test_failed_or_read_only_tool_results_do_not_claim_artifacts():
    assert _artifact_event_from_tool_result(
        "write_file",
        {"path": "x.md", "content": "x"},
        json.dumps({"error": "denied"}),
    ) is None
    assert _artifact_event_from_tool_result(
        "read_file",
        {"path": "x.md"},
        "contents",
    ) is None
