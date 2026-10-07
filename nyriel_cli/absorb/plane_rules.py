"""PLANE family: the Buzz relay adapter -> Oblivion Plane (WO-BRAIN/PLANE-RENAME-1).

Plane is the Oblivion whitefork of Block's Buzz. Upstream Hermes ships a Buzz
platform adapter; this table renames it on every path in (absorb, Seraphim cut,
`scripts/rename-buzz-to-plane.py`). Name-free on purpose: no Angel name appears
here, so the cutter copies it unchanged into every sibling.

Carve-outs stay "buzz": a haptic buzz, prose "buzzword", "FizzBuzz", contributor
mail, legal files, and Block's upstream URLs (credited in the plugin's NOTICE).
"""
from __future__ import annotations

import fnmatch
import re

# Files that keep every "buzz" (fnmatch, `*` crosses `/`).
CARVEOUT_FILES: tuple[str, ...] = (
    "apps/desktop/src/lib/haptics.ts",
    "optional-skills/*/anti-slop.md",
    "optional-skills/*/neuroskill-bci/references/protocols.md",
    "contributors/emails/*",
    "*LICENSE*", "*NOTICE*", "*COPYING*",
    # the rename machinery names the old word by design
    "*_cli/absorb/plane_rules.py",
    "scripts/rename-buzz-to-plane.py",
    "scripts/migrate-config-buzz-to-plane.py",
)

# Tokens that keep "buzz" inside renamed files.
_PROTECT = re.compile(r"https?://github\.com/block/buzz[\w./#-]*|FizzBuzz|[Bb]uzzwords?")

# Hand rules the case table cannot know, applied first.
PRE_RULES: list[tuple[str, str]] = [
    ("https://mycommunity.communities.buzz.xyz", "https://plane.0blivion.io"),
    ("Block's open-source", "Oblivion's open-source"),
    ("Block's Nostr-based", "Oblivion's Nostr-based"),
    ("Block's own ACP", "Plane's own ACP"),
]

RULES: list[tuple[str, str]] = [("BUZZ", "PLANE"), ("Buzz", "Plane"), ("buzz", "plane")]

_LEFTOVER = re.compile(r"buzz", re.I)


def is_carved(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in CARVEOUT_FILES)


def swap_path(path: str) -> str:
    if is_carved(path):
        return path
    for find, repl in RULES:
        path = path.replace(find, repl)
    return path


def swap_text(text: str, path: str = "") -> str:
    if is_carved(path) or not _LEFTOVER.search(text):
        return text
    kept: list[str] = []

    def _stash(m: re.Match) -> str:
        kept.append(m.group(0))
        return f"\x01{len(kept) - 1}\x01"

    text = _PROTECT.sub(_stash, text)
    for find, repl in PRE_RULES + RULES:
        text = text.replace(find, repl)
    return re.sub(r"\x01(\d+)\x01", lambda m: kept[int(m.group(1))], text)


def leftovers(text: str, path: str = "") -> list[tuple[int, str]]:
    """(line number, line) for every "buzz" a carve-out does not cover."""
    if is_carved(path):
        return []
    return [(n, line) for n, line in enumerate(text.splitlines(), 1)
            if _LEFTOVER.search(_PROTECT.sub("", line))]


if __name__ == "__main__":  # self-check
    assert swap_text("BuzzAdapter BUZZ_RELAY_URL buzz-acp", "a.py") == "PlaneAdapter PLANE_RELAY_URL plane-acp"
    assert swap_text("[Buzz](https://github.com/block/buzz/tree/main/crates/buzz-acp) @FizzBuzz", "d.md") == \
        "[Plane](https://github.com/block/buzz/tree/main/crates/buzz-acp) @FizzBuzz"
    assert swap_text("e.g. https://mycommunity.communities.buzz.xyz", "x") == "e.g. https://plane.0blivion.io"
    assert swap_text("buzz", "apps/desktop/src/lib/haptics.ts") == "buzz"
    assert swap_path("tests/gateway/test_buzz_adapter.py") == "tests/gateway/test_plane_adapter.py"
    assert leftovers("ok\nbuzz\nhttps://github.com/block/buzz", "x") == [(2, "buzz")]
    print("plane_rules ok")
