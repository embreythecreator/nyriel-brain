# Word memory provider

Backs Nyriel Brain's long-term memory with **Word**, the angel's memory
organ — a headless notebook store (Postgres+pgvector, procrastinate jobs,
Ward-authenticated REST API on `:5055`).

## Layer mapping (WO-B2)

| Layer | Home |
|---|---|
| Session context | Brain-local (SQLite/FTS5 — `sync_turn` is a no-op here) |
| Persistent facts | Word — mirrored from MEMORY.md/USER.md writes + `word_memory_save` |
| Procedural skills | Word-indexed (WO-B3, later) |

## Behavior

- **Recall**: per-turn `prefetch` runs a Word search (`text` by default;
  set `WORD_SEARCH_TYPE=vector` once Word has an embedding model) and
  injects a compact overlay. The MemoryManager sanitizes and fences it —
  ingested-external content cannot impersonate system text.
- **Writes**: durable SQLite write-behind queue (`$NYRIEL_HOME/word_queue.db`).
  Word down ⇒ writes wait and replay; reads degrade to silence with a 60s
  re-probe backoff. Nothing here ever blocks a turn on the organ.
- **Tools**: `word_memory_search` (scope `memory` | `all`),
  `word_memory_save`, `word_memory_read`.
- Memory notes land in the **Nyriel Memory** notebook (`note_type: ai`),
  created on first use.

## Setup

```bash
nyriel memory setup   # choose: word
```

Config env vars: `WORD_BASE_URL` (default `http://127.0.0.1:5055`),
`WORD_WARD_TOKEN` (or `OPEN_NOTEBOOK_WARD_TOKEN`; blank for a local
authless Word), `WORD_NOTEBOOK`, `WORD_SEARCH_TYPE`.
