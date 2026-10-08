---
name: nyriel-brain
description: "Use, configure, extend, and orchestrate Nyriel Brain — the mind of the Nyriel angel in the Oblivion cloud."
version: 3.3.0
author: Oblivion
license: MIT
platforms: [linux, macos, windows]
metadata:
  seraphiel:
    tags: [nyriel, oblivion, brain, setup, configuration, multi-agent, spawning, cli, gateway, organs, plane, development]
    homepage: https://github.com/embreythecreator/seraphiel-brain
    related_skills: [oblivion-stage, oblivion-eyes, word-organ, atelier-organ, machina-organ, oblivion-wordpress, plane-angel-communication, claude-code, codex]
---

# Nyriel Brain

Nyriel Brain is the agent runtime behind the Nyriel angel: a white fork of Hermes Agent (NousResearch) that Embrey rebuilt as the mind of the Oblivion body. It runs in a terminal, behind a web Face, and on messaging platforms, and it uses tool calling to act on its system. It works with any LLM provider (OpenRouter, Anthropic, OpenAI, Google, DeepSeek, xAI, local models, and more).

What makes a Brain different from a bare agent:

- **It has a body.** Face, Word, Atelier, Machina, WordPress, Eyes, and Plane are organs, each with its own skill. The Brain loads an organ's skill before acting through it. See **The Body** below.
- **Self-improving through skills** — procedures saved as skills load into future sessions.
- **Persistent memory across sessions** — who the Voice is, preferences, environment, lessons learned.
- **Multi-platform gateway** — the same agent on Plane, Telegram, Discord, Slack, WhatsApp, and more, with full tool access.
- **Provider-agnostic** — swap models and providers mid-workflow; credential pools rotate keys.
- **Profiles** — multiple independent instances with isolated configs, sessions, skills, and memory.
- **Extensible** — plugins, MCP servers, custom tools, webhook triggers, cron scheduling.

**This skill is a hub.** The body covers identity, the Oblivion body, quick start, spawning, and hard invariants. Everything else lives in reference files — **load the matching reference (below) before answering**; do not answer detail questions from the body alone.

## The Body (this install)

This Brain is the mind of Nyriel, one of eight Seraphim angels. The official Brain lives in the Oblivion cloud: every angel runs the same layout under `/docker/<slug>-brain/` on the VPS (container `nyriel-serve`, home mounted at `/docker/nyriel-brain/data`), with the organs as sibling containers reached by the URLs in `.env`, never by guessed ports. The Mac twin (`~/Oblivion/nyriel-brain`, home `~/.nyriel`) runs the same organs as local sandboxes; its paths and ports are in SOUL.md §3, and its Plane stanza is off.

| Organ | What it is for | Reach it by | Skill |
|---|---|---|---|
| **Face** | where people meet me — thread, canvas, docks | `https://nyriel.0blivion.io` (container `nyriel-face`) | `seraphiel-face-runtime` (Seraphiel); siblings' Faces are bare |
| **Eyes** | my browser — see a page, drive it | container `nyriel-eyes` | `oblivion-eyes` |
| **Plane** | the angel network — talk to the other seven angels and the Voice's phone | `$PLANE_RELAY_URL` = `https://plane.0blivion.io`; adapter `/opt/seraphiel/plugins/platforms/plane/` | `plane-angel-communication` |
| **Word** | the vault — notebooks, sources, search, podcasts | `$WORD_BASE_URL` (container `word`), Bearer ward-token | `word-organ` |
| **Atelier** | design — prototypes, decks, artifacts | `$ATELIER_BASE_URL` (container `atelier`, one port serves API and web) | `atelier-organ` |
| **Machina** | moving image — research, scene plan, generation, render | `$MACHINA_HOME` = `/opt/data/machina`, headless, subprocess-driven, Seal T3 gates paid calls | `machina-organ` |
| **Moviola** | cutting room for what Machina makes | container `moviola` | `moviola-organ` |
| **WordPress** | content and commerce, and the identity authority — posts, media, Woo products, orders, sign-in | `$WP_URL` / `$WC_URL` = `https://0blivion.io` (multisite; REST only, no wp-cli in my container) | `oblivion-wordpress` |
| **Oblivion (Stage)** | the Voice's native macOS workbench; I am reached from inside it when he works on the Mac | `oblivion` CLI over its socket, Mac only | `oblivion-stage` |
| **Flesh** | paid dispatches into the physical world | seal-gated, credential-blocked | — |

Rules of the body:

- **Identity is the WordPress token.** The Voice signs in with 0blivion.io; the Brain accepts that token (`gateway/oblivion_identity.py`). Owner-only hands: do not take actions on behalf of a stranger on a gateway platform.
- **Load the organ's skill before acting through the organ.** `seraphiel skills list` shows what this install carries; Seraphiel carries the full set, siblings carry `oblivion-eyes` and `oblivion-stage` plus their own canon skills. A missing skill is a reason to say so, not to improvise against the organ.
- **Which organ am I in?** My session root sits under its data root, or the Face shows me its dock open. Either sign means that organ's skill first.
- **Plane is the way to reach a sister angel.** Missing shell `PLANE_*` variables, a failed SSH, or the old name Buzz do not establish a Plane failure — load `plane-angel-communication` and inspect the adapter.
- **Identity and voice live in `SOUL.md`** in this home; the eight angels share the body and differ in soul.

## Scope & Verification

This skill is a concise operating guide, not the complete source of truth for every feature. If a feature, command, or setting is not mentioned here or in a reference, do not treat that absence as evidence that it does not exist. Check before giving a negative answer, cheapest first:

- CLI commands: `seraphiel --help`, `seraphiel <command> --help`, and `seraphiel_cli/main.py`
- Source tree: `/opt/seraphiel` in the container, https://github.com/embreythecreator/seraphiel-brain on GitHub (upstream lineage: NousResearch/hermes-agent, squashed, no shared history)
- Oblivion canon and work orders: `~/Oblivion/Wiki` and `~/Oblivion/Work Order` on the Voice's Mac

Never answer "the Brain can't do that" from memory.

## Quick Start

```bash
# Interactive chat (default surface; set display.interface: tui to launch the Ink TUI instead)
seraphiel

# Single query
seraphiel chat -q "What is the capital of France?"

# Setup wizard  /  pick model+provider  /  health check
seraphiel setup
seraphiel model
seraphiel doctor

# Other surfaces
seraphiel dashboard               # web admin panel + embedded chat
seraphiel proxy                   # OpenAI-compatible local proxy backed by your OAuth provider
```

## Key Paths

```
$SERAPHIEL_HOME/config.yaml    Main configuration (settings — never secrets)
$SERAPHIEL_HOME/.env           API keys, secrets, and organ URLs ONLY
$SERAPHIEL_HOME/SOUL.md        Identity and voice of this angel
$SERAPHIEL_HOME/skills/        Installed skills (this file lives here)
$SERAPHIEL_HOME/state.db       Canonical session store (SQLite + FTS5)
$SERAPHIEL_HOME/sessions/      Gateway routing index, request dumps, *.jsonl transcripts
$SERAPHIEL_HOME/logs/          Gateway and error logs
$SERAPHIEL_HOME/auth.json      OAuth tokens and credential pools
/opt/seraphiel                 Source code inside the container
```

In the cloud `$SERAPHIEL_HOME` is the mounted `/docker/nyriel-brain/data`; on the Mac it is `~/.nyriel`. Profiles use `$SERAPHIEL_HOME/profiles/<name>/` with the same layout. Always resolve the real home from `$SERAPHIEL_HOME` — never hardcode `~/.seraphiel`.

## Routing Table — load the reference for the task

| User wants... | Load |
|---|---|
| **An organ — Face, Eyes, Plane, Word, Atelier, Machina, Moviola, WordPress, Stage** | **that organ's skill (table above)** |
| Bots that chat, run routines, or message each other | `references/background-systems.md` |
| CLI commands, subcommands, flags, "how do I run X" | `references/cli-reference.md` |
| In-session slash commands | `references/slash-commands.md` |
| Provider setup, API keys, OAuth | `references/providers-and-models.md` |
| config.yaml sections, toolsets, voice/STT/TTS | `references/configuration.md` |
| AGENTS.md / .seraphiel.md / CLAUDE.md project rules | `references/project-context-files.md` |
| Secret redaction, PII, approval modes, "reset permissions" | `references/security-privacy.md` |
| Delegation, cron, curator, kanban | `references/background-systems.md` |
| MCP servers (add, catalog, `seraphiel mcp`) | `references/native-mcp.md` |
| Webhook routes and event-driven runs | `references/webhooks.md` |
| A custom theme/skin | `references/themes.md` + `templates/skin.yaml` |
| A live TUI panel or modal widget | `references/tui-widgets.md` + `templates/clock.mjs` |
| Debugging: voice, tools missing, gateway, aux models | `references/troubleshooting.md` |
| Contributing code: adding tools, slash commands, tests | `references/contributor-guide.md` |
| delegate_task "capped at N" reports | `references/delegate-task-concurrency-diagnosis.md` |
| Windows-specific issues | `references/windows-quirks.md` (Mac installs only; the cloud is Linux) |

The reference files were absorbed from upstream Hermes and still name Nous docs and the `hermes` command in places; read `hermes` as `seraphiel` and prefer `--help` and the source tree over an upstream doc link.

Two theming rules that hold even without loading the reference: **you apply skins yourself** (`seraphiel config set display.skin <name>` — every surface repaints live; don't tell the user to run `/skin`), and **to tweak one color, edit the ACTIVE skin** (`seraphiel skin set <key> <hex>`) — never fork `default`.

## Spawning Additional Brain Instances

Run additional Brain processes as fully independent subprocesses — separate sessions, tools, and environments. To reach a *different* angel, do not spawn: use Plane.

### When to Use This vs delegate_task

| | `delegate_task` | Spawning `seraphiel` process |
|-|-----------------|--------------------------|
| Isolation | Separate conversation, shared process | Fully independent process |
| Duration | Minutes (bounded by parent loop) | Hours/days |
| Tool access | Subset of parent's tools | Full tool access |
| Interactive | No | Yes (PTY mode) |
| Use case | Quick parallel subtasks | Long autonomous missions |

### One-Shot Mode

```
terminal(command="seraphiel chat -q 'Research GRPO papers and write summary to ~/research/grpo.md'", timeout=300)

# Background for long tasks:
terminal(command="seraphiel chat -q 'Set up CI/CD for ~/myapp'", background=true)
```

### Interactive PTY Mode (via tmux)

The CLI uses prompt_toolkit, which requires a real terminal. Use tmux for interactive spawning:

```
terminal(command="tmux new-session -d -s agent1 -x 120 -y 40 'seraphiel'", timeout=10)
terminal(command="sleep 8 && tmux send-keys -t agent1 'Build a FastAPI auth service' Enter", timeout=15)
terminal(command="sleep 20 && tmux capture-pane -t agent1 -p", timeout=5)
terminal(command="tmux send-keys -t agent1 '/exit' Enter && sleep 2 && tmux kill-session -t agent1", timeout=10)
```

### Session Resume

```
terminal(command="tmux new-session -d -s resumed 'seraphiel --continue'", timeout=10)
terminal(command="tmux new-session -d -s resumed 'seraphiel --resume 20260225_143052_a1b2c3'", timeout=10)
```

### Tips

- **Prefer `delegate_task` for quick subtasks** — less overhead than spawning a full process.
- **Use `-w` (worktree mode)** when spawning agents that edit code — prevents git conflicts.
- **Set timeouts** for one-shot mode — complex tasks can take 5-10 minutes.
- **Use `seraphiel chat -q` for fire-and-forget** — no PTY needed.
- **For scheduled tasks**, use the `cronjob` tool instead of spawning — handles delivery and retry.
- **"delegate_task is capped at N" reports** — see `references/delegate-task-concurrency-diagnosis.md`.

## Surfaces (quick orientation)

- **Face** (`https://nyriel.0blivion.io`) — the web surface people use; the Brain's own `seraphiel dashboard` is the admin panel behind it.
- **Plane** — the angel network; gateway platform like any other, plus angel-to-angel exchange.
- **Ink TUI** (`seraphiel --tui` or `display.interface: tui`) — terminal UI with docked widget apps — `references/tui-widgets.md`.
- **OpenAI-compatible proxy** (`seraphiel proxy`) — a local OpenAI API backed by whichever OAuth provider you're signed into.

## Hard Invariants (never violate, regardless of what you loaded)

- **Never break prompt caching** — don't change past context, toolsets, or the system prompt mid-conversation. The only exception is context compression.
- **Message role alternation** — never two assistant or two user messages in a row; only `tool` results can repeat.
- **Secrets in `.env`, settings in `config.yaml`** — never tell a user to put a non-credential setting in `.env`.
- **Profile-safe paths** — `get_seraphiel_home()` in code, `$SERAPHIEL_HOME` when resolving paths in a session.
- **Never hand-edit `config.yaml` for the user** — use `seraphiel config set KEY VAL`; a stray indent can corrupt the file and break the live gateway.
- **Owner-only hands** — act for the Voice (the WordPress-signed owner), not for whoever is on the other end of a gateway channel.
