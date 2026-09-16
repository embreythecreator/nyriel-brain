#!/usr/bin/env python3
"""Parity report for an absorb merge.

Classifies the merged tree against full rebranded upstream (THEIRS) and the prior
HEAD, so the operator can see exactly what full parity pulled in, what genuine
divergence was preserved, and whether anything still needs a human:

  re-added      files present in merged & not in prior HEAD (un-trimmed / new)
  divergence    files where merged differs from THEIRS (our genuine changes)
  CONFLICTS     blobs still carrying <<<<<<< markers  -> MUST be zero to commit
  STRAY         hermes/nousresearch tokens outside legal carve-outs -> MUST be 0

Usage: parity_report.py <merged-tree> <theirs-tree> <head-ref>
"""
import re
import subprocess
import sys

try:
    from . import divergence          # packaged
except ImportError:                    # direct-script fallback
    import divergence  # noqa: F401

# Legal carve-outs keep upstream attribution; UPSTREAM_BASE.md references upstream
# by name on purpose; the absorb harness itself defines the tokens it rewrites
# (both its legacy script home and its packaged home). The harness's own docs,
# skill, and tests name the upstream deliberately — carve them out by exact
# path (or narrow prefix), never by broad directory: a wide entry would mask
# real branding leaks. New absorb docs that trip the gate get added here
# consciously, one line each.
ALLOWED_STRAY = ("achievements/LICENSE", "security-guidance/NOTICE",
                 "UPSTREAM_BASE.md", "CHANGELOG.md", "scripts/absorb/",
                 "nyriel_cli/absorb/",
                 "docs/HANDOFF-self-absorb.md",
                 "docs/absorb-harness.md",
                 "docs/plans/2026-06-29-nyriel-self-absorb.md",
                 "docs/plans/2026-07-02-brain-absorb-v2.md",
                 "docs/specs/2026-06-29-nyriel-self-absorb-design.md",
                 "docs/specs/2026-07-03-word-memory-provider.md",
                 # names the historical WO slug sys_brain_hermes_spine_seating
                 "docs/specs/2026-07-03-a1-space-action-blocks.md",
                 "skills/software-development/absorb-upstream/",
                 "skills/software-development/self-update/",
                 "tests/nyriel_cli/test_absorb_",
                 # MoA golden-trace fixtures: recorded live sessions whose
                 # captured transcript mentions hermes-imports skills and an
                 # "upstream Hermes" commit subject — recordings, not branding.
                 "tests/agent/fixtures/moa_golden/")
CONFLICT_MARK = b"<<<<<<<"


def names(treeish):
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", treeish],
                         capture_output=True, text=True, check=True).stdout
    return set(out.splitlines())


def diff_names(a, b):
    out = subprocess.run(["git", "diff", "--name-only", a, b],
                         capture_output=True, text=True, check=True).stdout
    return [x for x in out.splitlines() if x]


def grep_conflict_markers(tree):
    # Find blobs with REAL conflict markers: a git marker sits at column 0
    # ("<<<<<<< " / ">>>>>>> "). Anchoring avoids false positives from test
    # fixtures / docs that embed marker strings as indented literals.
    r = subprocess.run(
        ["git", "grep", "-lI", "-E", "-e", r"^<<<<<<< ", "-e", r"^>>>>>>> ", tree],
        capture_output=True, text=True)
    return [l.split(":", 1)[1] for l in r.stdout.splitlines() if ":" in l]


# A true stray token: any "hermes", or "nousresearch" NOT part of the protected
# real-service domain nousresearch.com (kept on purpose — see rename_map's
# _DOMAIN_SENTINEL). Bytes-level so non-UTF-8 text blobs can't crash the check.
_STRAY_RE = re.compile(rb"hermes|nousresearch(?!\.(com|wtf))", re.I)


def grep_stray(tree):
    r = subprocess.run(["git", "grep", "-ilI", "-e", "hermes", "-e", "nousresearch", tree],
                       capture_output=True, text=True)
    files = [l.split(":", 1)[1] for l in r.stdout.splitlines() if ":" in l]
    files = [f for f in files if not any(a in f for a in ALLOWED_STRAY)]
    stray = []
    for f in files:
        body = subprocess.run(["git", "cat-file", "blob", f"{tree}:{f}"],
                              capture_output=True).stdout
        if _STRAY_RE.search(body):
            stray.append(f)
    return stray


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


def main():
    merged, theirs, head = sys.argv[1], sys.argv[2], sys.argv[3]
    r = report(merged, theirs, head)
    conflicts, stray = r["conflicts"], r["stray"]

    print(f"   re-added (un-trimmed / new from upstream): {r['re_added']} files")
    print(f"   removed vs prior HEAD:                     {r['removed']} files")
    print(f"   genuine divergence (merged != upstream):   {r['divergence']} files")

    status_ok = r["ready"]
    if conflicts:
        print(f"   !! UNRESOLVED CONFLICT MARKERS in {len(conflicts)} files:")
        for f in conflicts[:50]:
            print(f"        {f}")
    else:
        print("   conflict markers: none")

    if stray:
        status_ok = False
        print(f"   !! STRAY upstream tokens outside carve-outs in {len(stray)} files:")
        for f in stray[:50]:
            print(f"        {f}")
    else:
        print("   stray hermes/nousresearch tokens: none (outside legal carve-outs)")

    viol = r["divergence_violations"]
    if viol:
        status_ok = False
        print(f"   !! GENUINE-DIVERGENCE VIOLATIONS in {len(viol)} invariants:")
        for v in viol:
            print(f"        {v}")
    else:
        print("   genuine-divergence manifest: intact")

    print("   STATUS:", "READY to commit" if status_ok else "NEEDS RESOLUTION")
    return 0 if status_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
