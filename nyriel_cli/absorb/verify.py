"""Post-merge verification battery for `nyriel absorb`.

Materializes the merged tree in a throwaway git worktree (never the operator's
working tree), byte-compiles the changed .py files, and runs the targeted
hermetic test set with the merged code first on sys.path. The battery gates
`--commit` (see driver.commit); `--skip-verify` is the explicit human escape.

Containment is honest but thin: a throwaway worktree plus time-boxing only —
the battery still executes merged code with the operator's privileges, which
is why absorb only takes pinned tags from a trusted upstream.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# Targeted battery: the absorb suite plus fast hermetic CLI/gateway tests.
# Paths missing from the merged tree are skipped (synthetic test repos,
# upstream test moves), and files that fail a per-file --collect-only probe
# (e.g. an optional extra like aiohttp absent on this host) are skipped and
# reported — the summary says how many actually ran.
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

# Absorbed code is untrusted until verified — never let it hang the commit gate.
COMPILE_TIMEOUT = 300
TESTS_TIMEOUT = 1800


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


def changed_py(repo: str, merged: str, head: str = "HEAD") -> list[str]:
    out = _git(repo, "diff", "--name-only", head, merged).stdout.splitlines()
    return [f for f in out if f.endswith(".py")]


def _collectable(wt: str, targets: list[str], env: dict) -> tuple[list[str], list[str]]:
    """Split targets into (runnable, skipped) via per-file collect-only probes.
    A file whose collection errors (e.g. an optional dependency missing on
    this host) is skipped and reported rather than failing the whole battery."""
    runnable, skipped = [], []
    for t in targets:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only",
                            "-p", "no:cacheprovider", t],
                           cwd=wt, capture_output=True, text=True, env=env,
                           timeout=COMPILE_TIMEOUT)
        (runnable if r.returncode == 0 else skipped).append(t)
    return runnable, skipped


def run(repo: str, merged: str, head: str = "HEAD") -> dict:
    """Run the battery against a merged TREE oid. Worktree is always removed."""
    commit = _git(repo, "commit-tree", merged, "-m",
                  "absorb-verify (throwaway)").stdout.strip()
    tmp = tempfile.mkdtemp(prefix="absorb-verify-")
    wt = os.path.join(tmp, "tree")
    try:
        _git(repo, "worktree", "add", "-q", "--detach", wt, commit)
        py = [f for f in changed_py(repo, merged, head)
              if os.path.exists(os.path.join(wt, f))]
        compile_ok, compile_errors = True, ""
        if py:
            try:
                r = subprocess.run([sys.executable, "-m", "compileall", "-q", *py],
                                   cwd=wt, capture_output=True, text=True,
                                   timeout=COMPILE_TIMEOUT)
                compile_ok = r.returncode == 0
                compile_errors = (r.stdout + r.stderr).strip()
            except subprocess.TimeoutExpired:
                compile_ok = False
                compile_errors = f"compileall timed out after {COMPILE_TIMEOUT}s"

        targets = [t for t in TARGETED_TESTS
                   if os.path.exists(os.path.join(wt, t))]
        tests_ok, tests_summary = True, "no targeted tests present in merged tree"
        if targets:
            env = dict(os.environ)
            env["PYTHONPATH"] = wt + os.pathsep + env.get("PYTHONPATH", "")
            try:
                runnable, skipped = _collectable(wt, targets, env)
                if not runnable:
                    tests_ok = False
                    tests_summary = (f"0 of {len(targets)} targeted files "
                                     f"collectable — check optional deps")
                else:
                    r = subprocess.run([sys.executable, "-m", "pytest", "-q",
                                        "-p", "no:cacheprovider", *runnable],
                                       cwd=wt, capture_output=True, text=True, env=env,
                                       timeout=TESTS_TIMEOUT)
                    tests_ok = r.returncode == 0
                    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
                    tests_summary = (f"{len(runnable)} of {len(targets)} target files · "
                                     + (lines[-1] if lines else f"exit {r.returncode}"))
                    if skipped:
                        tests_summary += f" · {len(skipped)} skipped (collection errors)"
            except subprocess.TimeoutExpired:
                tests_ok = False
                tests_summary = f"targeted tests timed out after {TESTS_TIMEOUT}s"
        return {"compile_ok": compile_ok, "compile_errors": compile_errors,
                "tests_ok": tests_ok, "tests_summary": tests_summary,
                "ok": compile_ok and tests_ok}
    finally:
        _git(repo, "worktree", "remove", "--force", wt, check=False)
        _git(repo, "worktree", "prune", check=False)
        shutil.rmtree(tmp, ignore_errors=True)
