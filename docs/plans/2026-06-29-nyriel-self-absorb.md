# Nyriel Self-Absorb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make absorbing a new upstream Hermes release a first-class Nyriel capability — a deterministic `nyriel absorb` subcommand, proactive detection, and a skill so Nyriel can drive it agentically with a human approval gate.

**Architecture:** Graduate the existing `scripts/absorb/` harness into a packaged, importable `nyriel_cli/absorb/` module; port the one bash driver to Python; add a `nyriel absorb` subcommand, an upstream-tag detection hook on the existing banner update-check surface, and a repo-local skill.

**Tech Stack:** Python 3.13, argparse (existing CLI), git plumbing via `subprocess`, pytest 9.0.2 (+ pytest-asyncio).

## Global Constraints

- Independent version line; `pyproject.toml` `version` is the source of truth. Current base is `v2026.6.19` (see `UPSTREAM_BASE.md`).
- Pure stdlib + git only in the absorb module — **no new runtime dependencies**.
- Rebrand carve-outs that must keep upstream tokens: `**/achievements/LICENSE`, `plugins/security-guidance/NOTICE`, `UPSTREAM_BASE.md`, `CHANGELOG.md`, `scripts/absorb/`, and the new `nyriel_cli/absorb/` itself.
- Self-modifying-core guardrails are non-negotiable: git/source-install-only, branch isolation (`absorb/<tag>`, never `main`), gate-before-merge, parity-READY-before-commit, **never auto-push**, refuse pre-release/RC tags, one-step `--abort`.
- Genuine divergence to preserve (never reverted by an absorb): glyph `✶`, Brain Settings overlay, versioned model name, attribution "created by Embrey The Creator".
- Follow existing test patterns; tests live in `tests/nyriel_cli/`.

---

## File Structure

- Create `nyriel_cli/absorb/__init__.py` — package marker + public API re-exports.
- Move `scripts/absorb/rename_map.py` → `nyriel_cli/absorb/rename_map.py` (unchanged).
- Move `scripts/absorb/rebrand_tree.py` → `nyriel_cli/absorb/rebrand_tree.py` (drop the `sys.path` hack; use a package-relative import).
- Move `scripts/absorb/parity_report.py` → `nyriel_cli/absorb/parity_report.py` (add a callable `report()` alongside the CLI `main()`).
- Create `nyriel_cli/absorb/driver.py` — port of `absorb.sh`: `gate()`, `absorb()`, `commit()`, `abort()`, guardrails, base-ref discovery.
- Create `nyriel_cli/absorb/detect.py` — upstream-tag detection + cache.
- Modify `nyriel_cli/_parser.py` — register the `absorb` subparser.
- Modify `nyriel_cli/main.py` — dispatch `absorb`; call detect from the existing update-check.
- Modify `nyriel_cli/banner.py` — surface the absorb-available line.
- Create `skills/software-development/absorb-upstream/SKILL.md` — the agentic skill.
- Delete `scripts/absorb/absorb.sh`; leave `scripts/absorb/` empty or a one-line README pointing to the package.
- Tests: `tests/nyriel_cli/test_absorb_driver.py`, `test_absorb_detect.py`, `test_absorb_parity.py`.

---

## Task 1: Repackage the harness into `nyriel_cli/absorb/`

**Files:**
- Create: `nyriel_cli/absorb/__init__.py`
- Move: `scripts/absorb/{rename_map,rebrand_tree,parity_report}.py` → `nyriel_cli/absorb/`
- Modify: `nyriel_cli/absorb/rebrand_tree.py` (import fix), `nyriel_cli/absorb/parity_report.py` (add `report()`)
- Test: `tests/nyriel_cli/test_absorb_parity.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `rename_map.swap_path(path) -> str`, `swap_text(text, nyriel_path, attribution=True) -> str`, `looks_binary(bytes) -> bool`
  - `rebrand_tree.build_rebranded_tree(ref: str, attribution: bool = True) -> str` (returns a tree OID)
  - `parity_report.report(merged_tree: str, theirs_tree: str, head_ref: str) -> dict` with keys `{re_added:int, removed:int, divergence:int, conflicts:list[str], stray:list[str], ready:bool}`

- [ ] **Step 1: Move the three modules and create the package marker**

```bash
mkdir -p nyriel_cli/absorb
git mv scripts/absorb/rename_map.py nyriel_cli/absorb/rename_map.py
git mv scripts/absorb/rebrand_tree.py nyriel_cli/absorb/rebrand_tree.py
git mv scripts/absorb/parity_report.py nyriel_cli/absorb/parity_report.py
```

Create `nyriel_cli/absorb/__init__.py`:

```python
"""Rename-aware upstream absorb harness, packaged for the nyriel CLI.

Public API used by the `nyriel absorb` subcommand, detection, and the skill.
"""
from . import rename_map, rebrand_tree, parity_report  # noqa: F401
```

- [ ] **Step 2: Fix `rebrand_tree.py` import to be package-relative**

In `nyriel_cli/absorb/rebrand_tree.py`, replace the `sys.path.insert(...)` + `import rename_map as T` block with:

```python
try:
    from . import rename_map as T          # packaged
except ImportError:                         # direct-script fallback
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rename_map as T
```

- [ ] **Step 3: Add a callable `report()` to `parity_report.py`**

Refactor the body of `main()` into `report(merged, theirs, head) -> dict` returning the structured result, and have `main()` call it and print. Add:

```python
def report(merged: str, theirs: str, head: str) -> dict:
    merged_names, head_names = names(merged), names(head)
    conflicts = grep_conflict_markers(merged)
    stray = grep_stray(merged)
    return {
        "re_added": len(merged_names - head_names),
        "removed": len(head_names - merged_names),
        "divergence": len(diff_names(merged, theirs)),
        "conflicts": conflicts,
        "stray": stray,
        "ready": not conflicts and not stray,
    }
```

- [ ] **Step 4: Write the failing test**

Create `tests/nyriel_cli/test_absorb_parity.py`:

```python
from nyriel_cli.absorb import rename_map, parity_report


def test_swap_text_hermes_and_attribution():
    out = rename_map.swap_text("Hermes Agent, created by Hermes", "nyriel_cli/x.py")
    assert "Nyriel Brain" in out
    assert "created by Embrey The Creator" in out
    assert "Hermes" not in out


def test_legal_file_keeps_nous_attribution():
    out = rename_map.swap_text("Copyright (c) 2025 Nous Research", "LICENSE")
    assert out == "Copyright (c) 2025 Nous Research"


def test_report_flags_conflict_markers(monkeypatch):
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: ["a.py"])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is False and r["conflicts"] == ["a.py"]
```

- [ ] **Step 5: Run the test, expect FAIL then PASS**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_parity.py -v -o addopts=""`
Expected: PASS after Steps 1-3 (FAIL with ImportError before the move/refactor).

- [ ] **Step 6: Remove the bash driver + leave a pointer, then commit**

```bash
git rm scripts/absorb/absorb.sh
printf 'Absorb harness moved to nyriel_cli/absorb/. Run via `nyriel absorb`.\n' > scripts/absorb/README.md
git add -A
git commit -m "refactor(absorb): package harness into nyriel_cli/absorb"
```

---

## Task 2: Port the driver (`absorb.sh` → `driver.py`) with guardrails

**Files:**
- Create: `nyriel_cli/absorb/driver.py`
- Test: `tests/nyriel_cli/test_absorb_driver.py`

**Interfaces:**
- Consumes: `rebrand_tree.build_rebranded_tree`, `parity_report.report` (Task 1).
- Produces:
  - `current_base(repo: str) -> str` — reads the `Upstream tag` row from `UPSTREAM_BASE.md`.
  - `install_ok(repo: str) -> tuple[bool, str]` — git checkout + `upstream` remote present.
  - `gate(repo: str, base_ref: str) -> tuple[bool, str]` — fidelity gate (0 stray tokens).
  - `absorb(repo: str, tag: str, base_ref: str|None=None) -> dict` — runs merge onto `absorb/<tag>`, returns `{branch, merged_tree, parity, ready}`; raises `AbsorbRefused` on guardrail violation.
  - `commit(repo, tag) -> str`, `abort(repo, tag) -> None`.
  - Exception `AbsorbRefused(Exception)`.

- [ ] **Step 1: Write the failing test for the install guard + RC refusal**

Create `tests/nyriel_cli/test_absorb_driver.py`:

```python
import subprocess
import pytest
from nyriel_cli.absorb import driver


def _git(repo, *a):
    subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)


def test_install_ok_requires_upstream_remote(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(str(repo), "init", "-q")
    ok, msg = driver.install_ok(str(repo))
    assert ok is False and "upstream" in msg.lower()


def test_absorb_refuses_prerelease_tag(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(str(repo), "init", "-q")
    _git(str(repo), "remote", "add", "upstream", "https://example.invalid/x.git")
    with pytest.raises(driver.AbsorbRefused, match="pre-release"):
        driver.absorb(str(repo), "v2026.7.0-rc1")
```

- [ ] **Step 2: Run it, expect FAIL (module missing)**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -v -o addopts=""`
Expected: FAIL with `ModuleNotFoundError: nyriel_cli.absorb.driver`.

- [ ] **Step 3: Implement `driver.py`**

```python
"""nyriel absorb driver — ports absorb.sh to Python (stdlib + git only)."""
from __future__ import annotations
import re
import subprocess
from . import rebrand_tree, parity_report

ALLOWED_STRAY = ("achievements/LICENSE", "security-guidance/NOTICE",
                 "UPSTREAM_BASE.md", "CHANGELOG.md", "scripts/absorb/",
                 "nyriel_cli/absorb/")
_PRERELEASE = re.compile(r"(rc|alpha|beta|pre)", re.I)


class AbsorbRefused(Exception):
    pass


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


def install_ok(repo: str) -> tuple[bool, str]:
    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        return False, "absorb needs a git/source checkout (not a pip/docker install)."
    remotes = _git(repo, "remote").stdout.split()
    if "upstream" not in remotes:
        return False, "no `upstream` remote — add NousResearch/hermes-agent as `upstream`."
    return True, ""


def current_base(repo: str) -> str:
    text = (_git(repo, "show", "HEAD:UPSTREAM_BASE.md", check=False).stdout
            or open(f"{repo}/UPSTREAM_BASE.md").read())
    m = re.search(r"Upstream tag\s*\|\s*`?([vV][0-9.]+)`?", text)
    if not m:
        raise AbsorbRefused("could not read the current base tag from UPSTREAM_BASE.md")
    return m.group(1)


def gate(repo: str, base_ref: str) -> tuple[bool, str]:
    tree = rebrand_tree.build_rebranded_tree(base_ref, attribution=False)
    stray = _git(repo, "grep", "-ilI", "-e", "hermes", "-e", "nousresearch", tree,
                 check=False).stdout.splitlines()
    stray = [s.split(":", 1)[1] for s in stray if ":" in s
             and not any(a in s for a in ALLOWED_STRAY)]
    return (not stray), ("\n".join(stray) if stray else "")


def absorb(repo: str, tag: str, base_ref: str | None = None) -> dict:
    if _PRERELEASE.search(tag):
        raise AbsorbRefused(f"refusing pre-release/RC tag {tag}")
    ok, msg = install_ok(repo)
    if not ok:
        raise AbsorbRefused(msg)
    base_ref = base_ref or current_base(repo)
    if _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode != 0:
        _git(repo, "fetch", "-q", "upstream", "tag", tag)
    passed, detail = gate(repo, base_ref)
    if not passed:
        raise AbsorbRefused(f"fidelity gate failed (rebrand map drifted):\n{detail}")

    base_tree = rebrand_tree.build_rebranded_tree(base_ref, attribution=False)
    theirs_tree = rebrand_tree.build_rebranded_tree(tag, attribution=True)
    ours_tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    base_c = _git(repo, "commit-tree", base_tree, "-m", f"T({base_ref})").stdout.strip()
    theirs_c = _git(repo, "commit-tree", theirs_tree, "-p", base_c, "-m", f"T({tag})").stdout.strip()
    ours_c = _git(repo, "commit-tree", ours_tree, "-p", base_c, "-m", "ours").stdout.strip()

    mt = _git(repo, "merge-tree", "--write-tree", f"--merge-base={base_c}", ours_c, theirs_c,
              check=False)
    merged = mt.stdout.splitlines()[0]
    branch = f"absorb/{tag}"
    if _git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise AbsorbRefused(f"branch {branch} already exists; --abort it first")
    _git(repo, "branch", branch, "HEAD")
    rep = parity_report.report(merged, theirs_tree, "HEAD")
    # stash refs so commit()/abort() can finish the job
    _git(repo, "config", "--local", "absorb.lastTag", tag)
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    return {"branch": branch, "merged_tree": merged, "parity": rep, "ready": rep["ready"]}


def commit(repo: str, tag: str) -> str:
    merged = _git(repo, "config", "--local", "--get", "absorb.lastMerged").stdout.strip()
    rep = parity_report.report(merged,
                               rebrand_tree.build_rebranded_tree(tag, attribution=True), "HEAD")
    if not rep["ready"]:
        raise AbsorbRefused("parity not READY (conflict markers or stray tokens remain)")
    commit_oid = _git(repo, "commit-tree", merged, "-p", "HEAD",
                      "-m", f"absorb: {tag} (full parity)").stdout.strip()
    _git(repo, "update-ref", f"refs/heads/absorb/{tag}", commit_oid)
    return commit_oid


def abort(repo: str, tag: str) -> None:
    _git(repo, "branch", "-D", f"absorb/{tag}", check=False)
```

- [ ] **Step 4: Run the tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -v -o addopts=""`
Expected: PASS (both install-guard and RC-refusal).

- [ ] **Step 5: Add a gate regression test against the real repo**

Append to `tests/nyriel_cli/test_absorb_driver.py`:

```python
import os
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_fidelity_gate_passes_on_current_tree():
    """T(base) must still reproduce HEAD modulo genuine divergence — 0 stray tokens."""
    ok, detail = driver.gate(REPO, driver.current_base(REPO))
    assert ok, f"rebrand map drifted; stray tokens:\n{detail}"
```

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py::test_fidelity_gate_passes_on_current_tree -v -o addopts=""`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nyriel_cli/absorb/driver.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): python driver with guardrails + fidelity gate test"
```

---

## Task 3: Wire the `nyriel absorb` subcommand

**Files:**
- Modify: `nyriel_cli/_parser.py` (in `build_top_level_parser`, after the existing `subparsers.add_parser(...)` calls)
- Modify: `nyriel_cli/main.py` (dispatch, near `elif action == "update":` ~line 10621)
- Test: `tests/nyriel_cli/test_absorb_driver.py` (add CLI dispatch test)

**Interfaces:**
- Consumes: `driver.gate/absorb/commit/abort/current_base/install_ok` (Task 2), `detect.latest_absorbable` (Task 4, optional for `--check`).
- Produces: `nyriel_cli.main.cmd_absorb(args) -> int` (process exit code).

- [ ] **Step 1: Register the subparser in `_parser.py`**

After the last `subparsers.add_parser(...)` block in `build_top_level_parser()`:

```python
absorb_parser = subparsers.add_parser(
    "absorb", help="Absorb a new upstream Hermes release into the fork (maintainer/git installs)")
absorb_parser.add_argument("tag", nargs="?", help="upstream tag to absorb, e.g. v2026.7.0")
absorb_parser.add_argument("--base", default=None, help="override the merge-base tag")
absorb_parser.add_argument("--check", action="store_true", help="only report if a newer upstream tag exists")
absorb_parser.add_argument("--gate", action="store_true", help="only run the rebrand fidelity gate")
absorb_parser.add_argument("--commit", action="store_true", help="finalize the absorb (requires parity READY)")
absorb_parser.add_argument("--abort", action="store_true", help="delete the absorb branch and restore")
```

- [ ] **Step 2: Add the dispatch handler in `main.py`**

Add a handler function near the other `_cmd_*` helpers:

```python
def cmd_absorb(args) -> int:
    import os
    from nyriel_cli.absorb import driver, detect
    repo = os.getcwd()
    ok, msg = driver.install_ok(repo)
    if not ok and not args.check:
        print(f"  ✗ {msg}")
        return 2
    if args.check:
        tag = detect.latest_absorbable(repo)
        print(f"  ✶ upstream {tag} available to absorb" if tag else "  ✓ up to date with upstream")
        return 0
    if args.gate:
        passed, detail = driver.gate(repo, args.base or driver.current_base(repo))
        print("  ✓ gate passed (0 stray tokens)" if passed else f"  ✗ gate failed:\n{detail}")
        return 0 if passed else 1
    try:
        if args.abort:
            driver.abort(repo, args.tag); print(f"  ✓ aborted absorb/{args.tag}"); return 0
        if args.commit:
            oid = driver.commit(repo, args.tag); print(f"  ✓ committed {oid} on absorb/{args.tag}"); return 0
        if not args.tag:
            print("  usage: nyriel absorb <tag> | --check | --gate | --commit | --abort"); return 2
        res = driver.absorb(repo, args.tag, args.base)
        p = res["parity"]
        print(f"  branch {res['branch']} · re-added {p['re_added']} · divergence {p['divergence']}")
        if res["ready"]:
            print(f"  ✓ clean — review, then `nyriel absorb {args.tag} --commit`")
        else:
            print(f"  conflicts/markers in {len(p['conflicts'])} files — resolve then --commit")
        return 0
    except driver.AbsorbRefused as e:
        print(f"  ✗ {e}"); return 2
```

Then route it where commands dispatch on `action`/`args.command` (mirror the `elif action == "update":` site):

```python
elif args.command == "absorb":
    return cmd_absorb(args)
```

- [ ] **Step 3: Write the dispatch smoke test**

Append to `tests/nyriel_cli/test_absorb_driver.py`:

```python
def test_cli_absorb_gate_runs(capsys):
    from nyriel_cli import main as m
    ns = type("A", (), {"command": "absorb", "tag": None, "base": None,
                        "check": False, "gate": True, "commit": False, "abort": False})()
    rc = m.cmd_absorb(ns)
    assert rc in (0, 1)
    assert "gate" in capsys.readouterr().out.lower()
```

- [ ] **Step 4: Run it + the real CLI**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py::test_cli_absorb_gate_runs -v -o addopts=""`
Then: `.venv/bin/python nyriel absorb --gate`
Expected: PASS; the CLI prints "✓ gate passed (0 stray tokens)".

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/_parser.py nyriel_cli/main.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): nyriel absorb subcommand + dispatch"
```

---

## Task 4: Upstream-tag detection (`detect.py`)

**Files:**
- Create: `nyriel_cli/absorb/detect.py`
- Test: `tests/nyriel_cli/test_absorb_detect.py`

**Interfaces:**
- Consumes: `driver.current_base` (Task 2).
- Produces:
  - `list_upstream_tags(repo) -> list[str]` — release tags from `git ls-remote --tags upstream`.
  - `newer_tags(base: str, tags: list[str]) -> list[str]` — tags strictly newer than base (date-tag order `vYYYY.M.D[.N]`).
  - `latest_absorbable(repo, *, ttl=21600) -> str | None` — cached; newest absorbable tag or None.

- [ ] **Step 1: Write the failing test (pure ordering logic, no network)**

Create `tests/nyriel_cli/test_absorb_detect.py`:

```python
from nyriel_cli.absorb import detect


def test_newer_tags_orders_date_tags():
    tags = ["v2026.6.5", "v2026.6.19", "v2026.5.28", "v2026.6.19"]
    out = detect.newer_tags("v2026.6.5", tags)
    assert out == ["v2026.6.19"]


def test_newer_tags_handles_dotted_patch():
    out = detect.newer_tags("v2026.5.29", ["v2026.5.29.2", "v2026.5.29"])
    assert out == ["v2026.5.29.2"]


def test_no_newer_returns_empty():
    assert detect.newer_tags("v2026.6.19", ["v2026.6.5", "v2026.6.19"]) == []
```

- [ ] **Step 2: Run it, expect FAIL (module missing)**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_detect.py -v -o addopts=""`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `detect.py`**

```python
"""Detect newer absorbable upstream tags (cached)."""
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


def latest_absorbable(repo: str, *, ttl: int = 21600) -> str | None:
    cache = Path(repo) / ".git" / "absorb_check.json"
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
```

(`time.time()` is allowed here — this is product code, not a workflow script.)

- [ ] **Step 4: Run the tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_detect.py -v -o addopts=""`
Expected: PASS (all three ordering tests).

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/absorb/detect.py tests/nyriel_cli/test_absorb_detect.py
git commit -m "feat(absorb): upstream-tag detection with cache"
```

---

## Task 5: Surface the offer on the banner update-check

**Files:**
- Modify: `nyriel_cli/banner.py` (near `check_for_updates()` ~line 265, where the update line is rendered)
- Test: `tests/nyriel_cli/test_absorb_detect.py` (add a render test)

**Interfaces:**
- Consumes: `detect.latest_absorbable`, `driver.install_ok` (Tasks 2/4).
- Produces: `banner.absorb_offer_line(repo) -> str | None` — the one-line offer, or None when not a maintainer install / up to date.

- [ ] **Step 1: Write the failing test**

Append to `tests/nyriel_cli/test_absorb_detect.py`:

```python
def test_absorb_offer_line(monkeypatch):
    from nyriel_cli import banner
    from nyriel_cli.absorb import driver, detect
    monkeypatch.setattr(driver, "install_ok", lambda repo: (True, ""))
    monkeypatch.setattr(detect, "latest_absorbable", lambda repo: "v2026.7.0")
    line = banner.absorb_offer_line("/x")
    assert line and "v2026.7.0" in line and "nyriel absorb" in line


def test_absorb_offer_silent_on_non_git(monkeypatch):
    from nyriel_cli import banner
    from nyriel_cli.absorb import driver
    monkeypatch.setattr(driver, "install_ok", lambda repo: (False, "no upstream"))
    assert banner.absorb_offer_line("/x") is None
```

- [ ] **Step 2: Run it, expect FAIL (function missing)**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_detect.py -k absorb_offer -v -o addopts=""`
Expected: FAIL with `AttributeError: absorb_offer_line`.

- [ ] **Step 3: Implement `absorb_offer_line` in `banner.py`**

```python
def absorb_offer_line(repo: str) -> str | None:
    """Maintainer-only: one-line offer when a newer upstream tag is absorbable."""
    try:
        from nyriel_cli.absorb import driver, detect
        ok, _ = driver.install_ok(repo)
        if not ok:
            return None
        tag = detect.latest_absorbable(repo)
        if not tag:
            return None
        return f"✶ upstream Hermes {tag} available to absorb · run `nyriel absorb {tag}`"
    except Exception:
        return None
```

Then call it where `check_for_updates()`'s result is rendered into the banner (print the line when non-None, on git/source installs).

- [ ] **Step 4: Run the tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_detect.py -k absorb_offer -v -o addopts=""`
Expected: PASS (both render + silent cases).

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/banner.py tests/nyriel_cli/test_absorb_detect.py
git commit -m "feat(absorb): surface absorb-available offer on the banner"
```

---

## Task 6: The agentic skill

**Files:**
- Create: `skills/software-development/absorb-upstream/SKILL.md`

**Interfaces:** none (markdown skill). Follows `skills/software-development/nyriel-brain-skill-authoring/SKILL.md` conventions.

- [ ] **Step 1: Write the skill**

Create `skills/software-development/absorb-upstream/SKILL.md`:

```markdown
---
name: absorb-upstream
description: Use when the operator asks Nyriel to absorb / pull in / rebase onto a new upstream Hermes release into its own core base. Drives `nyriel absorb`, resolves conflicts preserving our genuine divergence, runs tests, and STOPS for human approval before committing. Maintainer/git-install only.
---

# Absorbing an upstream release into Nyriel's core

You are updating your OWN core base. This is a maintainer operation on a git/source
checkout with an `upstream` remote. If `nyriel absorb --check` reports a non-git or
no-upstream install, STOP and tell the operator this only works on the source repo.

## Loop
1. `nyriel absorb --check` — if a newer tag exists, confirm with the operator before proceeding.
2. `nyriel absorb <tag>` — produces branch `absorb/<tag>` + a parity summary. Never touches `main`.
3. If the fidelity GATE fails: STOP. The rebrand map (`nyriel_cli/absorb/rename_map.py`) has
   drifted; this needs a human — do not guess.
4. For each conflict file: read the base/ours/theirs versions. Distinguish OUR genuine divergence
   from the UPSTREAM change. Resolve by taking upstream's structure and RE-APPLYING our change where
   upstream relocated it (e.g. the v2026.6.19 WhatsApp prefix moved into `whatsapp_common.py`).
5. Preserve these genuine-divergence items — never revert them to upstream:
   - brand glyph `✶` (not `⚕`)
   - the Brain Settings overlay (`gateway/overlay/brain_settings.py`)
   - the versioned model name (`gateway/platforms/api_server.py`)
   - the attribution "created by Embrey The Creator"
6. Run the touched-core tests in isolation, e.g.
   `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -o addopts=""`.
7. PRESENT the resolved branch + parity summary to the operator and WAIT for approval.
8. On approval: `nyriel absorb <tag> --commit`. NEVER `git push` unless explicitly told.

## Hard stops
- Gate failure, or any conflict needing core-semantic judgment beyond mechanical re-application → STOP, hand to the operator.
- Parity not READY (conflict markers / stray hermes tokens remain) → `--commit` will refuse; do not force.
- Never auto-push, never absorb a pre-release/RC tag.
```

- [ ] **Step 2: Verify the skill is well-formed**

Run: `.venv/bin/python -c "t=open('skills/software-development/absorb-upstream/SKILL.md').read(); assert t.startswith('---') and 'name: absorb-upstream' in t; print('skill ok')"`
Expected: prints `skill ok`.

- [ ] **Step 3: Commit**

```bash
git add skills/software-development/absorb-upstream/SKILL.md
git commit -m "feat(absorb): repo-local absorb-upstream agentic skill"
```

---

## Self-Review notes

- **Spec coverage:** packaging (Task 1), command + modes (Tasks 2-3), detection (Tasks 4-5), guardrails (Task 2 `absorb`/`install_ok`/`gate`/RC), skill + genuine-divergence list + hard-stops (Task 6), testing incl. the gate-as-CI-regression (Task 2 Step 5). Install-type matrix is enforced by `install_ok`.
- **No auto-push / no main mutation:** `absorb()` only ever branches `absorb/<tag>`; there is no push anywhere in the module.
- **Type consistency:** `parity_report.report()` dict keys (`re_added/removed/divergence/conflicts/stray/ready`) are used identically in `driver.absorb/commit` and `cmd_absorb`.
- **Base-ref discovery** centralizes on `current_base()` (parses `UPSTREAM_BASE.md`); update that doc each absorb (already in the absorb commit checklist).
