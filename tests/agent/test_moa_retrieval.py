"""Retrieval-pack core + unresolved-critique marker tests (WO-MOA/2 R7/R6)."""

from agent.moa_retrieval import (
    RetrievalPack,
    build_retrieval_pack,
    needs_recall,
    render_pack,
)
from agent.moa_vote import format_unresolved_critiques

_HITS = [
    {"id": "note:a", "snippet": "canon says X"},
    {"id": "note:b", "content": "WO-H/RF-1 ratified Y"},
    {"id": "note:a", "snippet": "duplicate of a"},
    {"id": "note:c", "title": "seam doc"},
    {"id": "", "snippet": "no id — dropped"},
    {"id": "note:d", "snippet": ""},
]


class TestNeedsRecall:
    def test_plan_lifecycle_always_retrieves(self):
        assert needs_recall("debate") is True
        assert needs_recall("verify") is True
        # Flag cannot turn plan-lifecycle retrieval off.
        assert needs_recall("DEBATE", casual_recall_enabled=False) is True

    def test_casual_turns_follow_flag(self):
        assert needs_recall("ensemble") is False
        assert needs_recall("self_consistency") is False
        assert needs_recall("ensemble", casual_recall_enabled=True) is True


class TestBuildPack:
    def test_dedup_prefetch_subtraction_and_cap(self):
        pack = build_retrieval_pack(_HITS, prefetch_ids=["note:b"])
        ids = [n["id"] for n in pack.notes]
        assert ids == ["note:a", "note:c"]  # b subtracted, dup a dropped,
        assert pack.dropped_as_prefetched == 1  # empty-text d dropped
        assert pack.snapshot_ref.startswith("wordpack:")

    def test_snapshot_ref_is_order_independent_and_content_bound(self):
        a = build_retrieval_pack(_HITS[:2])
        b = build_retrieval_pack(list(reversed(_HITS[:2])))
        assert a.snapshot_ref == b.snapshot_ref
        changed = [dict(_HITS[0]), {"id": "note:b", "content": "EDITED"}]
        assert build_retrieval_pack(changed).snapshot_ref != a.snapshot_ref

    def test_max_notes_cap_preserves_ranking(self):
        many = [{"id": f"note:{i}", "snippet": f"s{i}"} for i in range(20)]
        pack = build_retrieval_pack(many, max_notes=3)
        assert [n["id"] for n in pack.notes] == ["note:0", "note:1", "note:2"]

    def test_render_pack(self):
        pack = build_retrieval_pack(_HITS[:2])
        text = render_pack(pack)
        assert pack.snapshot_ref in text
        assert "(note:a) canon says X" in text
        assert render_pack(build_retrieval_pack([])) == ""


class TestUnresolvedCritiques:
    def test_orphans_are_marked(self):
        out = format_unresolved_critiques(
            {"GPT-Wing": "the schema misses tenancy", "Kimi": "cache key unstable"}
        )
        assert "unresolved, NOT conceded" in out
        assert "GPT-Wing (unresolved — weigh accordingly): the schema misses tenancy" in out
        assert "Kimi (unresolved — weigh accordingly): cache key unstable" in out

    def test_empty_renders_empty(self):
        assert format_unresolved_critiques({}) == ""
