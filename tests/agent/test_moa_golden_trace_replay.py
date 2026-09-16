"""Golden trace-replay gate (WO-MOA/2 Phase 0/1).

Fixtures under fixtures/moa_golden/ are REAL captured /moa ensemble traces
(moa.save_traces). Two properties are pinned:

1. Schema + normalization: every fixture decodes, and
   ``normalize_trace_for_diff`` strips exactly the nondeterministic fields
   (timestamps, session ids, token usage, cost, streaming flags) —
   idempotently — leaving the deterministic core for byte-diffing.

2. Replay: the aggregator guidance block rebuilt by
   ``moa_loop.build_reference_guidance`` from the trace's recorded
   reference outputs appears BYTE-VERBATIM inside the aggregator input the
   trace recorded. Any format drift in the assembly path fails here before
   it can silently change what the acting model sees.
"""

import json
from pathlib import Path

import pytest

from agent.moa_loop import build_reference_guidance

FIXTURES = sorted(
    (Path(__file__).parent / "fixtures" / "moa_golden").glob("*.jsonl")
)

_VOLATILE_TOP = ("ts", "session_id")
_VOLATILE_SLOT = ("usage", "cost_usd", "cost_status", "cost_source")


def normalize_trace_for_diff(record: dict) -> dict:
    """Strip nondeterministic-by-construction fields from one trace record."""
    out = {k: v for k, v in record.items() if k not in _VOLATILE_TOP}
    out["references"] = [
        {k: v for k, v in ref.items() if k not in _VOLATILE_SLOT}
        for ref in record.get("references", [])
    ]
    agg = {
        k: v
        for k, v in (record.get("aggregator") or {}).items()
        if k not in _VOLATILE_SLOT + ("streamed", "output_location")
    }
    out["aggregator"] = agg
    return out


def _records():
    for path in FIXTURES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield path.name, json.loads(line)


def _flatten(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    return ""


def test_fixtures_exist():
    assert len(FIXTURES) >= 2, "golden corpus missing — recapture /moa traces"


@pytest.mark.parametrize("name,record", list(_records()))
def test_normalization_strips_volatile_and_is_idempotent(name, record):
    norm = normalize_trace_for_diff(record)
    assert "ts" not in norm and "session_id" not in norm
    for ref in norm["references"]:
        assert "usage" not in ref and "cost_usd" not in ref
        # Deterministic core preserved.
        assert ref["label"] and ref["model"] and "output" in ref
        assert isinstance(ref["input_messages"], list)
    assert "streamed" not in norm["aggregator"]
    assert norm["aggregator"]["input_messages"]
    # Idempotence: normalizing a normalized record is a no-op.
    assert normalize_trace_for_diff(norm) == norm


@pytest.mark.parametrize("name,record", list(_records()))
def test_guidance_replay_is_byte_verbatim(name, record):
    refs = [(r["label"], r["output"]) for r in record["references"]]
    joined = "\n\n".join(
        f"Reference {idx} — {label}:\n{text}"
        for idx, (label, text) in enumerate(refs, start=1)
    )
    guidance = build_reference_guidance(
        record["preset"],
        record["aggregator"]["label"],
        refs,
        joined=joined,
    )
    agg_text = "".join(
        _flatten(m.get("content"))
        for m in record["aggregator"]["input_messages"]
    )
    assert guidance in agg_text, (
        f"{name}: rebuilt guidance block is not byte-verbatim in the "
        "recorded aggregator input — assembly format drifted"
    )
    # And every reference's raw output made it into the aggregator prompt.
    for _label, output in refs:
        assert output in agg_text
