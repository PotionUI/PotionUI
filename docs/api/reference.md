---
title: "Endpoint reference"
order: 40
category: API
category_order: 90
---

# Endpoint reference

Every route of the HTTP API, grouped by area, with the key permission it needs, its parameters and its response. Generated from the routes of the running application. Do not edit by hand.

## Interactive documentation

Your instance serves interactive documentation of its own routes on the backend port: `/docs` (Swagger UI) and `/redoc`, with the raw schema at `/openapi.json`. In Swagger UI choose Authorize, paste an API key into the `ApiKey` box (a key starts with `pui_`; the page adds the Bearer prefix) and the Try it out buttons send it. Plugins that add routes show up there when they are enabled.

## How to read an entry

- Key permission: the permission an API key needs for that method and path. `read`, `generate`, `content`, `admin` and `mcp` are described on the Authentication page. `read` or `mcp` means either is enough. A key with `admin` also passes every `read`, `generate` and `content` check.
- Browser session only: the route never accepts an API key. A key gets `403` with the error `key_not_allowed`.
- No sign-in needed: the route answers without any credentials.
- Most routes answer with the standard envelope `{"success": true, "data": ..., "message": null, "error": null}`. Where an entry says the shape of `data` is not described, the Generating and Content pages show the real shapes for the routes a script uses.
- A request body that does not match the schema is refused with `422` and `{"success": false, "error": "validation_error", "message": "Request validation failed", "detail": [...]}`.

## Pages

714 routes in total.

| Page | Routes | What is in it |
| --- | --- | --- |
| Reference: Generation and media | 94 | Starting, following, cancelling and fetching generations, uploads and media files, saved sessions and media tools. |
| Reference: Presets, forms and models | 49 | What can be generated with: presets and their forms, models, backends and downloads. |
| Reference: History, library and prompts | 171 | Your own content: the library, kits, collections, tags, saved prompts and segments, phrasebook and workspaces. |
| Reference: Account, keys and MCP | 15 | Signing in, API keys, MCP and users. |
| Reference: Assistant and language models | 3 | The chat assistant and the language-model configuration behind it. |
| Reference: System | 22 | Health, settings, plugins, recipes, documentation and statistics. |
| Reference: Administration | 294 | Routes that need a key with the admin permission. Only an administrator can create such a key. |
| Reference: Browser session only | 66 | Routes that never accept an API key. A key that calls one gets 403 key_not_allowed. |
