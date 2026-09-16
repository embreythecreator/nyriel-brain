"""Generated media from hosted generators must reach the operator's eyes.

Higgsfield/Seedance return an https URL from a generic shell tool, so the
MEDIA: pipeline — which only matches local absolute paths from a four-tool
allowlist — never saw them, and the chat showed prose describing a picture the
operator could not look at.

    python -m pytest tests/test_remote_media.py -q
    python tests/test_remote_media.py          # same checks, no pytest
"""

import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Import the two helpers without booting the gateway (run.py pulls in the world).
_SRC = (pathlib.Path(__file__).resolve().parent.parent / "gateway" / "run.py").read_text()
_NS = {"re": re, "os": os, "List": list, "Dict": dict, "Any": object}
exec(compile(  # noqa: S102
    _SRC[_SRC.index("# ---- remote generated media"): _SRC.index("def _collect_auto_append_media_tags(")],
    "run.py", "exec",
), _NS)

collect_remote_media_markdown = _NS["collect_remote_media_markdown"]
_is_allowed_media_host = _NS["_is_allowed_media_host"]

HF_IMAGE = "https://d8j0ntlcm91z4.cloudfront.net/user_3GD/hf_20260804_150648_0dc5c525.png"
KIE_VIDEO = "https://cdn.kie.ai/out/final-full-hd-25fps.mp4"


def tool(content):
    return {"role": "tool", "content": content}


def test_higgsfield_image_becomes_an_inline_image():
    assert collect_remote_media_markdown([tool(f"Job complete.\nURL: {HF_IMAGE}\n")]) == [
        f"![generated media]({HF_IMAGE})"
    ]


def test_video_goes_out_as_a_link_for_the_face_to_upgrade():
    # thread-view.js decorateMediaLinks() turns this into a <video> player;
    # markdown cannot express video, so a link is the correct wire form.
    assert collect_remote_media_markdown([tool(f"done {KIE_VIDEO}")]) == [
        f"[generated media]({KIE_VIDEO})"
    ]


def test_unknown_host_is_never_auto_embedded():
    # Without the host allowlist, any .png a command printed — a docs link, a
    # CI artifact, a tracking pixel — would be fetched by the operator's browser.
    assert collect_remote_media_markdown([tool("see https://example.com/docs/diagram.png")]) == []
    assert not _is_allowed_media_host("example.com")
    assert not _is_allowed_media_host("evil-higgsfield.ai.attacker.test")


def test_host_allowlist_is_operator_extensible():
    os.environ["NYRIEL_MEDIA_URL_HOSTS"] = "img.mycdn.test"
    try:
        assert collect_remote_media_markdown([tool("https://img.mycdn.test/a/b.png")]) == [
            "![generated media](https://img.mycdn.test/a/b.png)"
        ]
    finally:
        del os.environ["NYRIEL_MEDIA_URL_HOSTS"]


def test_a_url_the_model_already_linked_is_not_duplicated():
    assert collect_remote_media_markdown([tool(HF_IMAGE)], already_present=f"here it is {HF_IMAGE}") == []


def test_repeated_urls_collapse():
    assert len(collect_remote_media_markdown([tool(HF_IMAGE), tool(HF_IMAGE)])) == 1


def test_history_offset_keeps_stale_media_off_a_later_reply():
    # Same current-turn scoping as _collect_auto_append_media_tags (#34608):
    # a URL printed several turns ago must not reappear on a text-only reply.
    assert collect_remote_media_markdown([tool(HF_IMAGE), tool("nothing")], history_offset=1) == []


def test_only_tool_results_are_scanned():
    assert collect_remote_media_markdown([{"role": "assistant", "content": HF_IMAGE}]) == []


def test_trailing_punctuation_stays_out_of_the_url():
    out = collect_remote_media_markdown([tool(f"saved to {HF_IMAGE}, then copied")])
    assert out[0].endswith(".png)"), out


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print("\nall remote-media checks passed" if not failures else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
