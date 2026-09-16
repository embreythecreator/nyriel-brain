"""nyriel absorb driver — ports absorb.sh to Python (stdlib + git only).

Orchestrates an absorb of an upstream Hermes tag into the rebranded fork:
read the current base from UPSTREAM_BASE.md, run the rebrand fidelity gate,
build BASE/THEIRS/OURS trees, 3-way merge onto `absorb/<tag>`, classify with
parity_report, and enforce every self-modifying-core guardrail. The merge is
dry-by-default: `absorb()` stops before committing; `commit()` finalizes only
when parity is READY; `abort()` is the one-step rollback. Nothing here ever
pushes or touches `main`.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import tempfile

from . import rebrand_tree, parity_report, divergence, verify

# Only blobs allowed to retain an upstream token after the rebrand: legal
# carve-outs, the provenance/changelog docs, and the absorb harness itself
# (both its legacy script home and its packaged home).
ALLOWED_STRAY = ("achievements/LICENSE", "security-guidance/NOTICE",
                 "UPSTREAM_BASE.md", "CHANGELOG.md", "scripts/absorb/",
                 "nyriel_cli/absorb/")
_PRERELEASE = re.compile(r"(rc|alpha|beta|pre)", re.I)

# A true stray token: any "hermes", or "nousresearch" NOT part of the protected
# real-service domain nousresearch.com (kept on purpose — see rename_map's
# _DOMAIN_SENTINEL). Bytes-level so non-UTF-8 text blobs can't crash the gate.
_STRAY_RE = re.compile(rb"hermes|nousresearch(?!\.(com|wtf))", re.I)


def _has_stray(repo, tree, path):
    body = subprocess.run(["git", "-C", repo, "cat-file", "blob", f"{tree}:{path}"],
                          capture_output=True).stdout
    return bool(_STRAY_RE.search(body))


class AbsorbRefused(Exception):
    """A guardrail blocked the absorb (bad install, RC tag, drifted map, ...)."""


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


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


def _store_verify(repo: str, res: dict) -> None:
    _git(repo, "config", "--local", "absorb.verifyOk",
         "true" if res["ok"] else "false")
    summary = res["tests_summary"] if res["compile_ok"] else \
        f"compileall failed: {res['compile_errors'][:200]}"
    _git(repo, "config", "--local", "absorb.verifySummary", summary)


def install_ok(repo: str) -> tuple[bool, str]:
    """Absorb only runs on a git/source checkout that has the `upstream` remote."""
    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        return False, "absorb needs a git/source checkout (not a pip/docker install)."
    remotes = _git(repo, "remote").stdout.split()
    if "upstream" not in remotes:
        return False, "no `upstream` remote — add NousResearch/hermes-agent as `upstream`."
    return True, ""


def current_base(repo: str) -> str:
    """Read the recorded merge-base upstream tag from UPSTREAM_BASE.md."""
    text = (_git(repo, "show", "HEAD:UPSTREAM_BASE.md", check=False).stdout
            or open(f"{repo}/UPSTREAM_BASE.md").read())
    m = re.search(r"Upstream tag\s*\|\s*`?([vV][0-9.]+)`?", text)
    if not m:
        raise AbsorbRefused("could not read the current base tag from UPSTREAM_BASE.md")
    return m.group(1)


def gate(repo: str, base_ref: str) -> tuple[bool, str]:
    """Fidelity gate: T(base) must reproduce HEAD with 0 stray tokens outside carve-outs."""
    tree = rebrand_tree.build_rebranded_tree(base_ref, attribution=False)
    stray = _git(repo, "grep", "-ilI", "-e", "hermes", "-e", "nousresearch", tree,
                 check=False).stdout.splitlines()
    stray = [s.split(":", 1)[1] for s in stray if ":" in s
             and not any(a in s for a in ALLOWED_STRAY)]
    stray = [p for p in stray if _has_stray(repo, tree, p)]
    return (not stray), ("\n".join(stray) if stray else "")


def absorb(repo: str, tag: str, base_ref: str | None = None) -> dict:
    """Build the 3-way absorb merge onto `absorb/<tag>`. Dry: stops before committing."""
    if _PRERELEASE.search(tag):
        raise AbsorbRefused(f"refusing pre-release/RC tag {tag}")
    ok, msg = install_ok(repo)
    if not ok:
        raise AbsorbRefused(msg)
    st = state(repo)
    if st and st["tag"] != tag:
        raise AbsorbRefused(f"absorb {st['tag']} already in flight — "
                            f"--commit or --abort it before starting {tag}")
    base_ref = base_ref or current_base(repo)
    if _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode != 0:
        _git(repo, "fetch", "-q", "upstream", "tag", tag)
    passed, detail = gate(repo, base_ref)
    if not passed:
        raise AbsorbRefused(f"fidelity gate failed (rebrand map drifted):\n{detail}")
    drift = divergence.check(repo, "HEAD")
    if drift:
        raise AbsorbRefused(
            "divergence manifest drifted on HEAD — update "
            "nyriel_cli/absorb/divergence.py first:\n" + "\n".join(drift))

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
    rep = parity_report.report(merged, theirs_tree, "HEAD", repo=repo)
    # stash refs so commit()/abort() can finish the job
    _git(repo, "config", "--local", "absorb.lastTag", tag)
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    _git(repo, "config", "--local", "absorb.oursHead",
         _git(repo, "rev-parse", "HEAD").stdout.strip())
    vres = verify.run(repo, merged)
    _store_verify(repo, vres)
    return {"branch": branch, "merged_tree": merged, "parity": rep,
            "verify": vres, "ready": rep["ready"]}


def worktree_path(repo: str) -> str:
    """Sidecar worktree where conflicts are resolved — NEVER the live tree.

    The live checkout is the running install (CLI entry point, gateway,
    launchd services all execute from it); materializing a half-merged tree
    into it bricks the product until the last conflict is resolved. The
    sidecar keeps the in-flight absorb fully outside the running install —
    the live tree only ever changes by the human's final ff-merge of a
    committed, verified absorb branch.
    """
    return os.path.abspath(repo).rstrip(os.sep) + "-absorb"


def _live_repo_on(repo: str, branch: str) -> None:
    if _current_branch(repo) == branch:
        raise AbsorbRefused(
            f"the live checkout is on {branch} — the running install must "
            f"never hold an in-flight absorb. `git checkout main` first; "
            f"resolution happens in the sidecar worktree (--continue).")


def materialize(repo: str) -> str:
    """--continue: put the merged tree (conflict markers and all) into a
    sidecar worktree on the absorb branch so conflicts can be edited there.
    The live working tree is never touched. Idempotent: an existing sidecar
    with in-progress resolution is reused, never clobbered."""
    st = state(repo)
    if not st:
        raise AbsorbRefused("no absorb in flight — run `nyriel absorb <tag>` first")
    branch = f"absorb/{st['tag']}"
    _live_repo_on(repo, branch)
    wt = worktree_path(repo)
    if os.path.isdir(wt):
        if _current_branch(wt) != branch:
            raise AbsorbRefused(
                f"{wt} exists but is not a worktree on {branch} — "
                f"remove it (or `git worktree remove` it) and re-run --continue")
        return wt
    _git(repo, "worktree", "add", "-q", wt, branch)
    _git(wt, "read-tree", "--reset", "-u", st["merged"])
    return wt


def verify_current(repo: str) -> dict:
    """--verify: snapshot the sidecar worktree as the new merged tree when it
    exists, then re-run parity + divergence + the verification battery.
    Without a sidecar it re-verifies the stashed tree."""
    st = state(repo)
    if not st:
        raise AbsorbRefused("no absorb in flight — run `nyriel absorb <tag>` first")
    branch = f"absorb/{st['tag']}"
    _live_repo_on(repo, branch)
    wt = worktree_path(repo)
    if os.path.isdir(wt) and _current_branch(wt) == branch:
        _git(wt, "add", "-A")
        merged = _git(wt, "write-tree").stdout.strip()
        _git(repo, "config", "--local", "absorb.lastMerged", merged)
    else:
        merged = st["merged"]
    theirs = rebrand_tree.build_rebranded_tree(st["tag"], attribution=True)
    rep = parity_report.report(merged, theirs, st["ours_head"], repo=repo)
    vres = verify.run(repo, merged, head=st["ours_head"])
    _store_verify(repo, vres)
    return {"parity": rep, "verify": vres, "merged": merged}


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
        return ch.rstrip() + "\n\n" + entry
    nxt = ch.find("\n## [", unrel + 1)
    if nxt == -1:
        return ch.rstrip() + "\n\n" + entry
    return ch[:nxt].rstrip("\n") + "\n\n" + entry.rstrip() + "\n\n" + ch[nxt + 1:]


def _bookkeep_tree(repo: str, merged: str, tag: str, rep: dict,
                   ours_head: str) -> str:
    """Fold version bump + UPSTREAM_BASE.md row + CHANGELOG entry into the tree."""
    old_base = current_base(repo)
    # The independent Nyriel version line bumps from OUR previous HEAD —
    # the merged tree's version can carry upstream's own bump, and reading it
    # would make our line ride upstream's number instead of incrementing ours.
    ours_py = _read_blob(repo, ours_head, "pyproject.toml")
    mo = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', ours_py, re.M)
    if not mo:
        raise AbsorbRefused("could not find the version line in ours-HEAD pyproject.toml")
    newver = f"{mo.group(1)}.{int(mo.group(2)) + 1}.0"     # minor bump per absorb
    py = _read_blob(repo, merged, "pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', py, re.M)
    if not m:
        raise AbsorbRefused("could not find the version line in pyproject.toml")
    py = py[:m.start()] + f'version = "{newver}"' + py[m.end():]

    # nyriel --version reads nyriel_cli.__version__, not pyproject; the
    # merge can bring upstream's value in, so sync it or the two lines skew.
    init = _read_blob(repo, merged, "nyriel_cli/__init__.py")
    mi = re.search(r'^__version__ = "[^"]+"', init, re.M)
    if not mi:
        raise AbsorbRefused("could not find __version__ in nyriel_cli/__init__.py")
    init = init[:mi.start()] + f'__version__ = "{newver}"' + init[mi.end():]

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
             "nyriel_cli/__init__.py": _hash_blob(repo, init),
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
    branch = f"absorb/{tag}"
    _live_repo_on(repo, branch)
    wt = worktree_path(repo)
    if os.path.isdir(wt) and _current_branch(wt) == branch:
        if _git(wt, "diff", "--quiet", merged, check=False).returncode != 0:
            raise AbsorbRefused("sidecar worktree differs from the verified snapshot — "
                                "run `nyriel absorb --verify` again before --commit")
    theirs = rebrand_tree.build_rebranded_tree(tag, attribution=True)
    rep = parity_report.report(merged, theirs, st["ours_head"], repo=repo)
    if not rep["ready"]:
        raise AbsorbRefused("parity not READY (conflicts, stray tokens, or "
                            "divergence violations remain) — run --verify for detail")
    if not skip_verify and not st["verify_ok"]:
        raise AbsorbRefused("verify battery not green — fix and re-run "
                            "`nyriel absorb --verify`, or pass --skip-verify (human call)")
    final_tree = _bookkeep_tree(repo, merged, tag, rep, st["ours_head"])
    oid = _git(repo, "commit-tree", final_tree, "-p", st["ours_head"], "-m",
               f"absorb: {tag} (full parity)").stdout.strip()
    # The branch may be checked out in the sidecar worktree; update-ref would
    # refuse or desync it, so retire the sidecar first — its content is the
    # verified snapshot the commit was built from, nothing is lost.
    if os.path.isdir(wt):
        _git(repo, "worktree", "remove", "--force", wt, check=False)
        _git(repo, "worktree", "prune", check=False)
    _git(repo, "update-ref", f"refs/heads/{branch}", oid)
    clear_state(repo)
    # The detect cache predates the absorb; left alone it keeps offering the
    # tag that was just absorbed until its TTL expires.
    from . import detect
    detect.cache_file(repo).unlink(missing_ok=True)
    _autoinstall(repo, oid, merged)
    return oid


def _autoinstall(repo: str, oid: str, merged: str) -> bool:
    """Fast-forward main onto the finalized commit when provably lossless.

    Two safe states: a clean tree (plain ff), or a tree that byte-matches the
    verified merged snapshot (legacy in-place resolution — everything is
    already inside the commit, so parking it in a stash loses nothing).
    Anything else leaves installation to the operator; --commit prints the
    manual step in that case.
    """
    if _current_branch(repo) != "main":
        return False
    clean = not _git(repo, "status", "--porcelain", check=False).stdout.strip()
    matches = (_git(repo, "diff", "--quiet", merged, check=False).returncode == 0
               and _git(repo, "diff", "--cached", "--quiet", merged,
                        check=False).returncode == 0)
    if not (clean or matches):
        return False
    if not clean:
        # recoverable, not destructive: the superseded staged copy goes to a stash
        _git(repo, "stash", "push", "-q", "-m",
             f"absorb {oid[:12]}: superseded staged copy", check=False)
    return _git(repo, "merge", "--ff-only", oid, check=False).returncode == 0


def abort(repo: str, tag: str | None = None) -> None:
    """One-step rollback: delete the absorb branch, clear all stashed state."""
    st = state(repo)
    tag = tag or (st["tag"] if st else None)
    if not tag:
        raise AbsorbRefused("no absorb in flight and no tag given — nothing to abort")
    branch = f"absorb/{tag}"
    wt = worktree_path(repo)
    if os.path.isdir(wt):
        _git(repo, "worktree", "remove", "--force", wt, check=False)
    if _current_branch(repo) == branch:
        # step off the branch before deleting it; fall back to detached ours-head
        if _git(repo, "checkout", "-q", "-f", "main", check=False).returncode != 0:
            _git(repo, "checkout", "-q", "-f",
                 (st or {}).get("ours_head") or "HEAD")
    if _current_branch(repo) == branch:
        raise AbsorbRefused(f"could not step off {branch} — "
                            f"check out another branch, then re-run --abort")
    _git(repo, "branch", "-D", branch, check=False)
    _git(repo, "worktree", "prune", check=False)
    clear_state(repo)
