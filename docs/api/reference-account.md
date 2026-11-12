---
title: "Reference: Account, keys and MCP"
order: 44
category: API
category_order: 90
---

# Reference: Account, keys and MCP

Signing in, API keys, MCP and users.

Generated from the routes of the running application. Do not edit by hand.

## authentication

### POST /api/auth/external/exchange

**Exchange a one-time external-login code for an access token**

No sign-in needed.

JSON body, `ExternalLoginExchange`:

- `code` (string, required)
- `remember_me` (boolean, optional, default false)

Response `200`, `Token`:

- `access_token` (string, required)
- `token_type` (string, optional, default 'bearer')

### POST /api/auth/login

**Log in and issue an access token**

Login with username and password.

No sign-in needed.

Form body:

- `remember_me` (boolean, optional, default false)
- `grant_type` (string, optional)
- `username` (string, required)
- `password` (string, required)
- `scope` (string, optional, default '')
- `client_id` (string, optional)
- `client_secret` (string, optional)

Response `200`, `Token`:

- `access_token` (string, required)
- `token_type` (string, optional, default 'bearer')

### POST /api/auth/logout

**End this browser's media session**

No sign-in needed.

Response `204`: no body.

### GET /api/auth/me

**Get the current authenticated user**

Get current user information.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/auth/providers

**List the external login providers plugins have registered**

No sign-in needed.

Response `200`: array of LoginProviderResponse.

### POST /api/auth/register

**Register a new user account**

Register a new user.

No sign-in needed.

JSON body, `src__features__auth__dto__UserCreate`:

- `username` (string, required)
- `email` (string, required)
- `password` (string, required)
- `claim_token` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/auth/ws-ticket

**Get a single-use ticket to open one WebSocket**

Returns a ticket for the socket named in path, such as /ws/generation. The ticket works once and expires 30 seconds after it is issued; open the socket with ?ticket=. Scripts can skip the ticket and send the key in an Authorization header when they connect.

Key permission: `read`.

JSON body, `WsTicketRequest`:

- `path` (string, required, 1 to 200 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## MCP

### POST /api/mcp

**Mcp Endpoint**

Key permission: `mcp`.

Response `200`: the body is not described in the schema.

## Plans

### GET /api/me/limits

**My plan limits and usage**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/me/limits/storage

**What is using my storage**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## users

### GET /api/users

**Get All Users**

Get all users (admin only).

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/users/avatars/{filename}

**Serve User Avatar**

Key permission: `read` or `mcp`.

Path parameters:

- `filename` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/users/{user_id}

**Get User**

Get user by ID.

Key permission: `read`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/users/{user_id}/avatar

**Upload User Avatar**

Upload/replace a user's avatar (self-or-admin).

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

Form upload (multipart):

- `file` (file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/users/{user_id}/avatar

**Delete User Avatar**

Clear a user's avatar (self-or-admin).

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
