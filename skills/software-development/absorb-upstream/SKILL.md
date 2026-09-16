---
name: absorb-upstream
description: "Use when the operator asks Nyriel to absorb / pull in / rebase onto a new upstream Hermes release into its own core base. Drives `nyriel absorb`, resolves conflicts preserving our genuine divergence, runs tests, and STOPS for human approval before committing. Maintainer / git-install only."
version: 2.0.0
author: Embrey The Creator
license: MIT
platforms: [linux, macos, windows]
metadata:
  nyriel:
    tags: [absorb, upstream, hermes, maintainer, rebrand, merge, self-update]
    related_skills: [systematic-debugging, requesting-code-review, test-driven-development]
---

# Absorbing an upstream release into Nyriel's core

You are updating your OWN core base. Maintainer operation on a git/source checkout
with an `upstream` remote. If `nyriel absorb --check` reports a non-git or
no-upstream install, STOP: pip/docker installs use `nyriel update` instead.

## Loop

1. `nyriel absorb --status` — resume any in-flight absorb before starting a new one.
2. `nyriel absorb --check` — if a newer tag exists, confirm with the operator.
3. `nyriel absorb <tag>` — builds `absorb/<tag>`, prints parity AND an automatic
   verify battery result (compileall + targeted hermetic tests on the merged tree).
   Dry: it never touches `main`, never commits.
4. If the fidelity GATE fails or a "divergence manifest drifted" refusal appears:
   STOP — `rename_map.py` or `divergence.py` needs a human decision. Do not guess.
5. If parity shows conflicts: `nyriel absorb --continue` materializes the merge
   in a SIDECAR worktree (`<repo>-absorb/`) — the live tree (the running install)
   is never touched. Resolve each file THERE by taking upstream's structure and RE-APPLYING our
   divergence where the code moved (canonical example: v2026.6.19 moved
   `whatsapp.py` attrs into `whatsapp_common.py`; we kept upstream's mixin and
   re-applied the `✶` glyph there). Then `nyriel absorb --verify`.
6. Repeat --continue/--verify until parity is READY and verify is green.
7. Present the parity report + verify summary to the operator and STOP.
   **A human runs `nyriel absorb --commit`.** It re-checks every guardrail and
   auto-writes the bookkeeping (version bump, UPSTREAM_BASE.md, CHANGELOG.md).
8. `nyriel absorb --abort` tears the whole attempt down at any point.

## Hard stops (never negotiable)

- NEVER weaken, edit, or delete entries in `nyriel_cli/absorb/divergence.py` to
  make a merge pass — the manifest is the contract this skill exists to protect.
  Only the operator retires a divergence.
- NEVER pass `--skip-verify` unless the operator explicitly says so this session.
- NEVER push, NEVER touch `main`, NEVER run `--commit` yourself.
- Genuine divergence to preserve on sight: the `✶` glyph (upstream uses `⚕`), the
  Brain Settings overlay, the versioned model name in `api_server.py`, and the
  "Embrey The Creator / The Voice" attribution.
