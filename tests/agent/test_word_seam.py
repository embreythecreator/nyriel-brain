"""Word seam accessor tests (WO-MOA/2 checklist 16, live part).

No plugin import, no HTTP: the seam is exercised through fake providers
registered the way the real plugin registers itself.
"""

from types import SimpleNamespace

from agent.word_seam import (
    build_pack_for_turn,
    capture,
    get_word_provider,
    prefetched_ids,
    retrieve,
)

_HITS = [
    {"id": "note:a", "snippet": "canon says X", "title": "A"},
    {"id": "note:b", "snippet": "WO ratified Y", "title": "B"},
    "not-a-dict",
]


class _FakeProvider:
    name = "word"

    def __init__(self, hits=None, available=True, raises=False):
        self._hits = hits if hits is not None else list(_HITS)
        self._available = available
        self._raises = raises
        self.calls = []

    def is_available(self):
        return self._available

    def _search_hydrated(self, query, *, limit=6):
        self.calls.append((query, limit))
        if self._raises:
            raise RuntimeError("word is down")
        return self._hits


class _FakeManager:
    def __init__(self, provider=None):
        self._provider = provider

    def get_provider(self, name):
        return self._provider if (self._provider and name == "word") else None


def _agent(provider=None, prefetch_ids=None):
    return SimpleNamespace(
        _memory_manager=_FakeManager(provider),
        _word_prefetch_ids=prefetch_ids,
    )


class TestProviderLookup:
    def test_no_manager_no_provider(self):
        assert get_word_provider(SimpleNamespace()) is None

    def test_unregistered_returns_none(self):
        assert get_word_provider(_agent(None)) is None

    def test_unavailable_provider_is_none(self):
        assert get_word_provider(_agent(_FakeProvider(available=False))) is None

    def test_registered_and_available(self):
        p = _FakeProvider()
        assert get_word_provider(_agent(p)) is p


class TestRetrieve:
    def test_returns_dict_hits_only(self):
        p = _FakeProvider()
        hits = retrieve(_agent(p), "cache key stability", limit=5)
        assert [h["id"] for h in hits] == ["note:a", "note:b"]
        assert p.calls == [("cache key stability", 5)]

    def test_blank_query_short_circuits(self):
        p = _FakeProvider()
        assert retrieve(_agent(p), "   ") == []
        assert p.calls == []

    def test_word_down_degrades_to_empty(self):
        assert retrieve(_agent(_FakeProvider(raises=True)), "q") == []

    def test_missing_word_degrades_to_empty(self):
        assert retrieve(_agent(None), "q") == []


class TestPrefetchedIds:
    def test_absent_is_empty(self):
        assert prefetched_ids(SimpleNamespace()) == set()

    def test_coerced_to_str_set(self):
        assert prefetched_ids(_agent(None, prefetch_ids=["note:a", 7])) == {
            "note:a",
            "7",
        }


class TestBuildPackForTurn:
    def test_gate_blocks_casual_turn_without_search(self):
        p = _FakeProvider()
        pack = build_pack_for_turn(_agent(p), "q", "ensemble")
        assert pack.empty and p.calls == []  # no Word call at all

    def test_casual_turn_with_flag_retrieves(self):
        p = _FakeProvider()
        pack = build_pack_for_turn(
            _agent(p), "q", "ensemble", casual_recall_enabled=True
        )
        assert not pack.empty and p.calls

    def test_plan_lifecycle_always_retrieves_and_dedups(self):
        p = _FakeProvider()
        pack = build_pack_for_turn(
            _agent(p, prefetch_ids=["note:a"]), "q", "debate"
        )
        assert [n["id"] for n in pack.notes] == ["note:b"]
        assert pack.dropped_as_prefetched == 1
        assert pack.snapshot_ref.startswith("wordpack:")

    def test_verify_turn_with_word_down_yields_empty_pack(self):
        pack = build_pack_for_turn(_agent(_FakeProvider(raises=True)), "q", "verify")
        assert pack.empty


class _WritingProvider(_FakeProvider):
    def __init__(self, result="note:1", raises=False):
        super().__init__()
        self._result = result
        self._write_raises = raises
        self.writes = []

    def capture_note(self, title, content, *, metadata=None):
        self.writes.append((title, content, metadata))
        if self._write_raises:
            raise RuntimeError("word is down")
        return self._result


class TestCapture:
    def test_returns_vault_id_and_passes_metadata(self):
        p = _WritingProvider()
        assert capture(_agent(p), "T", "body", metadata={"posture": "plan"}) == "note:1"
        assert p.writes == [("T", "body", {"posture": "plan"})]

    def test_blank_input_never_touches_provider(self):
        p = _WritingProvider()
        assert capture(_agent(p), " ", "body") is None
        assert capture(_agent(p), "T", "") is None
        assert p.writes == []

    def test_no_provider_or_no_write_half_is_none(self):
        assert capture(_agent(None), "T", "body") is None
        assert capture(_agent(_FakeProvider()), "T", "body") is None
        assert capture(_agent(_FakeProvider(available=False)), "T", "body") is None

    def test_queued_or_raising_write_is_none(self):
        assert capture(_agent(_WritingProvider(result=None)), "T", "body") is None
        assert capture(_agent(_WritingProvider(raises=True)), "T", "body") is None
