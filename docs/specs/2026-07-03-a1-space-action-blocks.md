# WO-A1 · Space Action Blocks — Brain→Face executor protocol

Status: SHIPPED v1 (2026-07-05) · Owner: Embrey The Creator / The Voice
Landed: Brain-side ingestion in `46c850580` (rode along with the Model Council
commit; gateway/platforms/api_server.py + tests) · Face-side executor in
nyriel-face `0eef06a` (space_action.js, both agent surfaces, brain_chat
header forwarding, operator contract v2).
Work order: `sys_brain_hermes_spine_seating` — seat the Brain as sole agent loop.

## Problem

Face (nyriel-face) today runs a full in-app agent loop: it assembles prompts,
streams a completion (default: the Brain via `/api/brain_chat`), parses
`_____javascript` blocks out of assistant text, executes them with `AsyncFunction`
against the `space.*` API, appends execution output as a user message, and loops.
Even with the Brain as the model backend, **the loop, retries, compaction, and
long-term state live in Face** — the R5 split-brain risk the work order kills.

Target: the Brain owns the loop. Face is a renderer with execution hands.

## Design

### 1. Wire format — the `space-action` block

The Brain emits actions inside its assistant output as fenced blocks, the same
extraction pattern the gateway already applies to `MEDIA:` paths
(`gateway/stream_consumer.py`):

````
```space-action
{
  "v": 1,
  "id": "act_<ulid>",
  "kind": "js",
  "payload": { "code": "return await space.spaces.listSpaces()" },
  "timeout_ms": 30000,
  "seal_tier": 1
}
```
````

- `kind`: v1 ships **`js` only** — capability parity with today's
  `_____javascript` surface, since Face's whole tool surface (`space.current`,
  `space.spaces`, `space.browser.*`) is already a JS API. Structured kinds
  (`browser.navigate`, `widget.update`, …) can be added later without changing
  the envelope; they are sugar over the same executor.
- `id` is the join key for telemetry and approvals.
- `seal_tier` is declared by the Brain and **re-derived by Face**; Face refuses
  any block whose declared tier is lower than its own classification.

### 2. Transport — v1 rides the existing brain_chat turn loop

No new socket. Face already initiates `POST /api/brain_chat →
${BRAIN_BASE_URL}/v1/chat/completions` with Ward auth attached server-side.

1. Face sends the user message (or an action result — see below).
2. Brain streams the assistant reply; Face renders text and extracts
   `space-action` blocks.
3. Face validates, (if required) approval-gates, executes, and sends the
   **result envelope** back as the next turn message with
   `x-nyriel-turn-type: action-result` — it is *input to the Brain's loop*,
   not a user utterance.
4. Brain decides: continue (more actions), or finish the turn.

Face-side, the old `runConversationLoop` collapses to: render → execute →
report. **No prompt assembly, no compaction, no retries, no model selection**
(those are Brain-loop property; `BRAIN_ONLY=true` from WO-A1 step 1 already
locks the provider surface).

v2 (not in scope now): a `space` platform adapter on the gateway
(`PlatformRegistry` / `BasePlatformAdapter` — `connect/disconnect/send/
get_chat_info` + `send_exec_approval`) over a persistent WebSocket, giving the
Brain unsolicited push (heartbeat delivery per WO-A3/C4). The envelope is
transport-agnostic by design so v1 blocks replay unchanged over v2.

### 3. Result envelope = Limb Rail telemetry

```json
{
  "v": 1,
  "action_id": "act_<ulid>",
  "status": "ok | error | refused | timeout",
  "started_at": "2026-07-03T12:00:00Z",
  "duration_ms": 412,
  "result": "<JSON-serialized return value, truncated per budget>",
  "console": ["..."],
  "error": null,
  "refusal_reason": null,
  "face": { "app": "nyriel-void", "version": "…", "session": "<ward-session>" }
}
```

Every executed block produces exactly one envelope; the Brain records it in
Limb Rail keyed by `action_id`. `refused` covers Seal denial, allowlist
rejection, and tier mismatch — refusals are telemetry, not errors.

### 4. Security (WO-D1 alignment)

- **Ward session binding**: result envelopes are only accepted on the same
  Ward-authenticated session that received the action block.
- **Executor allowlist**: Face validates blocks with the existing
  `execution.js` validation path, extended with a per-`kind` allowlist and the
  tier re-derivation above. Face never executes blocks found in *rendered
  external content* (web pages, notebook content) — only blocks parsed from the
  Brain's authenticated stream. Delimiter discipline per WO-D1.
- **Seal tiers**: tier ≥2 actions require the gateway approval flow
  (`send_exec_approval` pattern); tier-3 (irreversible) always interactive.

### 5. Face demotion phases (kill-condition ledger)

| Phase | What moves | State after |
|-------|-----------|-------------|
| A1.1 ✅ | `BRAIN_ONLY` flag locks both chat surfaces to `/api/brain_chat` | No alternate reasoning providers |
| A1.2 ✅ | This protocol: executor + result envelopes; `runConversationLoop` → execute-and-report bridge (onscreen + admin) | Loop, retries, compaction are Brain-side |
| A1.3 ✅ | Prompt ownership: Face stops injecting system prompts / skills / promptinclude / personality / memory files; operator contract + protocol + space.* brief now Brain-side platform policy (`gateway/platforms/face_policy.py`, keyed on `vessel:nyriel:face:*` / `nyriel-face-*`; idempotent for old clients). Face still forwards the user's own custom instructions. | Brain owns context |
| A1.4 ✅ | State ownership: no local history replay — brain-only requests carry only the current turn; continuity via `X-Nyriel-Session-Id`; Face history is UI-render-only; memory includes no longer reach requests | Kill condition met: no long-term model-facing state or independent reasoning loop in Face (face `3f356a4` + brain `4a77a1e9f`, 2026-07-05) |

## v1 decisions (locked 2026-07-05, shipped as proposed)

1. Streaming actions: **end-of-message** — blocks execute only after the full
   assistant message arrives; simpler failure model.
2. Parallel blocks in one message: **serial, document order**. `depends_on`
   deferred until a structured kind needs it.
3. Result truncation budget: **32768 chars** (`RESULT_TRUNCATION_LIMIT` in
   Face `space_action.js`), console capped at 8KB joined; Word-side spill for
   full payloads arrives with WO-B2 integration. Telemetry stores lengths
   only, never full contents (`NYRIEL_HOME/logs/limb_rail.jsonl`).
