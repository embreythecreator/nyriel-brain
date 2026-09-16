"""Tests for the work-orders dashboard plugin (PromptIR compiler edge)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import importlib.util

_LOADER_PATH = REPO_ROOT / "plugins" / "work-orders" / "dashboard" / "plugin_api.py"


def _load_api():
    spec = importlib.util.spec_from_file_location("wo_plugin_api", _LOADER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


API = _load_api()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(API.router, prefix="/api/plugins/work-orders")
    return TestClient(app)


def _catalog():
    return [
        {"id": "face-widgets", "name": "Face Widgets", "category": "face-widgets",
         "description": "Author, render, and persist widgets on Nyriel Face spaces."},
        {"id": "word-organ", "name": "Word", "category": "note-taking",
         "description": "Operate the Word organ knowledge base."},
        {"id": "python-testing", "name": "Python Testing", "category": "software",
         "description": "pytest strategies and TDD methodology."},
    ]


def _note():
    return "Draft a launch announcement for the new widget and verify it posts."


class TestSchemas:
    def test_note_required(self):
        with pytest.raises(Exception):
            API.WorkOrderRequest(note="")

    def test_title_bounded(self):
        with pytest.raises(Exception):
            API.WorkOrderRequest(note="x", title="t" * 401)

    def test_note_bounded(self):
        with pytest.raises(Exception):
            API.WorkOrderRequest(note="x" * 24_001)

    def test_duplicate_skill_ids_normalize(self):
        req = API.WorkOrderRequest(
            note="x", skills=[
                {"id": "face-widgets"}, {"id": "face-widgets"}, {"id": "word-organ"},
            ])
        ids = [s.id for s in req.skills]
        assert ids == ["face-widgets", "word-organ"]

    def test_fingerprint_stable_across_key_order(self):
        a = API.WorkOrderRequest(note="n", selected_skills=["a", "b"],
                                 skills=[{"id": "b", "description": "d2"},
                                         {"id": "a", "description": "d1"}])
        b = API.WorkOrderRequest(note="n", selected_skills=["a", "b"],
                                 skills=[{"id": "a", "description": "d1"},
                                         {"id": "b", "description": "d2"}])
        assert API.input_fingerprint(a, operation="analyze") == \
            API.input_fingerprint(b, operation="analyze")

    def test_fingerprint_changes_with_inputs(self):
        base = API.WorkOrderRequest(note="n", skills=[{"id": "a"}])
        fp = API.input_fingerprint(base, operation="analyze")
        assert fp != API.input_fingerprint(base, operation="refine")
        changed = API.WorkOrderRequest(note="n2", skills=[{"id": "a"}])
        assert fp != API.input_fingerprint(changed, operation="analyze")
        changed2 = API.WorkOrderRequest(note="n", selected_skills=["a"],
                                        skills=[{"id": "a"}])
        assert fp != API.input_fingerprint(changed2, operation="analyze")

    def test_selected_skill_not_in_catalog_rejected(self, client):
        r = client.post("/api/plugins/work-orders/analyze", json={
            "note": _note(), "skills": _catalog(),
            "selected_skills": ["not-a-skill"],
        })
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["reason"] == "input_invalid"


class TestPrefilter:
    def test_exact_id_match_outranks(self):
        req = API.WorkOrderRequest(
            note="use python-testing to write tests", skills=_catalog())
        out = API.prefilter_skills(req)
        assert out[0].id == "python-testing"

    def test_selected_survives_zero_score(self):
        req = API.WorkOrderRequest(
            note="zzz qqq nothing matches", selected_skills=["word-organ"],
            skills=_catalog())
        out = API.prefilter_skills(req)
        assert "word-organ" in [s.id for s in out]

    def test_capped_at_64(self):
        big = [{"id": f"skill-{i}", "description": f"d{i}"} for i in range(500)]
        req = API.WorkOrderRequest(note="n", skills=big)
        assert len(API.prefilter_skills(req)) <= 64


class TestEndpoints:
    def test_analyze_shape(self, client, monkeypatch):
        captured = {}

        def fake_call_llm(*, messages, task=None, temperature=None, max_tokens=None, **kw):
            captured.update(task=task, temperature=temperature, messages=messages)
            return {"content": json.dumps({
                "objective": "Announce the widget",
                "readiness": "needs_decision",
                "ambiguities": ["which channel"],
                "missing_decisions": ["launch date"],
                "assumptions": ["single author"],
                "suggested_acceptance_criteria": ["post is live"],
                "skill_recommendations": [
                    {"id": "face-widgets", "reason": "widget surface"},
                    {"id": "not-installed", "reason": "hallucinated"},
                ],
            })}

        monkeypatch.setattr(API, "call_llm", fake_call_llm)
        r = client.post("/api/plugins/work-orders/analyze", json={
            "note": _note(), "skills": _catalog(), "title": "Launch",
        })
        body = r.json()
        assert body["ok"] is True
        assert captured["task"] == "work_order_analysis"
        assert captured["temperature"] == 0
        # untrusted note isolated in user message, not policy
        roles = [m["role"] for m in captured["messages"]]
        assert roles == ["system", "user"]
        assert _note() in captured["messages"][1]["content"]
        assert _note() not in captured["messages"][0]["content"]
        # invalid skill filtered out
        ids = [s["id"] for s in body["analysis"]["skill_recommendations"]]
        assert ids == ["face-widgets"]
        assert body["invalid_skill_ids"] == ["not-installed"]
        assert body["input_fingerprint"]
        assert body["catalog_digest"]
        assert body["compiler_version"] == API.COMPILER_VERSION

    def test_refine_shape_and_prompts(self, client, monkeypatch):
        def fake_call_llm(*, messages, task=None, **kw):
            return {"content": json.dumps({
                "objective": "Announce the widget",
                "context": ["Big Bang space"],
                "deliverable": ["one launch post"],
                "constraints": ["concise"],
                "acceptance_criteria": ["post is live"],
                "assumptions": [],
                "missing_decisions": [],
                "skills": [{"id": "face-widgets", "reason": "surface"}],
                "concise_prompt": "Announce the launch.",
                "final_prompt": "# Work Order\nAnnounce the launch.",
            })}

        monkeypatch.setattr(API, "call_llm", fake_call_llm)
        r = client.post("/api/plugins/work-orders/refine", json={
            "note": _note(), "skills": _catalog(), "title": "Launch",
        })
        body = r.json()
        assert body["ok"] is True
        ir = body["prompt_ir"]
        assert ir["concise_prompt"] == "Announce the launch."
        assert ir["final_prompt"].startswith("# Work Order")

    def test_malformed_output_typed_failure(self, client, monkeypatch):
        monkeypatch.setattr(API, "call_llm",
                            lambda **kw: {"content": "not json at all"})
        r = client.post("/api/plugins/work-orders/analyze", json={
            "note": _note(), "skills": _catalog()})
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["reason"] in ("malformed_output", "schema_invalid")

    def test_model_unavailable_typed_failure(self, client, monkeypatch):
        def boom(**kw):
            raise RuntimeError("provider down")
        monkeypatch.setattr(API, "call_llm", boom)
        r = client.post("/api/plugins/work-orders/analyze", json={
            "note": _note(), "skills": _catalog()})
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["reason"] == "model_unavailable"
