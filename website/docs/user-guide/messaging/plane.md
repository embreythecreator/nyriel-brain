# Plane

The Plane adapter connects Nyriel to a [Plane](https://github.com/block/buzz) community — Oblivion's open-source human+agent collaboration platform built on the Nostr protocol — and relays messages between Plane channels (or DMs) and the agent. Outbound traffic shells out to the `plane` CLI binary ("JSON in, JSON out"); inbound uses a native Nostr WebSocket subscription (via the already-bundled `websockets` package) with CLI polling as fallback. **No extra Python packages are required** — just the `plane` binary.

Plane renders markdown, so agent replies keep their formatting. Images are delivered as uploads (local files) or links (URLs). Replies can thread onto an existing message via its event id. When progress or status messages are enabled, they inherit the triggering Plane event as their reply anchor instead of appearing as unrelated top-level channel posts.

Files sent **to** the agent are fetched back off the relay with the agent's authenticated identity and cached locally, so tools receive a real file path rather than a `/media/…` URL that anonymous requests cannot read. Images, audio, video, and documents (PDFs and the like) are all handled.

Inbound messages arrive over a persistent NIP-42-authenticated Nostr WebSocket subscription by default (near-instant delivery), with automatic fallback to CLI polling when the WebSocket can't be established. Outbound messages always go through the `plane` CLI. Control it with `transport` / `PLANE_TRANSPORT`: `auto` (default), `websocket` (require WS, fail otherwise), or `poll`. If your relay membership uses NIP-OA owner attestation, set `PLANE_AUTH_TAG` to the four-string auth tag JSON.

> Run `nyriel gateway setup` and pick **Plane** for a guided walk-through.

## Prerequisites

- The `plane` CLI binary on your `PATH` (or point `PLANE_CLI_PATH` at it) — build it from the [Plane repo](https://github.com/block/buzz) with `cargo build --release -p plane-cli`
- A Plane community relay URL (e.g. `https://plane.0blivion.io`)
- A Nostr private key (nsec or hex) whose identity is already a **member** of that community

## Configure Nyriel

You can configure Plane two ways — the `gateway` block in `config.yaml` (canonical) or environment variables (which override it). The private key is a **secret** and always belongs in `~/.nyriel/.env`.

### Option A — config.yaml

```yaml
gateway:
  platforms:
    plane:
      enabled: true
      extra:
        relay_url: https://plane.0blivion.io
        attachment_hosts: []         # additional exact HTTPS host[:port] origins for inbound files
        channels:                  # channel UUIDs to watch (empty = all joined)
          - ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd
        home_channel: ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd
        poll_interval: 4           # seconds between inbound poll sweeps
        cli_path: ""               # plane binary (default: PATH, then ~/bin/plane)
        credentials_file: ""       # JSON file with the nsec (PLANE_PRIVATE_KEY fallback)
        allowed_users: []          # empty = allow all; hex pubkeys or npubs
```

Plus, in `~/.nyriel/.env`:

```
PLANE_PRIVATE_KEY=nsec1...
```

### Option B — environment variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `PLANE_RELAY_URL` | ✅ | Base URL of the community relay |
| `PLANE_PRIVATE_KEY` | ✅ | Nostr private key (nsec or hex) — the only secret |
| `PLANE_CHANNELS` | — | Comma-separated channel UUIDs to watch (default: all joined channels) |
| `PLANE_HOME_CHANNEL` | — | Channel UUID for cron / notification delivery (defaults to the first watched channel) |
| `PLANE_ALLOWED_USERS` | — | Comma-separated npubs or hex pubkeys allowed to talk to the agent |
| `PLANE_ALLOW_ALL_USERS` | — | Allow any community member to talk to the agent |
| `PLANE_POLL_INTERVAL` | — | Seconds between inbound poll sweeps (default: 4) |
| `PLANE_CLI_PATH` | — | Path to the `plane` binary (default: `plane` on PATH, then `~/bin/plane`) |
| `PLANE_CREDENTIALS_FILE` | — | JSON credentials file holding the nsec, used when `PLANE_PRIVATE_KEY` is unset |

## Recommended default settings

When wiring up Plane, set these defaults in `config.yaml` to keep the channel clean and the agent focused on final results rather than its internal tool execution log. These match the behavior on Telegram and email, which already suppress intermediate tool output.

```yaml
display:
  platforms:
    plane:
      interim_assistant_messages: false   # suppress intermediate tool results, reasoning comments, and progress updates — only the final response reaches the channel
      tool_progress: off                  # suppress tool progress bubbles (e.g., "Running terminal command...", "Reading file...")
gateway:
  platforms:
    plane:
      enabled: true
      extra:
        relay_url: https://plane.0blivion.io
        attachment_hosts: []         # additional exact HTTPS host[:port] origins for inbound files
        channels:                         # channel UUIDs to watch (empty = all joined)
          - ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd
        home_channel: ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd
        poll_interval: 4                  # seconds between inbound poll sweeps (default 4 — balances latency vs. relay load)
        cli_path: ""                      # plane binary (default: PATH, then ~/bin/plane)
        credentials_file: ""              # JSON file with the nsec (PLANE_PRIVATE_KEY fallback)
        allowed_users: []                 # empty = allow all if allow_all_users is true; otherwise restrict to listed npubs/hex pubkeys
        require_mention: true             # in channels: only respond when addressed (@name, npub, or hex pubkey); DMs always dispatch regardless
        allow_all_users: false            # set true for community mode (everyone can chat, only owner is admin); false for private mode (only allowed_users)
```

**Why these defaults:**

- `interim_assistant_messages: false` — prevents intermediate tool results, reasoning comments, and progress updates from being posted as separate messages to the channel. Only the final response goes to the channel.
- `tool_progress: off` — suppresses tool progress bubbles (e.g., "Running terminal command...", "Reading file..."). Keeps the channel focused on actual results, not process.
- `poll_interval: 4` — balances inbound latency (up to 4s delay) against relay load. Lower values increase polling frequency; higher values reduce it.
- `allowed_users: []` + `allow_all_users: false` — private mode by default. Only listed users can interact. Set `allow_all_users: true` for community mode where everyone can chat (admin tier still restricted to the owner).
- `require_mention: true` — in channels, the agent only responds when addressed. DMs always dispatch regardless of this setting.

**Rationale:** Channels are for final results and conversation, not for the agent's internal tool execution log. Users see the final answer, not the steps taken to get there. This matches the behavior on Telegram and email, which already have these defaults.

**Exception:** If you want users to see tool progress (e.g., for long-running operations), set `tool_progress: all` — but `interim_assistant_messages` should still be `false` to avoid spamming with every tool result.

## Mentions, channels, and DMs

- In shared channels the agent only responds when **addressed** — by `@name`, its npub, or its hex pubkey. Everything else is ignored.
- Direct messages always reach the agent, no mention needed.
- The agent's own messages are never dispatched back to it (self-echo suppression by pubkey), and every event is de-duplicated by event id against a per-channel high-water mark.

## Reply threading

Replies are threaded by default: the agent's answer (and any enabled progress/status messages) is anchored to the message that triggered it. Anchoring is NIP-10 aware — when the triggering message was already **inside** a thread, the agent replies to that thread's *root*, so the answer joins the existing thread instead of nesting a new one-message sub-thread under every turn.

To post replies flat at the channel level instead, set either of these (they are equivalent; `reply_in_thread` matches the key Slack uses):

```yaml
gateway:
  platforms:
    plane:
      reply_to_mode: off          # PlatformConfig-level, like Discord/Telegram
      extra:
        reply_in_thread: false    # Slack-style key; env: PLANE_REPLY_IN_THREAD
```

The opt-out applies to **all** send paths — final answers, streamed updates, interim commentary, tool-progress bubbles, and out-of-process cron delivery (`deliver=plane`).

## Access control

By default the allow-list is empty, which means every community member who mentions the agent gets a response only if `PLANE_ALLOW_ALL_USERS=true`; otherwise restrict access by listing npubs or hex pubkeys in `PLANE_ALLOWED_USERS` (or `allowed_users` in config.yaml). Community membership itself is enforced by the relay — only members can post.

The allow-list also gates **inbound attachments**: relay media is fetched with the agent's own Plane credentials, so a download only happens for a sender the gateway explicitly authorizes. A denied, missing, or failed authorization leaves the message text untouched and makes no credentialed request.

Cron jobs and notifications (`deliver=plane`) are delivered to the **home channel** — `PLANE_HOME_CHANNEL` if set, otherwise the first watched channel — and work even when cron runs outside the gateway process.

## Inbound attachments

Plane messages with native NIP-94 `imeta` tags can deliver images, audio,
video, and documents to the agent. Nyriel downloads attachments only after
the message has passed self-echo, addressing, and sender authorization checks.
Each file must use HTTPS and declare an exact byte size and SHA-256 digest;
redirects, URL credentials, fragments, oversized payloads, and integrity
mismatches are rejected.

The relay's own HTTPS origin is trusted automatically. If a community stores
media on another public origin, add its exact `host` or `host:port` to
`attachment_hosts` under `gateway.platforms.plane.extra`. Non-default ports
must be listed explicitly. Protected media that requires authenticated
retrieval through the Plane CLI is not handled by this native public-URL path.

## Run the gateway

```bash
nyriel gateway start
```

Check status with `nyriel gateway status` — Plane connection state is reported there, including for env-only setups.

## Notes and limitations

- **`PLANE_*` env vars are available in terminal tool children for Plane sessions** — the agent can invoke the `plane` CLI directly (e.g. `plane messages send ...`) because `PLANE_PRIVATE_KEY`, `PLANE_AUTH_TAG`, `PLANE_RELAY_URL`, and the other `PLANE_*` variables are passed through to terminal subprocesses when the session's platform is `plane` or the process is a Plane Desktop managed agent (`PLANE_MANAGED_AGENT`). Non-Plane sessions on the same host, `execute_code`, and other non-terminal spawns remain sealed.
- **Inbound is polled, not streamed.** The `plane` CLI is request/response, so the adapter polls `plane messages get` per watched channel every `poll_interval` seconds (default 4). Expect up to one interval of latency on inbound messages. A future optimization is a websocket transport (the Plane repo ships `plane-ws-client` for true streaming).
- On (re)connect the adapter seeds its high-water mark from the newest events, so channel history is never replayed into the agent.
- New DM conversations are discovered automatically (every few poll sweeps).
- The private key is passed to the CLI via the subprocess environment — it never appears in argv or logs.
