"""Flesh organ plugin — registration entry point (mirrors plugins/spotify).

kind: standalone — explicit opt-in via plugins.enabled. This organ spends
real money and dispatches real humans; it must never auto-load.
"""

from __future__ import annotations

from plugins.flesh.handlers import (
    _check_flesh_available,
    _handle_flesh_cancel,
    _handle_flesh_dispatch,
    _handle_flesh_quote,
    _handle_flesh_status,
)
from plugins.flesh.schemas import (
    FLESH_CANCEL_SCHEMA,
    FLESH_DISPATCH_SCHEMA,
    FLESH_QUOTE_SCHEMA,
    FLESH_STATUS_SCHEMA,
)

_TOOLS = (
    ("flesh_quote",    FLESH_QUOTE_SCHEMA,    _handle_flesh_quote,    "🚚"),
    ("flesh_dispatch", FLESH_DISPATCH_SCHEMA, _handle_flesh_dispatch, "📦"),
    ("flesh_status",   FLESH_STATUS_SCHEMA,   _handle_flesh_status,   "📍"),
    ("flesh_cancel",   FLESH_CANCEL_SCHEMA,   _handle_flesh_cancel,   "✋"),
)


def register(ctx) -> None:
    """Register Flesh tools; resume tracking of any open dispatches."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="flesh",
            schema=schema,
            handler=handler,
            check_fn=_check_flesh_available,
            emoji=emoji,
        )
    if _check_flesh_available():
        from plugins.flesh import poller

        poller.start()
