"""WO-POSTURE/1 4.7 — evidence pins: pure helpers, policy fields, clearing,
and the fail-open regression (junk pins never brick a session)."""

import pytest

from agent.execution_policy import (
    ExecutionPolicy,
    ExecutionPolicyStore,
    PlanModeState,
    compute_plan_digest,
)
from agent.plan_pins import coerce_pins, evidence_digest, mint_pin, pins_from_evidence
from nyriel_state import SessionDB

SID = "sess-pins"


@pytest.fixture
def db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


@pytest.fixture
def store(db):
    return ExecutionPolicyStore(db)


def test_mint_pin_kinds_and_empty():
    assert mint_pin("2401.01234", "claim")["kind"] == "arxiv"
    assert mint_pin("note:abc")["kind"] == "vault"
    assert mint_pin("https://x.y/z")["kind"] == "url"
    assert mint_pin("   ") is None and mint_pin(None) is None


def test_pins_from_evidence_dedups_and_keeps_order():
    pins = pins_from_evidence([
        {"arxiv_id": "2401.1", "claim": "a"},
        {"vault_id": "note:1", "claim": "b"},
        {"arxiv_id": "2401.1", "claim": "dup"},
        "junk", {"claim": "no ref"}, None,
    ])
    assert [p["id"] for p in pins] == ["2401.1", "note:1"]
    assert pins[0]["claim"] == "a"


def test_evidence_digest_is_order_independent_and_empty_for_none():
    a = pins_from_evidence([{"arxiv_id": "1"}, {"arxiv_id": "2"}])
    b = pins_from_evidence([{"arxiv_id": "2"}, {"arxiv_id": "1"}])
    assert evidence_digest(a) == evidence_digest(b) != ""
    assert evidence_digest(()) == "" and evidence_digest(None) == ""
    assert evidence_digest(pins_from_evidence([{"arxiv_id": "3"}])) != evidence_digest(a)


@pytest.mark.parametrize("junk", [{"junk": 1}, "note:1", 42, None, [1, {"nope": 1}, "note:2"]])
def test_coerce_pins_is_total(junk):
    pins = coerce_pins(junk)
    assert isinstance(pins, tuple)
    assert all(isinstance(p, dict) and p["id"] for p in pins)


def test_fail_open_regression_junk_pins_keep_ready_state(db, store):
    """{"evidence_pins": {"junk": 1}, "state": "ready"} → READY, empty pins —
    never the from_dict except path that would silently drop to ACT."""
    db.set_execution_policy(SID, {"state": "ready", "evidence_pins": {"junk": 1}, "evidence_digest": 7})
    policy = store.load(SID)
    assert policy.state is PlanModeState.READY
    assert policy.evidence_pins == () and policy.evidence_digest == ""


def test_pins_round_trip_and_clear_on_revision_and_edit(store, tmp_path):
    store.enter_planning(SID, "research")
    plan = tmp_path / "plan.md"
    plan.write_text("# v1\n")
    policy, err = store.record_revision(SID, path=str(plan), digest=compute_plan_digest(str(plan)))
    assert err is None
    pins = pins_from_evidence([{"arxiv_id": "2401.1", "claim": "c"}])
    from dataclasses import replace

    store.save(SID, replace(policy, evidence_pins=pins, evidence_digest=evidence_digest(pins)))
    loaded = store.load(SID)
    assert loaded.evidence_pins[0]["id"] == "2401.1" and loaded.evidence_digest
    # A content edit clears pins (re-verify must re-pin) …
    updated, err = store.replace_plan_content(SID, content="# v2\n", expected_revision=loaded.revision)
    assert err is None and updated.evidence_pins == () and updated.evidence_digest == ""
    # … and so does a fresh save_plan revision.
    store.save(SID, replace(updated, evidence_pins=pins, evidence_digest="x"))
    plan.write_text("# v3\n")
    policy, err = store.record_revision(SID, path=str(plan), digest=compute_plan_digest(str(plan)))
    assert err is None and policy.evidence_pins == () and policy.evidence_digest == ""


def test_record_pins_is_cas_on_revision(store, tmp_path):
    store.enter_planning(SID, "r")
    plan = tmp_path / "plan.md"
    plan.write_text("# v1\n")
    policy, _ = store.record_revision(SID, path=str(plan), digest=compute_plan_digest(str(plan)))
    pins = pins_from_evidence([{"vault_id": "note:1", "claim": "c"}])
    _, err = store.record_pins(SID, pins, expected_revision=policy.revision + 1)
    assert err and "conflict" in err
    updated, err = store.record_pins(SID, pins, expected_revision=policy.revision)
    assert err is None and updated.evidence_pins[0]["id"] == "note:1" and updated.evidence_digest == evidence_digest(pins)
    assert store.load(SID).state is PlanModeState.READY
    store.finish(SID)
    _, err = store.record_pins(SID, pins, expected_revision=1)
    assert err
