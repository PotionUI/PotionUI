---
title: "MCP in one page"
order: 16
category: API
category_order: 90
---

# MCP in one page

PotionUI is also an [MCP](https://modelcontextprotocol.io) server, so an assistant such as Claude Desktop or Claude Code can search your gallery, look at models and presets, manage prompts and start generations for you. It connects with the same kind of API key that scripts use. This page is the short version; the user guide page on API keys and MCP has the details.

## Connect a client

1. An administrator turns on **MCP Access** (Administration, System Settings, Access). It is off by default.
2. Create a key in **Settings**, **API keys** and tick **Use with MCP clients**. The create dialog shows the connection settings with your address filled in.
3. Point the client at your instance's MCP address, `/api/mcp`, and give it the key as a bearer token.

```json
{
  "mcpServers": {
    "potionui": {
      "url": "http://localhost:26730/api/mcp",
      "headers": { "Authorization": "Bearer pui_YOUR_KEY" }
    }
  }
}
```

The endpoint speaks JSON-RPC over HTTP POST. You can try it from a terminal:

```bash
curl -sS -X POST "$POTIONUI_URL/api/mcp" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

```powershell
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
Invoke-RestMethod -Method Post -Uri "$($env:POTIONUI_URL)/api/mcp" -Headers $headers -ContentType "application/json" -Body '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

The answer lists the tools this key may use. A key with only `mcp` is meant for MCP clients: besides `/api/mcp` it can only fetch generated and uploaded files. Give it `read` as well if the same key should also call the HTTP API. `/api/mcp` accepts only API keys: a signed-in browser session gets `401` with the error `api_key_required`. When MCP is switched off for the instance or for the person, the answer is `403` with the error `mcp_disabled`.

## The tools

The tools are grouped by what they work on:

| Group | What it covers |
| --- | --- |
| Generation | Search your gallery, list Library items, start a generation (also from a saved session), follow it with `get_generation_status`, `wait_for_generation` and `get_generation_results`, `cancel_generation`, send a file with `upload_media`, list your sessions, delete a generation or Library item, organize the gallery, run a media tool. |
| Models & presets | List and search models, describe a model or a preset, list presets. Administrators only: search the model marketplaces, queue a model download and follow it. |
| Saved prompts | List, read, add, edit and delete saved prompts. |
| Prompt writing | Improve a prompt and find example prompts in your prompt library. |
| Form & segments | Saved segments, segment categories and templates, prompt variables, tab drafts, conditions. |
| Phrasebook vocabulary | The phrasebook's categories and values. |
| Collections | Manage collections. |
| Memory | Read and keep notes for the assistant. |
| Elixirs | List, describe and brew your elixirs, and follow a brew. |
| Automation | List, trigger and follow automations (administrators only). |
| Notifications | Read your notifications and mark them read. |
| Sharing | Browse the Inspirations feed and publish a generation to it. |

Ten tools of the in-app assistant cannot run over MCP, because they need the live Generate form that only the web app has (for example reading the open form or the Video Director document). They are listed in the settings, marked as unavailable, and a client does not see them. The complete, current list with each tool's description and arguments is in the app under Administration, Documentation, MCP Tools.

## Which tools a client sees: three levels

What a key may do is decided by three levels, and each can only narrow the one above.

1. **The administrator's ceiling.** For the whole instance, either every tool is available or only tools that read. The administrator can then switch individual tools off for everyone.
2. **Your default.** Your personal choice of tools for all your keys. It starts read-only: tools that only look at things are on, and tools that change things or spend (starting a generation, editing prompts, organizing the gallery) stay off until you turn them on in **Settings**.
3. **The key.** A single key can switch tools off for itself, so you can give one client less than your default. A key can never turn on a tool that is off above it.

Starting a generation from MCP spends compute, which is why that tool stays off until you allow it.

The two delete tools, `delete_generation` and `delete_library_item`, are destructive. No policy turns them on by itself, not even "all tools on": the administrator switches each one on in the administrator list, then you switch it on in your default. A key can still turn it off. Tools for automation and model downloads need an administrator's key that holds the `admin` permission.

These settings live in the browser session only: the routes that change them refuse API keys, so a connected assistant cannot give itself more tools.
