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
