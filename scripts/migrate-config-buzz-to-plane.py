#!/usr/bin/env python3
"""Migrate a Brain home's config from the Buzz adapter to Plane (WO-BRAIN/PLANE-RENAME-1).

    migrate-config-buzz-to-plane.py FILE... [--relay-url URL]

FILE is a config.yaml or a .env. Line edits only (comments and order survive):
  config.yaml  platforms.buzz -> platforms.plane, display.platforms.buzz -> .plane,
               BUZZ_* -> PLANE_* (env_passthrough), cli_path basename buzz -> plane,
               --relay-url rewrites platforms.plane.extra.relay_url
  .env         BUZZ_* keys -> PLANE_*, a .../buzz CLI path -> .../plane
Writes FILE.bak-pre-plane-rename first (once; a re-run keeps the first backup),
then proves the YAML still parses to the same tree with only the keys renamed.
Idempotent. Never prints values.
"""
import re
import shutil
import sys
from pathlib import Path

import yaml

KEY = re.compile(r"^(\s+)buzz:(\s*(#.*)?)$")
CLI = re.compile(r"^(\s*(?:cli_path:\s*|PLANE_CLI_PATH=)\S*/)buzz(\s*(#.*)?)$")
RELAY = re.compile(r"^(\s+relay_url:\s*)\S+(.*)$")


def migrate(text: str, is_env: bool, relay_url: str | None) -> str:
    out, in_plane = [], False
    for line in text.splitlines(keepends=True):
        body, nl = line.rstrip("\r\n"), line[len(line.rstrip("\r\n")):]
        if not is_env:
            body = KEY.sub(r"\1plane:\2", body)
        body = re.sub(r"\bBUZZ_", "PLANE_", body)
        body = CLI.sub(r"\1plane\2", body)
        if not is_env and relay_url:
            m = re.match(r"^(\s*)(\w+):", body)
            if m and len(m.group(1)) <= 2:  # a platforms.<name> block opens at indent 2
                in_plane = len(m.group(1)) == 2 and m.group(2) == "plane"
            if in_plane:
                body = RELAY.sub(lambda m: f"{m.group(1)}{relay_url}{m.group(2)}", body)
        out.append(body + nl)
    return "".join(out)


def renamed(node):
    """The expected parse after migration, built from the parse before it."""
    if isinstance(node, dict):
        return {("plane" if k == "buzz" else k): renamed(v) for k, v in node.items()}
    if isinstance(node, list):
        return [renamed(x) for x in node]
    if isinstance(node, str):
        s = re.sub(r"\bBUZZ_", "PLANE_", node)
        return re.sub(r"/buzz$", "/plane", s)
    return node


def drop_relay(node):
    if isinstance(node, dict):
        return {k: drop_relay(v) for k, v in node.items() if k != "relay_url"}
    return [drop_relay(x) for x in node] if isinstance(node, list) else node


def main() -> int:
    argv = sys.argv[1:]
    relay_url = None
    if "--relay-url" in argv:
        i = argv.index("--relay-url")
        relay_url = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        sys.exit(__doc__)
    for name in argv:
        p = Path(name)
        is_env = p.name.startswith(".env") or p.suffix == ".env"
        before = p.read_text(encoding="utf-8")
        after = migrate(before, is_env, relay_url)
        if not is_env:
            want, got = renamed(yaml.safe_load(before)), yaml.safe_load(after)
            if relay_url:
                want, got = drop_relay(want), drop_relay(got)
            if want != got:
                sys.exit(f"{p}: parse check FAILED, file left untouched")
        if after == before:
            print(f"{p}: already migrated")
            continue
        bak = p.with_name(p.name + ".bak-pre-plane-rename")
        if not bak.exists():
            shutil.copy2(p, bak)
        p.write_text(after, encoding="utf-8")
        left = [n for n, l in enumerate(after.splitlines(), 1) if re.search("buzz", l, re.I)]
        print(f"{p}: migrated ({sum(a != b for a, b in zip(before.splitlines(), after.splitlines()))} lines)"
              + (f"; 'buzz' still on lines {left}" if left else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
