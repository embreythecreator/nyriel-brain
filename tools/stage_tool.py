"""`oblivion_app` — drive the user's Oblivion app from the cloud (WO-STAGE/HANDS-1).

The app dials out to the Brain (gateway/stage_control.py); this tool sends one
`oblivion` CLI argv to it and returns the exit code and output. Offered only while
an app is attached for the turn's principal. The app enforces its own tier
(`panels` / `full`); a refusal is final — the agent reports it and never tries
another route.
"""

from __future__ import annotations

from tools.registry import registry, tool_error, tool_result


def check_app_attached() -> bool:
    from gateway import stage_control

    return stage_control.get_stage_control().is_attached(stage_control.turn_principal())


def oblivion_app_tool(args: dict, **_kw) -> str:
    from gateway import stage_control

    argv = (args or {}).get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return tool_error("argv must be a non-empty list of strings, e.g. [\"new-split\", \"down\"]")
    try:
        result = stage_control.get_stage_control().dispatch(stage_control.turn_principal(), argv)
    except stage_control.StageAppNotConnected:
        return tool_error("The Oblivion app is not connected. Tell the user; do not try another route.")
    except stage_control.StageCommandTimeout as exc:
        return tool_error(f"timed out: {exc}. Not retried; check with a read command before repeating.")
    except stage_control.StageAppDisconnected as exc:
        return tool_error(f"disconnected: {exc}. Not retried.")
    if result.get("refused"):
        return tool_result(refused=result["refused"], note="Refused by the app's tier policy. Report it; do not retry another way.")
    return tool_result(exit_code=result.get("exit_code"), stdout=result.get("stdout", ""), stderr=result.get("stderr", ""))


registry.register(
    name="oblivion_app",
    toolset="stage_control",
    schema={
        "name": "oblivion_app",
        "description": (
            "Run one `oblivion` CLI command on the user's Oblivion app (their Mac), exactly as "
            "`oblivion <argv...>` would locally: splits, panes, workspaces, browser panes, and on "
            "the `full` tier typing into and reading terminals. Pass the arguments only, no shell, "
            "e.g. [\"new-split\", \"down\"], [\"browser\", \"open\", \"https://example.com\"], "
            "[\"read-screen\", \"--surface\", \"surface:2\"]. Returns exit_code/stdout/stderr, or "
            "`refused` when the app's tier forbids it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments after `oblivion`, one string per argument.",
                }
            },
            "required": ["argv"],
        },
    },
    handler=oblivion_app_tool,
    check_fn=check_app_attached,
    description="Drive the user's Oblivion app",
)
