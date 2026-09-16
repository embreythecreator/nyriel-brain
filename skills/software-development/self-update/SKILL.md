---
name: self-update
description: "Use when the operator asks Nyriel to update itself — fold a new upstream release into the Brain (hermes-agent) or the Face (space-agent). Explains how the absorb machinery actually works, drives the full loop, and STOPS for human approval at --commit, merge, and push. Maintainer / git-install only."
version: 1.0.0
author: Embrey The Creator
license: MIT
platforms: [macos, linux]
metadata:
  nyriel:
    tags: [self-update, absorb, upstream, brain, face, maintainer, rebrand, merge]
    related_skills: [absorb-upstream, systematic-debugging, test-driven-development]
---

# Self-Update (Brain + Face)

## What I am, mechanically

I am two **white forks** — repos with **no shared git history** with their
upstreams, imported as squashed snapshots and renamed:

| Organ | Repo | Upstream | Harness |
|---|---|---|---|
| **Brain** (this body) | `~/Oblivion/nyriel-brain` | NousResearch/hermes-agent | `nyriel absorb` (packaged, `nyriel_cli/absorb/`) |
| **Face** (web + desktop) | `~/Oblivion/nyriel-face` | agent0ai/space-agent | `python3 scripts/absorb/absorb.py` (run from that repo) |

Because there is no shared history, `git merge upstream/main` is impossible.
Updating myself means **absorbing**: re-deriving what upstream changed and
folding it into my renamed tree without losing what makes me *me*.

(The Word organ — `~/Oblivion/word`, fork of open-notebook — has no absorb
harness; its upstream is ported by hand. Not covered here.)

## How an absorb actually works

Every absorb is the same five-step machine, entirely in git plumbing (no
working-tree churn until conflict resolution):

1. **Transform T** (`rename_map.py`): the upstream→Nyriel rename, encoded
   as ordered text/path substitution rules. It is **oracle-validated** — T
   applied to the original import base byte-reproduces the original rename
   commit. Brain T: Hermes/Nous → Nyriel (+ attribution rule). Face T:
   Space Agent/agent0ai wordmark+org+slug only — the `space` runtime
   namespace (`globalThis.space`, storage keys) is deliberately untouched.
2. **Rebrand both ends**: build `rebrand(BASE)` and `rebrand(TAG)` trees with
   `rebrand_tree.py` (batch blob rewrite → temp index → `write-tree`).
3. **3-way merge**: `git merge-tree --write-tree` with `rebrand(BASE)` as
   merge base, my HEAD as ours, `rebrand(TAG)` as theirs → a merged tree on
   branch `absorb/<tag>`. My genuine changes and upstream's changes combine;
   overlaps become conflict markers in the tree.
4. **Parity gates** (`parity_report.py`) — ALL must pass before commit:
   - **conflicts**: zero files with real `<<<<<<<` markers;
   - **stray tokens**: zero upstream branding outside explicit carve-outs
     (LICENSE, UPSTREAM_BASE.md, the harness itself, listed docs) — a broad
     carve-out is forbidden, one explicit path per line;
   - **divergence manifest** (`divergence.py`): machine-checked invariants
     that a clean merge could silently revert. Brain: ✶ glyph, Brain
     Settings overlay, `_nyriel_version`, "Embrey The Creator / The
     Voice" attribution. Face: `brain_chat.js`, `BRAIN_ONLY`, the vision
     subsystem (upstream deleted it in v0.66 — we keep it; the Brain is
     multimodal and the Face feeds it visual context), Oblivion CSS chassis,
     package identity.
5. **Verify battery** (`verify.py`): materialize the merged tree in a
   throwaway worktree and prove it runs. Brain: `compileall` + targeted
   pytest set (collect-only probes skip optional-dep files; 9/11 or 11/11
   collectable = normal). Face: `bun install` + the brain tests + an HTTP
   boot probe. Red battery = the merge broke something real.

State (tag, merged tree, ours-HEAD, verify result) lives in `git config
absorb.*`, so the flow survives session restarts. `--commit` re-checks every
guard, then writes the absorb commit **with bookkeeping folded in**: minor
version bump computed from *ours-HEAD* (never the merged tree — upstream's
own version bump would skew our independent line), UPSTREAM_BASE.md rows,
CHANGELOG entry (Brain only).

## Update the Brain

```sh
cd ~/Oblivion/nyriel-brain
nyriel absorb --check          # ✶ banner line also signals this at launch
nyriel absorb <tag>            # merge + gates + auto-verify
# conflicts? materialize into the sidecar worktree (<repo>-absorb/ —
# the live/running tree is NEVER touched), resolve there, re-gate:
nyriel absorb --continue
nyriel absorb --verify
nyriel absorb --status         # cold-resume: where am I?
# >>> STOP — show the operator the parity report + verify verdict <<<
nyriel absorb --commit         # only on explicit human go — finalizes AND ff-installs to main
```

After the human merges `absorb/<tag>` → `main`: the running gateway and
sessions still execute the *old* code — restart them to become the new
version. Tests always via `.venv/bin/python -m pytest` (`venv/` is a husk).

## Update the Face

```sh
cd ~/Oblivion/nyriel-face
python3 scripts/absorb/absorb.py --check
python3 scripts/absorb/absorb.py <tag>       # same flags as the Brain flow
python3 scripts/absorb/absorb.py --verify
# >>> STOP — human go, then --commit, then human merges to main <<<
```

**Then bring the running heads current — they never update themselves:**

- **web head**: `bun space serve` serves the tree as of launch → restart it;
- **desktop head**: `/Applications/Nyriel.app` is a frozen packaged
  snapshot → `node packaging/scripts/macos-package.js` (node lives at
  `~/.hermes/node/bin`; the final DMG step fails on a gettext dylib — ignore,
  the `.app` in `dist/desktop/macos/mac/` is complete) → replace the
  installed .app. Skipping this is how the desktop drifted weeks stale once.

Working tree dirty in the Face repo is normal (live design sessions): commit
a `wip:` snapshot first so the merge sees the true OURS tree.

## Hard stops — never cross these

- **A human always says go** for `--commit`, for merging the absorb branch
  to `main`, and for any push. Never do these unprompted.
- **Push only to origin** (embreythecreator/…). NEVER push to the `upstream`
  remotes (NousResearch, agent0ai) — they are fetch-only by policy.
- **Never weaken a divergence manifest to make a merge pass.** A violation
  means the merge is wrong. Fix the merge.
- `--skip-verify` exists for the human, not for me — only on their explicit
  say-so.
- Brand canon: glyph is **✶** (never ⚕); attribution is exactly
  **"Embrey The Creator / The Voice"**; no upstream tokens on product
  surfaces (the leak gate enforces this — reword, don't carve out).
- If an absorb goes sideways: `--abort` is the one-step rollback (deletes
  the absorb branch, clears state, touches nothing else).

## Deeper reference

Brain: `docs/absorb-harness.md`, `UPSTREAM_BASE.md`, spec
`docs/specs/2026-07-02-brain-absorb-v2-design.md`, and the repo-work skill
`absorb-upstream`. Face: `UPSTREAM_BASE.md` there. Both document live-fire
lessons (the aiohttp collection trap; the vision-subsystem silent revert).
