"""Plan-mode executor deny is NON-halting: the denied call gets a synthetic
error result, other calls in the batch still run, and the turn's guardrail
halt decision stays unset (unlike guardrail blocks, which end the turn)."""

import json
import threading
import time
from unittest.mock import MagicMock

from agent.execution_policy import ExecutionPolicy, PlanModeState
from agent.tool_guardrails import ToolCallGuardrailController


def _make_agent(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("NYRIEL_INFERENCE_PROVIDER", "")
    import run_agent as _ra

    class _Stub:
        _interrupt_requested = False
        _interrupt_message = None
        _execution_thread_id = threading.current_thread().ident
        _interrupt_thread_signal_pending = False
        log_prefix = ""
        quiet_mode = True
        verbose_logging = False
        log_prefix_chars = 200
        _checkpoint_mgr = MagicMock(enabled=False)
        _subdirectory_hints = MagicMock()
        tool_progress_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        _todo_store = MagicMock()
        _session_db = None
        valid_tool_names = set()
        _turns_since_memory = 0
        _iters_since_skill = 0
        _current_tool = None
        _last_activity = 0
        _print_fn = print
        _active_children: list = []
        _tool_guardrail_halt_decision = None
        session_id = "sess-deny-test"
        _current_turn_id = ""
        _current_api_request_id = ""

        def __init__(self):
            self._tool_worker_threads: set = set()
            self._tool_worker_threads_lock = threading.Lock()
            self._active_children_lock = threading.Lock()
            self._tool_guardrails = ToolCallGuardrailController()
            self._subdirectory_hints = MagicMock()
            self._subdirectory_hints.check_tool_call.return_value = None
            self._execution_policy = ExecutionPolicy(
                state=PlanModeState.PLANNING, task="test", plan_id="cafe0123beef"
            )

        def _touch_activity(self, desc):
            self._last_activity = time.time()

        def _vprint(self, msg, force=False):
            pass

        def _safe_print(self, msg):
            pass

        def _should_emit_quiet_tool_messages(self):
            return False

        def _should_start_quiet_spinner(self):
            return False

        def _has_stream_consumers(self):
            return False

        def _set_tool_guardrail_halt(self, decision):
            self._tool_guardrail_halt_decision = decision

        def _tool_result_content_for_active_model(self, name, result):
            return result

        def _append_guardrail_observation(self, name, args, result, failed=False):
            return result

        def _flush_messages_to_session_db(self, *a, **kw):
            pass

    stub = _Stub()
    stub._execute_tool_calls_concurrent = _ra.AIAgent._execute_tool_calls_concurrent.__get__(stub)
    stub._guardrail_block_result = _ra.AIAgent._guardrail_block_result.__get__(stub)
    stub._apply_pending_steer_to_tool_results = lambda *a, **kw: None
    stub._invoke_tool = MagicMock(side_effect=lambda *a, **kw: '{"ok": true}')
    return stub


class _FakeToolCall:
    def __init__(self, name, args="{}", call_id="tc_1"):
        self.function = MagicMock(name=name, arguments=args)
        self.function.name = name
        self.id = call_id


class _FakeAssistantMsg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def test_concurrent_deny_is_non_halting_and_batch_continues(monkeypatch):
    agent = _make_agent(monkeypatch)
    tc_denied = _FakeToolCall("terminal", args='{"command": "rm -rf /"}', call_id="tc_deny")
    tc_allowed = _FakeToolCall("read_file", args='{"path": "x.py"}', call_id="tc_ok")
    msg = _FakeAssistantMsg([tc_denied, tc_allowed])
    messages = []

    agent._execute_tool_calls_concurrent(msg, messages, "test_task")

    assert len(messages) == 2
    by_id = {m.get("tool_call_id"): m for m in messages}
    denied = by_id["tc_deny"]["content"]
    assert "Plan mode" in denied and "terminal" in denied
    # the allowed read executed normally
    assert json.loads(by_id["tc_ok"]["content"]) == {"ok": True}
    agent._invoke_tool.assert_called_once()
    # NON-halting: the guardrail halt decision must remain unset so the
    # conversation loop does not break the turn.
    assert agent._tool_guardrail_halt_decision is None


def test_concurrent_act_posture_runs_everything(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._execution_policy = ExecutionPolicy()  # OFF -> ACT
    msg = _FakeAssistantMsg([_FakeToolCall("terminal", call_id="tc_t")])
    messages = []
    agent._execute_tool_calls_concurrent(msg, messages, "test_task")
    assert json.loads(messages[0]["content"]) == {"ok": True}
    assert agent._tool_guardrail_halt_decision is None


def _bind_sequential(stub):
    import run_agent as _ra
    stub._execute_tool_calls_sequential = (
        _ra.AIAgent._execute_tool_calls_sequential.__get__(stub)
    )
    stub.tool_delay = 0
    return stub


def test_sequential_deny_is_non_halting_and_loop_continues(monkeypatch):
    """The sequential path must enforce the same deny: synthetic error
    result, no real execution, no guardrail halt, later calls still run."""
    agent = _bind_sequential(_make_agent(monkeypatch))
    tc_denied = _FakeToolCall(
        "write_file", args='{"path": "x.py", "content": "boom"}', call_id="tc_deny"
    )
    msg = _FakeAssistantMsg([tc_denied])
    messages = []

    agent._execute_tool_calls_sequential(msg, messages, "test_task")

    assert len(messages) == 1
    result = messages[0]
    assert result.get("tool_call_id") == "tc_deny"
    assert "Plan mode" in result["content"] and "write_file" in result["content"]
    agent._invoke_tool.assert_not_called()
    assert agent._tool_guardrail_halt_decision is None


def test_sequential_act_posture_save_plan_denied(monkeypatch):
    """Outside plan mode, save_plan itself is the denied tool."""
    agent = _bind_sequential(_make_agent(monkeypatch))
    agent._execution_policy = ExecutionPolicy()  # OFF -> ACT
    msg = _FakeAssistantMsg(
        [_FakeToolCall("save_plan", args='{"title": "t", "content": "c"}', call_id="tc_sp")]
    )
    messages = []
    agent._execute_tool_calls_sequential(msg, messages, "test_task")
    assert len(messages) == 1
    assert "only available in plan mode" in messages[0]["content"]
    assert agent._tool_guardrail_halt_decision is None
