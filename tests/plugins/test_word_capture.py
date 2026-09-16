"""WO-POSTURE/1 1.1 — capture_note idempotency, queue ledger, dead-letter.

All HTTP mocked. Reuses the isolation fixtures/helpers of test_word_plugin.
"""

import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from plugins.memory import word as word_mod
from plugins.memory.word import _Client, _WriteQueue
from tests.plugins.test_word_plugin import (  # noqa: F401 - fixtures
    _cap_word_sleeps,
    _init_provider,
    _isolate_env,
)


def _drain(q, seconds=5):
    deadline = time.time() + seconds
    while q.pending_count() and time.time() < deadline:
        time.sleep(0.05)


class TestCaptureNote:
    def test_sync_write_returns_id_and_second_call_makes_no_http(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.create_note = MagicMock(return_value={"id": "note:1"})
        assert p.capture_note("T", "body", metadata={"session_id": "s"}) == "note:1"
        assert p.capture_note("T", "body", metadata={"session_id": "other"}) == "note:1"
        assert p._client.create_note.call_count == 1
        _, content = p._client.create_note.call_args.args
        assert content.endswith("— capture: session_id=s")
        p.shutdown()

    def test_url_origin_also_registers_link_source(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.create_note = MagicMock(return_value={"id": "note:2"})
        p._client.create_source = MagicMock(side_effect=RuntimeError("no fetch"))
        assert p.capture_note("Paper", "body", metadata={"origin": "https://arxiv.org/abs/1"}) == "note:2"
        p._client.create_source.assert_called_once_with("https://arxiv.org/abs/1", title="Paper")
        p._client.create_source.reset_mock()
        p.capture_note("File", "body2", metadata={"origin": "/repo/a.py"})
        p._client.create_source.assert_not_called()
        p.shutdown()

    def test_word_down_queues_and_returns_none_once(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=False)
        p._queue.enqueue = MagicMock(wraps=p._queue.enqueue)
        p._client.create_note = MagicMock(side_effect=RuntimeError("down"))
        assert p.capture_note("T", "body") is None
        assert p.capture_note("T", "body") is None
        assert p._queue.enqueue.call_count == 1
        p.shutdown()

    def test_queued_capture_learns_vault_id_on_flush(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=False)
        p._client.create_note = MagicMock(return_value={"id": "note:9"})
        assert p.capture_note("T", "body") is None
        _drain(p._queue)
        assert p.capture_note("T", "body") == "note:9"
        assert p._client.create_note.call_count == 1
        p.shutdown()

    def test_empty_or_uninitialized_is_none(self):
        p = word_mod.WordMemoryProvider()
        assert p.capture_note("T", "body") is None
        assert p.capture_note("", "body") is None


class TestQueueLedger:
    def test_dead_letter_after_max_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(word_mod, "_MAX_WRITE_ATTEMPTS", 3)
        client = MagicMock()
        client.create_note.side_effect = RuntimeError("word down")
        q = _WriteQueue(client, tmp_path / "q.db")
        q.enqueue("t", "c")
        _drain(q)
        q.shutdown()
        conn = sqlite3.connect(str(tmp_path / "q.db"))
        assert q.pending_count() == 0
        dead = conn.execute("SELECT title, last_error FROM dead").fetchall()
        assert dead == [("t", "word down")]
        assert client.create_note.call_count == 3

    def test_paged_replay_exceeds_old_limit(self, tmp_path):
        db = tmp_path / "q.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, content TEXT, created_at TEXT, last_error TEXT)""")
        conn.executemany(
            "INSERT INTO pending (title, content, created_at) VALUES (?,?,?)",
            [(f"t{i}", "c", "now") for i in range(250)],
        )
        conn.commit()
        conn.close()
        client = MagicMock()
        q = _WriteQueue(client, db)  # migrates the old schema in place
        _drain(q, 10)
        q.shutdown()
        assert q.pending_count() == 0
        assert client.create_note.call_count == 250


class TestCreateSource:
    def test_link_source_targets_memory_notebook(self):
        c = _Client("http://w", "tok", "Nyriel Memory")
        with patch.object(_Client, "ensure_notebook", return_value="nb:1"), \
             patch.object(_Client, "request", return_value={"id": "source:1"}) as req:
            assert c.create_source("https://arxiv.org/abs/1", title="P")["id"] == "source:1"
        method, path = req.call_args.args
        body = req.call_args.kwargs["json_body"]
        assert (method, path) == ("POST", "/api/sources/json")
        assert body["type"] == "link" and body["notebooks"] == ["nb:1"]
        assert body["provenance"] == "ingested-external"


class TestQuarantine:
    def test_quarantine_metadata_routes_sync_write(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.create_note = MagicMock(return_value={"id": "note:q"})
        assert p.capture_note("T", "body", metadata={"quarantine": True}) == "note:q"
        assert p._client.create_note.call_args.kwargs == {"quarantine": True}
        p.shutdown()

    def test_quarantine_flag_survives_the_queue(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=False)
        p._client.create_note = MagicMock(return_value={"id": "note:q"})
        assert p.capture_note("T", "body", metadata={"quarantine": True}) is None
        _drain(p._queue)
        p.shutdown()
        assert p._client.create_note.call_args.kwargs == {"quarantine": True}

    def test_client_routes_to_quarantine_notebook(self):
        c = _Client("http://w", "tok", "Mem")
        with patch.object(_Client, "ensure_notebook", side_effect=lambda name=None: f"nb:{name or 'Mem'}"), \
             patch.object(_Client, "request", return_value={"id": "note:1"}) as req:
            c.create_note("t", "c", quarantine=True)
            assert req.call_args.kwargs["json_body"]["notebook_id"] == f"nb:{word_mod._QUARANTINE_NOTEBOOK}"
            c.create_note("t", "c")
            assert req.call_args.kwargs["json_body"]["notebook_id"] == "nb:Mem"

    def test_prefetch_excludes_ledger_quarantined_ids(self, monkeypatch, tmp_path):
        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.create_note = MagicMock(side_effect=[{"id": "note:bad"}, {"id": "note:ok"}])
        p.capture_note("bad", "unapproved claim", metadata={"quarantine": True, "session_id": "s-plan"})
        p.capture_note("ok", "trusted fact", metadata={"quarantine": False})
        p._client.search = MagicMock(return_value=[{"id": "note:ok"}, {"id": "note:bad"}])
        p._client.get_note = MagicMock(side_effect=lambda nid: {
            "note:ok": {"id": nid, "title": "ok", "content": "trusted fact"},
            "note:bad": {"id": nid, "title": "bad", "content": "unapproved claim"},
        }[nid])
        out = p.prefetch("fact", session_id="s-other")
        assert "trusted fact" in out and "unapproved claim" not in out
        # promotion lifts it; a foreign session promotes nothing
        assert p.promote_session("s-nope") == 0
        assert p.promote_session("s-plan") == 1 and p.promote_session("s-plan") == 0
        assert "unapproved claim" in p.prefetch("fact", session_id="s-other")
        p.shutdown()

    def test_executing_policy_promotes_on_prefetch(self, monkeypatch, tmp_path):
        from agent.execution_policy import PlanModeState

        p = _init_provider(monkeypatch, tmp_path, healthy=True)
        p._client.create_note = MagicMock(return_value={"id": "note:q"})
        p.capture_note("q", "body", metadata={"quarantine": True, "session_id": "s-exec"})
        assert p._queue.quarantined_ids() == {"note:q"}
        p._client.search = MagicMock(return_value=[])
        monkeypatch.setattr(p, "_policy_state", lambda sid: PlanModeState.READY if sid == "s-exec" else None)
        p.prefetch("x", session_id="s-exec")
        assert p._queue.quarantined_ids() == {"note:q"}
        monkeypatch.setattr(p, "_policy_state", lambda sid: PlanModeState.EXECUTING if sid == "s-exec" else None)
        p.prefetch("x", session_id="s-exec")
        assert p._queue.quarantined_ids() == set()
        p.shutdown()
