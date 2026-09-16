---
name: ditto
description: Rebuild another system's capability as native code.
version: 0.1.0
author: Embrey The Creator
license: MIT
platforms: [linux, macos, windows]
metadata:
  nyriel:
    tags: [ditto, capability, imitation, provenance, native, divergence]
    related_skills: [harvest, absorb-upstream, test-driven-development]
---

# Ditto Skill

Ditto copies the form and function of another creature while staying Ditto
underneath. When the operator says "ditto this," I reproduce a capability from an
unrelated system — Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw — in my own
idioms, my own config, my own state. No file crosses between trees. If code is
moving, this is the wrong move.

## When to Use

- A `harvest` returned a `GAP` and the operator wants it built
- Operator says "ditto <system>'s <capability>"
- Matching a behaviour observed in another agent, without adopting its source

Not this move:

- Pulling a release from my own upstream lineage → `absorb-upstream`
- Deciding whether a repo has anything worth taking → `harvest`
- Researching my own upgrades unprompted by a specific system → `evolve`
- Evolving a prompt, regex, or query against a fitness function → `darwinian-evolver`

## Prerequisites

- A completed `harvest` naming the gap and its provenance verdict
- Write access to a working branch; never build a ditto on `main`

## How to Run

Operator says: *"ditto <capability> from <system>"*. I confirm the gap is real,
build it natively on a branch, pin it if needed, and stop for review.

## Quick Reference

| step | gate |
|---|---|
| gap confirmed | a `harvest` verdict of `GAP`, not a hunch |
| implementation | native idioms; donor source never opened while writing |
| upstream-owned file touched | invariant added to `absorb/divergence.py` |
| done | tests green, operator reviews, human commits |

## Procedure

1. **Re-confirm the gap.** Search my own tree with `search_files` one more time
   before writing anything. Gaps evaporate on the second look more often than
   not; a ditto that duplicates an existing subsystem is worse than no ditto.
2. **Read the donor to understand the design, then close it.** Take the shape of
   the idea — the decision, the state it keeps, the failure it avoids. Do not
   keep the donor open while implementing, and never paste from it.
3. **Implement natively** with `write_file` and `patch`. My config lives in
   `cli-config.yaml`, my state in `state.db`, my tools in `tools/`. If the result
   reads like the donor's code, delete it and start from the design note.
4. **Default the capability off** when it changes existing behaviour, behind one
   config flag, so the untouched path stays identical.
5. **Pin it if it lands in a file upstream also owns.** Any wiring inside a file
   Hermes owns can be silently reverted by the next absorb. Add an invariant to
   `nyriel_cli/absorb/divergence.py` naming a substring that must survive.
   This is the seatbelt; the merge cannot reach READY without it.
6. **Leave one runnable check** — the smallest test that fails if the capability
   breaks. See `test-driven-development`.
7. **Stop for review.** A human merges. I never install a ditto into `main`.

## Pitfalls

- **Copying the file.** Several donor systems are themselves decompiled or
  regenerated from shipped binaries. A permissive licence does not clean that
  corpus, and my repos have public white-forks. Take the idea, never the file.
- **Vendoring the donor's tooling.** If a project publishes a useful delta about
  another system, read the delta; do not install the pipeline that produces it.
- **Skipping step 5.** The ditto works, ships, and quietly dies at the next
  absorb with a clean green merge. This is the exact failure the manifest exists
  to catch.
- **Building for parity.** Another agent having a feature is not a reason for me
  to have it. `DECLINE` is a real outcome.

## Verification

- `search_files` transcript shows the gap re-confirmed before implementation
- No donor file paths appear in the diff, and no donor code appears in the tree
- If an upstream-owned file changed, `divergence.check` covers the new wiring
- The new check fails when the capability is disabled and passes when enabled
