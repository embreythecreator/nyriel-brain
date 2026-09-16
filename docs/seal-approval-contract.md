# Seal approval contract (`seal_confirm`)

**Landed:** 2026-08-06, WO-FACE/CHAT-1 P0-1.
**Owns:** `agent/seal_approval.py`, `agent/seal_mode.py`, the `seal_grant` table
in `nyriel_state.py`, and `_seal_confirm_block()` / `_seal_gate()` in
`gateway/platforms/api_server.py`.

## Why this exists

`space_action.js` in the Face has always validated `seal_tier` and then
**hard-refused** anything above T1:

```js
if (sealTier >= 2) {
  return { ok: false, refusalReason: "seal-tier-approval-not-available-v1" };
}
```

So the most rigorous permission model in the product had no surface and could
only ever say no — the agent could not perform a bounded write at all. This
contract is the approval channel that refusal was waiting on.

## The wire

`seal_confirm` and `seal_authorized` are **always-present keys** on every
chat-completion payload, alongside `plan` and `flesh_confirm`, on all four sites
in `api_server.py`:

| Site | Shape |
|---|---|
| `/plan` control, non-streaming | `web.json_response({... "seal_confirm": seal_block})` |
| `/plan` control, streaming | final chunk carries `seal_confirm` |
| agent run, non-streaming | `response_data["seal_confirm"]` |
| agent run, streaming | `finish_chunk["seal_confirm"]` |

**Presence is the contract, not value.** `null` clears the Face card. The Face
must key on `Object.hasOwn(payload, "seal_confirm")`, exactly as it does for
`plan` and `flesh_confirm`, so a history re-render can never leave a stale card.

### Payload

```json
{
  "grant_id": "9f2c…",
  "action_id": "act_01J…",
  "nonce": 4,
  "seal_tier": 2,
  "tier_label": "bounded write",
  "status": "pending",
  "verified_fields": {
    "verb": "write",
    "effect": "write ~/notes/plan.md",
    "scope": { "root": "~/notes" }
  },
  "prose": "I'll save the outline we discussed",
  "digest": "9595b8…",
  "short_digest": "9595b8",
  "expires_at": 1786000000.0,
  "ttl_seconds": 299,
  "approval_streak": 3,
  "seconds_since_last_approval": 40,
  "session_grant_count": 1
}
```

`tier_label` maps `1 → read`, `2 → bounded write`, `3 → approval-gated`.
All times are epoch floats. Never a formatted date string.

### The split the renderer must preserve

`verified_fields` are **digest-bound**; `prose` is **model-authored**. They carry
different trust and must not look the same. The renderer shows `verified_fields`
as primary content and `prose` as a demoted caption, so an attacker who
prompt-injects persuasive card copy is writing in the caption, not the ledger.
Collapsing the two would hand the model the ability to describe its own action
persuasively in the position the operator reads as verified.

## Approving

The Face posts back a **`(nonce, digest)` pair**. The store re-verifies both.

**Both, not just the digest.** The nonce is what makes a click that lands after a
newer request rendered inert *on the server*; the client's remount keying is UX,
not a gate. Verifying only the digest would let a stale click approve the new
action whenever the two happen to share a shape.

```python
store = SealApprovalStore()
rec, err = store.approve(session_id, nonce=n, digest=d, for_session=False)
rec, err = store.deny(session_id, nonce=n)
count   = store.revoke_session_grants(session_id)   # the one revocation point
```

`for_session=True` records a standing grant with a 1-hour TTL.

## Two invariants

**1. A session grant binds the action-shape digest, not the tool name.**
`action_digest(kind, code, seal_tier, scope)` hashes the literal code, the
declared tier, and the scope. Whitespace is collapsed so reformatting cannot mint
a new digest; nothing else is normalized, so one changed character is a different
action. Binding a *name* would let a later, differently-dangerous call ride
through on a shared name — "approve `file_write` once" must never authorize every
future `file_write`. `tests/test_seal_approval.py` pins this with an `rm -rf` case
that shares a verb with an approved write and still gates.

**2. Nothing is deleted, only terminated.** Expiry writes an `expired` ledger row
— the `NOT TAKEN` line — because a card that silently vanished is
indistinguishable from one that was never sent. Deny writes a symmetric refusal
record carrying the same digest, so the operator can later prove they said no to
*this exact* action. A store that only records approvals cannot do that.

## Anti-fatigue

`approval_streak` and `seconds_since_last_approval` are server-computed so the
card can say *"4th approval this session · last one 40s ago."* This makes
reflex-clicking visible in the moment it is happening. Nothing in the eight
products surveyed for WO-FACE/CHAT-1 does this.

## Storage

One JSON blob per session in `seal_grant` (`session_id` PK, `grant_json`,
`updated_at`), lazily created, mirroring `execution_policy`:

```json
{ "nonce": 4, "pending": {…}|null, "grants": [{"digest","expires_at"}],
  "ledger": [{"at","outcome","digest","verb"}], "approved_count": 3,
  "last_approved_at": 1786000000.0 }
```

`set_seal_grant` is a plain upsert with **no compare-and-swap**, unlike
`set_execution_policy`. The correctness guard here is the nonce check, and a lost
update fails **closed** — the stale nonce is refused and the action re-requested.
CAS would buy nothing a refusal already covers.

A locked DB on read **raises** so `_load` can retry. Swallowing it would drop a
pending grant, which presents to the operator as a card that silently vanished —
the exact failure this store exists to prevent.

## Operator grammar (`/seal`) — landed

`agent/seal_mode.py`, intercepted in `api_server.py` on the same exact-token
pattern as `/flesh`. The Face card posts these; the CLI accepts the same words.

| Command | Effect |
|---|---|
| `/seal status` | pending grant + standing-grant count |
| `/seal approve <nonce> <digest>` | one execution |
| `/seal session <nonce> <digest>` | every action of this exact shape, 1h |
| `/seal deny <nonce>` | refuse; writes a durable refusal record |
| `/seal revoke` | drop all standing grants — the one revocation point |
| `/seal ledger` | recent approve/deny/expire outcomes |

`approve` and `deny` return a **`rewritten_message`**, not a plain reply, so the
agent learns the outcome and either proceeds with exactly the bound action or
stops without retrying. `status`/`ledger` are readable while the agent is busy;
the mutating verbs are not.

## The await path — LANDED

`space_action.js` no longer hard-refuses tier >= 2. Closing that required a
decision about a **trust boundary**, recorded here because getting it wrong is
the one failure this whole WO exists to prevent.

**The trap:** stamping a `grant_token` into the `space-action` envelope and
having the Face execute when it sees one. The envelope is *model-authored text*,
so the model could simply write its own token — the model self-approves. Any
scheme where the Face trusts a field inside the block is broken by construction.

**The decision: the Brain gates at emission, not reactively on a Face refusal.**
Reacting to a refusal would make the Face's refusal load-bearing control flow,
inverting Brain-as-model, and would digest a payload that has already
round-tripped through a renderer. Emission-time also deletes the
refuse -> request -> re-execute round trip, leaving a clean three-way
execute / await / refuse.

`gate_space_actions(session_id, final_text)` runs on the Brain's own final
response — code the model does not execute:

1. Parse the fenced `space-action` blocks out of the text.
2. T1 is authorized outright. For tier >= 2, compute `action_digest` from the
   block's own kind, code, tier and scope.
3. Live grant -> the action id goes on the payload as **`seal_authorized`**.
   No grant -> open a pending grant; `seal_confirm` carries the card.

`seal_authorized` is a **fifth always-present payload key**, beside `plan`,
`flesh_confirm` and `seal_confirm`, on all four `api_server.py` sites. It is
computed by the gate, so the model cannot forge it: reuse an approved id with
different code and the digest changes, so the id is not listed.

`_seal_gate` MUST run before `_seal_confirm_block` on the same payload — it is
what opens the grant that block then reports. It returns `[]` on any failure, so
the gate fails closed.

Face side, `validateSpaceActionEnvelope(env, authorizedIds)` gains a third
outcome, `{ok: false, awaiting: true}`. An awaiting action executes nothing and
**sends no result envelope** — the card is already on screen and a refusal
result would tell the agent the operator said no before they were asked. The
admin agent view passes no list and keeps refusing outright, since it has no
card to approve on.

### One-shot grants

`approve(for_session=False)` leaves a **single-use grant** behind, not just a
ledger row. Without it the loop never closes: the grant's `rewritten_message`
tells the agent to proceed, the agent re-emits the same block, and the gate —
finding no live grant — opens a fresh card for the action the operator just
approved. `request()` spends the once-grant on match. `status_summary` and
`session_grant_count` exclude once-grants: they are in flight, not standing.

### One card per message

The gate opens at most one pending grant per message. A second unapproved action
would supersede the first before the operator ever saw it, and superseding a
card mid-read is exactly the silent disappearance this store exists to prevent.
The rest wait for the turn after.

### verified_fields are derived, never model-supplied

`verb` and `effect` are computed from the digest-bound material (`kind`, and the
whitespace-collapsed `code`), not read from naming fields in the envelope. The
card presents them as verified, so they have to actually be projections of what
the digest binds — otherwise the model gets to write in the ledger column. The
envelope's optional `note` becomes `prose`, the demoted caption.

## Verification

`tests/test_seal_approval.py` — 16 checks, no pytest required
(`python tests/test_seal_approval.py`). Covers digest binding, the nonce and
digest refusals, expiry recording, shape-not-name grants, the deny record,
revocation, the streak counter, and the emission gate: T1-through/T2-withheld,
the approve-once round trip and its spend, standing grants across repeats, the
one-card-per-message rule, and derived verified fields.

Face side: `tests/space_action_test.mjs` pins the await outcome and the
authorized-id membership test.
