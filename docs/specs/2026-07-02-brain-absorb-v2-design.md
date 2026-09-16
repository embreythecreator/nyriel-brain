/# Brain absorb v2 — design

**Date:** 2026-07-02 · **Status:** approved · **Repo:** `~/Oblivion/nyriel-brain`
**Predecessor:** `docs/HANDOFF-self-absorb.md`, `docs/absorb-harness.md` (v1, shipped 2026-06-30)

## Context

`nyriel absorb` v1 (`nyriel_cli/absorb/`, ~620 lines, stdlib + git plumbing) does the
rename-aware 3-way merge and gates commits on parity (0 conflict markers, 0 stray tokens).
Four gaps remain:

1. **Safety** — parity cannot detect a *clean* merge that silently reverts our genuine
   divergence (the `✶` glyph, Brain Settings overlay, versioned model name, attribution).
   Three latent state bugs: `abort()` leaves stale `absorb.lastMerged` config that a later
   `commit()` would resurrect; `commit()` never checks its `tag` argument against
   `absorb.lastTag`; `commit()` parents the merged tree onto whatever HEAD is *now*, even
   if HEAD moved since `absorb()`.
2. **Autonomy** — nothing verifies the merged tree actually runs; that was manual.
3. **Bookkeeping** — version bump, `UPSTREAM_BASE.md` table, `CHANGELOG.md` are manual.
4. **Conflict UX** — resolving conflicts means hand-juggling a raw merged-tree OID stashed
   in git config; nothing materializes conflicted files for editing.

## Goals / non-goals

- **Goal:** close all four gaps by extending the existing modules in place (approved
  Approach A — no state-machine rewrite, no skill-only logic for safety invariants).
- **Autonomy boundary (locked):** Nyriel auto-verifies and auto-resolves only mechanical
  steps; **a human always runs `--commit`**. `main` is never touched; nothing pushes.
- **Non-goal:** Face absorb (`~/Oblivion/nyriel-face`, upstream `agent0ai/space-agent`)
  — deliberately split into a follow-up spec that reuses this hardened pattern.

## 1. Divergence manifest — new `nyriel_cli/absorb/divergence.py`

A checked-in, machine-checkable manifest of genuine divergence. Each entry checks against
a **tree OID** (git plumbing, no checkout):

| Path | Kind | Invariant |
|---|---|---|
| `gateway/platforms/whatsapp_common.py` | contains | `✶` (brand glyph in `DEFAULT_REPLY_PREFIX`) |
| `gateway/overlay/brain_settings.py` | exists | Brain Settings overlay present |
| `gateway/platforms/api_server.py` | contains | `_nyriel_version` (versioned model name) |
| `agent/prompt_builder.py` | contains | `Embrey The Creator / The Voice` |
| `nyriel_cli/default_soul.py` | contains | `Embrey The Creator / The Voice` |

API: `check(tree) -> list[Violation]` (path, kind, detail). Wired in two places:

- `driver.absorb()` asserts the invariants hold on **HEAD before merging** — catches
  manifest drift (renamed/moved file) early with `AbsorbRefused("divergence manifest
  drifted: ...")` instead of a confusing post-merge failure.
- `parity_report.report()` gains a `divergence_violations` key; `ready` now requires
  conflicts == 0 AND stray == 0 AND divergence_violations == 0. A silent revert flips
  parity to NOT-READY, and `commit()` (which recomputes the report) refuses.

## 2. Auto-verify battery — new `nyriel_cli/absorb/verify.py`

Runs automatically at the end of `absorb()` and on demand via `--verify`:

1. `commit-tree` the merged tree; materialize it in a **temp `git worktree`**
   (always removed + `git worktree prune` in `finally`).
2. `compileall` over `.py` files changed vs HEAD.
3. The **targeted hermetic test set** (the set that ran 295-green during the v2026.6.19
   absorb): `tests/nyriel_cli/test_absorb_*.py`, banner, build_info, brain_settings,
   api_server, status, config — run with the repo venv python and default addopts
   (per-file subprocess isolation preserved).

Result (`{"compile_ok", "passed", "failed", "failures": [...]}`) is stored with the absorb
state and shown in `--status`. **`--commit` refuses if verify failed**; the escape hatch is
an explicit `--skip-verify` (for known host-only flakes), never the default.

## 3. Conflict-resolution UX — new flags on the existing subcommand

- `nyriel absorb --continue` — switch to `absorb/<tag>` and materialize the merged
  tree (conflict markers and all) into the working tree via `git restore
  --source=<merged-commit> --worktree --staged -- .`. Refuses on a dirty working tree.
  The parity report already names the conflicted files.
- Operator or skill resolves in place. Canonical example remains the v2026.6.19
  `whatsapp.py` → `whatsapp_common.py` mixin case: take upstream's relocation, re-apply
  our divergence at the new location.
- `nyriel absorb --verify` — if the working tree is a materialized absorb, snapshot it
  (`git add -A` + `git write-tree`), update `absorb.lastMerged`, then re-run parity +
  divergence + battery. If nothing is materialized, re-verify the stored merged tree.
- `nyriel absorb --status` — print the in-flight absorb (tag, branch, parity summary,
  verify result) so a cold session resumes without archaeology.

## 4. Bookkeeping + state fixes in `commit()` / `abort()`

`commit()` amends the merged tree with three generated edits before `commit-tree`:

- `pyproject.toml` — **minor bump per absorb** (`0.17.0 → 0.18.0`; approved rule).
- `UPSTREAM_BASE.md` — table row updated to the new upstream tag + date.
- `CHANGELOG.md` — prepended entry: tag, date, parity stats (re-added / removed /
  divergence counts).

Commit message: `absorb: <tag> (full parity)`. State fixes (all `AbsorbRefused` on
violation):

- `absorb()` additionally records `absorb.oursHead` (HEAD OID at merge time).
- `commit()` refuses if `absorb.lastTag != tag` or HEAD moved since `absorb.oursHead`.
- `abort()` clears `absorb.lastTag` / `absorb.lastMerged` / `absorb.oursHead` and prunes
  any verify worktree.

## 5. CLI + skill surface

New flags only: `--continue`, `--verify`, `--status`, `--skip-verify` (modifier on
`--commit`). All three new flags operate on the in-flight absorb recorded in
`absorb.lastTag` — no tag argument needed; they refuse with a clear message when no
absorb is in flight. Existing surface (`[tag]`, `--base`, `--check`, `--gate`,
`--commit`, `--abort`) and the banner offer are unchanged.

`skills/software-development/absorb-upstream/SKILL.md` is rewritten around the new loop:
absorb → read auto-verify results → if conflicts: `--continue` / resolve / `--verify` →
present everything to the human → **human** runs `--commit`. New agent hard-stops:

- Never satisfy parity by deleting or weakening our divergence (the manifest is the
  contract, not an obstacle).
- Never pass `--skip-verify` without explicit human say-so.

## 6. Error handling

Every refusal is `AbsorbRefused` with an actionable message. Verify worktrees are cleaned
in `finally`. `--continue` refuses on dirty working tree. The banner absorb check keeps
its never-throw contract.

## 7. Testing

Extend the hermetic suite (`tests/nyriel_cli/test_absorb_{driver,detect,parity}.py`,
temp git repos; add `test_absorb_divergence.py`, `test_absorb_verify.py`):

- divergence: violations detected on a doctored tree; drift-on-HEAD refusal.
- verify: compileall failure caught on a synthetic bad `.py`; worktree cleaned on failure.
- driver: `abort()` clears all state; `commit()` refuses on tag mismatch, moved HEAD,
  failed verify; `--skip-verify` overrides only the verify refusal.
- bookkeeping: version bump, `UPSTREAM_BASE.md` row, `CHANGELOG.md` entry present in the
  finalized tree.
- parity: `ready == False` when only a divergence violation exists.

## Decisions log

- Approach A (extend in place) over a stateful session rewrite (B) or skill-only logic
  (C): the improvements are natural extensions of proven modules; safety belongs in the
  gate, not prose; YAGNI on a state machine for a roughly-monthly operation.
- Autonomy: auto-verify, human commits.
- Version rule: minor bump per absorb.
- `--commit` gated on the test battery, `--skip-verify` as the explicit escape.
- Face absorb: separate follow-up spec (Brain v2 ships first, Face inherits the pattern).

## Implementation deviations (accepted 2026-07-02)

- §2: verify result shape shipped as `{compile_ok, compile_errors, tests_ok,
  tests_summary, ok}` (summary string instead of passed/failed counts).
- §2: targeted tests run in one pytest process, not per-file subprocess
  isolation; the targeted set was verified isolation-safe run together.
- §2: the battery probes each targeted file with `--collect-only` and skips
  (with reporting) files that fail collection, e.g. optional deps absent on
  the host; it is red when zero files are collectable or any collected test fails.
- §3: `--continue` materializes via `git checkout` + `git read-tree --reset -u`
  (equivalent to the spec'd `git restore --source`, and handles deletions exactly).
