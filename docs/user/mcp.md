---
title: MCP
order: 85
---

# MCP

PotionUI exposes itself as an [MCP](https://modelcontextprotocol.io) (Model
Context Protocol) server, so an external AI client — Claude Desktop, Claude
Code, or any other MCP-speaking tool — can call PotionUI the same way the
built-in chat assistant does: searching your gallery, listing models,
managing prompts, and more.

## Creating a token

Open **Settings** and find the **MCP** section. Give a token a name and create
it — the token's plaintext value is shown once, so copy it immediately. Use it
as a bearer token when connecting your MCP client:

```
URL: https://<your-server>/api/mcp
Authorization: Bearer <your-token>
```

A token acts as *you*: an MCP client connected with your token sees the same
presets, models, and history you do, and nothing more. Revoke a token from
the same section at any time; existing tokens stay listable and revocable
even while MCP access is turned off.

## Admin switches

Two administrator-controlled switches gate MCP access, both edited in
**Administration**:

- A **global** on/off for the whole instance. It defaults to off — an admin
  has to turn MCP on before any token can connect.
- A **per-user** on/off, defaulting to on once the global switch is on, so an
  admin can turn MCP off for one account without touching everyone else's.

Both must be on for a token to work; either one being off refuses the
connection (existing tokens can still be reviewed and revoked from Settings
regardless).

## What a token can do

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
here: see **Help → Documentation → MCP Tools** (admin-only) for that
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
