"""WO-MOA/2 universal invariant: delegate children never run the `moa`
virtual provider.

A child inheriting (or being explicitly overridden onto) provider == "moa"
would build its own MoAClient and fan out reference slots per iteration —
a category violation (children are isolated reasoning branches; advisor
fan-out belongs to the parent's turn) and a cost multiplier. The guard in
_build_child_agent retargets such children onto the MoA aggregator's real
model via _resolve_moa_child_runtime.
"""

from unittest.mock import MagicMock, patch


def _moa_parent() -> MagicMock:
    parent = MagicMock()
    parent.base_url = "moa://local"
    parent.api_key = "moa-fake-key"
    parent.provider = "moa"
    parent.api_mode = None
    parent.model = "default"  # MoA agents store the preset name as model
    parent.platform = "cli"
    parent.enabled_toolsets = ["nyriel-cli"]
    parent.disabled_toolsets = []
    parent.valid_tool_names = {"terminal", "read_file", "delegate_task"}
    parent._execution_policy = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = None
    parent._session_db = None
    parent.session_id = "moa-parent"
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    return parent


_AGG_RUNTIME = {
    "provider": "anthropic",
    "model": "claude-sonnet-5",
    "base_url": "https://api.anthropic.com",
    "api_key": "agg-real-key",
    "api_mode": "anthropic_messages",
}


class TestMoaChildGuard:
    def test_child_never_inherits_moa_provider(self):
        from tools.delegate_tool import _build_child_agent

        parent = _moa_parent()
        with patch("run_agent.AIAgent") as agent_cls, patch(
            "tools.delegate_tool._resolve_moa_child_runtime",
            return_value=dict(_AGG_RUNTIME),
        ) as resolver:
            agent_cls.return_value = MagicMock()
            _build_child_agent(
                task_index=0,
                goal="isolated reasoning",
                context="ctx",
                toolsets=None,
                model=None,
                max_iterations=5,
                task_count=1,
                parent_agent=parent,
                role="leaf",
            )

        resolver.assert_called_once()
        _, kwargs = agent_cls.call_args
        assert kwargs["provider"] == "anthropic"
        assert kwargs["model"] == "claude-sonnet-5"
        assert kwargs["base_url"] == "https://api.anthropic.com"
        assert kwargs["api_key"] == "agg-real-key"
        assert kwargs["provider"] != "moa"
        assert "moa" not in str(kwargs["base_url"])

    def test_explicit_moa_override_is_also_defused(self):
        from tools.delegate_tool import _build_child_agent

        parent = _moa_parent()
        parent.provider = "openrouter"
        parent.base_url = "https://openrouter.ai/api/v1"
        parent.api_key = "or-key"
        with patch("run_agent.AIAgent") as agent_cls, patch(
            "tools.delegate_tool._resolve_moa_child_runtime",
            return_value=dict(_AGG_RUNTIME),
        ) as resolver:
            agent_cls.return_value = MagicMock()
            _build_child_agent(
                task_index=0,
                goal="isolated reasoning",
                context="ctx",
                toolsets=None,
                model=None,
                max_iterations=5,
                task_count=1,
                parent_agent=parent,
                role="leaf",
                override_provider="moa",
            )

        resolver.assert_called_once()
        _, kwargs = agent_cls.call_args
        assert kwargs["provider"] == "anthropic"

    def test_explicit_child_model_is_honored_on_aggregator_provider(self):
        from tools.delegate_tool import _build_child_agent

        parent = _moa_parent()
        with patch("run_agent.AIAgent") as agent_cls, patch(
            "tools.delegate_tool._resolve_moa_child_runtime",
            return_value=dict(_AGG_RUNTIME),
        ):
            agent_cls.return_value = MagicMock()
            _build_child_agent(
                task_index=0,
                goal="isolated reasoning",
                context="ctx",
                toolsets=None,
                model="claude-haiku-4-5",
                max_iterations=5,
                task_count=1,
                parent_agent=parent,
                role="leaf",
            )

        _, kwargs = agent_cls.call_args
        assert kwargs["provider"] == "anthropic"
        assert kwargs["model"] == "claude-haiku-4-5"


    def test_model_pin_keeps_model_equal_to_parent(self):
        """WO-POSTURE/1 4.2: a pinned frame seat keeps its model even when it
        equals the parent's (which the guard would otherwise swap for the
        aggregator's); provider/base_url/key still leave the moa virtual."""
        from tools.delegate_tool import _build_child_agent

        parent = _moa_parent()
        with patch("run_agent.AIAgent") as agent_cls, patch(
            "tools.delegate_tool._resolve_moa_child_runtime",
            return_value=dict(_AGG_RUNTIME),
        ):
            agent_cls.return_value = MagicMock()
            _build_child_agent(
                task_index=0,
                goal="framed reasoning",
                context="ctx",
                toolsets=None,
                model=parent.model,
                max_iterations=5,
                task_count=1,
                parent_agent=parent,
                role="leaf",
                model_pin=True,
            )

        _, kwargs = agent_cls.call_args
        assert kwargs["model"] == parent.model
        assert kwargs["provider"] == "anthropic"
        assert "moa" not in str(kwargs["base_url"])


class TestResolveMoaChildRuntime:
    def test_resolves_aggregator_slot_not_moa(self):
        from tools.delegate_tool import _resolve_moa_child_runtime

        parent = _moa_parent()
        with patch(
            "nyriel_cli.config.load_config", return_value={}
        ), patch(
            "nyriel_cli.runtime_provider.resolve_runtime_provider",
            return_value=dict(_AGG_RUNTIME),
        ) as rrp:
            result = _resolve_moa_child_runtime(parent)

        # The aggregator slot requested must never itself be `moa` (config
        # layer already rejects moa slots); the resolved bundle is real.
        requested = rrp.call_args.kwargs.get("requested") or rrp.call_args.args[0]
        assert str(requested).lower() != "moa"
        assert result["provider"].lower() != "moa"
        assert result["api_key"] == "agg-real-key"
        assert result["base_url"] == "https://api.anthropic.com"
