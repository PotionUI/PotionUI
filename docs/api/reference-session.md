---
title: "Reference: Browser session only"
order: 48
category: API
category_order: 90
---

# Reference: Browser session only

Routes that never accept an API key. A key that calls one gets 403 key_not_allowed.

Generated from the routes of the running application. Do not edit by hand.

## API Keys

### GET /api/keys

**List your API keys and what you may grant**

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/keys

**Create an API key**

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `ApiKeyCreateRequest`:

- `name` (string, optional)
- `scopes` (array of string, optional)
- `expires_in_days` (integer, optional)

Response `201`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/keys/{key_id}

**Rename an API key or change its scopes**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

JSON body, `ApiKeyUpdateRequest`:

- `name` (string, optional)
- `scopes` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/keys/{key_id}

**Revoke an API key**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/keys/{key_id}/events

**Recent activity of one of your API keys**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

Query parameters:

- `page` (integer, optional, at least 1, default 1)
- `page_size` (integer, optional, 1 to 100, default 20)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## API Keys Admin

### GET /api/admin/keys

**List every API key on the instance**

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `status` (one of active, expired, revoked, all, optional, default 'all')
- `user_id` (string, optional)
- `page` (integer, optional, at least 1, default 1)
- `page_size` (integer, optional, 1 to 200, default 50)
- `q` (string, optional, 0 to 100 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/admin/keys/{key_id}

**Revoke any API key**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/keys/{key_id}/events

**History of an API key**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/users/{user_id}/api-access

**A user's API key switches**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/users/{user_id}/api-access

**Set a user's API key switches**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

JSON body, `UserApiAccessRequest`:

- `keys_enabled` (boolean, required)
- `mcp_enabled` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## authentication

### POST /api/auth/change-password

**Change your own password**

Change the current user's own password.

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `ChangePasswordRequest`:

- `current_password` (string, required)
- `new_password` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Chat

### GET /api/chat/admin/sessions

**[Admin] List chat sessions across all users**

List chat sessions across all users, for the admin debug viewer.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `search` (string, optional)
- `limit` (integer, optional, default 20)
- `offset` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/chat/admin/sessions

**[Admin] Delete every chat session**

Delete every chat session of every user - the whole chat history.

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/admin/sessions/bulk-delete

**[Admin] Delete chat sessions of any owner**

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `AdminBulkDeleteSessionsRequest`:

- `session_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/admin/sessions/{session_id}

**[Admin] Get a session's messages and LLM call traces**

A session's messages plus every LLM call trace it produced.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/chat/admin/traces

**[Admin] Clear LLM call traces**

Delete LLM call traces for one session, or every session when omitted.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `session_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/memory

**List LLM memory notes**

List the current user's persistent LLM memory notes.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `scope` (string, optional)
- `scope_ref` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/memory

**Create or update an LLM memory note**

Create or update a persistent LLM memory note.

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `MemoryWriteRequest`:

- `key` (string, required)
- `content` (string, required)
- `scope` (string, optional, default 'global'): One of 'global', 'preset', 'model', 'mode', 'session'
- `scope_ref` (string, optional): Required for 'preset' / 'model' / 'mode' / 'session' scopes (session id for 'session')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/chat/memory/{note_id}

**Update an LLM memory note**

Update an existing persistent LLM memory note's key/content.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `note_id` (string, required)

JSON body, `MemoryUpdateRequest`:

- `key` (string, required)
- `content` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/chat/memory/{note_id}

**Delete an LLM memory note**

Delete a persistent LLM memory note.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `note_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/modes

**List chat modes**

List all registered chat modes.

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/pre-actions

**List registered pre-chat actions**

List all registered pre-chat actions.

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/resources/suggest

**Suggest @resource completions**

Suggest @resource completions for the chat input dropdown.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `query` (string, optional, default '')
- `mode` (string, optional)
- `limit` (integer, optional, default 15)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/sessions

**List chat sessions**

List sessions for the history view (filterable, paginated).

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `mode` (string, optional)
- `search` (string, optional)
- `limit` (integer, optional, default 20)
- `offset` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions

**Create a chat session**

Create a new chat session.

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `CreateSessionRequest`:

- `original_text` (string, optional)
- `llm_config_id` (string, optional)
- `mode` (string, optional, default 'generation')
- `name` (string, optional)
- `system_message` (string, optional)
- `tools_enabled` (boolean, optional, default true)
- `disabled_tools` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/sessions/{session_id}

**Get a chat session**

Get a session with its messages.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Query parameters:

- `tail` (integer, optional, 1 to 200)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/chat/sessions/{session_id}

**Update a chat session**

Update session properties.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

JSON body, `src__features__chat__dto__UpdateSessionRequest`:

- `name` (string, optional)
- `llm_config_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/chat/sessions/{session_id}

**Delete a chat session**

Delete a session and all its messages.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions/{session_id}/accept

**Accept a session's suggestion**

Accept the session's suggestion and close it.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/sessions/{session_id}/messages

**Page a session's messages**

Return the `limit` messages immediately preceding `before` (or the session's tail when `before` is omitted), ascending.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Query parameters:

- `before` (string, optional)
- `limit` (integer, optional, 1 to 200, default 60)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions/{session_id}/messages

**Send a chat message**

Send a message and get AI response.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

JSON body, `SendMessageRequest`:

- `content` (string, required)
- `image_data` (string, optional)
- `context_metadata` (object, optional)
- `resources` (array of ResourceRefRequest, optional)
- `continue_from` (ContinueFromRequest, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/sessions/{session_id}/messages/stream

**Reattach to an in-flight turn (SSE)**

Resume the live stream of a turn already running for this session.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Query parameters:

- `after_seq` (integer, optional)

Response `200`: the body is not described in the schema.

### POST /api/chat/sessions/{session_id}/messages/stream

**Stream a chat message response (SSE)**

Send a message and stream the AI response via SSE.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

JSON body, `SendMessageRequest`:

- `content` (string, required)
- `image_data` (string, optional)
- `context_metadata` (object, optional)
- `resources` (array of ResourceRefRequest, optional)
- `continue_from` (ContinueFromRequest, optional)

Response `200`: the body is not described in the schema.

### POST /api/chat/sessions/{session_id}/messages/{message_id}/prompt-feedback

**Record approve/reject feedback on an enhanced prompt**

Record an approve/reject verdict on an enhancement-proposed prompt.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)
- `message_id` (string, required)

JSON body, `PromptFeedbackRequest`:

- `action_index` (integer, required)
- `verdict` (string, required)
- `reason` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions/{session_id}/reject

**Reject a session's suggestion**

Reject the session's suggestion and close it.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions/{session_id}/tools/resolve

**Resolve a pending tool execution**

Approve or reject a pending tool execution, or retry a failed one.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

JSON body, `ToolResolveRequest`:

- `message_id` (string, required)
- `tool_index` (integer, required, at least 0.0)
- `approved` (boolean, required)
- `reason` (string, optional, 0 to 2000 characters)
- `context_metadata` (object, optional)
- `force` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/chat/sessions/{session_id}/turn/cancel

**Cancel the in-flight turn**

Explicitly stop the session's in-flight turn.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/chat/tools

**List available LLM tools**

List available LLM tools, optionally filtered to a chat mode.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `mode` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## LLM Tool Governance

### GET /api/llm/configurations/{config_id}/toolset

**Get LLM Config Toolset**

Every registered chat tool merged with this config's governance row (admin only).

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/llm/configurations/{config_id}/toolset/{tool_name}

**Update LLM Config Toolset**

Set a tool's `enabled`/`locked` config for one LLM config (admin only).

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `config_id` (string, required)
- `tool_name` (string, required)

JSON body, `ToolGovernanceUpdateRequest`:

- `enabled` (boolean, optional)
- `locked` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/toolset/preferences

**Get My Toolset Preferences**

The tools the current user may see, with their own opt-out state, scoped to `llm_config_id` (the chat composer's active session config) - admin-disabled tools for that config are omitted and `locked` reflects that config's rows.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `llm_config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/llm/toolset/preferences/{tool_name}

**Update My Toolset Preference**

Toggle the current user's own (global) opt-out for a tool, rejected if `request.llm_config_id` (the caller's active config) admin-disabled or locked the tool.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `tool_name` (string, required)

JSON body, `UserToolPreferenceRequest`:

- `disabled` (boolean, required)
- `llm_config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## MCP

### GET /api/mcp/admin/users/{user_id}

**A user's MCP switch (use GET /api/admin/users/{id}/api-access)**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/mcp/admin/users/{user_id}

**Set a user's MCP switch (use PUT /api/admin/users/{id}/api-access)**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

JSON body, `McpUserToggleRequest`:

- `enabled` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/mcp/keys/{key_id}/tools

**The MCP tools of one API key**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/mcp/keys/{key_id}/tools

**Narrow the MCP tools of one API key**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key_id` (string, required)

JSON body, `McpKeyToolsPatchRequest`:

- `overrides` (object of boolean, optional)
- `reset` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/mcp/status

**MCP status (use /api/keys and /api/settings)**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/mcp/tokens

**List MCP tokens (use GET /api/keys)**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/mcp/tokens

**Create an MCP token (use POST /api/keys)**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `McpTokenCreateRequest`:

- `name` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/mcp/tokens/{token_id}

**Revoke an MCP token (use DELETE /api/keys/{id})**

Deprecated. Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `token_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/mcp/tools

**Your default MCP tools**

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/mcp/tools

**Change your default MCP tools**

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `McpToolsPatchRequest`:

- `policy` (string, optional)
- `overrides` (object of boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## MCP Admin

### GET /api/admin/mcp/tools

**MCP tools the administrator allows**

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/admin/mcp/tools

**Change which MCP tools users may expose**

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `McpToolsPatchRequest`:

- `policy` (string, optional)
- `overrides` (object of boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Providers

### GET /api/providers/{provider_id}/settings

**Get a provider's current settings**

Get current settings for a provider.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `provider_id` (string, required)

Response `200`: the body is not described in the schema.

### PUT /api/providers/{provider_id}/settings

**Update a provider's settings**

Update settings for a provider.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `provider_id` (string, required)

JSON body, `ProviderSettingsUpdate`:

- `settings` (object, required)

Response `200`: the body is not described in the schema.

### POST /api/providers/{provider_id}/test

**Test a provider connection**

Test connection to a provider.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `provider_id` (string, required)

Response `200`, `ProviderTestResult`:

- `success` (boolean, required)
- `message` (string, required)

## Settings

### DELETE /api/settings/user/{key}

**Delete User Setting Override**

Delete a user setting override, reverting to system default.

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `key` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## System & Health

### GET /api/readiness

**Instance readiness aggregate**

Report whether the instance can actually generate, filtered to the caller's role. `recipe_id` narrows the content facet to one recipe.

Browser session only. An API key gets `403 key_not_allowed`.

Query parameters:

- `recipe_id` (string, optional)

Response `200`, `ReadinessReport`:

- `overall` (string, required)
- `checks` (array of ReadinessCheck, required)

### GET /api/setup/machine

**GPU, RAM and models-disk facts for the wizard**

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`, `MachineInfo`:

- `gpu` (MachineGpu, optional)
- `ram_total_bytes` (integer, optional)
- `models_disk` (ModelsDisk, optional)

### GET /api/setup/recipes

**Recipes the wizard can offer**

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`: the body is not described in the schema.

### POST /api/setup/runs

**Start (or return the active) onboarding run**

Browser session only. An API key gets `403 key_not_allowed`.

JSON body, `CreateRecipeRunRequest`:

- `recipe_id` (string, required, at least 1 characters)
- `recipe_version` (integer, optional)
- `safe_input` (object, optional)

Response `201`, `RecipeRunView`:

- `id` (string, required)
- `recipe_id` (string, required)
- `recipe_version` (integer, required)
- `scope` (string, required)
- `mode` (string, optional, default 'onboarding')
- `status` (string, required)
- `current_step` (string, optional)
- `safe_input` (object, optional)
- `safe_output` (object, optional)
- `error_code` (string, optional)
- `safe_error_detail` (string, optional)
- `created_at` (string (date-time), optional)
- `updated_at` (string (date-time), optional)
- `completed_at` (string (date-time), optional)
- `steps` (array of RecipeRunStepView, optional)
- `attempts` (array of RecipeStepAttemptView, optional)

### GET /api/setup/runs/active

**The currently active onboarding run, if any**

Read-only discovery for a display-only page: unlike `POST /runs` (idempotent, but CREATES a pending run as a side effect the first time), this never creates anything - 404 when no run is active, per the house 404-not-403 idiom. Registered ahead of `GET /runs/{run_id}` so "active" is never swallowed as a literal run id.

Browser session only. An API key gets `403 key_not_allowed`.

Response `200`, `RecipeRunView`:

- `id` (string, required)
- `recipe_id` (string, required)
- `recipe_version` (integer, required)
- `scope` (string, required)
- `mode` (string, optional, default 'onboarding')
- `status` (string, required)
- `current_step` (string, optional)
- `safe_input` (object, optional)
- `safe_output` (object, optional)
- `error_code` (string, optional)
- `safe_error_detail` (string, optional)
- `created_at` (string (date-time), optional)
- `updated_at` (string (date-time), optional)
- `completed_at` (string (date-time), optional)
- `steps` (array of RecipeRunStepView, optional)
- `attempts` (array of RecipeStepAttemptView, optional)

### GET /api/setup/runs/{run_id}

**Durable onboarding-run detail**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `run_id` (string, required)

Response `200`, `RecipeRunView`:

- `id` (string, required)
- `recipe_id` (string, required)
- `recipe_version` (integer, required)
- `scope` (string, required)
- `mode` (string, optional, default 'onboarding')
- `status` (string, required)
- `current_step` (string, optional)
- `safe_input` (object, optional)
- `safe_output` (object, optional)
- `error_code` (string, optional)
- `safe_error_detail` (string, optional)
- `created_at` (string (date-time), optional)
- `updated_at` (string (date-time), optional)
- `completed_at` (string (date-time), optional)
- `steps` (array of RecipeRunStepView, optional)
- `attempts` (array of RecipeStepAttemptView, optional)

### POST /api/setup/runs/{run_id}/actions/{action}

**Apply a run action (pause|resume|cancel|retry_step|grant_consent)**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `run_id` (string, required)
- `action` (string, required)

JSON body (optional): RecipeRunActionRequest.

Response `200`, `RecipeRunView`:

- `id` (string, required)
- `recipe_id` (string, required)
- `recipe_version` (integer, required)
- `scope` (string, required)
- `mode` (string, optional, default 'onboarding')
- `status` (string, required)
- `current_step` (string, optional)
- `safe_input` (object, optional)
- `safe_output` (object, optional)
- `error_code` (string, optional)
- `safe_error_detail` (string, optional)
- `created_at` (string (date-time), optional)
- `updated_at` (string (date-time), optional)
- `completed_at` (string (date-time), optional)
- `steps` (array of RecipeRunStepView, optional)
- `attempts` (array of RecipeStepAttemptView, optional)

## users

### PUT /api/users/{user_id}

**Update User**

Browser session only. An API key gets `403 key_not_allowed`.

Path parameters:

- `user_id` (string, required)

JSON body, `UserUpdate`:

- `username` (string, optional)
- `email` (string, optional)
- `password` (string, optional)
- `account_type` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
