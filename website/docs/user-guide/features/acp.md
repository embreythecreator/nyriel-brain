---
sidebar_position: 11
title: "ACP Host Integration"
description: "Use Nyriel Brain inside ACP-compatible editors and collaboration platforms"
---

# ACP Host Integration

Nyriel Brain can run as an ACP server, letting ACP-compatible hosts talk to
Nyriel over stdio. Editors can render:

- chat messages
- tool activity
- file diffs
- terminal commands
- approval prompts
- streamed thinking / response chunks

Other hosts can use the same protocol to route collaboration events into
Nyriel. ACP is a good fit when you want Nyriel to keep its existing identity,
provider setup, memory, skills, and tools while another application owns the
conversation transport.

## What Nyriel exposes in ACP mode

Nyriel runs with a curated `nyriel-acp` toolset designed for editor workflows. It includes:

- file tools: `read_file`, `write_file`, `patch`, `search_files`
- terminal tools: `terminal`, `process`
- web/browser tools
- memory, todo, session search
- skills
- execute_code and delegate_task
- vision

It intentionally excludes things that do not fit typical editor UX, such as messaging delivery and cronjob management.

## Installation

Install Nyriel normally, then add the ACP extra from the install checkout:

```bash
cd ~/.nyriel/nyriel-brain && uv pip install -e '.[acp]'
```

This installs the `agent-client-protocol` dependency and enables:

- `nyriel acp`
- `nyriel-acp`
- `python -m acp_adapter`

## Launching the ACP server

Any of the following starts Nyriel in ACP mode:

```bash
nyriel acp
```

```bash
nyriel-acp
```

```bash
python -m acp_adapter
```

Nyriel logs to stderr so stdout remains reserved for ACP JSON-RPC traffic.

For non-interactive checks:

```bash
nyriel acp --version
nyriel acp --check
```

### Browser tools (optional)

Browser tools (`browser_navigate`, `browser_click`, etc.) depend on the
`agent-browser` npm package and Chromium, which aren't part of the Python
wheel. Install them with:

```bash
nyriel acp --setup-browser           # interactive (prompts before ~400 MB download)
nyriel acp --setup-browser --yes     # accept the download non-interactively
```

This is the standalone command. The terminal-auth flow (`nyriel acp --setup`) also offers the browser bootstrap as a follow-up question after model selection, so most users never need to run `--setup-browser` directly.

What it does:

- Installs Node.js 26 into `~/.nyriel/node/` if missing
- `npm install -g agent-browser @askjo/camofox-browser` into that prefix (no sudo needed — `npm`'s `--prefix` points at the user-writable Nyriel-managed Node)
- Installs Playwright Chromium, or uses a detected system Chrome/Chromium when available

The bootstrap is idempotent — re-running it is fast and skips work that's already done.

## Host setup

### Plane channels (relay bridge)

[Plane](https://github.com/block/buzz) is a Nostr-based collaboration platform
for people and agents. Its `plane-acp` harness connects Plane channels to any ACP
agent over stdio:

```text
Plane relay <-- WebSocket --> plane-acp <-- ACP over stdio --> Nyriel Brain
```

This is a transport integration, not a second Nyriel installation. The
subprocess launched by `plane-acp` uses the same Nyriel configuration,
credentials, memory, skills, and state as `nyriel` on that host.

(This is distinct from [Plane Desktop's managed runtime](#plane-desktop), which
spawns Nyriel locally as a preset harness. The relay bridge is for joining Plane
*channels* as an agent identity, typically on a server.)

Prerequisites:

- Complete the ACP installation and `nyriel acp --check` above.
- Build `plane-acp` and the `plane` CLI from the
  [Plane repository](https://github.com/block/buzz)
  (`cargo build --release -p plane-acp`).
- Mint a dedicated Nostr keypair for Nyriel (`plane-admin generate-key`) and
  register it as a relay member (`plane-admin add-member`). Every agent needs
  its own identity — do not reuse a human keypair.
- Add that identity to the intended Plane channels.

Start a bridge with:

```bash
export PLANE_RELAY_URL="wss://community.example.com"
export PLANE_PRIVATE_KEY="..."
export PLANE_API_TOKEN="..."
export PLANE_ACP_AGENT_COMMAND="nyriel"
export PLANE_ACP_AGENT_ARGS="acp"

plane-acp
```

`PLANE_API_TOKEN` is needed only when the relay enforces token authentication.
Do not commit or paste the private key or API token.

For a persistent server deployment, run `plane-acp` under a service manager as
the same operating-system user that owns the intended Nyriel home. Setup,
key generation, channel discovery, and per-agent options are documented in the
[plane-acp README](https://github.com/block/buzz/tree/main/crates/buzz-acp).

The bridge discovers every Plane channel where the Nyriel identity is a member
and automatically subscribes when it is added to another channel. Plane channel
membership therefore remains the access boundary; Nyriel does not need a
separate channel list in its own configuration.

To expose Nyriel ACP activity in the owner's Plane Desktop, add:

```bash
export PLANE_ACP_RELAY_OBSERVER="true"
```

This publishes encrypted kind `24200` observer frames addressed to the agent's
owner (Plane's NIP-AO). Desktop renders the live lifecycle, tool, response, and
usage stream in the agent's **Activity log**. The relay treats these frames as
ephemeral, so Desktop must be online before the turn starts; its local observer
archive is the durable owner-side history.

Headless bridges answer ACP permission requests themselves because no editor
is present to show approval dialogs — see
[Keep Plane agents owner-only](#keep-plane-agents-owner-only). Treat the bridge
as privileged automation: use a dedicated operating-system account, restrict
which Plane users can prompt the agent (`plane-acp` supports an owner-only
respond gate via `PLANE_ACP_AGENT_OWNER`), and grant membership only in channels
where Nyriel is expected to work.

### VS Code

Install the [ACP Client](https://marketplace.visualstudio.com/items?itemName=formulahendry.acp-client) extension.

To connect:

1. Open the ACP Client panel from the Activity Bar.
2. Select **Nyriel Brain** from the built-in agent list.
3. Connect and start chatting.

If you want to define Nyriel manually, add it through VS Code settings under `acp.agents`:

```json
{
  "acp.agents": {
    "Nyriel Brain": {
      "command": "nyriel",
      "args": ["acp"]
    }
  }
}
```

### Zed

Configure Nyriel as a custom agent server in Zed settings:

1. Open the Agent Panel.
2. Add a custom agent server with the following configuration:

```json
{
  "agent_servers": {
    "nyriel-brain": {
      "type": "custom",
      "command": "nyriel",
      "args": ["acp"]
    }
  }
}
```

3. Start a new Nyriel external-agent thread.

Prerequisites:

- Configure Nyriel provider credentials first with `nyriel model`, or set them in `~/.nyriel/.env` / `~/.nyriel/config.yaml`.

### JetBrains

Use an ACP-compatible plugin and point it at `nyriel acp` or `nyriel-acp`.

### Plane Desktop

[Plane](https://github.com/block/buzz) ships Nyriel Brain as a preset runtime.
With Nyriel installed the normal way, Plane discovers it automatically —
open **Settings → Runtimes** and Nyriel appears under your runtimes.

If discovery fails (older installs), make sure the ACP launcher resolves on a
login-shell PATH:

```bash
command -v nyriel-acp || command -v nyriel
```

Recent installs write both `nyriel` and `nyriel-acp` launchers into
`~/.local/bin`; running `nyriel update` adds the `nyriel-acp` launcher to
older installs. As a manual fallback, configure Plane's agent command as
`nyriel` with args `["acp"]`.

#### Model picker

Plane Desktop (v0.5.1+) renders Nyriel' full model menu in the agent's runtime
settings. The list comes from Nyriel itself over ACP: it shows every model
from providers you have authenticated in Nyriel (the same inventory behind
`nyriel model` and the `/model` command), so a model missing from the menu
means its provider has no credentials configured on the Nyriel side.

Entry IDs take the form `provider:model` (e.g. `openrouter:z-ai/glm-5.1`), or
`custom:<name>:<model>` for custom OpenAI-compatible endpoints defined in
`config.yaml`. Picking a model applies to that agent's session; it does not
change your Nyriel-wide default — use `nyriel model` for that.

#### Keep Plane agents owner-only

Plane creates every agent with **Who can talk to this agent** set to `Owner only`.
Leave it there when the runtime is Nyriel.

Two behaviors combine on this path. The `nyriel-acp` toolset includes `terminal`
and `execute_code`, and Plane's ACP bridge answers Nyriel' permission requests
itself with `allow_once` rather than surfacing them. A Nyriel agent in Plane
therefore runs shell commands on the host without prompting. I asked one to run
`rm -rf` against a scratch directory and it deleted it, no prompt anywhere.

Selecting `Anyone` hands that same shell access to every author who can reach
the channel. Plane does not warn when you pick it.

Neither of the obvious mitigations works today:

- `approvals.mode: manual` does make Nyriel raise the permission request, but
  Plane auto-approves it and the command still runs.
- `platform_toolsets.acp` does not narrow the ACP toolset, so it cannot be used
  to drop `terminal`.

`!shutdown` from the owner stops the agent in any mode, and Plane ignores that
command from everyone else.

## Configuration and credentials

ACP mode uses the same Nyriel configuration as the CLI:

- `~/.nyriel/.env`
- `~/.nyriel/config.yaml`
- `~/.nyriel/skills/`
- `~/.nyriel/state.db`

Provider resolution uses Nyriel' normal runtime resolver, so ACP inherits the currently configured provider and credentials. Nyriel also advertises a terminal auth method (`--setup`) for first-run ACP clients; this opens Nyriel' interactive model/provider setup.

## Host integration

These variables are set by an **ACP host process** (an editor or another agent
harness) on the Nyriel subprocess it spawns. They are not user configuration —
do not set them by hand in `.env` or `config.yaml`.

| Variable | Value | Effect |
|----------|-------|--------|
| `NYRIEL_ACP_SKIP_CONFIGURED_MCP` | `1` | Skip starting the **globally configured** MCP servers from `config.yaml` before the ACP JSON-RPC loop begins. |

Nyriel normally starts every MCP server configured in `config.yaml` before it
enters the ACP JSON-RPC loop. A host that owns MCP itself — passing the
session's servers explicitly through `session/new` — does not need that global
startup, and an unrelated slow or interactive MCP server would otherwise delay
`initialize`. Setting the marker to exactly `1` lets such a host skip it.

Only the global `config.yaml` discovery is skipped. **MCP servers supplied by
the ACP session through `session/new` are still registered**, so a host loses
no capability it asked for. Any other value (unset, empty, `0`, `false`) keeps
the default behavior, so an unrelated truthy-looking string cannot silently
disable MCP.

## Session behavior

ACP sessions are tracked by the ACP adapter's in-memory session manager while the server is running.

Each session stores:

- session ID
- working directory
- selected model
- current conversation history
- cancel event

The underlying `AIAgent` still uses Nyriel' normal persistence/logging paths, but ACP `list/load/resume/fork` are scoped to the currently running ACP server process.

## Working directory behavior

ACP sessions bind the editor's cwd to the Nyriel task ID so file and terminal tools run relative to the editor workspace, not the server process cwd.

## Approvals

Dangerous terminal commands can be routed back to the editor as approval prompts. ACP approval options are simpler than the CLI flow:

- allow once
- allow always
- deny

Whether you actually see a prompt is up to the host. A host is free to answer the
request programmatically instead of showing it to you, in which case these
options exist on the wire but never reach a human. Plane Desktop does this, so
treat that path as unattended execution regardless of your `approvals` setting.

On timeout or error, the approval bridge denies the request.

### Session-scoped edit auto-approval

ACP exposes a third tier between *allow once* and *allow always*: **Allow for session**. Picking it from the editor's permission prompt records the approval inside the current ACP session only — every subsequent matching command in that session goes through without prompting, but a new ACP session (or restarting the editor) resets the slate and re-prompts the first time.

| Option | Editor label | Scope | Persisted across restarts |
|---|---|---|---|
| `allow_once` | Allow once | This one tool call | No |
| `allow_session` | Allow for session | All matching calls in this ACP session | No — cleared when the session ends |
| `allow_always` | Allow always | All future sessions | Yes (written to the Nyriel permanent allowlist) |
| `deny` | Deny | This one tool call | No |

`allow_session` is the right default for an editor workflow where you trust an agent for the duration of a task but don't want to grant a long-lived allowlist entry. The safety trade-off is straightforward: the broader the scope, the less the editor will interrupt you, and the more damage a misbehaving agent (or prompt injection) can do before you notice. Start with `allow_once` for unfamiliar commands; promote to `allow_session` once you've seen the agent run the same pattern correctly a few times; reserve `allow_always` for truly idempotent commands you trust forever (e.g. `git status`).

The ACP bridge maps these options onto Nyriel' internal approval semantics — `allow_always` writes a permanent allowlist entry the same way the CLI does, while `allow_session` only affects the in-process approval cache for the current ACP session.

## Troubleshooting

### ACP agent does not appear in the editor

Check:

- For manual/local development, verify the host command points to `nyriel acp`.
- Nyriel is installed and on your PATH.
- The ACP extra is installed (`cd ~/.nyriel/nyriel-brain && uv pip install -e '.[acp]'`).

### ACP starts but immediately errors

Try these checks:

```bash
nyriel acp --version
nyriel acp --check
nyriel doctor
nyriel status
```

### Missing credentials

ACP mode uses Nyriel' existing provider setup. Configure credentials with:

```bash
nyriel model
```

or by editing `~/.nyriel/.env`. The terminal auth flow (`nyriel acp --setup`) can also trigger the interactive provider/model setup.

## See also

- [Plane ACP harness](https://github.com/block/buzz/tree/main/crates/buzz-acp)
- [ACP Internals](../../developer-guide/acp-internals.md)
- [Provider Runtime Resolution](../../developer-guide/provider-runtime.md)
- [Tools Runtime](../../developer-guide/tools-runtime.md)
