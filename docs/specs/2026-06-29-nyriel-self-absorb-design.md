# Design: Nyriel self-absorb — `nyriel absorb` + skill

**Status:** approved design, pre-implementation
**Date:** 2026-06-29
**Author:** Embrey The Creator (with Claude)

## Context

The fork now has a working rename-aware absorb harness in `scripts/absorb/`
(`rename_map.py`, `rebrand_tree.py`, `absorb.sh`, `parity_report.py`). It folded
upstream `v2026.6.5 → v2026.6.19` into the rebranded tree at full parity. Today that
harness is a maintainer's loose script collection run by hand.

The goal of this work: make absorbing upstream a **first-class capability of Nyriel
itself** — a deterministic `nyriel absorb` subcommand, proactive detection of new
upstream releases, and a skill so Nyriel can *drive* the absorb agentically (resolve
conflicts, run tests) while pausing for human approval before committing.

### The two distinct "updates" (load-bearing distinction)

- **`nyriel update`** (exists) — exits + relaunches to pull the latest *published*
  release. The *install* side: "get the newest Nyriel Brain Embrey shipped."
- **Core-absorb** (this feature) — folds a new *upstream Hermes* release into the
  fork's source, *creating* new core. What the harness does.

### Hard constraint that shapes scope

Core-absorb requires a **git/source checkout + the `upstream` remote + occasional human
conflict judgment**. A pip/docker/managed install has none of these. So the value chain
is: *maintainer (on the source repo) absorbs upstream → publishes a release → end users
`nyriel update` to receive it.* Self-absorb is therefore a **maintainer capability that
runs on the source repo**, surfaced only on git/source installs; it stays silent
elsewhere. It also cannot be contributed to the real upstream (`NousResearch/hermes-agent`)
— the transform encodes *our* rebrand and belongs in our fork.

### Decisions locked during brainstorming

- **Autonomy:** layered — a deterministic command does the mechanics + safety rails; a
  skill wraps it so Nyriel can drive it, pausing for approval before committing.
- **Trigger:** proactive detect-and-offer — check `upstream` for new tags (reusing the
  existing update-check/banner surface), surface an offer; execution stays on-demand.

## Architecture

Graduate the harness from loose scripts into a packaged, importable Python module that the
CLI subcommand, the detection hook, and the skill all call. Port the one bash driver
(`absorb.sh`) to Python so the capability is cross-platform and ships with every git
install.

```
nyriel_cli/absorb/
  __init__.py
  rename_map.py      # transform T — moved as-is from scripts/absorb
  rebrand_tree.py    # apply T to a git ref — moved as-is
  driver.py          # absorb.sh ported to Python: gate, build trees, 3-way merge, branch,
                     #   --commit / --abort, all guardrails
  parity_report.py   # commit gate — moved as-is (keeps the anchored marker/stray logic)
  detect.py          # NEW: ls-remote upstream, compare to UPSTREAM_BASE base, cache
```

`scripts/absorb/` is reduced to a thin compatibility shim (or removed) so there is a single
home. The harness already proved itself on the v2026.6.19 absorb; this is repackaging plus
a command, a detection hook, and a skill — not a rewrite.

### Component responsibilities

- **`rename_map.py`** — single source of truth for transform T (HERMES + NOUS token
  families, per-family carve-outs, attribution rule, path swap). Unchanged.
- **`rebrand_tree.py`** — applies T to any ref via git plumbing, emits a rebranded tree.
  Unchanged.
- **`driver.py`** — orchestrates an absorb: reads the current base from `UPSTREAM_BASE.md`,
  runs the fidelity gate, builds BASE/THEIRS/OURS, 3-way merges onto `absorb/<tag>`, runs
  `parity_report`, and enforces every guardrail. Owns `--check`/`--gate`/`<tag>`/
  `--commit`/`--abort`.
- **`parity_report.py`** — classifies the merged tree (re-added / divergence / conflict
  markers / stray tokens) and gates the commit. Unchanged (anchored detection already
  hardened against the test-data false positive).
- **`detect.py`** — `git ls-remote upstream --tags`, find latest tag newer than the
  recorded base, cache the result with a TTL (in `.git/` or `~/.nyriel/`) so launch
  latency is unaffected.

## The `nyriel absorb` command

```
nyriel absorb --check          # detection only: newer upstream tag? (cheap, cached)
nyriel absorb --gate           # fidelity gate: does T still reproduce HEAD?
nyriel absorb <tag>            # gate → rebrand → 3-way merge onto absorb/<tag>
                                  #   → parity report. STOPS before committing.
nyriel absorb <tag> --commit   # finalize once clean + parity READY (after review)
nyriel absorb --abort          # delete the absorb branch, restore — one-step rollback
```

`nyriel absorb <tag>` is **dry-by-default**: it produces the merged branch + parity
report, then stops and prints what's left (conflicts, re-added counts, divergence).
Committing is a separate explicit step. The base ref auto-reads from `UPSTREAM_BASE.md`
(no manual `--base`).

## Proactive detection

`detect.py` finds the latest upstream tag newer than the recorded base and caches it (TTL).
It hooks the **existing update-check/banner surface** — the same place that prints
"Up to date" — and, on git/source installs only, adds:

```
✶ upstream Hermes v2026.7.0 available to absorb · run `nyriel absorb v2026.7.0`
```

On pip/docker/managed installs (no `upstream` remote) detection stays silent.

## Guardrails (safety contract for self-modifying core)

Enforced by `driver.py`; non-negotiable:

- **Install guard** — runs only on a git/source checkout *with* the `upstream` remote;
  refuses elsewhere with a clear "here's what to do instead" message.
- **Branch isolation** — always `absorb/<tag>`; `main` is never touched.
- **Gate-before-merge** — fidelity gate must pass (0 stray tokens outside carve-outs). If
  the rename map drifted, refuse and ask for a human.
- **Parity-before-commit** — `--commit` is blocked unless parity is READY (0 conflict
  markers, 0 stray tokens).
- **Tests-before-commit** — runs the touched-core test slice; failures block the default
  commit (overridable only with an explicit flag).
- **Never auto-push** — commit is local; pushing is always a separate, explicit human
  action.
- **Refuse pre-release/RC tags.**
- **One-step rollback** — `--abort` deletes the branch and restores.
- **Audit trail** — tag, base, conflict list, and parity summary written into the commit
  message.

## The skill (agentic driving + conflict resolution)

A **repo-local** skill at `skills/software-development/absorb-upstream/SKILL.md` (distinct
from the operator's global WebUI absorb-upstream skill — that one targets the Hermes WebUI
/ Docker install; this one targets the Brain CLI source). It ships in the repo `skills/`
tree as the source of truth; the live Brain picks it up via the existing skills
install/sync into its skill path (`~/.hermes/skills`) — it is not magically present just by
existing in the repo.

It teaches Nyriel the loop:

1. `nyriel absorb --check`; if a newer tag exists, offer it.
2. `nyriel absorb <tag>`; read the parity report.
3. For each conflict: read base/ours/theirs, distinguish *our genuine divergence* from the
   *upstream change*, and resolve by re-applying our change where upstream relocated it
   (the WhatsApp `whatsapp_common.py` pattern from the v2026.6.19 absorb).
4. Run the touched-core tests.
5. **Present the resolved branch + summary and wait for human approval**, then `--commit`.
   Never push without explicit say-so.

The skill encodes the **genuine-divergence list** (glyph ✶, Brain Settings overlay,
versioned model name, the "Embrey The Creator" attribution) so Nyriel knows what to
preserve, and a **hard stop rule**: if the gate fails (rename map drifted) or a conflict
needs core-semantic judgment beyond mechanical re-application, STOP and hand to the human —
do not guess at core semantics.

## Testing

- Unit tests: `detect.py` (tag comparison + cache TTL), `driver.py` (gate pass/fail,
  clean-merge vs conflict paths, every install-guard refusal, `--abort` rollback),
  `parity_report.py` (anchored marker/stray detection, incl. the test-data false-positive
  case already fixed).
- **The fidelity gate becomes a CI regression test** on the rename map: if a future edit to
  `rename_map.py` breaks T, CI fails. (This is the test that keeps the harness trustworthy.)
- A dry-run integration test of `nyriel absorb --check` and `--gate`.
- Follows existing patterns: `tests/nyriel_cli/test_absorb_*.py`.

## Install-type behavior matrix

| Install type | detection | `nyriel absorb` | how they get core updates |
|---|---|---|---|
| git/source + `upstream` remote (maintainer) | shows offer | full capability | self-absorb, then publish |
| git/source, no `upstream` remote | silent | refuses, explains how to add the remote | `nyriel update` |
| pip / docker / managed | silent | refuses with guidance | `nyriel update` (published release) |

## Phasing

1. **Repackage + command** — move harness into `nyriel_cli/absorb/`, port `driver.py`,
   wire `nyriel absorb` (`--check`/`--gate`/`<tag>`/`--commit`/`--abort`) with all
   guardrails. Tests.
2. **Detection** — `detect.py` + banner/update-check integration + cache.
3. **Skill** — repo-local `absorb-upstream` skill with the agentic loop + genuine-divergence
   list + hard-stop rules.

## Out of scope

- Auto-push or auto-publish of a release (always a separate human action).
- Absorbing into pip/docker/managed installs (architecturally N/A).
- Changes to the existing `nyriel update` install-update behavior.
- Contributing the harness to the real upstream (it is fork-specific by nature).
- Fully autonomous, unattended absorbs with no approval gate (explicitly rejected for
  self-modifying core).
