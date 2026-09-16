"""Detect newer absorbable upstream tags (cached).

Reads release tags from the `upstream` remote, compares them to the recorded
base in UPSTREAM_BASE.md, and caches the newest absorbable tag with a TTL so
launch-time banner checks stay cheap. Pre-release/RC tags are never offered.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from . import driver

_TAG = re.compile(r"refs/tags/(v\d{4}\.\d+\.\d+(?:\.\d+)?)\^?\{?\}?$")


def _key(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("vV").split("."))


def list_upstream_tags(repo: str) -> list[str]:
    out = subprocess.run(["git", "-C", repo, "ls-remote", "--tags", "upstream"],
                         capture_output=True, text=True, check=True).stdout
    tags = set()
    for line in out.splitlines():
        m = _TAG.search(line)
        if m and not re.search(r"(rc|alpha|beta|pre)", m.group(1), re.I):
            tags.add(m.group(1))
    return sorted(tags, key=_key)


def newer_tags(base: str, tags: list[str]) -> list[str]:
    b = _key(base)
    return sorted({t for t in tags if _key(t) > b}, key=_key)


def cache_file(repo: str) -> Path:
    """Single source of truth for the check-cache path (driver invalidates it
    on --commit so a fresh cache can't keep offering the just-absorbed tag)."""
    return Path(repo) / ".git" / "absorb_check.json"


def latest_absorbable(repo: str, *, ttl: int = 21600) -> str | None:
    cache = cache_file(repo)
    try:
        data = json.loads(cache.read_text())
        if time.time() - data["t"] < ttl:
            return data["tag"]
    except Exception:
        pass
    try:
        base = driver.current_base(repo)
        nt = newer_tags(base, list_upstream_tags(repo))
        tag = nt[-1] if nt else None
    except Exception:
        tag = None
    try:
        cache.write_text(json.dumps({"t": time.time(), "tag": tag}))
    except Exception:
        pass
    return tag
