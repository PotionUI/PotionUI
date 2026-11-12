---
title: "Authentication and permissions"
order: 12
category: API
category_order: 90
---

# Authentication and permissions

Every call to the API, except the three documentation pages, carries a credential. For scripts that credential is an API key.

## Sending the key

Send the key as a bearer token in the `Authorization` header of every request:

```bash
curl -sS "$POTIONUI_URL/api/auth/me" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
Invoke-RestMethod -Uri "$($env:POTIONUI_URL)/api/auth/me" -Headers $headers
```

A key is 50 characters: the prefix `pui_` followed by 46 letters and digits. Examples in this guide use the placeholder `pui_YOUR_KEY`. A key is a password: keep it in an environment variable or a secrets store, never in a repository. A key goes only in the header and is never accepted in a URL. PotionUI stores only a fingerprint of it.

A key acts as you. It sees the same presets, models and history that you do, and nothing of anyone else's.

## Permissions

A key carries one or more of five permissions. You choose them when you create the key and can change them later from its row in **Settings**, **API keys**.

| Permission | In the app | What it allows |
| --- | --- | --- |
| `read` | Can read | Every read-only call on your own data: presets and their forms, History, Library, kits, prompts, models, generation status and the generation queue. Downloading media files. A few read-shaped calls that use POST, such as exports, counts and memory previews. |
| `generate` | Can generate | Start, cancel and retry generations and grids, generate from a saved session, upload input files, run media tools. |
| `content` | Can change and delete your content | Create, change and delete your own data: gallery items (rating, favorite, tags, delete), Library, kits, collections, tags, prompts, segments, phrasebook, saved sessions and workspaces. |
| `admin` | Full administration | Every route an administrator can call. It includes `read`, `generate` and `content`. |
| `mcp` | Use with MCP clients | `POST /api/mcp` and media downloads. What an MCP client can then do is decided by the key's MCP tool list. See the MCP page. |

The permissions are separate. In particular `generate` does not include `read`: a key that can only generate can start a generation but cannot check on it or download the result. Tick both for a script that generates and fetches. `admin` is the only permission that includes others, and it does not include `mcp`.

Each entry of the endpoint reference states the permission its route needs. `read` or `mcp` on a route means either one is enough.

### What a key without `admin` can never do

- Call an administrator route. It answers `403` with the error `insufficient_scope` and `scope="admin"` in the `WWW-Authenticate` header, or `403` with the error `insufficient_permissions` and the message `Admin access required`.
- Act as an administrator, even when its owner is one. `GET /api/auth/me` on a key without `admin` reports `account_type` `USER`.
- Create, change, list or revoke API keys, edit MCP tool settings, change a password, edit a user profile, run the first-run setup or use the chat assistant. These routes are **browser session only**: a key gets `403` with the error `key_not_allowed`.

So a leaked key can neither mint more keys nor widen itself.

### The `admin` permission

Only an administrator can put `admin` on a key, and only if the instance allows it: the instance setting `api_key_scopes_allowed` (Administration, System Settings) lists the permissions a key may carry and does not include `admin` by default. A key with `admin` expires within 90 days.

## Expiry

When you create a key you choose **Never**, **30 days**, **90 days** or **1 year**. Through the API this is `expires_in_days`: `null`, `30`, `90` or `365`. Any other value is refused with `invalid_expiry`. Expiry is fixed at creation: to extend a key, create a new one.

An administrator can set the longest lifetime on the instance with `api_key_max_lifetime_days`; a key that asks for longer, or for no expiry, is refused with `lifetime_too_long`. An expired key behaves like a revoked one: `401` with the error `invalid_api_key`. It stays in the list and is marked expired.

## Revoking a key

Revoke a key from its row in **Settings**, **API keys**, or an administrator can revoke it from Administration. Revoking takes effect at once:

- The next request with the key answers `401` with the error `invalid_api_key`.
- WebSocket connections that were opened with the key are closed with code `4001` and the reason `API key revoked`. A key that expires closes its connections the same way with the reason `API key expired`.
- A request already in progress finishes. Generations the key already started keep running, because they belong to you.

A revoked key stays in the list, marked revoked, and cannot be changed or revived.

## What an administrator controls

Administrators decide what keys can do on their instance:

- Whether keys exist at all (`api_keys_enabled`). With keys off, creating one answers `403` with the error `keys_disabled`, and existing keys stop working.
- Which permissions a key may carry (`api_key_scopes_allowed`). A permission that is no longer allowed stops applying to keys that already have it.
- The longest lifetime (`api_key_max_lifetime_days`, 0 for no limit).
- For each person, whether they may use keys and whether they may use MCP. Switching keys off for a person stops their keys at once without revoking them, so switching them back on restores them.

A key can never do more than its owner can, and never more than this ceiling allows.

## Errors

Refusals about the key use these answers. Real examples follow.

| Status | Error | Meaning |
| --- | --- | --- |
| 401 | none (the body is `{"detail": "Not authenticated"}`) | No `Authorization` header was sent. |
| 401 | `invalid_api_key` | The key is wrong, revoked, expired, belongs to a switched-off person, or keys are turned off. |
| 403 | `insufficient_scope` | The key is valid but lacks the permission the route needs. The `WWW-Authenticate` header names it. |
| 403 | `key_not_allowed` | The route is browser session only. |
| 403 | `insufficient_permissions` | The route needs an administrator and the key does not have `admin`. |
| 429 | none (the body is `{"detail": "Too many invalid API key attempts. Please try again later."}`) | By default, five invalid keys from one address within 60 seconds. Wait the number of seconds in the `Retry-After` header. |

A missing header:

```json
{"detail": "Not authenticated"}
```

A wrong or revoked key, status `401` with the header `WWW-Authenticate: Bearer`:

```json
{"detail": {"success": false, "error": "invalid_api_key", "message": "The API key is not valid."}}
```

A key without the needed permission, status `403` with the header `WWW-Authenticate: Bearer error="insufficient_scope", scope="generate"`:

```json
{"detail": {"success": false, "error": "insufficient_scope", "message": "This API key needs the 'generate' scope for this route."}}
```

A browser-session-only route called with a key, status `403`:

```json
{"detail": {"success": false, "error": "key_not_allowed", "message": "This route cannot be used with an API key."}}
```

### The shape of errors

Most refusals put an object under `detail`: `{"detail": {"success": false, "error": "<code>", "message": "<text>"}}`. Switch on `error`, which is stable, and show `message` to a person. Some answers differ, and a robust client handles each:

- A request body that does not fit the schema answers `422` with a flat body: `{"success": false, "error": "validation_error", "message": "Request validation failed", "detail": [{"type": "missing", "loc": ["body", "preset_id"], "msg": "Field required"}]}`. `loc` says where.
- A form that is wrong for its preset answers `422` with `{"detail": {"error": "form_validation_failed", "field_errors": {"model": ["required field is missing"]}, "coercions": [], "stripped": [], "message": "..."}}`.
- A plan limit answers `403` with `{"detail": {"success": false, "error": "limit_exceeded", "code": "daily_generations_exceeded", ...}}`. See the Generating page.
- Some refusals carry a plain string: `{"detail": "..."}`.
- An unexpected failure answers `500` with `{"success": false, "error": "internal_error", "message": "Internal server error", "correlation_id": "..."}`. Quote the `correlation_id` to your administrator.

## Creating keys from a script

Keys are created in the app, in **Settings**, **API keys**. The key routes under `/api/keys` need a signed-in browser session and refuse keys, so a script cannot create or widen keys.
