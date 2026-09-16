"""WO-POSTURE/1 4.11 — rung contract parsing and the executor pass criteria."""

from agent.execution_policy import ExecutionPolicy, PlanModeState
from agent.plan_mode import PLAN_MODE_INSTRUCTIONS, build_execute_instruction
from agent.plan_rungs import VERIFICATION_EVIDENCE, parse_rung_evidence, pass_criteria_block

PLAN = """# Side panel

## Rung 1 — schema
- add table
verification_evidence: [tests, static]

## Rung 2 — wire it
- `verification_evidence: [artifact_exercised, vibes, No_Stray_Ports]`

## Rung 3 — cleanup
verification_evidence: []
"""


def test_parse_rungs_keeps_only_enum_words_and_order():
    rungs = parse_rung_evidence(PLAN)
    assert [r["rung"] for r in rungs] == ["Rung 1 — schema", "Rung 2 — wire it", "Rung 3 — cleanup"]
    assert rungs[0]["evidence"] == ["tests", "static"]
    assert rungs[1]["evidence"] == ["artifact_exercised", "no_stray_ports"]  # 'vibes' dropped
    assert rungs[2]["evidence"] == []
    assert set(VERIFICATION_EVIDENCE) == {"tests", "regression", "static", "artifact_exercised", "temp_removed", "no_stray_ports"}


def test_parse_is_total_on_junk_and_rungless_plans():
    assert parse_rung_evidence(None) == [] and parse_rung_evidence(42) == []
    assert parse_rung_evidence("# plain\n- step\n") == []
    assert pass_criteria_block("# plain\n") == ""


def test_execute_instruction_carries_pass_criteria(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)
    policy = ExecutionPolicy(state=PlanModeState.EXECUTING, plan_id="abcdef1234", revision=2, plan_path=str(plan))
    text = build_execute_instruction(policy)
    assert text.startswith("[Plan abcdef12 rev 2 APPROVED") and text.endswith("]")
    assert "Rung 1 — schema: tests, static" in text
    assert "Rung 3 — cleanup: NONE VALID" in text
    # rungless or missing plan → the classic one-liner, never an error
    policy2 = ExecutionPolicy(state=PlanModeState.EXECUTING, plan_id="abcdef1234", revision=1, plan_path=str(tmp_path / "nope.md"))
    assert "Pass criteria" not in build_execute_instruction(policy2)


def test_plan_instructions_name_the_enum():
    for word in VERIFICATION_EVIDENCE:
        assert word in PLAN_MODE_INSTRUCTIONS
