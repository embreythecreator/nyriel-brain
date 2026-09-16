# The Absorb Harness — Developer Guide

How Nyriel folds a new upstream **Hermes Agent** release into its own rebranded
core, reproducibly, without hand-porting a 1,000+-file diff. This is the reference
for the `nyriel_cli/absorb/` package and the `nyriel absorb` command.

> **Audience:** maintainers of `embreythecreator/nyriel-brain` working on a
> git/source checkout. This capability does **not** exist for pip/docker installs —
> they get core updates via `nyriel update` (latest *published* release), which is
> a different mechanism entirely.

---

## 1. The problem it solves

`nyriel-brain` is a **white fork** of `NousResearch/hermes-agent` imported as a
**squashed snapshot** — there is **no shared git history** with upstream, so a plain
`git merge upstream/main` cannot work. On top of that, the fork is a *total rebrand*:
every `Hermes`/`hermes`/`HERMES` token and `NousResearch`/`Nous Research` token is
renamed to `Nyriel`/`embreythecreator`, **including file and directory names**
(`hermes_cli/` → `nyriel_cli/`, `hermes_state.py` → `nyriel_state.py`, …). A raw
`git apply` of an upstream diff can't even locate the renamed files.

So an upstream bump faces three barriers at once:

1. **No common ancestor** — can't 3-way merge directly.
2. **Path renames** — ~640 paths carry a hermes token.
3. **Token renames** — every changed file carries brand tokens, *with carve-outs*
   (legal files, the real "Nous Portal" provider, etc. must NOT be rebranded).

The harness removes all three by putting both sides into the **same naming
namespace** first, then letting git's ordinary 3-way merge do the work.

---

## 2. The strategy: rebranded-upstream 3-way merge

Define a reproducible transform **T** = (path rename) + (token rename) + (attribution
fix) + (carve-outs). Then synthesize the three trees a 3-way merge needs:

```
BASE   = T(old upstream tag, attribution=OFF)   # rebranded prior upstream
THEIRS = T(new upstream tag, attribution=ON)    # rebranded new upstream
OURS   = HEAD^{tree}                             # our current tree
        │
        └── git merge-tree --merge-base=BASE OURS THEIRS  →  merged tree
```

Because BASE, THEIRS and OURS now all speak Nyriel, the merge's conflicts
**collapse to our genuine divergence** instead of thousands of spurious
path/token conflicts. Upstream content our original squash-import trimmed comes
back automatically as "files in THEIRS not in OURS" — that's what makes it a
*full-parity* absorb.

### Why BASE has attribution OFF

The operator attribution fix (`created by Hermes/Nyriel` →
`created by Embrey The Creator / The Voice`) is one of *our* divergences, so it lives
in OURS (HEAD). To make the merge see OURS and THEIRS as having made the **same**
edit (and therefore not conflict), BASE must predate it:

| tree | `created by …` reads | rule |
|---|---|---|
| BASE (`attribution=False`) | `…by Nyriel` | rename only, no attribution rule |
| OURS (HEAD) | `…by Embrey The Creator` | our genuine change |
| THEIRS (`attribution=True`) | `…by Embrey The Creator` | rename + attribution rule |

BASE→OURS and BASE→THEIRS are now identical edits → clean merge, attribution
preserved. (If BASE were built with attribution ON, it would match HEAD and the
merge would silently drop the credit.)

---

## 3. Package layout

```
nyriel_cli/absorb/
├── __init__.py          # public API re-exports (rename_map, rebrand_tree, parity_report)
├── rename_map.py        # Transform T — the single source of truth for the rebrand
├── rebrand_tree.py      # apply T to any git ref via plumbing → a rebranded tree OID
├── parity_report.py     # classify a merged tree (re-added / divergence / conflicts / stray)
├── driver.py            # orchestrate gate → 3-way merge → commit/abort, with guardrails
└── detect.py            # find newer absorbable upstream tags (cached)

nyriel_cli/main.py        # cmd_absorb() + the `absorb` subparser (the CLI surface)
nyriel_cli/banner.py      # absorb_offer_line() — proactive launch-time offer
skills/software-development/absorb-upstream/SKILL.md   # the agentic loop
scripts/absorb/README.md     # pointer only ("moved to nyriel_cli/absorb/")
tests/nyriel_cli/test_absorb_{driver,detect,parity}.py
UPSTREAM_BASE.md             # provenance + the recorded current base tag (driver parses this)
```

All stdlib + `git` plumbing. No new runtime deps, no working-tree checkout, no
mutation of the repo index/HEAD while building trees.

---

## 4. Transform T — `rename_map.py`

The single source of truth. Three ordered rule families plus carve-outs.

### Rules (longest-match-first within each family)

```python
HERMES_RULES        # product rename, applied to content + (a subset to) paths
  "Hermes Agent" → "Nyriel Brain"     # display wordmark — must precede bare token
  "hermes_agent" → "nyriel_brain"     # python package / identifiers
  "hermes-agent" → "nyriel-brain"     # pip / nix / npm / slug
  "HERMES"       → "NYRIEL"
  "Hermes"       → "Nyriel"
  "hermes"       → "nyriel"

NOUS_RULES          # vendor/brand/org rename (most-specific first)
  "NousResearch"     → "embreythecreator"
  "Nous Research"    → "Nyriel"
  "nousresearch"     → "embreythecreator"

  # NOT rebranded, and actively protected by _DOMAIN_SENTINEL in swap_text():
  "nousresearch.com" → "nousresearch.com"   # real third-party service domain

ATTRIBUTION_RULES   # operator self-credit (applied AFTER the families, only if attribution=True)
  "created by Nyriel" → "created by Embrey The Creator / The Voice"
  "created by Hermes"    → "created by Embrey The Creator / The Voice"

PATH_RULES          # case-aware path tokens (total swap; 643/643 hermes paths renamed, 0 kept)
  hermes_agent→nyriel_brain, hermes-agent→nyriel-brain,
  HERMES→NYRIEL, Hermes→Nyriel, hermes→nyriel
```

### Carve-outs (tokens deliberately kept)

| Carve-out | What's kept | Where |
|---|---|---|
| **Legal files** | upstream `Nous Research` attribution | basename in `LICENSE*`, `NOTICE*`, `COPYING` → NOUS family skipped |
| **Achievements LICENSE** | `Hermes Achievements contributors` | path ends `achievements/LICENSE` → HERMES family skipped |
| **Self-authored** | references upstream on purpose | `UPSTREAM_BASE.md` → T never rewrites it (keeps a self-sweep idempotent) |
| **"Nous Portal" provider** | `nous_account`, `NOUS_*`, bare `Nous` | a real external service — the NOUS rules only match brand/org/domain forms, never bare `Nous` |

### Public functions

| Function | Contract |
|---|---|
| `swap_path(path) -> str` | apply `PATH_RULES` to one tree path |
| `swap_text(text, nyriel_path, attribution=True) -> str` | apply the families + (optional) attribution to a blob body; carve-outs keyed on the **already-path-swapped** path |
| `is_self_authored(path) -> bool` | `UPSTREAM_BASE.md` guard |
| `looks_binary(data) -> bool` | NUL byte in first 8 KiB |

### What is deliberately NOT in T (genuine divergence)

These are *our* design choices, preserved by the merge — **never** add them to T as
mechanical rules:

- the brand glyph `✶` (vs upstream `⚕`) — selective, ~16 files
- the boot banner / figlet **NYRIEL** wordmark + Seraphim sigil
- the Brain Settings overlay (`gateway/overlay/brain_settings.py`)
- the versioned model name (`gateway/platforms/api_server.py`)
- the `created by Embrey The Creator` attribution (flows in via THEIRS, see §2)

The **fidelity gate** (§7) exists precisely to prove these — plus a couple of
per-file security-NOTICE hand-edits — are the *only* residuals between `T(base)` and
HEAD. If the gate finds more, T has drifted.

> **Known non-rule-derivable edges** (intentionally left as genuine divergence, not
> special-cased): the capitalised HTTP `Hermes-Agent` User-Agent product token and the
> lowercased HuggingFace model-id prefix were hand-edited inconsistently in the fork.

---

## 5. Applying T — `rebrand_tree.py`

`build_rebranded_tree(ref, attribution=True) -> tree_oid`

Pure git plumbing; produces a **new tree object** without ever checking anything out:

1. `git ls-tree -r -z <ref>` → every `(mode, type, oid, path)`.
2. `git cat-file --batch` reads all blobs **once** (deduped by oid). Uses
   `Popen(...).communicate()` — writing every oid to stdin *then* reading stdout would
   deadlock once the payload exceeds the OS pipe buffer. **Don't "simplify" this back
   to write-then-read.**
3. Per entry: compute the new path (`swap_path`), then decide **reuse vs rewrite**:
   - **reuse the original blob oid** if: self-authored carve-out, binary
     (`looks_binary`), non-UTF-8, or the swap is a no-op.
   - else rewrite the body with `swap_text(...)`; dedupe writes by *swapped content*.
4. `git hash-object -w --stdin-paths` batch-writes the changed blobs.
5. Materialise `<mode> <oid>\t<newpath>` lines into a **throwaway index**
   (`GIT_INDEX_FILE` + `read-tree --empty` + `update-index --index-info`) and
   `git write-tree` → the rebranded tree oid.

CLI form (also usable standalone): `python -m nyriel_cli.absorb.rebrand_tree <ref>
[--tree-only] [--no-attribution] [--parent <c>] [-m <msg>]`.

---

## 6. Classifying the result — `parity_report.py`

`report(merged, theirs, head) -> dict` returns:

| key | meaning |
|---|---|
| `re_added` | files in `merged` not in prior `head` (un-trimmed + new upstream) |
| `removed` | files in `head` not in `merged` |
| `divergence` | files where `merged` differs from `THEIRS` (our genuine changes) |
| `conflicts` | blobs still carrying real conflict markers — **must be empty to commit** |
| `stray` | `hermes`/`nousresearch` tokens outside carve-outs — **must be empty** |
| `ready` | `not conflicts and not stray` (the commit gate) |

**Conflict detection is anchored** — `git grep -E '^<<<<<<< '` / `'^>>>>>>> '` at
column 0 — so test fixtures or docs that embed marker strings as indented literals
don't trigger false positives.

`ALLOWED_STRAY` (tokens permitted to survive): `achievements/LICENSE`,
`security-guidance/NOTICE`, `UPSTREAM_BASE.md`, `CHANGELOG.md`, `scripts/absorb/`,
`nyriel_cli/absorb/` (the harness names the tokens it rewrites, so it self-excludes).

---

## 7. Orchestration + guardrails — `driver.py`

| Function | Role |
|---|---|
| `install_ok(repo) -> (ok, msg)` | refuse unless a git work-tree **with an `upstream` remote** |
| `current_base(repo) -> tag` | parse the recorded base from `UPSTREAM_BASE.md` (`Upstream tag \| \`vX\``) |
| `gate(repo, base_ref) -> (passed, detail)` | the **fidelity gate**: `T(base, attribution=False)` then `git grep` for stray `hermes`/`nousresearch` outside `ALLOWED_STRAY`; passes iff zero. Token-level, domain-aware: `nousresearch.com` occurrences don't count (protected carve-out, see `_DOMAIN_SENTINEL`) — only `hermes` or non-domain `nousresearch` are stray. Same rule in `parity_report.grep_stray` |
| `absorb(repo, tag, base_ref=None) -> dict` | the dry build (see below) |
| `commit(repo, tag) -> oid` | finalize — **refuses unless parity READY** |
| `abort(repo, tag)` | one-step rollback: delete `absorb/<tag>` |

`absorb()` step by step:

1. refuse pre-release/RC tags (`rc|alpha|beta|pre`, case-insensitive).
2. `install_ok` guard.
3. `base_ref = base_ref or current_base()`.
4. `git fetch upstream tag <tag>` if the tag isn't present locally.
5. **run the gate** — abort if the map drifted.
6. build `BASE=T(base,off)`, `THEIRS=T(tag,on)`, `OURS=HEAD^{tree}`; wrap each in a
   `commit-tree` (BASE as parent of THEIRS and OURS).
7. `git merge-tree --write-tree --merge-base=BASE OURS THEIRS` → merged tree.
8. refuse if `absorb/<tag>` already exists; else create it at HEAD.
9. `parity_report.report(...)`; stash `tag` + `merged` in **local git config**
   (`absorb.lastTag`, `absorb.lastMerged`) so `commit()` can finish later.
10. return `{branch, merged_tree, parity, ready}`.

**The safety contract** (every guardrail that makes self-modifying core safe):
git/source-install only · branch isolation (`absorb/<tag>`, never `main`) ·
gate-before-merge · **dry by default** (build & report, never auto-commit) ·
READY-before-commit · refuse pre-release/RC · one-step `--abort` · **never pushes**.

---

## 8. Tag detection — `detect.py`

- `list_upstream_tags(repo)` — `git ls-remote --tags upstream`, keep `vYYYY.M.P[.P]`,
  drop any `rc/alpha/beta/pre`.
- `newer_tags(base, tags)` — semver-tuple compare against the recorded base.
- `latest_absorbable(repo, ttl=21600)` — newest absorbable tag, **cached 6 h** in
  `.git/absorb_check.json`, so launch-time banner checks stay cheap. Any failure
  (offline, no upstream) degrades to `None` silently.

`banner.absorb_offer_line(repo)` turns a hit into a one-line maintainer offer at
launch: `✶ upstream Hermes <tag> available to absorb · run \`nyriel absorb <tag>\``
(wrapped so it can never break the banner).

---

## 9. The CLI — `nyriel absorb`

```
nyriel absorb [tag] [--base BASE] [--check] [--gate] [--commit] [--abort]
```

| invocation | effect | exit |
|---|---|---|
| `--check` | report whether a newer upstream tag exists (cached) | 0 |
| `--gate` | run the fidelity gate only | 0 pass / 1 fail |
| `<tag>` | dry build: `absorb/<tag>` branch + parity summary (no commit) | 0 |
| `<tag> --commit` | finalize — refuses unless parity READY | 0 / 2 refused |
| `<tag> --abort` | delete the absorb branch | 0 |
| `--base BASE` | override the merge-base tag (default: `UPSTREAM_BASE.md`) | — |

Exit codes: `0` ok · `1` gate failed · `2` refused (bad install, RC tag, drifted
map, branch exists, parity not READY) or usage. Output is the printed `✶/✓/✗` line;
the dry build never touches `main` and never commits on its own.

---

## 10. The agentic skill

`skills/software-development/absorb-upstream/SKILL.md` lets Nyriel drive an absorb
itself. The loop: `--check` → confirm with operator → `<tag>` → **stop if the gate
fails** → resolve each conflict by *taking upstream's structure and re-applying our
change where upstream moved it* → run the touched-core tests → **present and wait for
human approval** → `--commit`. Hard stops: gate failure, any core-semantic judgment
call, parity-not-READY, pre-release tags. **Never auto-pushes.**

The canonical conflict-resolution example (baked into the skill): in v2026.6.19
upstream relocated the WhatsApp reply prefix into a new `WhatsAppBehaviorMixin`
(`gateway/platforms/whatsapp_common.py`); the resolution took upstream's mixin and
**re-applied our `✶` glyph** to `DEFAULT_REPLY_PREFIX`.

---

## 11. Running an absorb — worked walkthrough

```sh
nyriel absorb --status                # resume state, if any
nyriel absorb v2026.7.0               # branch + parity + AUTO verify battery
nyriel absorb --continue              # materialize conflicts into the sidecar worktree
# ...resolve conflict files in <repo>-absorb/ — the live tree is never touched...
nyriel absorb --verify                # snapshot + re-run parity/divergence/battery
nyriel absorb --commit                # human step: guards + bookkeeping + finalize + ff-install to main
nyriel absorb --abort                 # rollback at any point
```

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

> **After committing/merging on a machine that runs a live gateway from this repo:
> restart the gateway** (`launchctl kickstart -k gui/$(id -u)/ai.nyriel.gateway` on
> macOS) and refresh the editable-install metadata
> (`VIRTUAL_ENV=$PWD/venv uv pip install -e . --no-deps`). See §13.

---

## 12. Extending T

- **Add a rename rule** → edit the relevant family list in `rename_map.py`. Keep
  **longest-match-first** (compound forms before bare tokens). Then **run the gate** —
  if `T(base)` no longer reproduces HEAD with zero stray, the rule is wrong or you've
  introduced a path/token collision.
- **Add a carve-out** → add the basename/path test in `rename_map.py` *and* the path
  prefix to `ALLOWED_STRAY` in **both** `driver.py` and `parity_report.py` (they're
  intentionally duplicated so each module is self-contained).
- **Never** encode genuine divergence (glyph, wordmark, overlay, model name) as a T
  rule — it must stay a merge-preserved change so upstream can still evolve those files.
- The fidelity gate is the regression test for the map; `test_absorb_*` cover the
  driver/detect/parity contracts. Run them after any change.

---

## 13. Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `gate failed (rebrand map drifted)` | T no longer reproduces HEAD. The printed paths show stray tokens — a missing rule or a new genuine-divergence file. **Do not guess**; reconcile by hand. |
| `--commit` refuses ("parity not READY") | conflict markers or stray tokens remain in the merged tree. Resolve on the branch; re-check the parity report. |
| `branch absorb/<tag> already exists` | a prior run left it — `nyriel absorb <tag> --abort` first. |
| `no upstream remote` / `needs a git checkout` | you're on a pip/docker install, or `upstream` isn't configured (`git remote add upstream https://github.com/NousResearch/hermes-agent.git`). |
| read deadlock / hang building a tree | someone reverted `read_blobs` to write-then-read; it must use `communicate()` (§5). |
| **live Face shows "Protocol correction: your previous response was empty"** after a checkout/merge | the gateway runs from this repo's working tree; the merge rewrote files under the *running* process → a per-request lazy import fails (e.g. `cannot import name … from agent.memory_manager`) → 500s → mute Brain. **Fix: restart the gateway** so in-process code matches disk. |
| `/v1/models` advertises an old version after a bump | `_nyriel_version()` reads `importlib.metadata` first; the editable-install dist-info is stale. `VIRTUAL_ENV=$PWD/venv uv pip install -e . --no-deps` then restart (the `venv` has no `pip`; use `uv`). |

---

## 14. Quick reference

```
Transform T            nyriel_cli/absorb/rename_map.py      swap_path / swap_text
Apply T → tree         nyriel_cli/absorb/rebrand_tree.py    build_rebranded_tree(ref, attribution=)
Classify merge         nyriel_cli/absorb/parity_report.py   report(merged, theirs, head)
Orchestrate + guard    nyriel_cli/absorb/driver.py          gate / absorb / commit / abort
Detect new tags        nyriel_cli/absorb/detect.py          latest_absorbable(repo)
CLI                    nyriel_cli/main.py:cmd_absorb         nyriel absorb …
Launch offer           nyriel_cli/banner.py                  absorb_offer_line(repo)
Agentic loop           skills/software-development/absorb-upstream/SKILL.md
Provenance / base tag  UPSTREAM_BASE.md
Tests                  tests/nyriel_cli/test_absorb_{driver,detect,parity}.py
```

**Merge identity:** `BASE = T(base, attribution=OFF)` · `THEIRS = T(tag, attribution=ON)`
· `OURS = HEAD` · `merge-tree --merge-base=BASE OURS THEIRS`.
