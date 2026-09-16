"""Nyriel middleware contract helpers.

Observer hooks report what happened. Middleware can change what happens by
rewriting a request or wrapping the actual execution callback. Keep the small
contract helpers here so agent-loop call sites and plugins share one vocabulary.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

OBSERVER_SCHEMA_VERSION = "nyriel.observer.v1"
MIDDLEWARE_SCHEMA_VERSION = "nyriel.middleware.v1"

TOOL_REQUEST_MIDDLEWARE = "tool_request"
TOOL_EXECUTION_MIDDLEWARE = "tool_execution"
LLM_REQUEST_MIDDLEWARE = "llm_request"
LLM_EXECUTION_MIDDLEWARE = "llm_execution"

# Back-compat aliases for older PoC branches that used API terminology.
API_REQUEST_MIDDLEWARE = LLM_REQUEST_MIDDLEWARE
API_EXECUTION_MIDDLEWARE = LLM_EXECUTION_MIDDLEWARE

VALID_MIDDLEWARE: set[str] = {
    TOOL_REQUEST_MIDDLEWARE,
    TOOL_EXECUTION_MIDDLEWARE,
    LLM_REQUEST_MIDDLEWARE,
    LLM_EXECUTION_MIDDLEWARE,
}


@dataclass
class RequestMiddlewareResult:
    """Result of applying request middleware to a mutable payload."""

    payload: Any
    original_payload: Any
    changed: bool = False
    trace: List[Dict[str, Any]] = field(default_factory=list)


def observer_payload(**kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("telemetry_schema_version", OBSERVER_SCHEMA_VERSION)
    return kwargs


def middleware_payload(**kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("telemetry_schema_version", OBSERVER_SCHEMA_VERSION)
    kwargs.setdefault("middleware_schema_version", MIDDLEWARE_SCHEMA_VERSION)
    return kwargs


def _safe_copy(payload: Any) -> Any:
    """Deep-copy a request payload, tolerating non-deepcopyable members.

    Request payloads are normally plain JSON-shaped dicts, but an LLM request
    can occasionally carry non-deepcopyable objects (clients, callbacks, file
    handles). A hard ``deepcopy`` failure there would otherwise abort the whole
    request-middleware pass. Fall back to a shallow ``dict`` copy so middleware
    still runs and the original nested objects are shared by reference rather
    than corrupting the live payload.
    """
    try:
        return deepcopy(payload)
    except Exception as exc:  # pragma: no cover - exercised via fallback test
        logger.debug("deepcopy failed for request payload (%s); using shallow copy", exc)
        if isinstance(payload, dict):
            return dict(payload)
        return payload


def apply_llm_request_middleware(
    request: Dict[str, Any],
    **context: Any,
) -> RequestMiddlewareResult:
    """Apply registered LLM request middleware.

    Middleware may return ``{"request": {...}}`` to replace the effective
    provider kwargs before Nyriel sends them.
    """
    if not _has_middleware(LLM_REQUEST_MIDDLEWARE):
        return RequestMiddlewareResult(
            payload=request,
            original_payload=request,
            changed=False,
            trace=[],
        )

    original_request = _safe_copy(request)
    current_request = _safe_copy(original_request)
    trace: List[Dict[str, Any]] = []

    for result in _invoke_middleware(
        LLM_REQUEST_MIDDLEWARE,
        request=current_request,
        original_request=original_request,
        **context,
    ):
        if not isinstance(result, dict):
            continue
        next_request = result.get("request")
        if not isinstance(next_request, dict):
            continue
        current_request = _safe_copy(next_request)
        trace.append(_trace_entry(result))

    return RequestMiddlewareResult(
        payload=current_request,
        original_payload=original_request,
        changed=bool(trace),
        trace=trace,
    )


def apply_tool_request_middleware(
    tool_name: str,
    args: Dict[str, Any],
    **context: Any,
) -> RequestMiddlewareResult:
    """Apply registered tool request middleware.

    Middleware may return ``{"args": {...}}`` to replace the effective tool
    arguments before hooks, guardrails, approvals, and execution see them.
    """
    original_args = _safe_copy(args)
    current_args = _safe_copy(original_args)
    trace: List[Dict[str, Any]] = []

    session_id = str(context.get("session_id") or "")
    skip_relay = bool(context.pop("skip_relay", False))
    if session_id and not skip_relay:
        from agent import relay_runtime

        relay_args = relay_runtime.apply_tool_request_intercepts(
            session_id=session_id,
            tool_name=tool_name,
            args=current_args,
        )
        if relay_args != current_args:
            current_args = _safe_copy(relay_args)
            trace.append({"source": "nemo_relay"})

    if not _has_middleware(TOOL_REQUEST_MIDDLEWARE):
        return RequestMiddlewareResult(
            payload=args if not trace else current_args,
            original_payload=args,
            changed=bool(trace),
            trace=trace,
        )

    for result in _invoke_middleware(
        TOOL_REQUEST_MIDDLEWARE,
        tool_name=tool_name,
        args=current_args,
        original_args=original_args,
        **context,
    ):
        if not isinstance(result, dict):
            continue
        next_args = result.get("args")
        if not isinstance(next_args, dict):
            continue
        current_args = _safe_copy(next_args)
        trace.append(_trace_entry(result))

    return RequestMiddlewareResult(
        payload=current_args,
        original_payload=original_args,
        changed=bool(trace),
        trace=trace,
    )


def apply_api_request_middleware(
    request: Dict[str, Any],
    **context: Any,
) -> RequestMiddlewareResult:
    """Compatibility wrapper for older ``api_request`` naming."""
    return apply_llm_request_middleware(request, **context)


def run_llm_execution_middleware(
    request: Dict[str, Any],
    next_call: Callable[[Dict[str, Any]], Any],
    **context: Any,
) -> Any:
    """Run provider execution through registered LLM execution middleware."""
    callbacks = _get_middleware_callbacks(LLM_EXECUTION_MIDDLEWARE)
    if not callbacks:
        return next_call(request)
    return _run_execution_chain(
        LLM_EXECUTION_MIDDLEWARE,
        callbacks,
        next_call,
        request=request,
        original_request=context.pop("original_request", request),
        **context,
    )


def run_tool_execution_middleware(
    tool_name: str,
    args: Dict[str, Any],
    next_call: Callable[[Dict[str, Any]], Any],
    **context: Any,
) -> Any:
    """Run tool execution through registered tool execution middleware."""
    # WO-POSTURE/1 1.5 — host-private capture frame (Relay precedent: popped
    # before any plugin callback can see it). Sits ABOVE the no-callbacks
    # short-circuit so it runs with zero plugins registered.
    capture_agent = context.pop("capture_agent", None)
    if capture_agent is not None and tool_name in CAPTURE_TOOLS:
        next_call = _wrap_capture(tool_name, next_call, capture_agent)
    callbacks = _get_middleware_callbacks(TOOL_EXECUTION_MIDDLEWARE)
    if not callbacks:
        return next_call(args)
    return _run_execution_chain(
        TOOL_EXECUTION_MIDDLEWARE,
        callbacks,
        next_call,
        tool_name=tool_name,
        args=args,
        original_args=context.pop("original_args", args),
        **context,
    )


# Fixed allowlist: the evidence-bearing read tools. Everything the Brain reads
# through these lands in Word (memory or, from 1.7, quarantine).
CAPTURE_TOOLS = frozenset({"web_search", "web_extract", "read_file", "search_files", "save_plan"})
_CAPTURE_MIN_CHARS = 200
# ponytail: flat ceiling (~5k tokens); make it per-tool config if the model
# starts round-tripping word_memory_read on every code read.
ELIDE_CHARS = 20_000
_ELIDE_HEAD_CHARS = 400


def _capture_title_and_origin(tool_name: str, args: Dict[str, Any], payload: Any):
    if tool_name == "web_extract":
        first = (payload.get("results") or [{}])[0] if isinstance(payload, dict) else {}
        url = str(first.get("url") or (args.get("urls") or [args.get("url", "")])[0] or "")
        return (str(first.get("title") or "").strip() or url or "web_extract"), url
    if tool_name == "web_search":
        query = str(args.get("query") or "")
        return f"web_search: {query}"[:120], f"web_search:{query}"
    if tool_name == "read_file":
        path = str(args.get("path") or "")
        return f"read_file: {path}"[:120], path
    pattern = str(args.get("pattern") or "")
    return f"search_files: {pattern}"[:120], f"search_files:{pattern}"


def _live_posture(agent: Any) -> str:
    policy = getattr(agent, "_execution_policy", None)
    posture = getattr(policy, "posture", None)
    value = getattr(posture, "value", None)
    return str(value) if value else "act"


def _wrap_capture(tool_name: str, next_call: Callable[[Dict[str, Any]], Any], agent: Any):
    """Run the tool, then capture its result into Word via the seam.

    A capture failure must NEVER surface as a tool failure — the chain
    re-raises anything thrown after ``next_call`` succeeded, so everything
    past the dispatch is inside one swallow-all.
    """

    def _call(next_args: Dict[str, Any]) -> Any:
        result = next_call(next_args)
        try:
            return _capture_result(tool_name, next_args, result, agent)
        except Exception as exc:  # pragma: no cover - defensive by contract
            logger.debug("Word capture skipped for %s: %s", tool_name, exc)
            return result

    return _call


def _capture_plan_artifact(args: Dict[str, Any], result: str, agent: Any) -> str:
    """4.10 — a successful save_plan becomes a Word artifact node citing the
    plan's evidence pins; the note id rides on the policy as word_note_id.
    Write-behind: Word down → no id, never blocks, result untouched."""
    try:
        payload = json.loads(result)
    except ValueError:
        return result
    if not isinstance(payload, dict) or not payload.get("plan_id"):
        return result
    from agent.word_seam import capture

    policy = getattr(agent, "_execution_policy", None)
    pins = [p.get("id") for p in (getattr(policy, "evidence_pins", ()) or ()) if isinstance(p, dict)]
    title = f"[plan] {str(args.get('title') or '')[:100]} · {payload.get('short_id', '')} rev {payload.get('revision', '')}"
    vault_id = capture(
        agent,
        title,
        str(args.get("content") or ""),
        metadata={
            "origin": "angel",
            "session_id": getattr(agent, "session_id", "") or "",
            "posture": _live_posture(agent),
            "node_type": "artifact",
            "cites": ",".join(pins),
            "plan_id": payload.get("plan_id"),
            "revision": payload.get("revision"),
            "quarantine": False,
        },
    )
    if vault_id:
        try:
            from dataclasses import replace as _replace
            from agent.execution_policy import ExecutionPolicyStore

            store = ExecutionPolicyStore(getattr(agent, "_session_db", None))
            sid = getattr(agent, "session_id", "") or ""
            fresh = store.load(sid)
            if fresh.plan_id == payload.get("plan_id"):
                store.save(sid, _replace(fresh, word_note_id=str(vault_id)))
        except Exception as exc:
            logger.debug("word_note_id not recorded: %s", exc)
        payload["vault_id"] = vault_id
        return json.dumps(payload, ensure_ascii=False)
    return result


def _capture_result(tool_name: str, args: Dict[str, Any], result: Any, agent: Any) -> Any:
    if tool_name == "save_plan":
        return _capture_plan_artifact(args, result, agent) if isinstance(result, str) else result
    if not isinstance(result, str) or len(result) < _CAPTURE_MIN_CHARS:
        return result
    payload = None
    if result.lstrip().startswith("{"):
        try:
            payload = json.loads(result)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and (payload.get("success") is False or payload.get("error")):
        return result  # failed tool call — nothing worth remembering
    from agent.word_seam import capture

    title, origin = _capture_title_and_origin(tool_name, args, payload)
    posture = _live_posture(agent)
    untrusted = tool_name in ("web_search", "web_extract")
    # ponytail: sync write bounded by the Word client timeout (8s) on the tool
    # thread; D-5 "always queue" would move this off-thread if latency bites.
    vault_id = capture(
        agent,
        title,
        result,
        metadata={
            "origin": origin,
            "session_id": getattr(agent, "session_id", "") or "",
            "posture": posture,
            "trust_tier": "T3" if untrusted else "T1",
            # 1.7: PLAN-posture and untrusted-origin captures are quarantined
            # until /plan approve promotes them (1.8).
            "quarantine": posture == "plan" or untrusted,
            "node_type": "source",
            "tool": tool_name,
        },
    )
    if not vault_id:
        return result
    if len(result) > ELIDE_CHARS:
        # 1.6 capture-and-elide: the body lives in Word now; hand the model a
        # handle + head instead of the whole thing. Only possible here, in the
        # frame that owns the result — an observer hook could never do this.
        return json.dumps({
            "vault_id": vault_id,
            "title": title,
            "head": result[:_ELIDE_HEAD_CHARS],
            "elided_chars": len(result) - _ELIDE_HEAD_CHARS,
            "note": "Full result captured in Word — call word_memory_read(note_id=vault_id) for the rest.",
        }, ensure_ascii=False)
    if isinstance(payload, dict):
        payload["vault_id"] = vault_id
        return json.dumps(payload, ensure_ascii=False)
    return f"{result}\n\n[word vault_id={vault_id}]"


def run_api_execution_middleware(
    request: Dict[str, Any],
    next_call: Callable[[Dict[str, Any]], Any],
    **context: Any,
) -> Any:
    """Compatibility wrapper for older ``api_execution`` naming."""
    return run_llm_execution_middleware(request, next_call, **context)


def _invoke_middleware(kind: str, **kwargs: Any) -> List[Any]:
    from nyriel_cli.plugins import invoke_middleware

    return invoke_middleware(kind, **middleware_payload(**kwargs))


def _has_middleware(kind: str) -> bool:
    from nyriel_cli.plugins import has_middleware

    return has_middleware(kind)


def _get_middleware_callbacks(kind: str) -> List[Callable]:
    from nyriel_cli.plugins import get_plugin_manager

    return list(get_plugin_manager()._middleware.get(kind, []))


def _run_execution_chain(
    kind: str,
    callbacks: List[Callable],
    terminal_call: Callable[[Any], Any],
    **kwargs: Any,
) -> Any:
    payload_key = "request" if "request" in kwargs else "args"

    class _DownstreamExecutionError(Exception):
        def __init__(self, original: BaseException) -> None:
            super().__init__(str(original))
            self.original = original

    def call_at(index: int, payload: Any) -> Any:
        if index >= len(callbacks):
            return terminal_call(payload)

        callback = callbacks[index]
        next_called = False
        next_succeeded = False
        next_result: Any = None

        def next_call(next_payload: Any = None) -> Any:
            nonlocal next_called, next_succeeded, next_result
            # ``next_call`` is single-use per middleware frame. Calling it more
            # than once would re-run the downstream provider/tool, so a second
            # invocation is a contract violation rather than a retry. Surface it
            # instead of silently executing the terminal call twice.
            if next_called:
                raise RuntimeError(
                    f"Middleware '{kind}' callback "
                    f"{getattr(callback, '__name__', repr(callback))} called "
                    "next_call() more than once; downstream execution is single-use"
                )
            next_called = True
            try:
                next_result = call_at(index + 1, payload if next_payload is None else next_payload)
                next_succeeded = True
                return next_result
            except Exception as exc:
                raise _DownstreamExecutionError(exc) from exc

        call_kwargs = middleware_payload(**kwargs)
        call_kwargs[payload_key] = payload
        call_kwargs["next_call"] = next_call
        try:
            return callback(**call_kwargs)
        except _DownstreamExecutionError as exc:
            raise exc.original
        except Exception as exc:
            logger.warning(
                "Middleware '%s' callback %s raised: %s",
                kind,
                getattr(callback, "__name__", repr(callback)),
                exc,
            )
            if next_succeeded:
                return next_result
            if next_called:
                raise
            return call_at(index + 1, payload)

    return call_at(0, kwargs[payload_key])


def _trace_entry(result: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {}
    for key in ("source", "reason", "name"):
        value = result.get(key)
        if isinstance(value, str) and value:
            entry[key] = value
    if not entry:
        entry["source"] = "plugin"
    return entry
