"""``nyriel hello`` subcommand parser."""

from __future__ import annotations

from collections.abc import Callable


def build_hello_parser(subparsers, *, cmd_hello: Callable) -> None:
    """Attach the ``hello`` subcommand to ``subparsers``."""
    hello_parser = subparsers.add_parser(
        "hello",
        help="Print a greeting from Nyriel Brain",
    )
    hello_parser.set_defaults(func=cmd_hello)