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
