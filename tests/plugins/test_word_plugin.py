"""Tests for the Word memory plugin.

Covers: _Client (headers, notebook find-or-create), _WriteQueue durability
(flush, retry-on-down, crash replay), _build_overlay formatting, and
WordMemoryProvider lifecycle / prefetch health-gate / tools / memory-write
mirroring. All HTTP is mocked — no live Word needed.
"""

import json
import sqlite3
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Repo root on sys.path so the plugin can import agent.memory_provider
import sys
_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from plugins.memory import word as word_mod
from plugins.memory.word import (
    WordMemoryProvider,
    _build_overlay,
    _Client,
    _WriteQueue,
)


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    nyriel_home = tmp_path / ".nyriel"
    nyriel_home.mkdir()
    monkeypatch.setenv("NYRIEL_HOME", str(nyriel_home))
    for var in ("WORD_BASE_URL", "WORD_WARD_TOKEN", "OPEN_NOTEBOOK_WARD_TOKEN",
                "WORD_NOTEBOOK", "WORD_SEARCH_TYPE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _cap_word_sleeps(monkeypatch):
    """Cap the write queue's retry sleep so failure-path tests stay fast."""
    real_sleep = word_mod.time.sleep
    fake_time = types.SimpleNamespace(
        sleep=lambda s: real_sleep(min(float(s), 0.05)),
        time=word_mod.time.time,
    )
    monkeypatch.setattr(word_mod, "time", fake_time)


def _mk_client(**kw) -> _Client:
    return _Client(
        kw.get("base_url", "http://127.0.0.1:5055"),
        kw.get("token", "tok"),
        kw.get("notebook", "Nyriel Memory"),
    )


# ---------------------------------------------------------------------------
# _Client
# ---------------------------------------------------------------------------

class TestClient:
    def test_headers_include_bearer(self):
        c = _mk_client(token="Bearer abc ")
        assert c._headers()["Authorization"] == "Bearer abc"

    def test_headers_omit_auth_when_no_token(self):
        c = _mk_client(token="")
        assert "Authorization" not in c._headers()

    def test_base_url_trailing_slash_stripped(self):
        c = _mk_client(base_url="http://x:5055///")
        assert c.base_url == "http://x:5055"

    def test_request_raises_on_error_with_detail(self):
        c = _mk_client()
        resp = MagicMock(ok=False, status_code=401)
        resp.json.return_value = {"detail": "Invalid bearer token"}
        with patch("requests.request", return_value=resp):
            with pytest.raises(RuntimeError, match="Invalid bearer token"):
                c.request("GET", "/api/notebooks")

    def test_ensure_notebook_finds_existing(self):
        c = _mk_client()
        c.request = MagicMock(return_value=[
            {"id": "notebook:other", "name": "Research"},
            {"id": "notebook:mem", "name": "Nyriel Memory"},
        ])
        assert c.ensure_notebook() == "notebook:mem"
        # Cached: second call does not re-request
        c.request.reset_mock()
        assert c.ensure_notebook() == "notebook:mem"
        c.request.assert_not_called()

    def test_ensure_notebook_creates_when_missing(self):
        c = _mk_client()

        def fake_request(method, path, **kw):
            if method == "GET":
                return []
            assert method == "POST"
            assert kw["json_body"]["name"] == "Nyriel Memory"
            return {"id": "notebook:new"}

        c.request = MagicMock(side_effect=fake_request)
        assert c.ensure_notebook() == "notebook:new"

    def test_create_note_targets_memory_notebook(self):
        c = _mk_client()
        c._notebook_id = "notebook:mem"
        c.request = MagicMock(return_value={"id": "note:1"})
        c.create_note("t", "c")
        body = c.request.call_args.kwargs["json_body"]
        assert body["notebook_id"] == "notebook:mem"
        assert body["note_type"] == "ai"

    def test_health_false_on_connection_error(self):
        c = _mk_client()
        with patch("requests.request", side_effect=ConnectionError("down")):
            assert c.health() is False


# ---------------------------------------------------------------------------
# _WriteQueue
# ---------------------------------------------------------------------------

class TestWriteQueue:
    def test_enqueue_flushes_to_word(self, tmp_path):
        client = MagicMock()
        q = _WriteQueue(client, tmp_path / "q.db")
        q.enqueue("title", "content")
        deadline = time.time() + 5
        while q.pending_count() and time.time() < deadline:
            time.sleep(0.05)
        q.shutdown()
        client.create_note.assert_called_with("title", "content")
        assert q.pending_count() == 0

    def test_failed_write_stays_pending(self, tmp_path):
        client = MagicMock()
        client.create_note.side_effect = RuntimeError("word down")
        q = _WriteQueue(client, tmp_path / "q.db")
        q.enqueue("t", "c")
        time.sleep(0.3)
        q.shutdown()
        assert q.pending_count() == 1
        row = sqlite3.connect(str(tmp_path / "q.db")).execute(
            "SELECT last_error FROM pending").fetchone()
        assert "word down" in row[0]

    def test_pending_rows_replay_on_startup(self, tmp_path):
        db = tmp_path / "q.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, content TEXT, created_at TEXT, last_error TEXT)""")
        conn.execute("INSERT INTO pending (title, content, created_at) VALUES ('a','b','now')")
        conn.commit()
        conn.close()

        client = MagicMock()
        q = _WriteQueue(client, db)
        deadline = time.time() + 5
        while q.pending_count() and time.time() < deadline:
            time.sleep(0.05)
        q.shutdown()
        client.create_note.assert_called_with("a", "b")


# ---------------------------------------------------------------------------
# _build_overlay
# ---------------------------------------------------------------------------

class TestOverlay:
    def test_formats_items_with_ids(self):
        out = _build_overlay([
            {"id": "note:1", "title": "Fact", "snippet": "the sky is blue"},
        ])
        assert out.startswith("[Word Long-Term Memory]")
        assert "Fact" in out and "note:1" in out

    def test_empty_and_snippetless_items_yield_empty(self):
        assert _build_overlay([]) == ""
        assert _build_overlay([{"id": "note:1", "title": "t", "snippet": ""}]) == ""


# ---------------------------------------------------------------------------
# WordMemoryProvider
# ---------------------------------------------------------------------------

def _init_provider(monkeypatch, tmp_path, *, healthy=True) -> WordMemoryProvider:
    p = WordMemoryProvider()
    with patch.object(_Client, "health", return_value=healthy), \
         patch.object(_Client, "ensure_notebook", return_value="notebook:mem"):
        p.initialize("session-1")
    return p


class TestProvider:
    def test_name_and_availability(self):
        p = WordMemoryProvider()
        assert p.name == "word"
        assert p.is_available() is True

    def test_initialize_healthy_creates_notebook(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        assert p._healthy is True
        assert p._queue is not None
        p.shutdown()

    def test_initialize_down_is_nonfatal(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=False)
        assert p._healthy is False
        assert p.prefetch("anything") == ""     # degrades to silence
        p.shutdown()

    def test_env_config_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORD_BASE_URL", "http://elsewhere:9999/")
        monkeypatch.setenv("OPEN_NOTEBOOK_WARD_TOKEN", "fallback-tok")
        monkeypatch.setenv("WORD_SEARCH_TYPE", "bogus")
        p = _init_provider(monkeypatch, tmp_path, healthy=False)
        assert p._client.base_url == "http://elsewhere:9999"
        assert p._client.token == "fallback-tok"
        assert p._search_type == "text"          # bogus falls back to text
        p.shutdown()

    def test_prefetch_builds_overlay_from_hydrated_hits(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.search = MagicMock(return_value=[
            {"id": "note:1", "relevance": 0.9},
        ])
        p._client.get_note = MagicMock(return_value={
            "id": "note:1", "title": "Preference",
            "content": "likes terse answers", "notebook_id": "notebook:mem",
        })
        out = p.prefetch("answers")
        assert "[Word Long-Term Memory]" in out
        assert "likes terse answers" in out and "note:1" in out
        p.shutdown()

    def test_prefetch_failure_trips_backoff(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.search = MagicMock(side_effect=RuntimeError("boom"))
        assert p.prefetch("q") == ""
        assert p._healthy is False
        # Inside the backoff window, no further Word calls happen
        p._client.health = MagicMock()
        assert p.prefetch("q") == ""
        p._client.health.assert_not_called()
        p.shutdown()

    def test_sync_turn_is_noop(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._queue.enqueue = MagicMock()
        p.sync_turn("user says", "assistant says")
        p._queue.enqueue.assert_not_called()
        p.shutdown()

    def test_tool_schemas_exactly_three(self):
        p = WordMemoryProvider()
        names = [s["name"] for s in p.get_tool_schemas()]
        assert names == ["word_memory_search", "word_memory_save", "word_memory_read"]

    def test_tool_save_enqueues(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._queue.enqueue = MagicMock()
        result = json.loads(p.handle_tool_call(
            "word_memory_save", {"title": "T", "content": "C"}))
        assert result["status"] == "queued"
        p._queue.enqueue.assert_called_once_with("T", "C")
        p.shutdown()

    def test_tool_search_memory_scope_filters_notebook(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._search_hydrated = MagicMock(return_value=[
            {"id": "note:in", "title": "a", "snippet": "s", "notebook_id": "notebook:mem", "relevance": 1},
            {"id": "note:out", "title": "b", "snippet": "s", "notebook_id": "notebook:other", "relevance": 1},
        ])
        with patch.object(_Client, "ensure_notebook", return_value="notebook:mem"):
            result = json.loads(p.handle_tool_call(
                "word_memory_search", {"query": "q"}))
        ids = [r["id"] for r in result["results"]]
        assert ids == ["note:in"]
        p.shutdown()

    def test_tool_search_all_scope_keeps_everything(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._search_hydrated = MagicMock(return_value=[
            {"id": "note:in", "title": "a", "snippet": "s", "notebook_id": "notebook:mem", "relevance": 1},
            {"id": "source:x", "title": "b", "snippet": "s", "notebook_id": None, "relevance": 1},
        ])
        result = json.loads(p.handle_tool_call(
            "word_memory_search", {"query": "q", "scope": "all"}))
        assert len(result["results"]) == 2
        p.shutdown()

    def test_tool_read_fetches_note(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.get_note = MagicMock(return_value={
            "id": "note:9", "title": "T", "content": "C"})
        result = json.loads(p.handle_tool_call(
            "word_memory_read", {"note_id": "note:9"}))
        assert result == {"id": "note:9", "title": "T", "content": "C"}
        p.shutdown()

    def test_tool_error_when_uninitialized(self):
        p = WordMemoryProvider()
        out = p.handle_tool_call("word_memory_search", {"query": "q"})
        assert "not initialized" in out

    def test_on_memory_write_mirrors_adds_only(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._queue.enqueue = MagicMock()
        p.on_memory_write("add", "user", "prefers dark mode",
                          metadata={"session_id": "s-1"})
        title, content = p._queue.enqueue.call_args.args
        assert title.startswith("[user]")
        assert "prefers dark mode" in content and "s-1" in content
        p._queue.enqueue.reset_mock()
        p.on_memory_write("remove", "memory", "old fact")
        p._queue.enqueue.assert_not_called()
        p.shutdown()

    def test_on_delegation_mirrors_task_result_provenance(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._queue.enqueue = MagicMock()
        p.on_delegation("research arXiv on X", "found 3 papers",
                        child_session_id="c-1", posture="plan")
        title, content = p._queue.enqueue.call_args.args
        assert title.startswith("[delegation]")
        for needle in ("research arXiv on X", "found 3 papers",
                       "origin=delegate_task", "child_session=c-1", "posture=plan"):
            assert needle in content
        p._queue.enqueue.reset_mock()
        p.on_delegation("task", "")
        p._queue.enqueue.assert_not_called()
        p.shutdown()

    def test_discovery_lists_word(self):
        from plugins.memory import discover_memory_providers
        names = [n for n, _desc, _avail in discover_memory_providers()]
        assert "word" in names
