# Word memory provider (WO-B2 `sys_word_hermes_memory_provider`)

Wire Word (`~/Oblivion/word`, the headless Open Notebook fork: Postgres+pgvector,
procrastinate jobs, Ward-authenticated REST on :5055) in as a Nyriel Brain
external memory provider. Per the angel work orders: **session context stays
Brain-local · persistent facts → Word · procedural skills → Word-indexed (B3
later).** The built-in MEMORY.md/USER.md store remains active (it always is);
Word becomes the durable, searchable long-term layer behind it.

## Shape

New bundled plugin `plugins/memory/word/` (auto-discovered; activated via
`memory.provider: word`), implementing the `MemoryProvider` ABC
(`agent/memory_provider.py`). Reference implementation for structure and
idioms: `plugins/memory/retaindb/` (single `__init__.py` with `_Client`,
`_WriteQueue`, `_build_overlay`) — same three-part shape here.

- `plugin.yaml`: name `word`, pip_dependencies: `requests`, no hard
  `requires_env`.
- **Config** (`get_config_schema` / `save_config`, stored under `memory.word.*`
  in config.yaml): `base_url` (default `http://127.0.0.1:5055`), `token` (Ward
  bearer; falls back to env `OPEN_NOTEBOOK_WARD_TOKEN`), `notebook` (default
  `Nyriel Memory`), `search_type` (`text` default | `vector` — vector
  requires Word to have an embedding model configured; do NOT default to it).
- `initialize()`: `GET /health`; find-or-create the configured notebook by
  name (`GET/POST /api/notebooks`); cache its id. Word being down is
  non-fatal: `is_available()` False, everything degrades to no-ops with one
  warning log.
- `system_prompt_block()`: 3–4 lines — Word long-term memory is active, what
  the tools do, facts saved there persist across sessions.
- `prefetch(query)`: `POST /api/search` (`{query, type, limit≈6,
  search_sources: true, search_notes: true}`); hits return ids + relevance —
  fetch note/source content per hit (`GET /api/notes/{id}` etc.), truncate
  each to ~400 chars, build a compact overlay (title · snippet · id). Manager
  already sanitizes and fences all prefetch output centrally
  (`memory_manager.sanitize_context` + fenced wrap) — do not duplicate that,
  but DO tag each item with its Word provenance id.
- `sync_turn()`: **no-op** (session context stays Brain-local by design).
- `on_memory_write(action, target, content, metadata)`: mirror built-in
  memory writes (MEMORY.md/USER.md edits) into the memory notebook as notes —
  durable local SQLite-backed write queue with background flush thread
  (retaindb `_WriteQueue` pattern) so Brain never blocks on Word and writes
  survive Word downtime. Note title from target+action, body = content.
- **Tools** (keep to exactly these three; schema bloat is why the
  one-provider limit exists):
  - `word_memory_search(query, scope?)` — explicit search; scope `memory`
    (default, the memory notebook) or `all` (every notebook incl. ingested
    research).
  - `word_memory_save(title, content)` — save a durable fact/note to the
    memory notebook (through the write queue).
  - `word_memory_read(note_id)` — fetch one full note by id (for following
    up a prefetch/search hit).
- `shutdown()`: flush + stop the queue. `backup_paths()`: the queue db path.

## Constraints (Brain repo rules)

- New files only under `plugins/memory/word/`, `tests/plugins/`, plus the
  static provider list in the `nyriel memory` CLI help text and README
  touch-ups. Do not modify divergence-manifest files
  (`nyriel_cli/absorb/divergence.py` invariants) or absorb harness code.
- No new "hermes"/"nousresearch" tokens anywhere (stray-token gate).
- Tests run with `.venv/bin/python -m pytest` (NOT `venv/`).
- Mirror test structure of `tests/plugins/test_retaindb_plugin.py` (mocked
  HTTP; no live Word needed in unit tests).

## Acceptance

1. `.venv/bin/python -m pytest tests/plugins/test_word_plugin.py
   tests/agent/test_memory_provider.py tests/run_agent/ -q` green; full
   `tests/plugins/` + `tests/nyriel_cli/` still green.
2. `discover_memory_providers()` lists `word`; `nyriel memory status`/help
   text includes it.
3. Live smoke (Word running on :5055 with Ward token): initialize →
   notebook created; `word_memory_save` → note lands in Word (via queue);
   `prefetch("<saved fact keyword>")` → overlay contains the fact;
   `word_memory_search` scope=all returns hits. Word stopped → provider
   degrades to no-ops, no exceptions to the caller, queue holds writes.
