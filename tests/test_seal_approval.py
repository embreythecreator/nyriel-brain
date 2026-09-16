"""SealApprovalStore — the guards that must not regress (WO-FACE/CHAT-1 P0-1).

Runs against an in-memory fake DB, so no state.db and no gateway needed:
    python -m pytest tests/test_seal_approval.py -q
    python tests/test_seal_approval.py          # same checks, no pytest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.seal_approval import (  # noqa: E402
    SealApprovalStore,
    action_digest,
    extract_space_action_envelopes,
    gate_space_actions,
)


class FakeDB:
    """Stands in for SessionDB's two seal-grant methods."""

    def __init__(self):
        self.rows = {}

    def get_seal_grant(self, session_id):
        return self.rows.get(str(session_id))

    def set_seal_grant(self, session_id, state):
        # Store a copy so the test cannot accidentally share mutable state with
        # the store and mask a persistence bug.
        import copy
        self.rows[str(session_id)] = copy.deepcopy(state)
        return True


def store():
    return SealApprovalStore(session_db=FakeDB())


ACTION = dict(action_id="act_1", kind="js", code="writeFile('a.md')", seal_tier=2,
              verb="write", effect="write ~/notes/a.md", scope={"root": "~/notes"})


def test_digest_binds_code_not_name():
    a = action_digest("js", "writeFile('a.md')", 2)
    b = action_digest("js", "writeFile('b.md')", 2)
    assert a != b, "different code must produce a different digest"
    # Cosmetic reformatting must NOT mint a new digest.
    assert action_digest("js", "writeFile('a.md')", 2) == \
        action_digest("js", "writeFile('a.md')  ", 2)
    # Tier is bound: the same code at a higher tier is a different action.
    assert action_digest("js", "x()", 2) != action_digest("js", "x()", 3)


def test_request_then_approve_roundtrip():
    s = store()
    rec, granted = s.request("sess", **ACTION)
    assert granted is False
    assert rec["status"] == "pending" and rec["nonce"] == 1

    payload = s.confirm_payload("sess")
    assert payload is not None
    assert payload["seal_tier"] == 2 and payload["tier_label"] == "bounded write"
    # Digest-bound fields and model prose must stay separated on the wire.
    assert payload["verified_fields"]["verb"] == "write"
    assert "prose" in payload and payload["short_digest"] == rec["digest"][:6]

    ok, err = s.approve("sess", nonce=rec["nonce"], digest=rec["digest"])
    assert err is None and ok["status"] == "approved"
    # Card clears after a decision.
    assert s.confirm_payload("sess") is None


def test_stale_nonce_is_refused():
    """The guard that makes a click landing after a newer request inert."""
    s = store()
    first, _ = s.request("sess", **ACTION)
    second, _ = s.request("sess", **{**ACTION, "code": "writeFile('other.md')"})
    assert second["nonce"] == first["nonce"] + 1

    ok, err = s.approve("sess", nonce=first["nonce"], digest=first["digest"])
    assert ok is None and "stale" in err.lower()
    # The superseded request left a record rather than vanishing.
    assert any(e["outcome"] == "superseded" for e in s.ledger("sess"))


def test_mutated_payload_is_refused():
    s = store()
    rec, _ = s.request("sess", **ACTION)
    ok, err = s.approve("sess", nonce=rec["nonce"], digest="deadbeef")
    assert ok is None and "digest" in err.lower()


def test_expiry_leaves_a_not_taken_record():
    s = store()
    rec, _ = s.request("sess", **ACTION, ttl_seconds=-1)  # already lapsed
    assert s.confirm_payload("sess") is None, "lapsed card must not render"
    outcomes = [e["outcome"] for e in s.ledger("sess")]
    assert "expired" in outcomes, "expiry must be recorded, never a silent deletion"
    ok, err = s.approve("sess", nonce=rec["nonce"], digest=rec["digest"])
    assert ok is None and err


def test_session_grant_binds_shape_not_tool_name():
    s = store()
    rec, _ = s.request("sess", **ACTION)
    s.approve("sess", nonce=rec["nonce"], digest=rec["digest"], for_session=True)

    # Same action shape rides the standing grant: no card.
    _, granted = s.request("sess", **ACTION)
    assert granted is True, "identical action should be covered by the session grant"

    # A DIFFERENT action with the same verb must still gate. This is the whole
    # reason the grant binds a digest instead of a name.
    rec3, granted3 = s.request("sess", **{**ACTION, "code": "rm('-rf','/')"})
    assert granted3 is False, "a differently-dangerous call must not ride a shared verb"
    assert rec3["status"] == "pending"


def test_deny_leaves_a_refusal_record_with_the_digest():
    s = store()
    rec, _ = s.request("sess", **ACTION)
    ok, err = s.deny("sess", nonce=rec["nonce"])
    assert err is None and ok["status"] == "denied"
    denied = [e for e in s.ledger("sess") if e["outcome"] == "denied"]
    assert denied and denied[0]["digest"] == rec["digest"], \
        "operator must be able to prove they refused THIS exact action"


def test_revoke_clears_standing_grants():
    s = store()
    rec, _ = s.request("sess", **ACTION)
    s.approve("sess", nonce=rec["nonce"], digest=rec["digest"], for_session=True)
    assert s.revoke_session_grants("sess") == 1
    _, granted = s.request("sess", **ACTION)
    assert granted is False, "revoked grant must stop auto-approving"


def test_streak_counter_surfaces_reflex_clicking():
    s = store()
    for i in range(3):
        rec, _ = s.request("sess", **{**ACTION, "code": f"f{i}()"})
        s.approve("sess", nonce=rec["nonce"], digest=rec["digest"])
    rec, _ = s.request("sess", **{**ACTION, "code": "f9()"})
    payload = s.confirm_payload("sess")
    assert payload["approval_streak"] == 3
    assert payload["seconds_since_last_approval"] is not None


# -- Emission-time gate ------------------------------------------------------


def _block(action_id, tier, code="writeFile('a.md')", extra=""):
    return (
        "Some prose the model wrote.\n\n"
        "```space-action\n"
        '{"v":1,"id":"%s","kind":"js","payload":{"code":"%s"},"seal_tier":%d%s}\n'
        "```\n" % (action_id, code, tier, extra)
    )


def test_gate_extracts_only_well_formed_blocks():
    text = _block("act_1", 1) + "```space-action\n{not json\n```\n"
    envs = extract_space_action_envelopes(text)
    assert len(envs) == 1, envs
    assert envs[0]["id"] == "act_1"


def test_gate_authorizes_t1_and_withholds_t2():
    s = store()
    ids = gate_space_actions("sess", _block("act_1", 1) + _block("act_2", 2, code="rm()"), store=s)
    # T1 has never been gated; T2 has no grant, so it is withheld and a card
    # is opened instead.
    assert ids == ["act_1"], ids
    pending = s.confirm_payload("sess")
    assert pending is not None
    assert pending["action_id"] == "act_2"


def test_gate_authorizes_after_approve_once_then_the_grant_is_spent():
    s = store()
    text = _block("act_2", 2, code="rm()")
    assert gate_space_actions("sess", text, store=s) == []
    rec = s.confirm_payload("sess")
    s.approve("sess", nonce=rec["nonce"], digest=rec["digest"])

    # The agent re-emits the same block after the grant's rewritten message.
    # Without one-shot consumption this returns [] forever and the loop hangs.
    assert gate_space_actions("sess", text, store=s) == ["act_2"]
    # Spent: a third emission asks again.
    assert gate_space_actions("sess", text, store=s) == []


def test_session_grant_authorizes_repeatedly_but_only_the_same_shape():
    s = store()
    text = _block("act_2", 2, code="rm()")
    gate_space_actions("sess", text, store=s)
    rec = s.confirm_payload("sess")
    s.approve("sess", nonce=rec["nonce"], digest=rec["digest"], for_session=True)

    assert gate_space_actions("sess", text, store=s) == ["act_2"]
    assert gate_space_actions("sess", text, store=s) == ["act_2"]
    # A different action shape sharing the tier still gates.
    assert gate_space_actions("sess", _block("act_3", 2, code="wipe()"), store=s) == []


def test_gate_opens_at_most_one_card_per_message():
    s = store()
    text = _block("act_2", 2, code="rm()") + _block("act_3", 2, code="wipe()")
    assert gate_space_actions("sess", text, store=s) == []
    # The second would have superseded the first before the operator saw it.
    assert s.confirm_payload("sess")["action_id"] == "act_2"


def test_gate_derives_verified_fields_from_the_digest_bound_material():
    s = store()
    # The model's own note must not reach the verified column; the effect the
    # card shows has to be a projection of the code the digest binds.
    text = _block("act_2", 2, code="rm()", extra=',"note":"totally safe","scope":{"root":"~/x"}')
    gate_space_actions("sess", text, store=s)
    payload = s.confirm_payload("sess")
    assert payload["verified_fields"]["effect"] == "rm()"
    assert payload["verified_fields"]["scope"] == {"root": "~/x"}
    assert payload["prose"] == "totally safe"
    assert payload["digest"] == action_digest("js", "rm()", 2, {"root": "~/x"})


def test_gate_returns_nothing_for_a_message_with_no_blocks():
    assert gate_space_actions("sess", "just a plain answer", store=store()) == []


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
    print("\nall seal-approval checks passed" if not failures else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
