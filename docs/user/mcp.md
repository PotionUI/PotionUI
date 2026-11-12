---
title: API keys and MCP
order: 85
---

# API keys and MCP

PotionUI exposes itself as an [MCP](https://modelcontextprotocol.io) (Model
Context Protocol) server, so an external AI client — Claude Desktop, Claude
Code, or any other MCP-speaking tool — can call PotionUI the same way the
built-in assistant, PotionAI, does: searching your gallery, listing models,
managing prompts, and more. The same **API keys** also let a script call
PotionUI's HTTP API without signing in.

## Creating an API key

Open **Settings** and choose **API keys**. Press **Create key**, give it a
**Name**, choose when it **Expires** (Never, 30 days, 90 days or 1 year) and
tick what it can do:

- **Can read** — gallery, prompts, library, presets.
- **Can generate** — start and cancel generations.
- **Can change and delete your content** — edit and delete your gallery,
  prompts and library.
- **Full administration** — users, settings, plugins and backups. Only an
  administrator can add it, it includes the three rows above, and such a key
  lasts 90 days at most.
- **Use with MCP clients** — lets an MCP client connect with this key.

The key's value, which starts with `pui_`, is shown once, so copy it
immediately with **Copy key**. The dialog also offers a ready-to-copy `curl`
example for a script and a JSON block for an MCP client. A key acts as *you*:
a client connected with your key sees the same presets, models, and history
you do, and nothing more. Rename a key or change what it can do from its row,
and revoke it from the same list at any time; a revoked or expired key stays
listed. Your administrator may allow fewer rows than shown, or set a longest
lifetime; a row that is not allowed says so.

## Using a key from a script

Send the key as a bearer token on any API request:

```
curl -H "Authorization: Bearer pui_..." https://<your-server>/api/presets
```

A key can only reach what its rows allow: reading needs **Can read**, starting
a generation needs **Can generate**, changing or deleting content needs **Can
change and delete your content**, and administrative routes need **Full
administration**. Managing keys themselves, and signing-in routes, work only
from a signed-in browser session, never with a key.

### WebSockets

Live generation updates arrive over WebSockets such as `/ws/generation`. Ask
for a single-use ticket first and open the socket with it:

1. `POST /api/auth/ws-ticket` with the body `{"path": "/ws/generation"}`,
   authenticated like any other request (a key with **Can read** is enough).
   The response carries a `ticket` that is valid for 30 seconds and for that
   one socket path.
2. Connect to `/ws/generation?ticket=<ticket>`.

A client that can set request headers may instead send
`Authorization: Bearer <key or session token>` when it opens the socket. A
session token in the URL (`?token=`) is still accepted so that older clients
and plugins keep working, but it is deprecated: use a header or a ticket. An
API key is never accepted in the URL.

## Connecting an MCP client

Point the client at your server's MCP endpoint and send the key as a bearer
token (the create dialog shows this block filled in):

```
URL: https://<your-server>/api/mcp
Authorization: Bearer <your-key>
```

```json
{
  "mcpServers": {
    "potionui": {
      "url": "https://<your-server>/api/mcp",
      "headers": { "Authorization": "Bearer <your-key>" }
    }
  }
}
```

The key needs **Use with MCP clients**. The older `/api/mcp/tokens` and
`/api/mcp/status` routes, which managed MCP-only tokens, still answer so that
existing clients keep working, but they are deprecated: use `/api/keys`. Every
MCP token created before API keys became **API keys** appears in the same list.

## Admin switches

Administrators control API keys and MCP access:

- **MCP Access** (Administration, System Settings, Access) turns MCP on or off
  for the whole instance. It defaults to off.
- On a user's page in **Administration, Users**, a per-user switch turns MCP
  off for one account without touching everyone else's.
- The instance settings `api_keys_enabled`, `api_key_scopes_allowed` (the
  scopes a key may carry; `read`, `generate`, `content` and `mcp` by default)
  and `api_key_max_lifetime_days` (0 for no limit) turn keys off, limit what
  they can do, and cap how long they last. `mcp_tools_ceiling_policy` is
  `all_on` or `read_only_on` and sets which MCP tools are available to users
  until an administrator decides per tool.

Both MCP switches must be on for a key to connect over MCP. Keys can still be
reviewed and revoked from Settings while keys or MCP are turned off.

## What an MCP client can do

MCP exposes the same tool surface a chat session sees, minus a handful of
tools whose result depends on a live frontend session's open form (the active
Generate form, prompt editor segments, the Video/Music Director document) —
an MCP caller has no equivalent state to hand over, so those never appear
here. A tool that changes something the client already gated behind its own
consent UI runs immediately, without a second approval round trip inside
PotionUI. Model listings and lookups apply the same access policy your
account has everywhere else, so a token never sees a model you couldn't.

The exact, live list of exposed tools — names, parameters, and which ones
mutate state — is generated from the running server, not hand-maintained
here: see **Administration → Documentation → MCP Tools** (admin-only) for that
reference.

## Following a generation

`start_generation` returns a `generation_id`. A client can follow the run to the end
with these tools, all limited to your own generations (another user's id behaves as
if it did not exist):

- `get_generation_status` — state (pending, running, completed, failed, cancelled),
  progress, the current step in plain words, the queue position while it waits, and
  the error if it failed.
- `wait_for_generation` — returns as soon as the run finishes, or after a bounded
  time (30 seconds by default, 55 at most) with `timed_out` set. Call it again to
  keep waiting; clients without notifications need not poll tightly.
- `cancel_generation` — stops a queued or running generation.
- `get_generation_results` — the outputs of a finished run. Up to four images
  (eight at most) come back as image content, each kept small; every other output
  (more images, video, audio, meshes) is a resource link. The text part carries the
  seed and the resolved prompt.
- `upload_media` — sends a picture, video or audio file from the client (base64, up
  to 10 MB) into your library and returns a `path` to pass as a media field value in
  `start_generation`'s `form_overrides`. The content is checked, not the file name.

The first three reads never write or spend. `cancel_generation` and `upload_media`
write, so they are off by default for a user and a key until turned on, like every
other tool that writes.

## More of PotionUI over MCP

Besides generating, a client can reach the same areas the app and its HTTP API have. Every tool
does exactly what the matching HTTP route allows for the same account, and nothing more.

| Area | Tools | Who |
| --- | --- | --- |
| Elixirs | `list_elixirs`, `describe_elixir`, `run_elixir`, `get_elixir_run` | your own elixirs |
| Sessions | `list_sessions`, `get_session` | your own saved forms |
| Notifications | `list_notifications`, `mark_notifications_read` | your own |
| Sharing | `list_inspirations`, `publish_inspiration` | the Inspirations feed |
| Deletes | `delete_generation`, `delete_library_item` | your own, permanent |
| Automation | `list_automations`, `run_automation`, `get_automation_status` | administrators only |
| Model downloads | `search_provider_models`, `download_model`, `get_download_status` | administrators only |

Administrator-only tools need an administrator's key that also holds the admin scope; for anyone
else they answer that the authority is missing. Adult results from model marketplaces are left out
of `search_provider_models`.

### Destructive tools

The two delete tools are flagged destructive. A policy alone never turns them on, not even "all
tools on": the administrator has to switch each one on in the administrator list, and the user
then has to switch it on in their own default. A key can still turn it off again. Clients see a
`destructiveHint` on them.
