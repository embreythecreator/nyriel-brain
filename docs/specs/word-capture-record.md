# Word capture record (WO-POSTURE/1 1.4)

Every Brain-side write into Word goes through `agent/word_seam.capture()` →
`WordMemoryProvider.capture_note()`. This is the record shape the seam
stamps, and the Word-side placement rules.

## Fields (the `metadata` dict; rendered as the `— capture:` footer)

| field | type | meaning |
|---|---|---|
| `content_hash` | sha256 hex | identity = `sha256(title + "\n" + content)`; the dedup key of the `captured` ledger. Metadata never changes identity. |
| `origin` | `url` \| `commit sha` \| `angel` | where the content came from: the fetched URL, the commit that produced a file read, or the Angel/organ that authored it. |
| `posture` | `chat` \| `plan` \| `act` | the session's `ExecutionPosture` at capture time (`unknown` until 1.7 threads the policy through). |
| `session_id` | str | Brain session id of the capturing turn. |
| `trust_tier` | `T1` \| `T2` \| `T3` | trust of the origin; untrusted-origin captures never leave quarantine without approval. |
| `quarantine` | bool | `true` → written to the quarantine notebook, excluded from `prefetch`. Set for PLAN-posture and untrusted-origin captures (1.7). |
| `node_type` | `source` \| `artifact` | `source` = evidence the Brain read; `artifact` = something the Brain produced (plan, brief). |
| `cites` | `[vault_id]` | Word ids this record depends on (plans cite their evidence pins). |

## Placement

- URL-bearing captures (`origin` is a URL) → `_Client.create_source(url, title=…)`
  (Word link-source, `provenance: ingested-external`, async embed).
- Everything else → `create_note` (`note_type: ai`).
- Two notebooks: **memory** (`WORD_NOTEBOOK`, default "Nyriel Memory") and
  **quarantine** — name assumed `Nyriel Quarantine` until D-4 rules.
  Promotion to memory happens on `/plan approve` (1.8; auto-on-approve
  assumed under D-4).

## Return contract

`capture_note` returns the Word id when the write landed (now or earlier),
`None` when queued or Word is down. Callers must treat `None` as "not yet
addressable", never as failure — the durable queue will land it.
