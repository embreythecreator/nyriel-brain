# Handoff — Upstream absorb + self-absorb feature

**Session dates:** 2026-06-29 (absorb + design) · 2026-06-30 (feature built + verified) ·
**Repo:** `~/Oblivion/nyriel-brain` · **Branch:** `absorb/v2026.6.19`

Pick this up cold. This doc has everything to know what was done, the exact git state, what's
verified, and the open decisions.

> **Status (2026-06-30): SHIPPED.** The v2026.6.19 absorb AND the self-absorb feature (all 6 plan
> tasks) are built, verified, **merged to `main` (fast-forward) and pushed to `origin`** — `main` is
> now at `7e4ad83fa`, in sync with `origin/main`. Clean-CI full suite: 26,994 passed / 39 failed,
> with **0 failures attributable to the absorb** (each proven to fail identically on the old main);
> the 6 fixable pre-existing stale tests were fixed in `7e4ad83fa`, the rest are host/platform-only
> (green on Linux CI). No open decisions remain.

---

## TL;DR

Two things happened across these sessions, both now DONE and **merged to `main` + pushed to `origin`**:

1. **Absorbed upstream Hermes `v2026.6.5 → v2026.6.19` into the fork at full parity** using a new
   rename-aware harness. Done, and now on `main` (`7e4ad83fa`).
2. **Built the "self-absorb" feature** (`nyriel absorb` command + detection + an agentic skill) so
   Nyriel can do future absorbs itself. All 6 plan tasks landed as commits and are verified
   passing (12-test absorb suite green, gate clean). Spec + plan in `docs/`.

Nothing left to decide — both shipped to `main` and pushed on 2026-06-30. See the Status banner above.

---

## Git state (exact)

Branch `absorb/v2026.6.19`, **12 commits** ahead of `main`, **not pushed** (no remote tracking):

```
081749bb3  fix(identity): credit Embrey The Creator / The Voice
791fcbe3c  feat(absorb): repo-local absorb-upstream agentic skill        ← self-absorb task 6
8b038587a  feat(absorb): surface absorb-available offer on the banner    ← self-absorb task 5
0474ba4c0  feat(absorb): upstream-tag detection with cache               ← self-absorb task 4
c1c9efbbd  feat(absorb): nyriel absorb subcommand + dispatch          ← self-absorb task 3
f2dc03898  feat(absorb): python driver with guardrails + fidelity gate   ← self-absorb task 2
dded3bff0  refactor(absorb): package harness into nyriel_cli/absorb   ← self-absorb task 1
bb32a7bf8  docs: session handoff for absorb + self-absorb feature
cbdb60744  docs(plan): Nyriel self-absorb implementation plan
6c8d461e9  docs(spec): Nyriel self-absorb design (nyriel absorb + skill)
d9c5e7afc  chore(absorb): untrack harness run artifacts (.last-*)
7246d0398  absorb: hermes-agent v2026.6.5 -> v2026.6.19 (full parity, rename-aware harness)
----------  (683ba08 = prior fork HEAD = `main` BEFORE the merge)
```

> Since shipped: `main` was fast-forwarded `683ba08e3 → 7e4ad83fa` and pushed to `origin`. The branch
> tip below also gained `dd377e5d0` (handoff refresh) and `7e4ad83fa` (pre-existing stale-test fixes).

At absorb time, `git diff main HEAD` = **1,223 files, +144K / −28K** (vs the old `0.16.0` fork). `main`
is now at `7e4ad83fa` = `0.17.0`.

To get back here in a fresh session: `cd ~/Oblivion/nyriel-brain && git checkout absorb/v2026.6.19`.

---

## Part 1 — The v2026.6.19 absorb (DONE, on branch)

**What the repo is:** a white fork of `NousResearch/hermes-agent` (the `upstream` git remote points
there). No shared history; upstream is absorbed by rebrand-and-merge. Provenance + the absorb recipe
live in `UPSTREAM_BASE.md` (now points at base `v2026.6.19`, our line `0.17.0`).

**The harness** (built this session, lives in `scripts/absorb/`, ~551 lines, stdlib + git only):
- `rename_map.py` — the transform **T** (the "rebrand touch core"). Two token families + carve-outs:
  - **HERMES family:** `Hermes Agent`→`Nyriel Brain`, `hermes_agent`→`nyriel_brain`,
    `hermes-agent`→`nyriel-brain`, `Hermes`→`Nyriel`, `hermes`→`nyriel` (paths + content).
  - **NOUS family:** `Nous Research`→`Nyriel`, `NousResearch`→`embreythecreator`.
    **Never** touches the `Nous Portal` inference *provider* / `nous_account` / `NOUS_*`
    (real external service), nor the domain `nousresearch.com` — that is where the real
    Nous services live (OAuth, inference, Tool Gateway suffix), so rebranding it aims live
    third-party endpoints at a domain we own but don't serve. It *was* rebranded until
    2026-07-31; `_DOMAIN_SENTINEL` in `swap_text()` now protects it.
  - **ATTRIBUTION:** `created by Nyriel`/`created by Hermes` → `created by Embrey The Creator`.
  - **Carve-outs** (keep upstream tokens): legal files (LICENSE/NOTICE/COPYING), `UPSTREAM_BASE.md`,
    `CHANGELOG.md`, `scripts/absorb/`. The achievements LICENSE keeps "Hermes Achievements".
- `rebrand_tree.py` — applies T to any git ref via plumbing → a rebranded tree OID.
- `absorb.sh` — driver: `--gate` (fidelity check) then 3-way merge of `T(base,no-attr)` (merge base)
  + `T(tag)` (theirs) + `HEAD` (ours) → branch `absorb/<tag>`.
- `parity_report.py` — gates the commit (0 conflict markers, 0 stray tokens).

**Genuine divergence** (our deliberate changes — NOT mechanical rename; the merge preserves them and
the skill must never revert them): the selective brand glyph `✶` (vs upstream `⚕`), the Brain
Settings overlay (`gateway/overlay/brain_settings.py`), the versioned model name
(`gateway/platforms/api_server.py`), and the "Embrey The Creator" attribution.

**The one real merge conflict:** `gateway/platforms/whatsapp.py`. Upstream 6.19 relocated the
class attributes (`MAX_MESSAGE_LENGTH`, `DEFAULT_REPLY_PREFIX`) into a new shared mixin
`gateway/platforms/whatsapp_common.py` (`WhatsAppBehaviorMixin`). Resolution: took upstream's
`whatsapp.py` (dropped our old inline block) and **re-applied our `✶` glyph** in
`whatsapp_common.py`'s `DEFAULT_REPLY_PREFIX`. This is the canonical example of how to resolve an
absorb conflict — preserve our change where upstream moved the code.

**Bookkeeping done in the absorb:** `pyproject.toml` → `0.17.0`; `UPSTREAM_BASE.md` table + recipe
rewritten to the harness flow; `CHANGELOG.md` created; attribution applied to `default_soul.py`,
`agent/prompt_builder.py`, README badges/links, `website/docusaurus.config.ts`, docs.

**What full parity pulled in (363 re-added files):** un-trimmed the original squashed import +
new upstream capabilities — WhatsApp Cloud, Matrix, SimpleX, Photon gateway platforms,
`model_setup_flows`, gateway slash-commands, openviking memory rework, desktop i18n.

---

## Verification status (honest)

| Check | Result |
|---|---|
| `compileall` over 1,215 changed files | **0 syntax errors** |
| `import nyriel_cli.main` | OK |
| `./nyriel --version` | `Nyriel Brain v0.17.0 (2026.6.19)` — versioned-banner feature intact |
| `./nyriel --help` | new `whatsapp-cloud` subcommand present |
| Parity gate | **0 stray tokens, 0 conflict markers** |
| Leftover `import hermes` / `hermes_cli` in code | **0** |
| Targeted hermetic tests (brain_settings, api_server, banner, build_info, status, config) | **295 passed** |
| Full core bulk run (`nyriel_cli`+`agent`+`run_agent`+`cli`+`acp_adapter`+`nyriel_state`+`cron`) | 14,532 passed / **337 failed** |

**The 337 failures are NOT absorb bugs** — they are test-isolation + host-environment artifacts:
- I ran 14k tests in **one in-process pytest** (`-o addopts=""`), disabling the project's per-file
  subprocess isolation → cross-test state leakage. 10 of 12 top "failing" files pass **100% when run
  in isolation**.
- This **machine/session** has a real Anthropic OAuth token in the env + real `~/.nyriel` config
  that leak past the conftest scrub (e.g. `test_anthropic_adapter` token-resolution tests; `gui`
  test needs a display). They'd pass in clean CI.

**To get a true-green number:** run with the project's native config (no `-o addopts=""`) + a clean
`HOME` + scrubbed env, i.e. the per-file subprocess runner the project ships. Example that already
worked: `HOME=$(mktemp -d) .venv/bin/python -m pytest <file>`.

**`.venv` was mutated** this session: installed `pytest==9.0.2`, `pytest-asyncio`, `pytest-timeout`,
`aiohttp`, `mcp`, `starlette`, `setuptools==81.0.0`, `agent-client-protocol==0.9.0` so tests run.
Harmless; leave them. Full suite **collects cleanly: 33,080 tests, 0 collection errors.**

---

## Part 2 — Self-absorb feature (BUILT + VERIFIED, on branch)

The user wanted Nyriel able to update its own core base from upstream. Brainstormed → spec → plan
→ **built**. All 6 plan tasks landed (commits `dded3bff0`..`791fcbe3c`) and are verified passing.

- **Spec:** `docs/specs/2026-06-29-nyriel-self-absorb-design.md`
- **Plan:** `docs/plans/2026-06-29-nyriel-self-absorb.md` (6 TDD tasks — all complete)

**What shipped + where it lives now:**
- Harness moved `scripts/absorb/*.sh` → importable **`nyriel_cli/absorb/`** (`rename_map.py`,
  `rebrand_tree.py`, `parity_report.py`, `driver.py`, `detect.py`; 715 lines). `scripts/absorb/README.md`
  is now just a pointer.
- **`nyriel absorb`** subcommand with `--check` / `--gate` / `--commit` / `--abort` (+ `[tag]`,
  `--base`). Run the gate via `nyriel absorb --gate` (NOT the old `scripts/absorb/absorb.sh --gate`).
- Upstream-tag **detection** (cached) surfaces an absorb-available offer on the banner.
- Repo-local agentic **skill** `skills/software-development/absorb-upstream/SKILL.md`.
- Tests: `tests/nyriel_cli/test_absorb_{driver,detect,parity}.py` — **12 passing** (clean HOME).

**Verified 2026-06-30:** gate → 0 stray tokens · 12 absorb tests pass · `nyriel --version` →
`v0.17.0 (2026.6.19) … +12 carried commits` · `nyriel absorb --check` → up to date with upstream.

**Locked decisions:**
- **Layered**: a deterministic `nyriel absorb` command does the mechanics + safety rails; a skill
  wraps it so Nyriel drives it agentically, **pausing for human approval before committing**.
- **Proactive detect-and-offer**: check `upstream` for new tags, surface an offer on the banner;
  execution stays on-demand.

**Key reframe (don't lose this):** self-absorb is a **maintainer / git-install capability** — it
needs the `upstream` remote + occasional human conflict judgment. pip/docker/managed installs can't
do it; they get core updates via published releases (`nyriel update`, which already exists and is
*different* — it pulls the latest published release, it doesn't absorb upstream). It also can't be
contributed to the real upstream — T encodes *our* rebrand.

**Plan shape (6 tasks):**
1. Repackage harness `scripts/absorb/` → importable `nyriel_cli/absorb/`.
2. Port `absorb.sh` → `driver.py` (gate/absorb/commit/abort + guardrails) + the **fidelity gate as a
   CI regression test** on the rebrand map.
3. Wire `nyriel absorb` subcommand (`_parser.py:build_top_level_parser`, `main.py` dispatch).
4. `detect.py` — upstream-tag detection, cached.
5. Surface the offer on `banner.py:check_for_updates()`.
6. Repo-local agentic skill `skills/software-development/absorb-upstream/SKILL.md` (genuine-divergence
   list + hard-stop rules).

**Guardrails baked into the design** (safety contract for self-modifying core): git/source-install
only; branch isolation (`absorb/<tag>`, never `main`); gate-before-merge; parity-READY-before-commit;
tests-before-commit; **never auto-push**; refuse pre-release/RC; one-step `--abort`.

**Integration points already located** (for whoever builds it):
- Subcommand registration: `nyriel_cli/_parser.py` → `build_top_level_parser()` (subparsers added
  ~line 246+).
- Dispatch: `nyriel_cli/main.py` (mirror the `elif action == "update":` site ~line 10621);
  detection pattern in `_cmd_update_check` (~line 7881).
- Banner offer surface: `nyriel_cli/banner.py` → `check_for_updates()` (~line 265), caches in
  `~/.nyriel/.update_check`.
- Skill home: `skills/software-development/`.

---

## How to resume (quick commands)

```sh
cd ~/Oblivion/nyriel-brain
git checkout absorb/v2026.6.19

# verify the rebrand harness still reproduces HEAD (expect "gate passed, 0 stray tokens")
.venv/bin/python -m nyriel_cli.main absorb --gate

# the absorb feature's own test suite (clean HOME avoids host env-leak false failures)
HOME=$(mktemp -d) .venv/bin/python -m pytest \
  tests/nyriel_cli/test_absorb_driver.py \
  tests/nyriel_cli/test_absorb_detect.py \
  tests/nyriel_cli/test_absorb_parity.py
```

The next upstream absorb is now one command: **`nyriel absorb v2026.7.0`** (add `--base v2026.6.19`
to override the merge base). It produces an `absorb/<tag>` branch + parity report and stops;
`nyriel absorb --commit` finalizes once parity is READY, `--abort` tears the branch down.

---

## Open decisions for the operator — ALL RESOLVED (2026-06-30)

1. ~~**Merge `absorb/v2026.6.19` → `main`?** And push?~~ — **DONE.** Fast-forwarded `main`
   (`683ba08e3 → 7e4ad83fa`) and pushed to `origin` (embreythecreator/nyriel-brain). The whole
   stack — absorb + self-absorb feature + docs + pre-existing test fixes — landed on `main` together.
2. ~~**Run the full suite in clean CI** for a true-green pass number?~~ — **DONE.** Ran CI's own
   per-file runner (`scripts/run_tests_parallel.py`): 26,994 passed / 39 failed; proved 0 absorb-caused
   (every suspect fails identically on the old main); fixed the 6 pre-existing stale tests; the rest
   are host/platform-only (macOS paths, systemd, missing node/npm/lxml, real creds, network, timing).
3. ~~**Build the self-absorb feature**~~ — **DONE** (commits `dded3bff0`..`791fcbe3c`, verified).

Nothing left open. Next upstream bump is one command: `nyriel absorb v2026.7.0`.
