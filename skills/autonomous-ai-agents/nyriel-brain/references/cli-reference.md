# Nyriel CLI Reference

Live sources when anything looks stale: `nyriel --help`, `nyriel <command> --help`,
https://nyriel-brain.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
nyriel [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
nyriel chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
nyriel setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
nyriel model                Interactive model/provider picker
nyriel fallback [add|remove|list]  Fallback provider chain
nyriel config [show|edit|get|set|unset|path|env-path|check|migrate]
nyriel login / logout       OAuth sign-in / clear stored auth
nyriel doctor [--fix]       Check dependencies and config
nyriel status [--all]       Component status
```

### Tools & Skills

```
nyriel tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

nyriel skills list|browse|search QUERY|inspect ID
nyriel skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
nyriel skills config        Enable/disable skills per platform
nyriel skills check|update|uninstall|publish PATH
nyriel skills tap add REPO  Add a GitHub repo as a skill source
nyriel bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
nyriel mcp add NAME (--url or --command) | remove | list | test NAME
nyriel mcp catalog | install NAME     Curated catalog install
nyriel mcp configure NAME             Toggle tool selection
nyriel mcp serve                      Run Nyriel as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
nyriel gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `nyriel photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://nyriel-brain.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
nyriel sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
nyriel cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
nyriel webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
nyriel profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
nyriel profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
nyriel auth                 Interactive credential manager
nyriel auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
nyriel auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
nyriel desktop / gui        Native desktop app
nyriel dashboard            Web admin panel + embedded chat (--stop / --status)
nyriel proxy                OpenAI-compatible local proxy backed by an OAuth provider
nyriel portal               Quick setup / sign in via Nous Portal
nyriel kanban <verb>        Multi-agent work-queue board
nyriel project              Named multi-folder workspaces
nyriel skin list|use|set    Switch/tweak skins (see references/themes.md)
nyriel pets <verb>          Pet mascots (see references/petdex.md)
nyriel memory setup|status|off|reset   Memory provider
nyriel secrets bitwarden|onepassword   External secret stores
nyriel moa                  Mixture-of-Agents slots
nyriel hooks / security / backup / import / checkpoints / console
nyriel logs [-f] [errors]   View agent/error logs
nyriel send                 One-off message through a gateway platform
nyriel pairing / plugins / insights / journey / computer-use
nyriel acp                  ACP server (IDE integration)
nyriel completion bash|zsh|fish
nyriel update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `nyriel photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `nyriel config edit` · [Configuration docs](https://nyriel-brain.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `nyriel tools list` · [Tools reference](https://nyriel-brain.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `nyriel skills browse` · [Skills catalog](https://nyriel-brain.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `nyriel model` · [Providers guide](https://nyriel-brain.nousresearch.com/docs/integrations/providers) |
| Env variables | `nyriel config env-path` · [Env vars reference](https://nyriel-brain.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.nyriel/logs/gateway.log` (or `nyriel logs`) |
| Sessions | `nyriel sessions browse` (reads state.db) |
