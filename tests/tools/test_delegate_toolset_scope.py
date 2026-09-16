"""Tests for delegate_tool toolset scoping.

Verifies that subagents cannot gain tools that the parent does not have.
The LLM controls the `toolsets` parameter — without intersection with the
parent's enabled_toolsets, it can escalate privileges by requesting
arbitrary toolsets.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.delegate_tool import _strip_blocked_tools, _emit_parent_console


class TestToolsetIntersection:
    """Subagent toolsets must be a subset of parent's enabled_toolsets."""

    def test_requested_toolsets_intersected_with_parent(self):
        """LLM requests toolsets parent doesn't have — extras are dropped."""
        parent = SimpleNamespace(enabled_toolsets=["terminal", "file"])

        # Simulate the intersection logic from _build_child_agent
        parent_toolsets = set(parent.enabled_toolsets)
        requested = ["terminal", "file", "web", "browser", "rl"]
        scoped = [t for t in requested if t in parent_toolsets]

        assert sorted(scoped) == ["file", "terminal"]
        assert "web" not in scoped
        assert "browser" not in scoped
        assert "rl" not in scoped


    def test_strip_blocked_removes_delegation(self):
        """Blocked toolsets (delegation, clarify, etc.) are always removed."""
        child = _strip_blocked_tools(["terminal", "delegation", "clarify", "memory"])
        assert "delegation" not in child
        assert "clarify" not in child
        assert "memory" not in child
        assert "terminal" in child

    def test_empty_intersection_yields_empty_toolsets(self):
        """If parent has no overlap with requested, child gets nothing extra."""
        parent = SimpleNamespace(enabled_toolsets=["terminal"])

        parent_toolsets = set(parent.enabled_toolsets)
        requested = ["web", "browser"]
        scoped = [t for t in requested if t in parent_toolsets]

        assert scoped == []

    def test_plan_mode_children_are_reasoning_only(self, tmp_path):
        """A plan-mode parent may fan out divergent reasoning, but its
        children must receive no tools and therefore no side-effect path."""
        from agent.execution_policy import ExecutionPolicy, PlanModeState
        from tools.delegate_tool import _build_child_agent

        parent = MagicMock()
        parent.base_url = "https://openrouter.ai/api/v1"
        parent.api_key = "test-key"
        parent.provider = "openrouter"
        parent.api_mode = "chat_completions"
        parent.model = "test-model"
        parent.platform = "cli"
        parent.enabled_toolsets = ["nyriel-cli"]
        parent.disabled_toolsets = []
        parent.valid_tool_names = {"terminal", "read_file", "delegate_task"}
        parent._execution_policy = ExecutionPolicy(
            state=PlanModeState.PLANNING,
            task="design safely",
        )
        parent._delegate_depth = 0
        parent._active_children = []
        parent._active_children_lock = None
        from nyriel_state import SessionDB

        parent._session_db = SessionDB(db_path=tmp_path / "state.db")
        parent.session_id = "plan-parent"
        parent.tool_progress_callback = None
        parent.thinking_callback = None
        parent.providers_allowed = None
        parent.providers_ignored = None
        parent.providers_order = None
        parent.provider_sort = None

        with patch("run_agent.AIAgent") as agent_cls:
            agent_cls.return_value = MagicMock()
            agent_cls.return_value.session_id = "plan-child-1"
            agent_cls.return_value._session_db = None
            _build_child_agent(
                task_index=0,
                goal="Generate ideas without evaluation",
                context="Repository facts gathered by the parent",
                toolsets=None,
                model=None,
                max_iterations=10,
                task_count=1,
                parent_agent=parent,
                role="leaf",
            )

        _, kwargs = agent_cls.call_args
        # 4.3: read/search toolsets only (whatever the parent holds), and a
        # PLAN(child) policy row persisted for the child's own session so the
        # per-turn reload keeps name-level enforcement.
        from agent.execution_policy import ExecutionPolicyStore, ExecutionPosture, policy_deny_message

        toolsets = set(kwargs["enabled_toolsets"])
        assert toolsets and "delegation" not in toolsets and "terminal" not in toolsets and "plan" not in toolsets
        child_policy = ExecutionPolicyStore(parent._session_db).load("plan-child-1")
        assert child_policy.plan_child is True and child_policy.posture is ExecutionPosture.PLAN
        assert policy_deny_message(child_policy, "read_file") is None
        assert policy_deny_message(child_policy, "write_file") is not None
        assert policy_deny_message(child_policy, "save_plan") is not None


class TestEmitParentConsole:
    """Progress lines (e.g. ``✓ [N/M] …``) must route through the parent's
    configured ``_safe_print`` in headless stdio hosts (ACP, gateway) so
    they don't land on stdout and corrupt JSON-RPC frames. Regression for a
    bug where delegate_task completion lines pushed to stdout caused
    ``Failed to parse JSON message: ✓ [3/3] …`` errors in the ACP adapter."""

    def test_routes_through_parent_safe_print_when_available(self, capsys):
        captured_lines = []
        parent = SimpleNamespace(_safe_print=lambda line: captured_lines.append(line))

        _emit_parent_console(parent, "  ✓ [1/3] Research done  (11.55s)")

        assert captured_lines == ["  ✓ [1/3] Research done  (11.55s)"]
        stdout_stderr = capsys.readouterr()
        assert stdout_stderr.out == ""
        assert stdout_stderr.err == ""


    def test_non_callable_safe_print_is_ignored(self, capsys):
        """Defensive: if _safe_print is set but not callable, fall back."""
        parent = SimpleNamespace(_safe_print="not-a-function")
        _emit_parent_console(parent, "  ✓ [3/3] non-callable guard")
        captured = capsys.readouterr()
        assert "non-callable guard" in captured.out
