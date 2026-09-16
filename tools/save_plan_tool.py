#!/usr/bin/env python3
"""save_plan — the one write permitted in plan mode.

The host owns the path: plans always land under the active workspace's
``.nyriel/plans/`` with a host-generated filename, so the model never
supplies a path and there is no traversal surface. Each call records a new
revision (sha256 digest + revision counter) into the session's execution
policy so the operator's ``/plan approve <short-id>`` can verify it is
approving exactly the artifact on disk.

Registered unconditionally (schema never varies with mode — prompt-cache
safe); calls outside plan mode are denied by the execution policy at the
tool executor, with a defense-in-depth state check here.
"""

import datetime
import hashlib
import json
import re
from pathlib import Path

MAX_PLAN_CONTENT_CHARS = 512_000
MAX_PLAN_TITLE_CHARS = 200


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "plan"


def save_plan_tool(title: str, content: str, session_id: str = "") -> str:
    title = (title or "").strip()
    content = content or ""
    if not title:
        return json.dumps({"error": "save_plan requires a non-empty title."})
    if not content.strip():
        return json.dumps({"error": "save_plan requires non-empty content."})
    if len(title) > MAX_PLAN_TITLE_CHARS:
        return json.dumps({"error": f"Title exceeds {MAX_PLAN_TITLE_CHARS} chars."})
    if len(content) > MAX_PLAN_CONTENT_CHARS:
        return json.dumps({
            "error": (
                f"Plan exceeds {MAX_PLAN_CONTENT_CHARS} chars. Plans are "
                "bounded artifacts — trim detail or split the work."
            )
        })

    from agent.execution_policy import ExecutionPolicyStore
    from agent.runtime_cwd import resolve_agent_cwd

    store = ExecutionPolicyStore()
    plans_dir = resolve_agent_cwd() / ".nyriel" / "plans"
    try:
        plans_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = plans_dir / f"{stamp}-{_slugify(title)}.md"
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return json.dumps({"error": f"Could not write plan file: {e}"})

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    policy, err = store.record_revision(
        session_id or "", path=str(path), digest=digest, title=title
    )
    if err is not None:
        # Only the semantic denial orphans the file. A transient read failure
        # (locked DB) is not evidence that the session is out of plan mode —
        # deleting the plan on it would destroy work over an infra hiccup.
        from agent.execution_policy import PLAN_MODE_REQUIRED_ERROR

        if err == PLAN_MODE_REQUIRED_ERROR:
            try:
                path.unlink()
            except OSError:
                pass
        return json.dumps({"error": err})

    return json.dumps({
        "plan_id": policy.plan_id,
        "short_id": policy.short_id,
        "revision": policy.revision,
        "path": str(path),
        "digest": digest,
        "note": (
            "Plan revision saved. Present the plan to the operator; they "
            f"approve it with /plan approve {policy.short_id}."
        ),
    })


SAVE_PLAN_SCHEMA = {
    "name": "save_plan",
    "description": (
        "Save the current plan as a markdown artifact (plan mode only). "
        "The host writes it under the workspace's .nyriel/plans/ and "
        "returns a short id the operator uses to approve execution. Call "
        "again with revised content to record a new revision."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short plan title (used for the filename slug).",
            },
            "content": {
                "type": "string",
                "description": "The full plan as markdown.",
            },
        },
        "required": ["title", "content"],
    },
}


# --- Registry ---
from tools.registry import registry

registry.register(
    name="save_plan",
    toolset="plan",
    schema=SAVE_PLAN_SCHEMA,
    handler=lambda args, **kw: save_plan_tool(
        title=args.get("title", ""),
        content=args.get("content", ""),
        session_id=kw.get("session_id", "") or "",
    ),
    emoji="🗺️",
)
