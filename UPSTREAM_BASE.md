# Upstream base

This is a **white fork** of Hermes Agent (NousResearch/hermes-agent), imported as a
squashed snapshot — there is **no shared git history with upstream**, so `git merge`
does not work directly. Upstream is absorbed with the **rename-aware harness** in
`seraphiel_cli/absorb/`, driven by the **`seraphiel absorb`** command — it rebrands an
upstream tag into our namespace and 3-way merges it onto our tree. Full developer
guide: [`docs/absorb-harness.md`](docs/absorb-harness.md).

| | value |
|---|---|
| Current tree corresponds to | **Hermes v0.20.0** (upstream pyproject at `v2026.8.3`; our independent line is 0.21.0) |
| Upstream tag | `v2026.8.31` |
| Upstream commit | `29112bef09` |
| Our version (independent line) | `0.23.0` (pyproject.toml — source of truth) |

`upstream` remote is configured locally (`NousResearch/hermes-agent`), pushed nowhere,
invisible to public clones of this repo.

## Make our own update
Commit to `main`, bump the patch in `pyproject.toml` (`0.17.0` → `0.17.1`). Done.

## Absorb a Hermes update (`seraphiel absorb`)
The transform (Hermes→Seraphiel, Nous Research→Seraphiel/Embrey, plus carve-outs) is
codified in `seraphiel_cli/absorb/rename_map.py` — no more hand-porting.

Conflicts are resolved in place: `seraphiel absorb --continue` materializes the
merge into the working tree, and `seraphiel absorb --verify` snapshots your
resolutions and re-runs parity + the verify battery. Bookkeeping is automatic:
`seraphiel absorb --commit` bumps the minor version in `pyproject.toml`, rewrites
this table, and prepends the `CHANGELOG.md` entry — no hand edits.

**Base ref bookkeeping:** the merge base for the 3-way is the *previously absorbed*
upstream tag, read automatically from the **"Upstream tag" row of the table above**
(`v2026.6.19` as of this absorb) — so just keep that row current and `--base` is rarely
needed (override only to re-base). The original fork base was `f2a5cd1` (squashed import
of `v2026.6.5`); the v2026.6.5→v2026.6.19 absorb un-trimmed the import to full parity, so
future absorbs diff cleanly against the full upstream tree.

## How the harness works (so the next maintainer trusts it)
- `rename_map.py` — the transform T: HERMES + NOUS token families with per-family
  carve-outs (legal files keep upstream attribution; the achievements LICENSE keeps its
  "Hermes Achievements" line; the "Nous Portal" inference *provider* is never rebranded),
  plus the `created by … → Embrey The Creator` attribution rule and the path swap.
- `rebrand_tree.py` — applies T to any git ref via plumbing, emitting a rebranded tree.
- `driver.py` — builds BASE=T(base,no-attr), THEIRS=T(tag), OURS=HEAD and 3-way merges so
  conflicts collapse to genuine divergence; enforces the guardrails (git/source-install
  only, branch isolation, gate-before-merge, READY-before-commit, never pushes). `seraphiel
  absorb --gate` runs the fidelity check; `detect.py` powers `--check` + the banner offer.
- `parity_report.py` — classifies the merged tree (re-added / divergence / conflicts /
  stray tokens) and gates the commit.
- **Genuine divergence** deliberately NOT in T (re-applied by hand during conflict
  resolution if upstream moves it): the selective brand glyph swap (⚕→✶), the boot
  banner/figlet wordmark, the Brain Settings overlay, and the versioned model name.

<!-- ponytail: the harness replaced the by-hand diff port once the upstream diff grew to
     ~1,700 files. If a future upstream restructures the rename surface wholesale,
     re-derive rename_map.py from the rebrand commits rather than patching token-by-token. -->
