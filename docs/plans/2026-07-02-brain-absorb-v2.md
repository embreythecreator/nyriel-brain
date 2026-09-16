# Brain Absorb v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `nyriel absorb` with a machine-checked divergence gate, an auto-verify battery, a real conflict-resolution flow, and automated bookkeeping — human stays the `--commit` gate.

**Architecture:** Extend the existing `nyriel_cli/absorb/` modules in place (approved spec: `docs/specs/2026-07-02-brain-absorb-v2-design.md`). Two new modules (`divergence.py`, `verify.py`), guard/bookkeeping upgrades to `driver.py` and `parity_report.py`, four new CLI flags in `main.py`, and a rewrite of the repo-local `absorb-upstream` skill.

**Tech Stack:** Python 3.11+ stdlib + git plumbing only. pytest for tests (hermetic temp repos).

## Global Constraints

- **No new dependencies.** stdlib + `subprocess` git calls only — matches every existing absorb module.
- **Nothing ever pushes or touches `main`.** `commit()` only moves `refs/heads/absorb/<tag>`. A human runs `--commit`.
- Every refusal raises `driver.AbsorbRefused` with an actionable message.
- Brand glyph is `✶` (never `⚕`). Attribution string is exactly `Embrey The Creator / The Voice`.
- Finalize commit message is exactly `absorb: <tag> (full parity)`.
- Version rule: **minor bump per absorb** (`0.17.0 → 0.18.0`, patch resets to 0).
- Run everything from the repo root `~/Oblivion/nyriel-brain` with `.venv/bin/python`. `rebrand_tree.py` and `parity_report.py` use ambient-cwd git (no `-C`), so process cwd must be the repo — existing constraint, keep it.
- Test commands: `.venv/bin/python -m pytest <file> -q`. Hermetic tests create temp git repos with `git init -q -b main` and set `user.email`/`user.name` locally.

---

### Task 1: Divergence manifest — `divergence.py`

**Files:**
- Create: `nyriel_cli/absorb/divergence.py`
- Test: `tests/nyriel_cli/test_absorb_divergence.py`

**Interfaces:**
- Consumes: nothing (leaf module; git via subprocess).
- Produces: `INVARIANTS: list[tuple[str, str, str | None]]` and `check(repo: str, tree: str) -> list[str]` (empty list == all invariants hold; each violation is a human-readable string starting with the path).

- [ ] **Step 1: Write the failing tests**

Create `tests/nyriel_cli/test_absorb_divergence.py`:

```python
import subprocess
from nyriel_cli.absorb import divergence


def _git(repo, *a, input_bytes=None):
    return subprocess.run(["git", "-C", repo, *a], input=input_bytes,
                          capture_output=True, check=True)


def _mkrepo(tmp_path, files: dict[str, str]) -> str:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    for path, body in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    return str(repo)


GOOD = {
    "gateway/platforms/whatsapp_common.py": 'DEFAULT_REPLY_PREFIX = "✶ *Nyriel Brain*"\n',
    "gateway/overlay/brain_settings.py": "# overlay\n",
    "gateway/platforms/api_server.py": "def _nyriel_version():\n    return 'dev'\n",
    "agent/prompt_builder.py": "# created by Embrey The Creator / The Voice\n",
    "nyriel_cli/default_soul.py": "# created by Embrey The Creator / The Voice\n",
}


def test_all_invariants_hold(tmp_path):
    repo = _mkrepo(tmp_path, GOOD)
    assert divergence.check(repo, "HEAD") == []


def test_missing_file_is_violation(tmp_path):
    files = dict(GOOD)
    del files["gateway/overlay/brain_settings.py"]
    repo = _mkrepo(tmp_path, files)
    v = divergence.check(repo, "HEAD")
    assert len(v) == 1 and "brain_settings.py" in v[0] and "missing" in v[0]


def test_reverted_glyph_is_violation(tmp_path):
    files = dict(GOOD)
    files["gateway/platforms/whatsapp_common.py"] = 'DEFAULT_REPLY_PREFIX = "⚕ *Hermes*"\n'
    repo = _mkrepo(tmp_path, files)
    v = divergence.check(repo, "HEAD")
    assert len(v) == 1 and "whatsapp_common.py" in v[0] and "✶" in v[0]


def test_checks_a_tree_oid_not_just_head(tmp_path):
    repo = _mkrepo(tmp_path, GOOD)
    tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert divergence.check(repo, tree) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_divergence.py -q`
Expected: FAIL — `ImportError: cannot import name 'divergence'`

- [ ] **Step 3: Write the implementation**

Create `nyriel_cli/absorb/divergence.py`:

```python
"""Genuine-divergence manifest — the machine-checked contract for absorb merges.

Each entry pins one deliberate Nyriel divergence from upstream Hermes so a
clean-but-wrong merge (upstream relocating code and the merge silently dropping
our change) can never reach READY. Checked against any TREE OID via git
plumbing — no checkout. Update this manifest ONLY when the operator
deliberately moves or retires a divergence; never weaken it to make a merge
pass (that is the exact failure mode it exists to catch).
"""
from __future__ import annotations

import subprocess

# (path, kind, needle) — kind is "exists" (blob must be present in the tree)
# or "contains" (utf-8 body must contain needle).
INVARIANTS: list[tuple[str, str, str | None]] = [
    ("gateway/platforms/whatsapp_common.py", "contains", "✶"),
    ("gateway/overlay/brain_settings.py", "exists", None),
    ("gateway/platforms/api_server.py", "contains", "_nyriel_version"),
    ("agent/prompt_builder.py", "contains", "Embrey The Creator / The Voice"),
    ("nyriel_cli/default_soul.py", "contains", "Embrey The Creator / The Voice"),
]


def _blob(repo: str, tree: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "-C", repo, "cat-file", "-p", f"{tree}:{path}"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def check(repo: str, tree: str) -> list[str]:
    """Return human-readable violations for `tree`; empty == all invariants hold."""
    violations = []
    for path, kind, needle in INVARIANTS:
        data = _blob(repo, tree, path)
        if data is None:
            violations.append(f"{path}: missing (genuine-divergence file gone)")
        elif kind == "contains" and needle not in data.decode("utf-8", "replace"):
            violations.append(f"{path}: no longer contains {needle!r}")
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_divergence.py -q`
Expected: 4 passed

- [ ] **Step 5: Sanity-check against the real repo**

Run: `.venv/bin/python -c "from nyriel_cli.absorb import divergence; print(divergence.check('.', 'HEAD'))"`
Expected: `[]` (all five invariants hold on today's HEAD; if not, STOP — the manifest entries must be corrected before anything else lands)

- [ ] **Step 6: Commit**

```bash
git add nyriel_cli/absorb/divergence.py tests/nyriel_cli/test_absorb_divergence.py
git commit -m "feat(absorb): genuine-divergence manifest (machine-checked)"
```

---

### Task 2: Wire divergence into the parity report

**Files:**
- Modify: `nyriel_cli/absorb/parity_report.py` (function `report`, lines 56–73; function `main`, lines 76–102)
- Modify: `nyriel_cli/absorb/driver.py:94` (the `parity_report.report(...)` call in `absorb()`)
- Test: `tests/nyriel_cli/test_absorb_parity.py` (extend)

**Interfaces:**
- Consumes: `divergence.check(repo, tree) -> list[str]` (Task 1).
- Produces: `parity_report.report(merged, theirs, head, repo=".") -> dict` — new key `divergence_violations: list[str]`; `ready` is now `not conflicts and not stray and not divergence_violations`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/nyriel_cli/test_absorb_parity.py`:

```python
def test_report_flags_divergence_violation(monkeypatch):
    from nyriel_cli.absorb import divergence
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: [])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    monkeypatch.setattr(divergence, "check",
                        lambda repo, tree: ["x.py: no longer contains '✶'"])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is False
    assert r["divergence_violations"] == ["x.py: no longer contains '✶'"]


def test_report_ready_when_no_violations(monkeypatch):
    from nyriel_cli.absorb import divergence
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: [])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    monkeypatch.setattr(divergence, "check", lambda repo, tree: [])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is True and r["divergence_violations"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_parity.py -q`
Expected: 2 new tests FAIL with `KeyError: 'divergence_violations'`

- [ ] **Step 3: Implement**

In `nyriel_cli/absorb/parity_report.py`, add the import at the top (after `import sys`):

```python
try:
    from . import divergence          # packaged
except ImportError:                    # direct-script fallback
    import divergence  # noqa: F401
```

Replace the body of `report()` (keep its docstring, extend the Keys line to mention `divergence_violations`):

```python
def report(merged: str, theirs: str, head: str, repo: str = ".") -> dict:
    """Classify the merged tree; structured result for `nyriel absorb`.

    Keys: re_added / removed (vs prior HEAD), divergence (merged != THEIRS),
    conflicts (files w/ markers), stray (upstream tokens outside carve-outs),
    divergence_violations (genuine-divergence manifest breaches — see
    divergence.py), ready (commit gate: no conflicts, no stray, no violations).
    """
    merged_names, head_names = names(merged), names(head)
    conflicts = grep_conflict_markers(merged)
    stray = grep_stray(merged)
    violations = divergence.check(repo, merged)
    return {
        "re_added": len(merged_names - head_names),
        "removed": len(head_names - merged_names),
        "divergence": len(diff_names(merged, theirs)),
        "conflicts": conflicts,
        "stray": stray,
        "divergence_violations": violations,
        "ready": not conflicts and not stray and not violations,
    }
```

In `main()`, after the stray-token print block and before the `STATUS` print, add:

```python
    viol = r["divergence_violations"]
    if viol:
        status_ok = False
        print(f"   !! GENUINE-DIVERGENCE VIOLATIONS in {len(viol)} invariants:")
        for v in viol:
            print(f"        {v}")
    else:
        print("   genuine-divergence manifest: intact")
```

In `nyriel_cli/absorb/driver.py:94`, pass the repo through:

```python
    rep = parity_report.report(merged, theirs_tree, "HEAD", repo=repo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_parity.py tests/nyriel_cli/test_absorb_driver.py -q`
Expected: all pass (the pre-existing `test_report_flags_conflict_markers` still passes — its monkeypatching leaves `divergence.check` running against the real repo tree `"m"`, which returns 5 "missing" violations, but `ready` was already False from conflicts; if it fails instead, add the same `divergence.check` monkeypatch to it)

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/absorb/parity_report.py nyriel_cli/absorb/driver.py tests/nyriel_cli/test_absorb_parity.py
git commit -m "feat(absorb): parity report enforces the divergence manifest"
```

---

### Task 3: Driver state hygiene — helpers, `oursHead`, HEAD pre-check, fixed `abort()`

**Files:**
- Modify: `nyriel_cli/absorb/driver.py` (imports; `absorb()` lines 66–98; `abort()` lines 114–116; new helpers after `_git`)
- Test: `tests/nyriel_cli/test_absorb_driver.py` (extend)

**Interfaces:**
- Consumes: `divergence.check` (Task 1).
- Produces (later tasks rely on these exact names):
  - `_cfg_get(repo: str, key: str) -> str | None`
  - `clear_state(repo: str) -> None`
  - `state(repo: str) -> dict | None` — keys `tag`, `merged`, `ours_head`, `verify_ok: bool`, `verify_summary: str`; `None` when no absorb in flight
  - `_current_branch(repo: str) -> str` (empty string when detached)
  - `absorb()` additionally records git config `absorb.oursHead`
  - `abort(repo: str, tag: str | None = None) -> None` — resolves tag from state, clears state, prunes worktrees

- [ ] **Step 1: Write the failing tests**

Append to `tests/nyriel_cli/test_absorb_driver.py`:

```python
def _mkrepo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    return str(repo)


def test_state_none_when_not_in_flight(tmp_path):
    repo = _mkrepo(tmp_path)
    assert driver.state(repo) is None


def test_state_roundtrip_and_clear(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", "deadbeef")
    _git(repo, "config", "--local", "absorb.oursHead", "cafebabe")
    _git(repo, "config", "--local", "absorb.verifyOk", "true")
    st = driver.state(repo)
    assert st == {"tag": "v2026.7.0", "merged": "deadbeef",
                  "ours_head": "cafebabe", "verify_ok": True,
                  "verify_summary": ""}
    driver.clear_state(repo)
    assert driver.state(repo) is None


def test_abort_clears_state_and_branch(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", "deadbeef")
    driver.abort(repo)                      # tag resolved from state
    assert driver.state(repo) is None
    r = subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify",
                        "refs/heads/absorb/v2026.7.0"], capture_output=True)
    assert r.returncode != 0


def test_abort_refuses_without_state_or_tag(tmp_path):
    repo = _mkrepo(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.abort(repo)


def test_absorb_refuses_when_head_divergence_drifted(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    (tmp_path / "r" / "UPSTREAM_BASE.md").write_text("| Upstream tag | `v2026.6.19` |\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base doc")
    _git(repo, "tag", "v2026.7.0")
    monkeypatch.setattr(driver, "gate", lambda r, b: (True, ""))
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused")
    with pytest.raises(driver.AbsorbRefused, match="divergence manifest drifted"):
        driver.absorb(repo, "v2026.7.0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: new tests FAIL (`AttributeError: module ... has no attribute 'state'`, etc.)

- [ ] **Step 3: Implement**

In `nyriel_cli/absorb/driver.py`:

Change the import line 16 to:

```python
from . import rebrand_tree, parity_report, divergence
```

Add after `_git()` (line 33):

```python
_STATE_KEYS = ("absorb.lastTag", "absorb.lastMerged", "absorb.oursHead",
               "absorb.verifyOk", "absorb.verifySummary")


def _cfg_get(repo: str, key: str) -> str | None:
    r = _git(repo, "config", "--local", "--get", key, check=False)
    return r.stdout.strip() or None


def _current_branch(repo: str) -> str:
    return _git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()


def clear_state(repo: str) -> None:
    """Drop every absorb.* stash — abort/commit both end the in-flight absorb."""
    for k in _STATE_KEYS:
        _git(repo, "config", "--local", "--unset-all", k, check=False)


def state(repo: str) -> dict | None:
    """The in-flight absorb, or None. Single source of truth for --continue/--verify/--status."""
    tag = _cfg_get(repo, "absorb.lastTag")
    if not tag:
        return None
    return {"tag": tag,
            "merged": _cfg_get(repo, "absorb.lastMerged"),
            "ours_head": _cfg_get(repo, "absorb.oursHead"),
            "verify_ok": _cfg_get(repo, "absorb.verifyOk") == "true",
            "verify_summary": _cfg_get(repo, "absorb.verifySummary") or ""}
```

In `absorb()`, after the gate check (line 78) insert the HEAD pre-check:

```python
    drift = divergence.check(repo, "HEAD")
    if drift:
        raise AbsorbRefused(
            "divergence manifest drifted on HEAD — update "
            "nyriel_cli/absorb/divergence.py first:\n" + "\n".join(drift))
```

In `absorb()`, replace the two `git config` stash lines (96–97) with:

```python
    _git(repo, "config", "--local", "absorb.lastTag", tag)
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    _git(repo, "config", "--local", "absorb.oursHead",
         _git(repo, "rev-parse", "HEAD").stdout.strip())
```

Replace `abort()` entirely:

```python
def abort(repo: str, tag: str | None = None) -> None:
    """One-step rollback: delete the absorb branch, clear all stashed state."""
    st = state(repo)
    tag = tag or (st["tag"] if st else None)
    if not tag:
        raise AbsorbRefused("no absorb in flight and no tag given — nothing to abort")
    branch = f"absorb/{tag}"
    if _current_branch(repo) == branch:
        # step off the branch before deleting it; fall back to detached ours-head
        if _git(repo, "checkout", "-q", "-f", "main", check=False).returncode != 0:
            _git(repo, "checkout", "-q", "-f",
                 (st or {}).get("ours_head") or "HEAD")
    _git(repo, "branch", "-D", branch, check=False)
    _git(repo, "worktree", "prune", check=False)
    clear_state(repo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: all pass (including the pre-existing 4)

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/absorb/driver.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): state helpers, oursHead stash, HEAD divergence pre-check, clean abort"
```

---

### Task 4: Verification battery — `verify.py`

**Files:**
- Create: `nyriel_cli/absorb/verify.py`
- Test: `tests/nyriel_cli/test_absorb_verify.py`

**Interfaces:**
- Consumes: nothing from other tasks (leaf module).
- Produces: `verify.TARGETED_TESTS: list[str]` and `verify.run(repo: str, merged: str, head: str = "HEAD") -> dict` with keys `compile_ok: bool`, `compile_errors: str`, `tests_ok: bool`, `tests_summary: str`, `ok: bool` (`compile_ok and tests_ok`). Always cleans up its worktree.

- [ ] **Step 1: Write the failing tests**

Create `tests/nyriel_cli/test_absorb_verify.py`:

```python
import subprocess
from nyriel_cli.absorb import verify


def _git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                          text=True, check=True)


def _mkrepo(tmp_path, files: dict[str, str]) -> str:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    for path, body in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    return str(repo)


def _commit_tree(repo, files: dict[str, str]) -> str:
    """Commit files on a throwaway branch; return the new tree oid."""
    _git(repo, "checkout", "-q", "-b", "tmp")
    for path, body in files.items():
        with open(f"{repo}/{path}", "w", encoding="utf-8") as f:
            f.write(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    return tree


def test_green_tree_passes(tmp_path):
    repo = _mkrepo(tmp_path, {
        "mod.py": "X = 1\n",
        "tests/nyriel_cli/test_absorb_driver.py": "def test_ok():\n    assert True\n",
    })
    merged = _commit_tree(repo, {"mod.py": "X = 2\n"})
    res = verify.run(repo, merged)
    assert res["compile_ok"] and res["tests_ok"] and res["ok"]


def test_syntax_error_fails_compile(tmp_path):
    repo = _mkrepo(tmp_path, {"mod.py": "X = 1\n"})
    merged = _commit_tree(repo, {"mod.py": "def broken(:\n"})
    res = verify.run(repo, merged)
    assert res["compile_ok"] is False and res["ok"] is False


def test_failing_targeted_test_fails(tmp_path):
    repo = _mkrepo(tmp_path, {
        "tests/nyriel_cli/test_absorb_driver.py": "def test_ok():\n    assert True\n",
    })
    merged = _commit_tree(
        repo, {"tests/nyriel_cli/test_absorb_driver.py":
               "def test_bad():\n    assert False\n"})
    res = verify.run(repo, merged)
    assert res["tests_ok"] is False and res["ok"] is False


def test_worktree_always_cleaned(tmp_path):
    repo = _mkrepo(tmp_path, {"mod.py": "X = 1\n"})
    merged = _commit_tree(repo, {"mod.py": "def broken(:\n"})
    verify.run(repo, merged)
    out = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(out) == 1  # only the main worktree remains
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_verify.py -q`
Expected: FAIL — `ImportError: cannot import name 'verify'`

- [ ] **Step 3: Write the implementation**

Create `nyriel_cli/absorb/verify.py`:

```python
"""Post-merge verification battery for `nyriel absorb`.

Materializes the merged tree in a throwaway git worktree (never the operator's
working tree), byte-compiles the changed .py files, and runs the targeted
hermetic test set with the merged code first on sys.path. The battery gates
`--commit` (see driver.commit); `--skip-verify` is the explicit human escape.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# The hermetic set that ran green during the v2026.6.19 absorb, plus the absorb
# suite itself. Paths missing from the merged tree are skipped (synthetic test
# repos, upstream test moves) — the summary says how many actually ran.
TARGETED_TESTS = [
    "tests/nyriel_cli/test_absorb_driver.py",
    "tests/nyriel_cli/test_absorb_detect.py",
    "tests/nyriel_cli/test_absorb_parity.py",
    "tests/nyriel_cli/test_absorb_divergence.py",
    "tests/nyriel_cli/test_absorb_verify.py",
    "tests/nyriel_cli/test_banner.py",
    "tests/nyriel_cli/test_build_info.py",
    "tests/nyriel_cli/test_config.py",
    "tests/gateway/test_api_server.py",
    "tests/gateway/test_brain_settings_overlay.py",
    "tests/gateway/test_status.py",
]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


def changed_py(repo: str, merged: str, head: str = "HEAD") -> list[str]:
    out = _git(repo, "diff", "--name-only", head, merged).stdout.splitlines()
    return [f for f in out if f.endswith(".py")]


def run(repo: str, merged: str, head: str = "HEAD") -> dict:
    """Run the battery against a merged TREE oid. Worktree is always removed."""
    commit = _git(repo, "commit-tree", merged, "-m",
                  "absorb-verify (throwaway)").stdout.strip()
    wt = os.path.join(tempfile.mkdtemp(prefix="absorb-verify-"), "tree")
    _git(repo, "worktree", "add", "-q", "--detach", wt, commit)
    try:
        py = [f for f in changed_py(repo, merged, head)
              if os.path.exists(os.path.join(wt, f))]
        compile_ok, compile_errors = True, ""
        if py:
            r = subprocess.run([sys.executable, "-m", "compileall", "-q", *py],
                               cwd=wt, capture_output=True, text=True)
            compile_ok = r.returncode == 0
            compile_errors = (r.stdout + r.stderr).strip()

        targets = [t for t in TARGETED_TESTS
                   if os.path.exists(os.path.join(wt, t))]
        tests_ok, tests_summary = True, "no targeted tests present in merged tree"
        if targets:
            env = dict(os.environ)
            env["PYTHONPATH"] = wt + os.pathsep + env.get("PYTHONPATH", "")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q",
                                "-p", "no:cacheprovider", *targets],
                               cwd=wt, capture_output=True, text=True, env=env)
            tests_ok = r.returncode == 0
            lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
            tests_summary = (f"{len(targets)} target files · "
                             + (lines[-1] if lines else f"exit {r.returncode}"))
        return {"compile_ok": compile_ok, "compile_errors": compile_errors,
                "tests_ok": tests_ok, "tests_summary": tests_summary,
                "ok": compile_ok and tests_ok}
    finally:
        _git(repo, "worktree", "remove", "--force", wt, check=False)
        _git(repo, "worktree", "prune", check=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_verify.py -q`
Expected: 4 passed (this runs pytest-in-pytest against tiny synthetic repos; a few seconds is normal)

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/absorb/verify.py tests/nyriel_cli/test_absorb_verify.py
git commit -m "feat(absorb): post-merge verification battery (worktree + compileall + targeted tests)"
```

---

### Task 5: Auto-verify in `absorb()` + `materialize()` / `verify_current()` for conflict UX

**Files:**
- Modify: `nyriel_cli/absorb/driver.py` (import; end of `absorb()`; new functions after `absorb()`)
- Test: `tests/nyriel_cli/test_absorb_driver.py` (extend)

**Interfaces:**
- Consumes: `verify.run` (Task 4), `state`/`_cfg_get`/`_current_branch` (Task 3), `parity_report.report(..., repo=...)` (Task 2).
- Produces:
  - `_store_verify(repo: str, res: dict) -> None` — stashes `absorb.verifyOk` (`"true"`/`"false"`) and `absorb.verifySummary`
  - `absorb()` return dict gains `"verify": dict` (the battery result), auto-run
  - `materialize(repo: str) -> str` — checks out `absorb/<tag>` and materializes the merged tree; returns branch name; refuses on dirty tree or no state
  - `verify_current(repo: str) -> dict` — snapshots the working tree when materialized, else re-verifies the stashed tree; returns `{"parity": dict, "verify": dict, "merged": str}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/nyriel_cli/test_absorb_driver.py`:

```python
def test_materialize_refuses_without_state(tmp_path):
    repo = _mkrepo(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.materialize(repo)


def test_materialize_refuses_dirty_tree(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    (tmp_path / "r" / "a.txt").write_text("dirty\n")
    with pytest.raises(driver.AbsorbRefused, match="dirty"):
        driver.materialize(repo)


def test_materialize_checks_out_merged_tree(tmp_path):
    repo = _mkrepo(tmp_path)
    # build a "merged" tree with a conflict-marker file, stash it as state
    (tmp_path / "r" / "a.txt").write_text("<<<<<<< ours\nX\n=======\nY\n>>>>>>> theirs\n")
    _git(repo, "add", "-A")
    merged = subprocess.run(["git", "-C", repo, "write-tree"],
                            capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "reset", "-q", "--hard")   # back to clean HEAD (index + working tree)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    _git(repo, "config", "--local", "absorb.oursHead", head)
    branch = driver.materialize(repo)
    assert branch == "absorb/v2026.7.0"
    assert "<<<<<<<" in (tmp_path / "r" / "a.txt").read_text()


def test_verify_current_snapshots_resolved_tree(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "checkout", "-q", "absorb/v2026.7.0")
    (tmp_path / "r" / "a.txt").write_text("resolved\n")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", "0" * 40)
    _git(repo, "config", "--local", "absorb.oursHead", head)
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused-theirs")
    fake_rep = {"ready": True, "conflicts": [], "stray": [],
                "divergence_violations": [], "re_added": 0, "removed": 0,
                "divergence": 0}
    monkeypatch.setattr(driver.parity_report, "report",
                        lambda m, t, h, repo=".": fake_rep)
    monkeypatch.setattr(driver.verify, "run",
                        lambda repo, merged, head="HEAD":
                        {"ok": True, "compile_ok": True, "compile_errors": "",
                         "tests_ok": True, "tests_summary": "stub"})
    res = driver.verify_current(repo)
    assert res["merged"] != "0" * 40                       # snapshot happened
    assert driver.state(repo)["merged"] == res["merged"]   # state updated
    assert driver.state(repo)["verify_ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: new tests FAIL (`AttributeError: ... no attribute 'materialize'`, etc.)

- [ ] **Step 3: Implement**

In `nyriel_cli/absorb/driver.py`:

Change the import to:

```python
from . import rebrand_tree, parity_report, divergence, verify
```

Add after `state()`:

```python
def _store_verify(repo: str, res: dict) -> None:
    _git(repo, "config", "--local", "absorb.verifyOk",
         "true" if res["ok"] else "false")
    summary = res["tests_summary"] if res["compile_ok"] else \
        f"compileall failed: {res['compile_errors'][:200]}"
    _git(repo, "config", "--local", "absorb.verifySummary", summary)
```

At the end of `absorb()`, replace the `return` statement with:

```python
    vres = verify.run(repo, merged)
    _store_verify(repo, vres)
    return {"branch": branch, "merged_tree": merged, "parity": rep,
            "verify": vres, "ready": rep["ready"]}
```

Add after `absorb()`:

```python
def materialize(repo: str) -> str:
    """--continue: put the merged tree (conflict markers and all) into the
    working tree on the absorb branch so conflicts can be edited in place."""
    st = state(repo)
    if not st:
        raise AbsorbRefused("no absorb in flight — run `nyriel absorb <tag>` first")
    if _git(repo, "status", "--porcelain", check=False).stdout.strip():
        raise AbsorbRefused("working tree is dirty — commit or stash before --continue")
    branch = f"absorb/{st['tag']}"
    _git(repo, "checkout", "-q", branch)
    _git(repo, "read-tree", "--reset", "-u", st["merged"])
    return branch


def verify_current(repo: str) -> dict:
    """--verify: snapshot the working tree as the new merged tree when
    materialized (on the absorb branch), then re-run parity + divergence +
    the verification battery. Off-branch it re-verifies the stashed tree."""
    st = state(repo)
    if not st:
        raise AbsorbRefused("no absorb in flight — run `nyriel absorb <tag>` first")
    if _current_branch(repo) == f"absorb/{st['tag']}":
        _git(repo, "add", "-A")
        merged = _git(repo, "write-tree").stdout.strip()
        _git(repo, "config", "--local", "absorb.lastMerged", merged)
    else:
        merged = st["merged"]
    theirs = rebrand_tree.build_rebranded_tree(st["tag"], attribution=True)
    rep = parity_report.report(merged, theirs, st["ours_head"], repo=repo)
    vres = verify.run(repo, merged, head=st["ours_head"])
    _store_verify(repo, vres)
    return {"parity": rep, "verify": vres, "merged": merged}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add nyriel_cli/absorb/driver.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): auto-verify after merge + materialize/verify conflict flow"
```

---

### Task 6: `commit()` guards + automated bookkeeping

**Files:**
- Modify: `nyriel_cli/absorb/driver.py` (replace `commit()`, lines now after `verify_current()`; add `_bookkeep_tree()` and `_changelog_insert()` helpers; add `import datetime`, `import os`, `import tempfile` to the module imports)
- Test: `tests/nyriel_cli/test_absorb_driver.py` (extend)

**Interfaces:**
- Consumes: `state`/`clear_state`/`_cfg_get`/`_current_branch` (Task 3), `parity_report.report` (Task 2), `current_base` (existing).
- Produces: `commit(repo: str, tag: str | None = None, skip_verify: bool = False) -> str` (the finalize commit oid). Refuses on: no state, tag mismatch, HEAD moved, parity not READY, verify not green (unless `skip_verify`). Folds bookkeeping (version bump / UPSTREAM_BASE.md / CHANGELOG.md) into the finalized tree, parents onto `oursHead`, moves the branch ref, clears state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/nyriel_cli/test_absorb_driver.py`:

```python
BOOK_FILES = {
    "pyproject.toml": '[project]\nname = "nyriel-brain"\nversion = "0.17.0"\n',
    "UPSTREAM_BASE.md": (
        "| | value |\n|---|---|\n"
        "| Current tree corresponds to | **Hermes v0.17.0** |\n"
        "| Upstream tag | `v2026.6.19` |\n"
        "| Upstream commit | `2bd1977d8` |\n"
        "| Our version (independent line) | `0.17.0` (pyproject.toml — source of truth) |\n"),
    "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n### Added\n- thing\n",
}


def _mkrepo_book(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    for path, body in BOOK_FILES.items():
        (repo / path).write_text(body, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    _git(str(repo), "tag", "v2026.7.0")
    return str(repo)


def _arm_state(repo, verify_ok="true"):
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", tree)
    _git(repo, "config", "--local", "absorb.oursHead", head)
    _git(repo, "config", "--local", "absorb.verifyOk", verify_ok)
    return head, tree


READY = {"ready": True, "conflicts": [], "stray": [], "divergence_violations": [],
         "re_added": 3, "removed": 1, "divergence": 4}


def _stub_parity(monkeypatch, rep=READY):
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused-theirs")
    monkeypatch.setattr(driver.parity_report, "report",
                        lambda m, t, h, repo=".": rep)


def test_commit_refuses_without_state(tmp_path):
    repo = _mkrepo_book(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.commit(repo)


def test_commit_refuses_tag_mismatch(tmp_path):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    with pytest.raises(driver.AbsorbRefused, match="tag mismatch"):
        driver.commit(repo, "v2026.8.0")


def test_commit_refuses_moved_head(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    (tmp_path / "r" / "new.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "moved")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="HEAD moved"):
        driver.commit(repo)


def test_commit_refuses_red_verify_unless_skipped(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo, verify_ok="false")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="verify battery"):
        driver.commit(repo)
    oid = driver.commit(repo, skip_verify=True)
    assert len(oid) == 40


def test_commit_bookkeeping_and_state_clear(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    _stub_parity(monkeypatch)
    oid = driver.commit(repo)
    def show(path):
        return subprocess.run(["git", "-C", repo, "show", f"{oid}:{path}"],
                              capture_output=True, text=True, check=True).stdout
    assert 'version = "0.18.0"' in show("pyproject.toml")
    ub = show("UPSTREAM_BASE.md")
    assert "| Upstream tag | `v2026.7.0` |" in ub
    assert "**Hermes v0.18.0**" in ub and "`0.18.0` (pyproject.toml" in ub
    ch = show("CHANGELOG.md")
    assert "## [0.18.0]" in ch and "`v2026.6.19` → `v2026.7.0`" in ch
    assert "re-added 3, removed 1, divergence 4" in ch
    assert ch.index("[Unreleased]") < ch.index("[0.18.0]")
    msg = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%s",
                          "absorb/v2026.7.0"], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert msg == "absorb: v2026.7.0 (full parity)"
    assert driver.state(repo) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: new tests FAIL (old `commit()` crashes on missing config / lacks kwargs)

- [ ] **Step 3: Implement**

In `nyriel_cli/absorb/driver.py`, add three imports to the existing block (`re` and `subprocess` are already there — do not duplicate):

```python
import datetime
import os
import tempfile
```

Add helpers before `commit()`:

```python
def _read_blob(repo: str, tree: str, path: str) -> str:
    return _git(repo, "cat-file", "-p", f"{tree}:{path}").stdout


def _hash_blob(repo: str, text: str) -> str:
    r = subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"],
                       input=text.encode(), capture_output=True, check=True)
    return r.stdout.decode().strip()


def _changelog_insert(ch: str, entry: str) -> str:
    """Insert a new release section after [Unreleased], before the next release."""
    unrel = ch.find("## [Unreleased]")
    if unrel == -1:
        return ch.rstrip() + "\n" + entry
    nxt = ch.find("\n## [", unrel + 1)
    return (ch.rstrip() + "\n" + entry) if nxt == -1 else ch[:nxt] + "\n" + entry.rstrip() + "\n" + ch[nxt + 1:]


def _bookkeep_tree(repo: str, merged: str, tag: str, rep: dict) -> str:
    """Fold version bump + UPSTREAM_BASE.md row + CHANGELOG entry into the tree."""
    old_base = current_base(repo)
    py = _read_blob(repo, merged, "pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', py, re.M)
    if not m:
        raise AbsorbRefused("could not find the version line in pyproject.toml")
    newver = f"{m.group(1)}.{int(m.group(2)) + 1}.0"       # minor bump per absorb
    py = py[:m.start()] + f'version = "{newver}"' + py[m.end():]

    up_commit = _git(repo, "rev-parse", "--short",
                     f"{tag}^{{commit}}").stdout.strip()
    ub = _read_blob(repo, merged, "UPSTREAM_BASE.md")
    ub = re.sub(r"\| Upstream tag \| `[^`]+` \|",
                f"| Upstream tag | `{tag}` |", ub)
    ub = re.sub(r"\| Upstream commit \| `[^`]+` \|",
                f"| Upstream commit | `{up_commit}` |", ub)
    ub = re.sub(r"\| Current tree corresponds to \| \*\*Hermes v[0-9.]+\*\* \|",
                f"| Current tree corresponds to | **Hermes v{newver}** |", ub)
    ub = re.sub(r"\| Our version \(independent line\) \| `[0-9.]+`",
                f"| Our version (independent line) | `{newver}`", ub)

    today = datetime.date.today().isoformat()
    entry = (f"## [{newver}] — {today}\n\n### Absorbed\n"
             f"- **hermes-agent `{old_base}` → `{tag}`** (full parity): "
             f"re-added {rep['re_added']}, removed {rep['removed']}, "
             f"divergence {rep['divergence']} files.\n")
    ch = _changelog_insert(_read_blob(repo, merged, "CHANGELOG.md"), entry)

    blobs = {"pyproject.toml": _hash_blob(repo, py),
             "UPSTREAM_BASE.md": _hash_blob(repo, ub),
             "CHANGELOG.md": _hash_blob(repo, ch)}
    with tempfile.NamedTemporaryFile(prefix="absorb-idx-", delete=False) as f:
        idx = f.name
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        subprocess.run(["git", "-C", repo, "read-tree", merged],
                       env=env, check=True)
        info = "".join(f"100644 {oid}\t{path}\n" for path, oid in blobs.items())
        subprocess.run(["git", "-C", repo, "update-index", "--index-info"],
                       env=env, input=info.encode(), check=True)
        return subprocess.run(["git", "-C", repo, "write-tree"], env=env,
                              capture_output=True, check=True).stdout.decode().strip()
    finally:
        os.unlink(idx)
```

Replace `commit()` entirely:

```python
def commit(repo: str, tag: str | None = None, skip_verify: bool = False) -> str:
    """Finalize the in-flight absorb — every guardrail re-checked here."""
    st = state(repo)
    if not st:
        raise AbsorbRefused("no absorb in flight — nothing to commit")
    if tag and tag != st["tag"]:
        raise AbsorbRefused(f"tag mismatch: in-flight absorb is {st['tag']}, got {tag}")
    tag = st["tag"]
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != st["ours_head"]:
        raise AbsorbRefused("HEAD moved since the absorb was prepared — "
                            "re-run `nyriel absorb` or --abort first")
    merged = st["merged"]
    theirs = rebrand_tree.build_rebranded_tree(tag, attribution=True)
    rep = parity_report.report(merged, theirs, st["ours_head"], repo=repo)
    if not rep["ready"]:
        raise AbsorbRefused("parity not READY (conflicts, stray tokens, or "
                            "divergence violations remain) — run --verify for detail")
    if not skip_verify and not st["verify_ok"]:
        raise AbsorbRefused("verify battery not green — fix and re-run "
                            "`nyriel absorb --verify`, or pass --skip-verify (human call)")
    final_tree = _bookkeep_tree(repo, merged, tag, rep)
    branch = f"absorb/{tag}"
    oid = _git(repo, "commit-tree", final_tree, "-p", st["ours_head"], "-m",
               f"absorb: {tag} (full parity)").stdout.strip()
    _git(repo, "update-ref", f"refs/heads/{branch}", oid)
    if _current_branch(repo) == branch:
        _git(repo, "reset", "-q", "--hard", oid)   # sync a materialized worktree
    clear_state(repo)
    return oid
```

Note: `re` and `subprocess` were already imported; keep one import block, no duplicates.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole absorb suite**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py tests/nyriel_cli/test_absorb_detect.py tests/nyriel_cli/test_absorb_parity.py tests/nyriel_cli/test_absorb_divergence.py tests/nyriel_cli/test_absorb_verify.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add nyriel_cli/absorb/driver.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): commit guards (tag/HEAD/verify) + automated bookkeeping"
```

---

### Task 7: CLI surface — `--continue` / `--verify` / `--status` / `--skip-verify`

**Files:**
- Modify: `nyriel_cli/main.py` — `cmd_absorb` (line ~11569) and the absorb parser block (lines ~11834–11852)
- Test: `tests/nyriel_cli/test_absorb_driver.py` (extend the CLI test; fix the existing namespace)

**Interfaces:**
- Consumes: `driver.state/materialize/verify_current/commit(tag, skip_verify)/abort(tag=None)` (Tasks 3, 5, 6).
- Produces: argparse args `cont`, `verify`, `status`, `skip_verify` on the absorb namespace (note: `--continue` maps to `dest="cont"` — `continue` is a Python keyword).

- [ ] **Step 1: Write the failing tests**

In `tests/nyriel_cli/test_absorb_driver.py`, first UPDATE the existing `test_cli_absorb_gate_runs` namespace to carry the new attributes (it will otherwise crash with `AttributeError` once `cmd_absorb` reads them):

```python
def _ns(**kw):
    base = dict(command="absorb", tag=None, base=None, check=False, gate=False,
                commit=False, abort=False, cont=False, verify=False,
                status=False, skip_verify=False)
    base.update(kw)
    return type("A", (), base)()


def test_cli_absorb_gate_runs(capsys):
    from nyriel_cli import main as m
    rc = m.cmd_absorb(_ns(gate=True))
    assert rc in (0, 1)
    assert "gate" in capsys.readouterr().out.lower()
```

Then append:

```python
def test_cli_status_no_absorb(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(status=True))
    assert rc == 0
    assert "no absorb in flight" in capsys.readouterr().out


def test_cli_status_in_flight(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.verifyOk", "true")
    _git(repo, "config", "--local", "absorb.verifySummary", "11 target files · 295 passed")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(status=True))
    out = capsys.readouterr().out
    assert rc == 0 and "v2026.7.0" in out and "green" in out


def test_cli_continue_reports_refusal(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(cont=True))
    assert rc == 2
    assert "no absorb in flight" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: new CLI tests FAIL (`AttributeError: 'A' object has no attribute 'status'` inside `cmd_absorb`, or wrong output)

- [ ] **Step 3: Implement**

In `nyriel_cli/main.py`, parser block (after the `--abort` line ~11851), add:

```python
    absorb_parser.add_argument("--continue", dest="cont", action="store_true",
                               help="materialize the in-flight merge into the working tree to resolve conflicts")
    absorb_parser.add_argument("--verify", action="store_true",
                               help="snapshot resolved files (when materialized) and re-run parity + the verify battery")
    absorb_parser.add_argument("--status", action="store_true",
                               help="show the in-flight absorb (tag, verify state)")
    absorb_parser.add_argument("--skip-verify", dest="skip_verify", action="store_true",
                               help="with --commit: finalize despite a red verify battery (human call)")
```

In `cmd_absorb`, inside the existing `try:` block, add BEFORE the `if args.abort:` line:

```python
        if args.status:
            st = driver.state(repo)
            if not st:
                print("  ✓ no absorb in flight")
                return 0
            light = "green" if st["verify_ok"] else "RED"
            print(f"  absorb/{st['tag']} in flight · verify {light}"
                  + (f" · {st['verify_summary']}" if st["verify_summary"] else ""))
            print(f"  next: `nyriel absorb --continue` to resolve, "
                  f"`nyriel absorb --verify` to re-check, "
                  f"`nyriel absorb --commit` to finalize")
            return 0
        if args.cont:
            branch = driver.materialize(repo)
            print(f"  ✓ merge materialized on {branch} — resolve conflicts, "
                  f"then `nyriel absorb --verify`")
            return 0
        if args.verify:
            res = driver.verify_current(repo)
            p, v = res["parity"], res["verify"]
            print(f"  parity: {'READY' if p['ready'] else 'NEEDS RESOLUTION'} · "
                  f"conflicts {len(p['conflicts'])} · stray {len(p['stray'])} · "
                  f"divergence violations {len(p['divergence_violations'])}")
            print(f"  verify: {'green' if v['ok'] else 'RED'} · {v['tests_summary']}")
            return 0 if (p["ready"] and v["ok"]) else 1
```

Also update the two existing dispatch lines to the new signatures:

```python
        if args.abort:
            driver.abort(repo, args.tag)
            print(f"  ✓ aborted{' absorb/' + args.tag if args.tag else ' the in-flight absorb'}")
            return 0
        if args.commit:
            oid = driver.commit(repo, args.tag, skip_verify=args.skip_verify)
            print(f"  ✓ committed {oid} — merge to main when ready (human step)")
            return 0
```

And update the bare-usage line to:

```python
            print("  usage: nyriel absorb <tag> | --check | --gate | --status | "
                  "--continue | --verify | --commit | --abort")
```

Finally, in the fresh-absorb result print (after `res = driver.absorb(...)`), add the verify line after the branch print:

```python
        v = res["verify"]
        print(f"  verify: {'green' if v['ok'] else 'RED'} · {v['tests_summary']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py -q`
Expected: all pass

- [ ] **Step 5: Smoke the real CLI**

Run: `.venv/bin/python -m nyriel_cli.main absorb --help` — expect the four new flags listed.
Run: `.venv/bin/python -m nyriel_cli.main absorb --status` — expect `✓ no absorb in flight`.

- [ ] **Step 6: Commit**

```bash
git add nyriel_cli/main.py tests/nyriel_cli/test_absorb_driver.py
git commit -m "feat(absorb): --continue/--verify/--status/--skip-verify CLI surface"
```

---

### Task 8: Skill rewrite, docs, full-suite gate

**Files:**
- Modify: `skills/software-development/absorb-upstream/SKILL.md` (full rewrite, v2.0.0)
- Modify: `docs/absorb-harness.md` (flag table / flow section — add the four new flags and the new loop)
- Modify: `UPSTREAM_BASE.md` (recipe section — mention `--continue`/`--verify`; the version-bump/table/changelog steps are now automatic in `--commit`)

**Interfaces:**
- Consumes: the full CLI surface (Task 7).
- Produces: operator/agent documentation only — no code.

- [ ] **Step 1: Rewrite the skill**

Replace the body of `skills/software-development/absorb-upstream/SKILL.md` with (keep the YAML frontmatter, bump `version: 2.0.0`, keep name/author/license/platforms/metadata as-is):

```markdown
# Absorbing an upstream release into Nyriel's core

You are updating your OWN core base. Maintainer operation on a git/source checkout
with an `upstream` remote. If `nyriel absorb --check` reports a non-git or
no-upstream install, STOP: pip/docker installs use `nyriel update` instead.

## Loop

1. `nyriel absorb --status` — resume any in-flight absorb before starting a new one.
2. `nyriel absorb --check` — if a newer tag exists, confirm with the operator.
3. `nyriel absorb <tag>` — builds `absorb/<tag>`, prints parity AND an automatic
   verify battery result (compileall + targeted hermetic tests on the merged tree).
   Dry: it never touches `main`, never commits.
4. If the fidelity GATE fails or a "divergence manifest drifted" refusal appears:
   STOP — `rename_map.py` or `divergence.py` needs a human decision. Do not guess.
5. If parity shows conflicts: `nyriel absorb --continue` materializes the merge on
   the branch. Resolve each file by taking upstream's structure and RE-APPLYING our
   divergence where the code moved (canonical example: v2026.6.19 moved
   `whatsapp.py` attrs into `whatsapp_common.py`; we kept upstream's mixin and
   re-applied the `✶` glyph there). Then `nyriel absorb --verify`.
6. Repeat --continue/--verify until parity is READY and verify is green.
7. Present the parity report + verify summary to the operator and STOP.
   **A human runs `nyriel absorb --commit`.** It re-checks every guardrail and
   auto-writes the bookkeeping (version bump, UPSTREAM_BASE.md, CHANGELOG.md).
8. `nyriel absorb --abort` tears the whole attempt down at any point.

## Hard stops (never negotiable)

- NEVER weaken, edit, or delete entries in `nyriel_cli/absorb/divergence.py` to
  make a merge pass — the manifest is the contract this skill exists to protect.
  Only the operator retires a divergence.
- NEVER pass `--skip-verify` unless the operator explicitly says so this session.
- NEVER push, NEVER touch `main`, NEVER run `--commit` yourself.
- Genuine divergence to preserve on sight: the `✶` glyph (upstream uses `⚕`), the
  Brain Settings overlay, the versioned model name in `api_server.py`, and the
  "Embrey The Creator / The Voice" attribution.
```

- [ ] **Step 2: Update `docs/absorb-harness.md`**

In the CLI section (around line 312 where `nyriel absorb v2026.7.0` appears), extend the flow to:

```sh
nyriel absorb --status                # resume state, if any
nyriel absorb v2026.7.0               # branch + parity + AUTO verify battery
nyriel absorb --continue              # materialize conflicts into the working tree
# ...resolve conflict files in place...
nyriel absorb --verify                # snapshot + re-run parity/divergence/battery
nyriel absorb --commit                # human step: guards + bookkeeping + finalize
nyriel absorb --abort                 # rollback at any point
```

Immediately after that flow block, add these two paragraphs verbatim:

```markdown
**Divergence manifest.** `nyriel_cli/absorb/divergence.py` pins our genuine
divergence (the `✶` glyph, the Brain Settings overlay, the versioned model name,
the "Embrey The Creator / The Voice" attribution) as machine-checked invariants.
The parity report enforces it, so a clean merge that silently reverts a deliberate
change flips to NEEDS RESOLUTION. Never weaken the manifest to make a merge pass —
update it only when the operator deliberately moves or retires a divergence.

**Verify battery.** `nyriel_cli/absorb/verify.py` materializes the merged tree
in a throwaway worktree, byte-compiles the changed .py files, and runs the targeted
hermetic test set. It runs automatically after the merge and on `--verify`;
`--commit` refuses while it is red unless a human passes `--skip-verify`.
```

- [ ] **Step 3: Update `UPSTREAM_BASE.md` recipe**

In the "Absorb a Hermes update" section, replace the manual bookkeeping instruction (bump version / update table) with this text:

```markdown
Conflicts are resolved in place: `nyriel absorb --continue` materializes the
merge into the working tree, and `nyriel absorb --verify` snapshots your
resolutions and re-runs parity + the verify battery. Bookkeeping is automatic:
`nyriel absorb --commit` bumps the minor version in `pyproject.toml`, rewrites
this table, and prepends the `CHANGELOG.md` entry — no hand edits.
```

- [ ] **Step 4: Full absorb + neighbors test run**

Run: `.venv/bin/python -m pytest tests/nyriel_cli/test_absorb_driver.py tests/nyriel_cli/test_absorb_detect.py tests/nyriel_cli/test_absorb_parity.py tests/nyriel_cli/test_absorb_divergence.py tests/nyriel_cli/test_absorb_verify.py tests/nyriel_cli/test_banner.py tests/nyriel_cli/test_build_info.py -q`
Expected: all pass

- [ ] **Step 5: Real-repo smoke**

Run: `.venv/bin/python -m nyriel_cli.main absorb --gate`
Expected: `✓ gate passed (0 stray tokens)`

Run: `.venv/bin/python -c "from nyriel_cli.absorb import divergence; v = divergence.check('.', 'HEAD'); print(v or 'divergence manifest intact')"`
Expected: `divergence manifest intact`

- [ ] **Step 6: Commit**

```bash
git add skills/software-development/absorb-upstream/SKILL.md docs/absorb-harness.md UPSTREAM_BASE.md
git commit -m "docs(absorb): skill v2 + harness guide for the v2 flow"
```
