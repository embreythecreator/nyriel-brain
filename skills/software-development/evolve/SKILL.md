---
name: evolve
description: Research my own upgrade and pre-package it for approval.
version: 0.1.0
author: Embrey The Creator
license: MIT
platforms: [macos, linux]
metadata:
  nyriel:
    tags: [evolve, self-update, research, proposal, review-gate, approval]
    related_skills: [harvest, ditto, self-update, absorb-upstream]
---

# Evolve Skill

Evolve is the long move: I research what I should become, then hand back a
pre-packaged upgrade for approval. It is never one shot and it never ends with
me installing anything. The deliverable is a branch plus a proposal that two
reviewers — an independent model and the operator — must pass before a human
merges it.

## When to Use

- Operator says "evolve" with or without a direction
- A standing question of what I lack that comparable systems have
- After several `harvest` runs have accumulated `GAP` items worth batching

Not this move:

- One named repo to judge → `harvest`
- One named capability to build → `ditto`
- A new release from my own upstream lineage → `self-update` / `absorb-upstream`
- Optimizing a prompt or query against a fitness function → `darwinian-evolver`

## Prerequisites

- Git checkout with an `upstream` remote; pip and docker installs cannot evolve
- Network for `web_search` and `web_extract`
- `delegate_task` reachable with an OpenRouter provider for the reviewer gate

## How to Run

Operator says: *"evolve"*, optionally with a direction. I run stages 1-5 and
stop at the reviewer gate. The operator resumes me for stages 6-7. Each stage
ends in an artifact, so an interrupted evolve resumes rather than restarts.

## Quick Reference

| stage | artifact | who unblocks it |
|---|---|---|
| 1 scope | direction statement | operator |
| 2 self-inventory | what I already am | — |
| 3 external sweep | dated findings | — |
| 4 gap set | gaps + declines | — |
| 5 pre-package | branch + proposal | — |
| 6 reviewer gate | Fable 5 verdict | independent model |
| 7 approval | merge | human only |

## Procedure

1. **Scope.** State the direction in one sentence and get the operator to
   confirm it. An unscoped evolve returns a shopping list nobody wanted.
2. **Inventory myself first.** Enumerate my own tools, skills, agent loop and
   CLI surface with `search_files` before looking outward. I cannot judge a gap
   without knowing what is already in `tools/`, `agent/`, `skills/` and
   `nyriel_cli/`. This stage is why most candidate gaps die.
3. **Sweep outward, bounded by recency.** `web_search` and `web_extract` against
   comparable agent systems and their release notes. State the window explicitly
   — default the last 30 days — and date every finding. An undated finding is a
   rumor. Run `harvest` per repo rather than judging any repo from its README.
4. **Produce the gap set with its declines.** Every gap names the search that
   came back empty; every decline names why, so it is not re-proposed next
   evolve. The decline list is as much of the deliverable as the gap list.
5. **Pre-package, do not install.** One branch, one proposal document, code
   built per `ditto` for each accepted gap, defaulted off. Nothing touches
   `main`. Nothing is installed. If a gap needs more research than the evolve
   can carry, it is written down as a follow-up, not half-built.
6. **Reviewer gate.** Send the proposal and diff to an independent model with
   `delegate_task(model='anthropic/claude-fable-5', provider='openrouter')`,
   asked to refute: is this a real gap, is the implementation native, does it
   duplicate something already in tree, is the provenance clean. A refused item
   drops out of the batch. I do not review my own evolve.
7. **Human approval.** Present the surviving batch, the reviewer verdict and the
   decline list, then STOP. A human merges. Approval of one evolve is not
   approval of the next.

## Pitfalls

- **Self-installing.** The move ends at a reviewed branch. Anything that writes
  to `main`, restarts the running install, or edits my live config has broken
  the gate the whole skill exists to enforce.
- **Skipping stage 2.** Outward research before self-inventory produces gaps I
  already filled, and it is the most expensive mistake here.
- **Batching too wide.** A twelve-item evolve gets rejected whole. Three
  defensible items beat a list.
- **Reviewing my own work.** If the reviewer gate runs on my own model with my
  own context, it is not independent and the verdict is worthless.
- **Undated research.** Agent systems change weekly; a finding without a date
  cannot be judged stale.

## Verification

- The proposal names its recency window and dates every external finding
- `git status` on `main` is clean; the branch is unmerged and uninstalled
- The reviewer verdict is recorded verbatim, including refusals
- Declines are written down with reasons
- The final message ends in a STOP for human approval, not a merge
