"""Transform T: the Hermes/Nous -> Nyriel/Embrey rebrand, as a reproducible
function.

Single source of truth for the rename-aware absorb harness. The fork rebrands two
token families, with different carve-outs:

  HERMES family  (product):  Hermes Agent -> Nyriel Brain, Hermes -> Nyriel,
                 hermes -> nyriel, hermes-agent -> nyriel-brain.
                 Applied everywhere EXCEPT achievements/LICENSE, which keeps its
                 upstream "Hermes Achievements contributors" copyright line.

  NOUS family    (vendor/brand/org):  Nous Research -> Nyriel,
                 NousResearch -> embreythecreator. Applied everywhere EXCEPT
                 legal files (LICENSE / NOTICE / COPYING), which keep upstream
                 attribution (mirroring how the top-level LICENSE keeps "Nous
                 Research").
                 NOTE: the "Nous Portal" inference *provider* and its
                 nous_account / nous_subscription / NOUS_* identifiers are real
                 external service names and are deliberately NOT rebranded — the
                 rules only match the brand/org forms, never bare "Nous".

                 The DOMAIN `nousresearch.com` is likewise NOT rebranded, and is
                 actively protected (see _DOMAIN_SENTINEL). It is where the real
                 Nous services live — portal.nousresearch.com (OAuth signup),
                 inference-api.nousresearch.com (models), and the TOOL_GATEWAY
                 suffix that derives firecrawl-gateway.* / openai-audio-gateway.*
                 — so rewriting it aims live third-party endpoints at a domain we
                 own but do not serve, silently killing the whole Nous Portal
                 subscription path. It WAS rebranded until 2026-07-31; the fix is
                 the carve-out here plus a one-time revert of the functional refs
                 in the tree. Attribution links (README badges, docs "built by",
                 site footer) deliberately still point at our own domain and are
                 left as genuine divergence for the 3-way merge to preserve.

  ATTRIBUTION    self-credit: "created by Nyriel"/"created by Hermes" ->
                 "created by Embrey The Creator" (operator requirement, enforced
                 on every absorb).

PATH swap reuses the HERMES path tokens (case-aware); 643/643 hermes paths were
renamed and 0 kept, so the path swap is total and needs no path carve-outs. (No
"nous" path component is rebranded — nous_account.py etc. stay.)

Deliberately NOT in T (scoped Nyriel design changes = genuine divergence the
3-way merge preserves, NOT mechanical rebrand): the brand glyph swap (selective)
and the boot banner / figlet wordmark. The transform-fidelity gate proves these
plus the security-guidance NOTICE per-file hand-edits are the only residuals.
"""

from __future__ import annotations

import posixpath

# --- HERMES family: product rename (longest match first) ---
HERMES_RULES: list[tuple[str, str]] = [
    # The product's "agent" suffix becomes "brain" (Nyriel Brain), so the
    # compound forms must be matched before the bare token.
    ("Hermes Agent", "Nyriel Brain"),   # display wordmark
    ("hermes_agent", "nyriel_brain"),   # python package / identifiers
    ("hermes-agent", "nyriel-brain"),   # pip / nix / npm / slug
    ("HERMES", "NYRIEL"),
    ("Hermes", "Nyriel"),
    ("hermes", "nyriel"),
]
# NOTE: the capitalised hyphen form "Hermes-Agent" (HTTP User-Agent product token)
# and the lowercased HuggingFace model-id prefix are hand-edited inconsistently in
# the fork (-> "Nyriel Brain" vs "nyriel-Agent"); not rule-derivable, so they
# are left as genuine divergence for the merge rather than special-cased here.

# --- NOUS family: vendor/brand/org rename (longest/most-specific first) ---
# Order matters: do the CamelCase org before the spaced brand so each is consumed
# by its most specific rule.
NOUS_RULES: list[tuple[str, str]] = [
    ("NousResearch", "embreythecreator"),
    ("Nous Research", "Nyriel"),
    ("nousresearch", "embreythecreator"),   # any leftover lowercase handle
]

# The real Nous service domain. Dropping it from NOUS_RULES is NOT sufficient —
# the trailing lowercase-handle rule above would still rewrite the host half of
# "nousresearch.com" and reintroduce the exact break. So it is swapped out for a
# sentinel before the family runs and restored after. Sentinel uses \x01 (never
# \x00, which would trip looks_binary) and never escapes this function.
_NOUS_DOMAIN = "nousresearch.com"
_DOMAIN_SENTINEL = "\x01__NOUS_DOMAIN__\x01"
# Staging/preview deployments (nas-pr-NNN.nousresearch.wtf) are the same class
# of real third-party surface as the prod domain — protect them identically.
_NOUS_DOMAIN_WTF = "nousresearch.wtf"
_DOMAIN_SENTINEL_WTF = "\x01__NOUS_DOMAIN_WTF__\x01"

# --- Attribution: enforce operator self-credit (applied after the families) ---
ATTRIBUTION_RULES: list[tuple[str, str]] = [
    ("created by Nyriel", "created by Embrey The Creator / The Voice"),
    ("created by Hermes", "created by Embrey The Creator / The Voice"),
]

# --- Path rules: case-aware HERMES path tokens, total swap ---
# Same agent->brain slug rule as content (nix/, homebrew, skill dirs, egg-info).
PATH_RULES: list[tuple[str, str]] = [
    ("hermes_agent", "nyriel_brain"),
    ("hermes-agent", "nyriel-brain"),
    ("HERMES", "NYRIEL"),
    ("Hermes", "Nyriel"),
    ("hermes", "nyriel"),
]

# Legal files keep upstream attribution -> NOUS family is skipped in them.
_LEGAL_BASENAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "NOTICE",
                    "NOTICE.txt", "NOTICE.md", "COPYING")

# Files our line authors itself / that reference upstream by name on purpose.
# They never originate from the upstream tree, so T never processes them during an
# absorb; the hard skip just makes a self-sweep idempotent.
SELF_AUTHORED_CARVEOUTS: tuple[str, ...] = (
    "UPSTREAM_BASE.md",
)


def swap_path(path: str) -> str:
    """Apply the case-aware path rebrand to a single tree path."""
    for find, repl in PATH_RULES:
        path = path.replace(find, repl)
    return path


def _is_legal_file(nyriel_path: str) -> bool:
    return posixpath.basename(nyriel_path) in _LEGAL_BASENAMES


def _is_hermes_carveout(nyriel_path: str) -> bool:
    # achievements/LICENSE keeps "Hermes Achievements contributors"
    return nyriel_path.endswith("achievements/LICENSE")


def is_self_authored(nyriel_path: str) -> bool:
    return any(nyriel_path == s or nyriel_path.endswith("/" + s)
               for s in SELF_AUTHORED_CARVEOUTS)


def swap_text(text: str, nyriel_path: str, attribution: bool = True) -> str:
    """Apply the rebrand families + attribution to a text blob body.

    Carve-outs are per token-family and keyed on the (already path-swapped) path:
      - HERMES family skipped for achievements/LICENSE
      - NOUS family skipped for legal files (LICENSE/NOTICE/COPYING)
      - `nousresearch.com` preserved everywhere (real third-party service domain)

    `attribution=False` runs pure rename only (used by the fidelity gate, which
    measures rename faithfulness against a HEAD that predates the attribution fix).
    """
    if not _is_hermes_carveout(nyriel_path):
        for find, repl in HERMES_RULES:
            text = text.replace(find, repl)
    if not _is_legal_file(nyriel_path):
        text = text.replace(_NOUS_DOMAIN, _DOMAIN_SENTINEL)
        text = text.replace(_NOUS_DOMAIN_WTF, _DOMAIN_SENTINEL_WTF)
        for find, repl in NOUS_RULES:
            text = text.replace(find, repl)
        text = text.replace(_DOMAIN_SENTINEL, _NOUS_DOMAIN)
        text = text.replace(_DOMAIN_SENTINEL_WTF, _NOUS_DOMAIN_WTF)
    if attribution:
        for find, repl in ATTRIBUTION_RULES:
            text = text.replace(find, repl)
    return text


def looks_binary(data: bytes) -> bool:
    """Cheap binary sniff: a NUL byte in the first 8KiB marks a binary blob."""
    return b"\x00" in data[:8192]
