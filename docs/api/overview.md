---
title: "API overview"
order: 10
category: API
category_order: 90
---

# API overview

Everything the PotionUI web app does goes through an HTTP API that you can call from your own scripts, bots and other programs. You sign in with an **API key** that you create in Settings, you choose what the key may do, and you can revoke it at any time. The same key also connects MCP clients such as Claude Desktop.

The API is served by your own PotionUI instance, on the backend port (26730 by default), so `http://localhost:26730` on the machine that runs it. All paths in this guide, such as `/api/presets`, are relative to that address.

## Where to start

| Page | Read it to |
| --- | --- |
| Quickstart | Create a key, make the first call, generate an image and download it. Copy and paste. |
| Authentication and permissions | Understand keys, the five permissions, expiry, revoking and every error a key can get. |
| Generating | Start a generation, follow it by polling or over a WebSocket, queue position, cancel, results, errors and limits. |
| Generate from a saved session | Configure a generation once in the app and send only the prompt from a script. |
| Working with your content | History, Library, Kits, collections and tags. |
| MCP in one page | Connect an assistant with the same key. |
| Endpoint reference | Every route with the key permission it needs. Generated from the running app. |

## Conventions

- Requests and responses are JSON, except file uploads (`multipart/form-data`) and file downloads.
- Most successful answers use one envelope: `{"success": true, "data": ..., "message": null, "error": null}`. The useful part is `data`.
- Identifiers such as generation ids are strings. Treat them as opaque.
- Times in responses are ISO 8601 strings in UTC. The `created_at` and `completed_at` of a generation's live status are Unix timestamps in seconds, sent as strings.
- Durations are in seconds.
- Every example in this guide is given for Linux and macOS (bash and `curl`) and for Windows (PowerShell, using `Invoke-RestMethod` or `curl.exe`), and as Python with `httpx`. Install it with `pip install httpx`. The bash examples use `jq` to read JSON.

## Live documentation on your instance

Your instance serves interactive documentation of its own routes at `/docs` (Swagger UI) and `/redoc`, and the machine-readable schema at `/openapi.json`. These three pages open without signing in. In Swagger UI, choose Authorize and paste an API key into the `ApiKey` box; the Try it out buttons then send it. The guide's endpoint reference is generated from the same schema.
