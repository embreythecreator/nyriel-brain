---
name: harvest
description: Inspect a foreign repo and report what is worth taking.
version: 0.1.0
author: Embrey The Creator
license: MIT
platforms: [linux, macos, windows]
metadata:
  nyriel:
    tags: [harvest, inspection, capability, verdict, provenance]
    related_skills: [ditto, absorb-upstream, requesting-code-review]
---

# Harvest Skill

The operator points at a repository and says "harvest this." I inspect what is
actually in the tree, diff it against what I already have, and return a verdict.
I do not write code, open branches, or install anything during a harvest — the
output is a report, and "nothing worth taking" is the most common correct answer.

## When to Use

- Operator names a repo or URL and says harvest, inspect, or "anything in this for us"
- Before any `ditto`, to establish there is a real gap worth reproducing
- Sizing up a project the operator read about and wants judged

Not this move:

- Pulling a new release from my own upstream lineage → `absorb-upstream`
- Reproducing a capability I decided to take → `ditto`
- Researching an upgrade to myself → `evolve`

## Prerequisites

- Network access for remote repos (`web_search`, `web_extract`)
- For a local clone, `search_files` and `read_file` over the checkout

## How to Run

Operator says: *"harvest <repo>"*. I run the procedure below and return the
verdict block. No approval gate — a harvest changes nothing.

## Quick Reference

| verdict | meaning | what follows |
|---|---|---|
| `ALREADY HAVE` | capability exists in my tree | nothing; say where it lives |
| `GAP` | genuinely absent and worth having | propose a `ditto` |
| `DECLINE` | absent, not worth having | record why, so it is not re-proposed |
| `TAINTED` | useful but provenance is unsafe | idea only, never the file |

## Procedure

1. **Read the tree, not the README.** Enumerate real source paths with
   `search_files target='files'`. Marketing counts capabilities the code does
   not have; star counts measure attention, not substance. Note the actual line
   and file count of the runtime, excluding vendored deps and archives.
2. **Check provenance before anything else.** If the corpus was decompiled,
   reverse-engineered, or LLM-regenerated from a shipped binary, the verdict for
   every file in it is `TAINTED` regardless of the stated licence. A permissive
   licence on the surface does not clean the corpus underneath.
3. **List the foreign capability surface** — tools, transports, permission
   model, session and context handling, agent spawning, scheduling.
4. **Diff against myself, capability by capability.** Search `tools/`, `agent/`,
   `skills/`, `nyriel_cli/` with `search_files` before believing any item is
   missing. I am a large fork with LSP, tool search, native compaction, cron,
   worktrees, sandboxing, MCP with OAuth, subagents, plugins and skills already
   in tree. **Assume `ALREADY HAVE` and make the repo prove otherwise.**
5. **Assign one verdict per capability.** Every `GAP` must name the file I
   searched and did not find it in — an unsearched gap is a guess.
6. **Return the verdict block**, `GAP` items first, then `DECLINE` with reasons.

## Pitfalls

- **Reporting the feature list instead of the diff.** A list of what the repo
  does is not a harvest. The deliverable is what it has that I do not.
- **Skipping step 4 to save time.** This is the step the whole move exists for.
  A harvest returning many gaps almost always skipped it.
- **Treating an MIT badge as a provenance answer.** See step 2.
- **Drifting into implementation.** The moment I write code, this stopped being
  a harvest and became an unapproved `ditto`.

## Verification

- Every `GAP` names the paths searched and the search that came back empty
- Provenance is stated explicitly, even when clean
- No files were created or modified in my own tree
