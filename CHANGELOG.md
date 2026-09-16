# Changelog

Nyriel Brain follows an independent version line (see `UPSTREAM_BASE.md`).

## [Unreleased]

### Absorbed
- **hermes-agent `v2026.6.5` → `v2026.6.19`** (full parity) via the new rename-aware
  absorb harness (`scripts/absorb/`). The upstream diff was ~1,700 files — too large to
  hand-port — so the Hermes→Nyriel / Nous→Nyriel rebrand was codified as a
  reproducible transform and folded in with a 3-way merge. Un-trimmed the original
  squashed import to match upstream fully. New upstream capabilities now present:
  WhatsApp Cloud / Matrix / SimpleX / Photon gateway platforms, `model_setup_flows`,
  gateway slash-commands, and the openviking memory rework.

### Added
- `scripts/absorb/` — rename-aware upstream absorb harness (`rename_map.py`,
  `rebrand_tree.py`, `absorb.sh`, `parity_report.py`) with a transform-fidelity gate.

### Changed
- Default identity attribution is now **"created by Embrey The Creator"** (was "created
  by Nyriel"), enforced on every absorb via the harness attribution rule.
- Bumped independent line to `0.17.0`.

### Notes / gotchas (for future absorbs)
- The WhatsApp reply-prefix moved upstream into `gateway/platforms/whatsapp_common.py`
  (`WhatsAppBehaviorMixin`); our brand glyph (✶) genuine-divergence had to be re-applied
  there after the merge relocated it out of `whatsapp.py`.
- The brand glyph swap (⚕→✶) is selective and intentionally NOT part of the mechanical
  rename transform — it is genuine divergence preserved by the merge.

## [0.23.0] — 2026-09-01

### Absorbed
- **hermes-agent `v2026.8.16` → `v2026.8.31`** (full parity): re-added 1745, removed 231, divergence 291 files.

## [0.22.0] — 2026-08-17

### Absorbed
- **hermes-agent `v2026.8.3` → `v2026.8.16`** (full parity): re-added 1147, removed 171, divergence 267 files.

## [0.21.0] — 2026-08-05

### Absorbed
- **hermes-agent `v2026.7.20` → `v2026.8.3`** (full parity): re-added 1782, removed 80, divergence 231 files.

## [0.20.0] — 2026-07-22

### Absorbed
- **hermes-agent `v2026.7.1` → `v2026.7.20`** (full parity): re-added 921, removed 122, divergence 123 files.

## [0.19.0] — 2026-07-02

### Absorbed
- **hermes-agent `v2026.6.19` → `v2026.7.1`** (full parity): re-added 752, removed 80, divergence 79 files.
