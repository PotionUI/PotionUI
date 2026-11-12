---
title: "Reference: Administration"
order: 47
category: API
category_order: 90
---

# Reference: Administration

Routes that need a key with the admin permission. Only an administrator can create such a key.

Generated from the routes of the running application. Do not edit by hand.

## Auto-organize admin

### PUT /api/admin/organize/controls

**Kill switch, default cap and hourly limit**

Key permission: `admin`.

JSON body, `AdminControlsBody`:

- `paused_all` (boolean, optional)
- `default_rule_cap` (integer, optional, 0.0 to 1000.0)
- `hourly_limit` (integer, optional, 1.0 to 100000.0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/activity

**What the rules did**

Key permission: `admin`.

Query parameters:

- `subject` (string, optional)
- `rule_id` (string, optional)
- `before` (string, optional)
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/catalog

**Facts and actions a rule can use**

Key permission: `admin`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/organize/instance/controls

**Pause or cap the instance rules**

Key permission: `admin`.

JSON body, `AdminUserBody`:

- `paused` (boolean, optional)
- `rule_cap` (integer, optional, 0.0 to 1000.0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/facts/{key}/options

**Choices for a fact**

Key permission: `admin`.

Path parameters:

- `key` (string, required)

Query parameters:

- `subject` (string, optional)
- `q` (string, optional, default '')
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/jobs

**Apply-to-existing jobs**

Key permission: `admin`.

Query parameters:

- `active` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/jobs/{job_id}

**One job**

Key permission: `admin`.

Path parameters:

- `job_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/jobs/{job_id}/cancel

**Cancel a job**

Key permission: `admin`.

Path parameters:

- `job_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/preview

**Count what a rule would match**

Key permission: `admin`.

JSON body, `PreviewBody`:

- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `rule_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/preview/matches

**Page through what a rule would match**

Key permission: `admin`.

JSON body, `MatchesBody`:

- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `rule_id` (string, optional)
- `status` (string, optional)
- `page` (integer, optional, at least 1.0, default 1)
- `page_size` (integer, optional, 1.0 to 100.0, default 25)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/rules

**List rules**

Key permission: `admin`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/rules

**Create a rule**

Key permission: `admin`.

JSON body, `RuleBody`:

- `name` (any, optional)
- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `enabled` (boolean, optional)
- `stop_after` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/rules/reorder

**Reorder rules**

Key permission: `admin`.

JSON body, `ReorderBody`:

- `subject` (string, required)
- `rule_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/rules/{rule_id}

**Get a rule**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/organize/instance/rules/{rule_id}

**Replace a rule**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

JSON body, `RuleBody`:

- `name` (any, optional)
- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `enabled` (boolean, optional)
- `stop_after` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/admin/organize/instance/rules/{rule_id}

**Switch, rename or set stop on a rule**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

JSON body, `RulePatch`:

- `name` (any, optional)
- `enabled` (boolean, optional)
- `stop_after` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/admin/organize/instance/rules/{rule_id}

**Delete a rule**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/rules/{rule_id}/apply-existing

**Apply a rule to existing items**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

JSON body (optional): ApplyExistingBody.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/rules/{rule_id}/duplicate

**Duplicate a rule**

Key permission: `admin`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/runs/{run_id}/items

**What a run did, item by item**

Key permission: `admin`.

Path parameters:

- `run_id` (string, required)

Query parameters:

- `status` (string, optional)
- `page` (integer, optional, at least 1, default 1)
- `page_size` (integer, optional, 1 to 100, default 25)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/runs/{run_id}/retry

**Try the waiting items of a run again**

Key permission: `admin`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/organize/instance/runs/{run_id}/undo

**Undo a run**

Key permission: `admin`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/instance/templates

**Starter rules**

Key permission: `admin`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/organize/overview

**Auto-organize counts across users**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/organize/users/{user_id}

**Pause or cap one user**

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

JSON body, `AdminUserBody`:

- `paused` (boolean, optional)
- `rule_cap` (integer, optional, 0.0 to 1000.0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Automations

### GET /api/automations

**List Automations**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations

**Create Automation**

Key permission: `admin`.

JSON body, `CreateAutomationRequest`:

- `name` (string, required)
- `graph` (object, required)
- `description` (string, optional)
- `enabled` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations/http-request/test

**Test HTTP Request Node**

Key permission: `admin`.

JSON body, `HttpRequestProbeRequest`:

- `config` (object, required)
- `automation_id` (string, optional)
- `node_id` (string, optional)
- `event` (object, optional, default {})
- `upstream` (object, optional, default {})

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations/import

**Import Automation**

Create a new (always disabled) automation from an exported envelope.

Key permission: `admin`.

JSON body, `ImportAutomationRequest`:

- `document` (object, required)
- `name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/node-types

**List Automation Node Types**

Palette catalog of every registered trigger/condition/action node type.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/templates

**List Automation Templates**

List immutable core and enabled-plugin templates with requirement status.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations/templates/{template_key}/instantiate

**Instantiate Automation Template**

Create a fresh, always-disabled automation from a catalog template.

Key permission: `admin`.

Path parameters:

- `template_key` (string, required)

JSON body, `InstantiateAutomationTemplateRequest`:

- `name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations/validate

**Validate Automation Graph**

Validate a graph without persisting it (unknown types, cycles, dangling edges, missing config).

Key permission: `admin`.

JSON body, `ValidateGraphRequest`:

- `graph` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/{automation_id}

**Get Automation**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/automations/{automation_id}

**Update Automation**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

JSON body, `UpdateAutomationRequest`:

- `name` (string, optional)
- `graph` (object, optional)
- `description` (string, optional)
- `enabled` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/automations/{automation_id}

**Delete Automation**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/automations/{automation_id}/disable

**Disable Automation**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/automations/{automation_id}/enable

**Enable Automation**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/{automation_id}/export

**Export Automation**

Portable envelope for one automation - no ids, no enabled state, no timestamps.

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/automations/{automation_id}/run

**Run Automation Now**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

JSON body, `RunNowRequest` (optional):

- `node_id` (string, optional)
- `payload` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/{automation_id}/runs

**List Automation Runs**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)

Query parameters:

- `limit` (integer, optional, default 20): Max results
- `before` (string, optional): Keyset cursor: id of the oldest already-seen run

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/automations/{automation_id}/runs/{run_id}

**Get Automation Run**

Key permission: `admin`.

Path parameters:

- `automation_id` (string, required)
- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Backends

### POST /api/backends

**Create Backend**

Create a new backend instance for an engine (admin only).

Key permission: `admin`.

JSON body, `BackendCreateRequest`:

- `name` (string, required): Human-readable name for the backend
- `engine` (string, required): Engine this backend provides (e.g. 'comfyui')
- `enabled` (boolean, optional, default true): Whether the backend is enabled
- `priority` (integer, optional, default 1): Priority for backend selection (higher = preferred)
- `timeout_seconds` (integer, optional, default 300): Timeout for generation requests
- `scheduling_policy` (string, optional, default 'fifo'): 'fifo' or 'fair' - see docs/backends.md 'Scheduling policy'
- `scheduling_max_consecutive_same_model` (integer, optional, at least 1.0, default 3): Fair policy only: consecutive same-model jobs allowed before yielding

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/health

**Get All Backends Health**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/backends/{backend_id}

**Update Backend**

Update an existing backend configuration with new settings (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `BackendUpdateRequest`:

- `name` (string, optional): Human-readable name for the backend
- `enabled` (boolean, optional): Whether the backend is enabled
- `priority` (integer, optional): Priority for backend selection
- `timeout_seconds` (integer, optional): Timeout for generation requests
- `scheduling_policy` (string, optional): 'fifo' or 'fair' - see docs/backends.md 'Scheduling policy'
- `scheduling_max_consecutive_same_model` (integer, optional, at least 1.0): Fair policy only: consecutive same-model jobs allowed before yielding

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/backends/{backend_id}

**Delete Backend**

Delete a backend configuration (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/actions/clear-cache

**Clear Native Backend VRAM & RAM Cache**

Drop every cached model/artifact from the native engine's RAM cache (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/actions/clear-vram

**Clear Native Backend VRAM**

Offload GPU-resident models to host RAM; the RAM cache stays warm (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/engine-flags

**Get Native Engine Settings**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/health

**Get Backend Health**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/index-models

**Index Backend Models**

Enumerate the models this backend can load and record their availability (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/optimizations

**Get Native Backend Optimizations**

System probe + acceleration-library catalog status for a native backend (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/backends/{backend_id}/optimizations/attention-backend

**Pin Native Attention Backend**

Pin (or clear, via 'auto') the live attention backend (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `AttentionBackendRequest`:

- `backend` (string, required): 'auto', or one of kernels.known_backends() (BACKEND_PRIORITY + PIN_ONLY_BACKENDS, e.g. 'sparge')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/optimizations/benchmark

**Benchmark Native Attention Backends**

Time every available attention backend on identical tensors (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/backends/{backend_id}/optimizations/engine-flags

**Set Native Engine Flags**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `EngineFlagsRequest`:

- `flags` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/optimizations/jobs/current

**Get Current Optimization Install Job**

Poll the (at most one) install job's log from a given offset (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Query parameters:

- `offset` (integer, optional, at least 0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/optimizations/jobs/current/cancel

**Cancel Current Optimization Install Job**

Cancel the currently running install job, if any (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/optimizations/{opt_id}/install

**Install A Native Backend Optimization**

Start building/installing one catalog optimization (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)
- `opt_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/set-default

**Set Default Backend**

Make this backend the default for its engine (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/system-info

**Get Backend System Info**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/backends/{backend_id}/test

**Test Backend Connection**

Test connectivity and health of a specific backend (admin only).

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Cloud

### GET /api/cloud/backends/{backend_id}/catalog

**List a cloud backend's catalog**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Query parameters:

- `task` (string, optional)
- `output` (string, optional)
- `enabled` (boolean, optional)
- `search` (string, optional)
- `suggested` (boolean, optional)
- `limit` (integer, optional, 1 to 200, default 50)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/cloud/backends/{backend_id}/catalog/disable

**Disable catalog models**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `CatalogSelectionRequest`:

- `slugs` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/cloud/backends/{backend_id}/catalog/enable

**Enable catalog models**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `CatalogSelectionRequest`:

- `slugs` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/cloud/backends/{backend_id}/catalog/refresh

**Refresh a cloud backend's catalog**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/cloud/models/{model_id}/estimate

**Estimate what a run of cloud video shots costs**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `DirectorEstimateRequest`:

- `shots` (array of ShotEstimate, required)
- `driver` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/cloud/models/{model_id}/scope

**Get the presets a cloud model is limited to**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/cloud/models/{model_id}/scope

**Limit a cloud model to presets**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `ModelScopeRequest`:

- `preset_ids` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Compute Provisioning

### GET /api/admin/provisioning

**List Provisioned Compute**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/provisioning

**Provision Compute (starts a background bring-up)**

Key permission: `admin`.

JSON body, `ProvisionComputeRequest`:

- `provider_id` (string, required)
- `backend_id` (string, required)
- `name` (string, optional)
- `values` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/provisioning/by-backend/{backend_id}

**Get Provisioned Compute By Backend**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/provisioning/providers

**List Compute Providers**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/provisioning/providers/{provider_id}/fields

**Describe Provider Fields**

Key permission: `admin`.

Path parameters:

- `provider_id` (string, required)

JSON body, `ProviderFieldsRequest`:

- `values` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/provisioning/{row_id}

**Refresh Provisioned Compute Status**

Key permission: `admin`.

Path parameters:

- `row_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/provisioning/{row_id}/start

**Start Provisioned Compute (background)**

Key permission: `admin`.

Path parameters:

- `row_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/provisioning/{row_id}/stop

**Stop Provisioned Compute**

Key permission: `admin`.

Path parameters:

- `row_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/provisioning/{row_id}/terminate

**Terminate Provisioned Compute**

Key permission: `admin`.

Path parameters:

- `row_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Content Safety

### POST /api/content-safety/backfill

**Rate Unrated Media**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/content-safety/status

**Content Safety Status**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Developer

### GET /api/developer/docs/lint

**Lint the typed documentation (Docs 2.0)**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/developer/presets/lint

**Lint all presets and return validation errors**

Get preset schema validation errors plus a full lint run.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/developer/template-functions

**Get template-function documentation for pipelines**

Get documentation for the template surface available in pipeline.yml files.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Documentation

### GET /api/docs/live/mcp-tools

**Get Live MCP Tools Reference**

Get the live MCP tool-surface reference.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/docs/live/output-types

**Get Live Output Types Reference**

Get the live output-type registry reference.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/docs/live/pipes

**Get Live Pipes Reference**

Get the live pipe registry reference.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Downloads

### GET /api/downloads

**List Downloads**

List all downloads with optional filtering. Admin only.

Key permission: `admin`.

Query parameters:

- `status` (string, optional): Filter by status
- `type` (string, optional): Filter by type (model/media/hf_repo)
- `limit` (integer, optional, 0 to 200, default 50)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the body is not described in the schema.

### POST /api/downloads/batch

**Queue Batch Downloads**

Queue multiple files for download. Admin only.

Key permission: `admin`.

JSON body, `QueueBatchDownloadRequest`:

- `urls` (array of string, required)
- `destination_dir` (string, optional)
- `download_type` (string, optional, default 'media')

Response `200`: the body is not described in the schema.

### POST /api/downloads/clear-cancelled

**Clear Cancelled Downloads**

Clear all cancelled downloads from history. Admin only.

Key permission: `admin`.

Response `200`: the body is not described in the schema.

### POST /api/downloads/clear-completed

**Clear Completed Downloads**

Clear all completed downloads from history. Admin only.

Key permission: `admin`.

Response `200`: the body is not described in the schema.

### POST /api/downloads/hf-repo

**Queue Hugging Face Repo Download**

Queue a whole Hugging Face repo as one grouped download. Admin only.

Key permission: `admin`.

JSON body, `QueueHfRepoDownloadRequest`:

- `repo_id` (string, required)
- `destination_dir` (string, optional)
- `revision` (string, optional)
- `allow_patterns` (array of string, optional)

Response `200`: the body is not described in the schema.

### POST /api/downloads/media

**Queue Media Download**

Queue a media file for download. Admin only.

Key permission: `admin`.

JSON body, `QueueMediaDownloadRequest`:

- `url` (string, required)
- `destination_dir` (string, optional)
- `filename` (string, optional)

Response `200`: the body is not described in the schema.

### POST /api/downloads/model

**Queue Model Download**

Queue a model file for download. Admin only.

Key permission: `admin`.

JSON body, `QueueModelDownloadRequest`:

- `url` (string, required)
- `destination_dir` (string, optional)
- `model_type` (string, optional)
- `subdir` (string, optional)
- `filename` (string, optional)
- `tags` (array of string, optional)
- `checksum_sha256` (string, optional)
- `provider_id` (string, optional)
- `destination_backend_id` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/downloads/settings

**Get Download Settings**

Get current download service settings. Admin only.

Key permission: `admin`.

Response `200`: the body is not described in the schema.

### PUT /api/downloads/settings

**Update Download Settings**

Update download service settings. Admin only.

Key permission: `admin`.

JSON body, `UpdateSettingsRequest`:

- `max_concurrent_downloads` (integer, optional)
- `auto_retry_failed` (boolean, optional)
- `max_retries` (integer, optional)
- `chunk_size_kb` (integer, optional)
- `verify_checksum` (boolean, optional)
- `default_media_directory` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/downloads/{download_id}

**Get Download**

Get a specific download by ID (with its per-file children for grouped jobs). Admin only.

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

### DELETE /api/downloads/{download_id}

**Delete Download**

Delete a download record. Admin only.

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/downloads/{download_id}/cancel

**Cancel Download**

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/downloads/{download_id}/pause

**Pause Download**

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/downloads/{download_id}/resume

**Resume Download**

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/downloads/{download_id}/retry

**Retry Download**

Key permission: `admin`.

Path parameters:

- `download_id` (string, required)

Response `200`: the body is not described in the schema.

## Generation

### GET /api/admin/generations

**List All Generations (Admin)**

Generation history across every user, optionally filtered to one.

Key permission: `admin`.

Query parameters:

- `user_id` (string, optional)
- `limit` (integer, optional, default 50)
- `offset` (integer, optional, default 0)
- `status` (string, optional)
- `error_category` (string, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `completed_from` (string, optional)
- `completed_to` (string, optional)
- `media_type` (string, optional)
- `search` (string, optional)
- `mode` (string, optional)
- `preset_id` (string, optional)
- `model_name` (string, optional)
- `sort_by` (string, optional)
- `sort_dir` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/generations/bulk-delete

**Bulk Delete Generations (Admin)**

Key permission: `admin`.

JSON body, `BulkDeleteRequest`:

- `generation_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/generations/queue

**Every User's Running And Queued Generations (Admin)**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/generations/{generation_id}

**Get Generation + Run Report (Admin)**

The full generation record plus its persisted run report, if any.

Key permission: `admin`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/debug/{generation_id}

**Debug Generation Data**

Debug endpoint to inspect raw generation data and files (development only).

Key permission: `admin`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/generations/{generation_id}/failure

**Get Generation Failure Detail**

Key permission: `admin`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/{generation_id}/profile

**Get Generation Resource Profile**

Serve the per-generation resource profile. Admin-only: profiles bundle captured application logs. ``?format=report`` renders the text report, ``?file=log`` serves the ``generation.log`` cut, default is the raw ``profile.jsonl`` download.

Key permission: `admin`.

Path parameters:

- `generation_id` (string, required)

Query parameters:

- `file` (string, optional)
- `format` (string, optional)

Response `200`: the body is not described in the schema.

## LLM

### GET /api/llm/assignment-summary

**Get LLM Assignment Summary**

Direct-user and group assignment counts for every LLM configuration (admin only).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/configurations

**Get LLM Configurations**

Get all configured LLM providers and their settings.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/configurations

**Create LLM Configuration**

Create a new LLM provider configuration with API keys and settings.

Key permission: `admin`.

JSON body, `LLMConfigRequest`:

- `id` (string, optional)
- `name` (string, required)
- `type` (string, required)
- `enabled` (boolean, required)
- `base_url` (string, required)
- `api_key` (string, optional)
- `model` (string, required)
- `system_message` (string, required)
- `temperature` (number, optional)
- `max_tokens` (integer, optional)
- `timeout` (integer, optional, default 30)
- `supports_vision` (boolean, optional, default false)
- `disable_system_prompt` (boolean, optional, default false)
- `memory_reflection` (boolean, optional, default true)
- `show_thinking` (boolean, optional, default false)
- `provider_options` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/configurations/{config_id}

**Get LLM Configuration**

Get detailed configuration for a specific LLM provider.

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/llm/configurations/{config_id}

**Update LLM Configuration**

Update an existing LLM provider configuration.

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

JSON body, `LLMConfigRequest`:

- `id` (string, optional)
- `name` (string, required)
- `type` (string, required)
- `enabled` (boolean, required)
- `base_url` (string, required)
- `api_key` (string, optional)
- `model` (string, required)
- `system_message` (string, required)
- `temperature` (number, optional)
- `max_tokens` (integer, optional)
- `timeout` (integer, optional, default 30)
- `supports_vision` (boolean, optional, default false)
- `disable_system_prompt` (boolean, optional, default false)
- `memory_reflection` (boolean, optional, default true)
- `show_thinking` (boolean, optional, default false)
- `provider_options` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/llm/configurations/{config_id}

**Delete LLM Configuration**

Delete an LLM provider configuration.

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/configurations/{config_id}/assignments

**Get LLM Configuration Assignments**

Get the users directly assigned to an LLM configuration (admin only).

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/configurations/{config_id}/profile

**Get LLM Configuration Profile**

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/llm/configurations/{config_id}/profile

**Update LLM Configuration Profile**

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

JSON body, `ProfileOverridesRequest`:

- `overrides` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/configurations/{config_id}/profile/refresh

**Refresh LLM Configuration Profile**

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/configurations/{config_id}/set-default

**Set Default LLM Provider**

Set a specific LLM configuration as the default provider.

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/configurations/{config_id}/test

**Test LLM Configuration**

Test connectivity and functionality of an LLM configuration.

Key permission: `admin`.

Path parameters:

- `config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/models/discover

**Discover Provider Models**

Key permission: `admin`.

JSON body, `ModelDiscoveryRequest`:

- `type` (string, required)
- `base_url` (string, optional, default '')
- `api_key` (string, optional)
- `config_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/native/checkpoints

**List Native LLM Checkpoints**

List HF-layout checkpoints available to the 'native' LLM provider.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/native/checkpoints/gemma3-tokenizer/fetch

**Fetch Gemma-3 Chat Tokenizer Assets**

Fetch the gemma3 chat-tokenizer assets that lift a gemma3 TE-adoption candidate's "not downloaded yet" gate.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/profile/schema

**Get LLM Profile Schema**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/types

**List LLM Provider Types**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/user-assignments

**Get All User LLM Assignments**

Get all user LLM assignments (admin only).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/user-assignments

**Assign LLM to User**

Assign an LLM configuration to a user (admin only).

Key permission: `admin`.

JSON body, `UserLLMAssignmentRequest`:

- `user_id` (string, required)
- `llm_config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/llm/user-assignments/{user_id}/{llm_config_id}

**Unassign LLM from User**

Remove LLM configuration assignment from a user (admin only).

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)
- `llm_config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Media

### GET /api/admin/thumbnails

**Get Thumbnail Settings**

Active thumbnail profile, the named profiles and their estimated cost, current disk usage, and any regeneration run.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/thumbnails/job

**Get Thumbnail Regeneration Job**

The current or most recent regeneration run, or null.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/thumbnails/regenerate

**Regenerate Thumbnails**

Start re-rendering every thumbnail whose profile is out of date.

Key permission: `admin`.

Response `202`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/thumbnails/regenerate/cancel

**Cancel Thumbnail Regeneration**

Stop the running regeneration after the file it is on.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Media Index

### POST /api/media-index/backfill

**Backfill Media Index Queue**

Queue untagged history; optionally requeue files tagged by an older model.

Key permission: `admin`.

JSON body, `BackfillRequest`:

- `retag_stale` (boolean, optional, default false)
- `pass_type` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media-index/models-status

**Tagger + Vision-embedder Model Status**

Presence/path/size of the local tagger and vision-embedder weights, plus whether each is currently resident in memory and any in-flight fetch job for it. Admin only.

Key permission: `admin`.

Query parameters:

- `tagger_model` (string, optional): Override the saved tagger model id (e.g. an unsaved admin edit)
- `vision_model` (string, optional): Override the saved vision-embedder model id (e.g. an unsaved admin edit)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-index/process

**Process Media Index Queue**

Drain a batch of pending queue items inline.

Key permission: `admin`.

JSON body, `ProcessPendingRequest`:

- `pass_type` (string, optional, default 'tags')
- `batch_size` (integer, optional, 1.0 to 100.0, default 8)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media-index/status

**Media Index Status**

Queue counts, tagged-file count and the active tagger provenance.

Key permission: `admin`.

Query parameters:

- `pass_type` (string, optional): Restrict queue counts to one pass type

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Model Layouts

### GET /api/models/layouts

**Available model layout profiles**

Key permission: `admin`.

Response `200`, `ModelLayoutListResponse`:

- `layouts` (array of ModelLayoutSummary, optional)
- `load_errors` (object of array of string, optional)

## Model Roots

### GET /api/models/roots

**List Model Roots**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/roots

**Create Model Root**

Key permission: `admin`.

JSON body, `CreateRootRequest`:

- `path` (string, required)
- `label` (string, optional)
- `bindings` (array of BindingRequest, optional, default [])
- `read_only` (boolean, optional, default false)
- `write_types` (array of string, optional, default [])
- `profile` (string, optional)

Response `201`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/roots/browse

**Browse Subfolders Of A Model Folder**

Key permission: `admin`.

Query parameters:

- `path` (string, required)
- `sub` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/roots/detect

**Detect Model Root Layout**

Key permission: `admin`.

JSON body, `DetectRootRequest`:

- `path` (string, required)
- `profile` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/roots/order

**Reorder Model Roots**

Key permission: `admin`.

JSON body, `ReorderRootsRequest`:

- `model_type` (string, optional)
- `root_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/roots/write

**Set Model Root Write Target**

Key permission: `admin`.

JSON body, `SetWriteRootRequest`:

- `model_type` (string, optional)
- `root_id` (string, required)
- `subdir` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/models/roots/{root_id}

**Update Model Root**

Key permission: `admin`.

Path parameters:

- `root_id` (string, required)

JSON body, `UpdateRootRequest`:

- `label` (string, optional)
- `path` (string, optional)
- `read_only` (boolean, optional)
- `bindings` (array of BindingRequest, optional)
- `remove_types` (array of string, optional)
- `remove_bindings` (array of BindingRefRequest, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/roots/{root_id}

**Delete Model Root**

Key permission: `admin`.

Path parameters:

- `root_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/models/roots/{root_id}/bindings

**Update Model Root Binding**

Key permission: `admin`.

Path parameters:

- `root_id` (string, required)

JSON body, `BindingScanRequest`:

- `model_type` (string, required)
- `subdir` (string, required)
- `scan_headers` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/roots/{root_id}/probe

**Probe Model Root**

Key permission: `admin`.

Path parameters:

- `root_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Models

### GET /api/models/assignment-summary

**Get Model Assignment Summary**

Direct-user and group assignment counts for every model (admin only).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/attributes

**Create Attribute Definition**

Create a model attribute definition (admin only).

Key permission: `admin`.

JSON body, `CreateAttributeDefinitionRequest`:

- `key` (string, required)
- `label` (string, required)
- `field_type` (string, required)
- `model_types` (array of string, optional, default [])
- `config` (object, optional, default {})
- `default_value` (any, optional)
- `description` (string, optional)
- `per_user` (boolean, optional, default false)
- `admin_only` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/attributes/{definition_id}

**Update Attribute Definition**

Update a model attribute definition (admin only). On a system definition, `key`/`field_type` are immutable.

Key permission: `admin`.

Path parameters:

- `definition_id` (string, required)

JSON body, `UpdateAttributeDefinitionRequest`:

- `key` (string, optional)
- `label` (string, optional)
- `field_type` (string, optional)
- `model_types` (array of string, optional)
- `config` (object, optional)
- `default_value` (any, optional)
- `description` (string, optional)
- `per_user` (boolean, optional)
- `admin_only` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/attributes/{definition_id}

**Delete Attribute Definition**

Delete a model attribute definition (admin only). Rejected for system definitions.

Key permission: `admin`.

Path parameters:

- `definition_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/cleanup

**Cleanup Deleted Models**

Remove models from index that no longer exist on disk.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/download

**Download and Index Model**

Download a model from a URL and index it.

Key permission: `admin`.

JSON body, `DownloadModelRequest`:

- `name` (string, required)
- `link` (string, required)
- `size` (string, required)
- `sha256` (string, required)
- `model_type` (string, optional, default 'checkpoint')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/downloads

**Queue Recommendation Download**

Queue a `model` field recommendation for download (admin only). Provider-gated - see docs/presets.md "recommendations".

Key permission: `admin`.

JSON body, `RecommendationDownloadRequest`:

- `name` (string, required)
- `model_type` (string, optional, default 'checkpoint')
- `provider` (string, optional)
- `ref` (string, optional)
- `link` (string, optional)
- `sha256` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/index

**Index Models**

Start model indexing process.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/indexing/digests

**Queue Files For Indexing**

Key permission: `admin`.

JSON body, `IndexingDigestsRequest`:

- `mode` (one of all, selected, matching, required)
- `model_ids` (array of string, optional)
- `query` (string, optional)
- `model_type` (string, optional)
- `exclude_model_ids` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/indexing/pause

**Pause Background Indexing**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/indexing/pending

**List Files Waiting To Be Indexed**

Key permission: `admin`.

Query parameters:

- `query` (string, optional, default ''): Case-insensitive filename substring
- `model_type` (string, optional): Only files of this model type
- `root_id` (string, optional): Only files under this model root
- `offset` (integer, optional, at least 0, default 0)
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/indexing/resume

**Resume Background Indexing**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/indexing/settings

**Update Indexing Settings**

Key permission: `admin`.

JSON body, `IndexingSettingsRequest`:

- `level` (one of low, normal, max, optional)
- `policy` (one of unset, background, on_demand, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/indexing/status

**Get Indexing Status**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/info/fetch

**Fetch Provider Info**

Fetch model metadata from marketplace providers.

Key permission: `admin`.

JSON body, `ModelInfoFetchRequest`:

- `model_ids` (array of string, optional)
- `provider` (string, required)
- `force_refresh` (boolean, optional, default false)
- `wait` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/thumbnails/generate

**Generate Thumbnails**

Generate thumbnails from videos for models that don't have images.

Key permission: `admin`.

Query parameters:

- `model_ids` (array of string, optional): Specific model IDs to process (if empty, processes all models)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/unindexed-count

**Count Unindexed Models**

Count model files on disk not yet indexed, by type - no hashing, no writes.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/user-assignments

**Assign Model to User**

Assign a model to a user (admin only).

Key permission: `admin`.

JSON body, `UserModelAssignmentRequest`:

- `user_id` (string, required)
- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/user-assignments/{user_id}

**Get User Model Assignments**

Get model assignments for a user (admin only).

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/user-assignments/{user_id}/{model_id}

**Unassign Model from User**

Unassign a model from a user (admin only).

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)
- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/{model_id}

**Delete Model**

Delete model from index (does not delete file).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/{model_id}/assignments

**Get Model Assignments**

Get the users directly assigned to a model (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/{model_id}/availability

**Get Model Availability**

List the backends that can load this model, with each one's ref, size and confidence.

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/description

**Update Model Description**

Update description for a model (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `UpdateDescriptionRequest`:

- `description` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/{model_id}/digest

**Request A Model's Content Hash**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Query parameters:

- `retry` (boolean, optional, default false): Try again a file that failed to be read

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/metadata

**Update Model Metadata**

Update a model's shared attribute values (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `UpdateModelMetadataRequest`:

- `values` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/preview

**Update Model Preview**

Set or clear a model's admin-set preview media (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `UpdateModelPreviewRequest`:

- `preview` (ModelPreviewInput, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/{model_id}/previews

**Add Model Preview**

Append one preview to a model's preview list (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `AddModelPreviewRequest`:

- `preview` (ModelPreviewInput, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/previews/order

**Reorder Model Previews**

Reorder a model's preview list (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `ReorderModelPreviewsRequest`:

- `ordered_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/{model_id}/previews/{preview_id}

**Delete Model Preview**

Remove one preview from a model's preview list (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)
- `preview_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/prompting-guidance

**Update Model Prompting Guidance**

Update the admin-authored prompting guidance for a model (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `UpdatePromptingGuidanceRequest`:

- `prompting_guidance` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/tags

**Update Model Tags**

Update tags for a model (admin only).

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `src__features__models__dto__UpdateTagsRequest`:

- `tag_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/type

**Set Model Type**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

JSON body, `SetModelTypeRequest`:

- `model_type` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/{model_id}/type

**Reset Model Type to Automatic**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Pipes

### GET /api/pipes/{pipe_name}

**Get Pipe Install State**

One pipe's status, requirements, and why its last install failed. Admin only.

Key permission: `admin`.

Path parameters:

- `pipe_name` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/pipes/{pipe_name}/install

**Install Pipe Requirements**

Start installing a pipe's requirements. Admin only.

Key permission: `admin`.

Path parameters:

- `pipe_name` (string, required)

Response `200`: the body is not described in the schema.

## Plans admin

### GET /api/admin/plans

**Plans, kinds and plan settings**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/plans

**Create a plan**

Key permission: `admin`.

JSON body, `PlanBody`:

- `name` (string, optional, default '')
- `description` (string, optional)
- `limits` (array of object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/groups

**Groups and their plans**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/plans/groups/{group_id}

**Set a group's plan**

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `AssignBody`:

- `plan_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/groups/{group_id}/impact

**Preview a group plan change**

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Query parameters:

- `plan_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/kinds

**Registered limit kinds**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/settings

**Plan settings**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/plans/settings

**Change plan settings**

Key permission: `admin`.

JSON body, `SettingsBody`:

- `default_plan_id` (string, optional)
- `exempt_admins` (boolean, optional)
- `day_timezone` (string, optional)
- `contact_line` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/users

**Users with usage per limit**

Key permission: `admin`.

Query parameters:

- `q` (string, optional)
- `plan_id` (string, optional)
- `sort` (string, optional)
- `limit` (integer, optional, 1 to 500, default 100)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/users/{user_id}

**A user's plan resolution and usage**

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/plans/users/{user_id}

**Set a user's personal override**

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

JSON body, `AssignBody`:

- `plan_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/plans/{plan_id}

**A plan with its assignments and usage**

Key permission: `admin`.

Path parameters:

- `plan_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/admin/plans/{plan_id}

**Replace a plan**

Key permission: `admin`.

Path parameters:

- `plan_id` (string, required)

JSON body, `PlanBody`:

- `name` (string, optional, default '')
- `description` (string, optional)
- `limits` (array of object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/admin/plans/{plan_id}

**Delete a plan**

Key permission: `admin`.

Path parameters:

- `plan_id` (string, required)

Query parameters:

- `reassign_to` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Plugins

### GET /api/plugins

**List All Plugins**

List all plugins with their status (admin only - plugin management view).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/plugins/catalog

**Read a Plugin Catalog**

Key permission: `admin`.

JSON body, `PluginCatalogRequest`:

- `url` (string, required, 1 to 2048 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/plugins/catalog/install

**Install a Plugin from a Catalog**

Key permission: `admin`.

JSON body, `PluginCatalogInstallRequest`:

- `url` (string, required, 1 to 2048 characters)
- `plugin_id` (string, required, 1 to 64 characters)
- `version` (string, required, 1 to 128 characters)
- `sha256` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/plugins/scan

**Scan for Plugins**

Rescan plugin directories to discover new or updated plugins (admin only).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/settings/audit

**Get Plugin Settings Audit Trail**

Who changed which plugin setting and when (admin only).

Key permission: `admin`.

Query parameters:

- `plugin_id` (string, optional)
- `limit` (integer, optional, default 200)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/setup

**Get Plugin Setup Progress**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/{plugin_id}

**Get Plugin Details**

Get details for a specific plugin (admin only - plugin management view).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/plugins/{plugin_id}

**Delete Plugin**

Delete a plugin from the database (does not remove files). Re-scan to rediscover (admin only).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/plugins/{plugin_id}/disable

**Disable Plugin**

Disable a plugin and unregister its hooks (admin only).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/plugins/{plugin_id}/enable

**Enable Plugin**

Enable a plugin and register its hooks (admin only).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/{plugin_id}/settings

**Get Plugin Settings**

Get a plugin's global settings (admin only).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/plugins/{plugin_id}/settings

**Update Plugin Settings**

Update a plugin's global settings (admin only).

Key permission: `admin`.

Path parameters:

- `plugin_id` (string, required)

JSON body, `PluginSettingsUpdateRequest`:

- `settings` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Presets

### GET /api/presets/{preset_id}/admin

**Get Preset Admin Detail**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/assign

**Assign Preset to Users**

Assign a preset to multiple users (admin only).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body: a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/assignments

**Get Preset Assignments**

Get assignment summary for a preset (admin only).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/configuration

**Get Preset Configuration**

Get a preset's declared `configuration:` schema merged with its stored admin-set values.

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/{preset_id}/configuration

**Set Preset Configuration**

Set admin-set configuration values for a preset (admin only).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body: a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/{preset_id}/display

**Set Preset Name, Description and Cover**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body (optional): any.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/display/cover

**Upload Preset Cover**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Form upload (multipart):

- `file` (file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/fixed-prompt

**Get Preset Fixed Prompt Text**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/{preset_id}/fixed-prompt

**Set Preset Fixed Prompt Text**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body: any.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/form-overrides

**Get Preset Form Overrides**

Get `mode`'s unmerged field inventory and current admin per-field overrides (admin only; defaults to the preset's first mode when `mode` is omitted).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `mode` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/{preset_id}/form-overrides

**Set Preset Form Overrides**

Set admin per-field form overrides for one mode of a preset (admin only). Sending an empty object or `null` for a field clears its override.

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body: a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/install

**Install Preset**

Install a preset for the current user.

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/mirrors

**Create Preset Mirror**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body (optional): any.

Response `201`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/{preset_id}/modes

**Set Mirror Modes**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

JSON body (optional): any.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/reload

**Reload Preset**

Reload a preset configuration from disk and return its updated state.

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/requirements

**Get Preset Requirements**

Check this preset's declared `requirements:` against this instance (admin only).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `backend_id` (string, optional)
- `refresh` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/unassign/{user_id}

**Unassign Preset from User**

Unassign a preset from a user (admin only).

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)
- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/uninstall

**Uninstall Preset**

Uninstall a preset (admin only) - removes all user assignments.

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Prompts

### GET /api/prompts/embedding-status

**Local prompt-embedding model status**

Presence/path/size of the local text-embedder weights on disk, plus whether they're currently resident in memory and any in-flight fetch job for them. Admin only.

Key permission: `admin`.

Query parameters:

- `model_name` (string, optional): Override the saved model id (e.g. an unsaved admin edit)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Providers

### POST /api/providers/{provider_id}/initialize

**Initialize a provider**

Initialize a provider with current settings.

Key permission: `admin`.

Path parameters:

- `provider_id` (string, required)

Response `200`: the body is not described in the schema.

## Recipes

### GET /api/recipes

**Available recipes**

Key permission: `admin`.

Response `200`: the body is not described in the schema.

### GET /api/recipes/runs

**Recipe runs, newest first**

Key permission: `admin`.

Query parameters:

- `recipe_id` (string, optional)
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the body is not described in the schema.

### GET /api/recipes/runs/{run_id}

**Recipe-run detail**

Key permission: `admin`.

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

### POST /api/recipes/runs/{run_id}/actions

**Apply a recipe-run action (pause|resume|cancel|retry_step)**

Key permission: `admin`.

Path parameters:

- `run_id` (string, required)

JSON body, `RecipeRunActionRequest`:

- `action` (string, optional)
- `step_key` (string, optional)
- `selections` (object of string, optional)
- `modes` (array of string, optional)
- `extras` (array of string, optional)
- `target` (string, optional)

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

### POST /api/recipes/runs/{run_id}/consent/{step_key}

**Approve the step a recipe run is parked on**

Key permission: `admin`.

Path parameters:

- `run_id` (string, required)
- `step_key` (string, required)

JSON body (optional): GrantConsentRequest.

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

### GET /api/recipes/step-kinds

**Recipe step kinds this instance can run**

Key permission: `admin`.

Response `200`: the body is not described in the schema.

### POST /api/recipes/variants/download

**Download one variant of a recipe slot**

Key permission: `admin`.

JSON body, `VariantDownloadRequest`:

- `recipe_id` (string, required, at least 1 characters)
- `artifact_id` (string, required, at least 1 characters)
- `variant_id` (string, required, at least 1 characters)

Response `202`: the body is not described in the schema.

### GET /api/recipes/variants/model/{model_id}

**The recipe slot an installed model belongs to**

Key permission: `admin`.

Path parameters:

- `model_id` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/recipes/variants/preset/{preset_id}

**Download variants a preset's recipes offer**

Key permission: `admin`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `model_type` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/recipes/{recipe_id}

**Recipe detail**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)

Response `200`, `RecipeDetail`:

- `id` (string, required)
- `name` (string, required)
- `summary` (string, required)
- `description` (string, required)
- `engine` (string, required)
- `category` (string, required)
- `artifact_count` (integer, required)
- `step_count` (integer, required)
- `presets` (array of RecipeLinkedPresetView, optional)
- `source` (string, required)
- `plugin_id` (string, optional)
- `total_download_bytes` (integer, optional)
- `blurb` (string, optional)
- `min_vram_gb` (integer, optional)
- `recommended_vram_gb` (integer, optional)
- `sources` (array of string, optional)
- `first_run` (boolean, optional, default false)
- `last_completed_at` (string (date-time), optional)
- `preset_id` (string, optional)
- `modes_total` (integer, optional)
- `modes_ready` (integer, optional)
- `missing_bytes` (integer, optional)
- `installed_bytes` (integer, optional)
- `modes` (array of RecipeModeView, optional)
- `steps` (array of RecipeStepView, optional)
- `artifacts` (array of RecipeArtifactView, optional)
- `smoke` (RecipeSmokeView, optional)
- `load_errors` (array of string, optional)

### GET /api/recipes/{recipe_id}/modes/{mode}/removal

**What removing a mode's files would delete**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)
- `mode` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/recipes/{recipe_id}/modes/{mode}/removal

**Delete the files only this mode uses**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)
- `mode` (string, required)

JSON body, `ModeRemovalRequest`:

- `model_ids` (array of string, optional)

Response `200`: the body is not described in the schema.

### POST /api/recipes/{recipe_id}/plan

**Preview a recipe run without starting it**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)

JSON body (optional): RecipePlanRequest.

Response `200`: the body is not described in the schema.

### GET /api/recipes/{recipe_id}/readiness

**Whether this instance is ready to run a recipe**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)

Response `200`, `ReadinessReport`:

- `overall` (string, required)
- `checks` (array of ReadinessCheck, required)

### POST /api/recipes/{recipe_id}/runs

**Start a recipe run from the admin page**

Key permission: `admin`.

Path parameters:

- `recipe_id` (string, required)

JSON body, `StartRecipeRunRequest`:

- `recipe_version` (integer, optional)
- `preselect_modes` (array of string, optional)

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

## Remote Model Sync

### GET /api/admin/remote-models/{backend_id}

**Remote Model Sync View**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/remote-models/{backend_id}/push

**Push Models To Worker**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

JSON body, `ModelIdsRequest`:

- `model_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/remote-models/{backend_id}/transfers

**List Worker Model Transfers**

Key permission: `admin`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Settings

### GET /api/admin/backups

**Get Backup Overview**

The backup settings, the archives on disk, the media mirror and the cron line.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/backups/job

**Get Backup Job**

The last run's state, or null when nothing has run this process.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/backups/run

**Run Backup**

Take a backup now, at the requested tier or the configured default.

Key permission: `admin`.

JSON body (optional): BackupRunRequest.

Response `202`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/admin/backups/{name}

**Delete Backup**

Remove one archive from the backup directory.

Key permission: `admin`.

Path parameters:

- `name` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/housekeeping

**Get Housekeeping Overview**

Retention windows, what they would remove right now, and the last pass.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/admin/housekeeping/generations

**Delete Generations Matching Criteria**

Delete every generation, for every user, matching the given criteria.

Key permission: `admin`.

Query parameters:

- `older_than_days` (integer, optional)
- `without_media` (boolean, optional, default false)
- `statuses` (string, optional)
- `keep_favorites` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/housekeeping/generations/preview

**Preview Generation Deletion**

How many generations, across every user, the given criteria match.

Key permission: `admin`.

Query parameters:

- `older_than_days` (integer, optional)
- `without_media` (boolean, optional, default false)
- `statuses` (string, optional)
- `keep_favorites` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/housekeeping/run

**Run Housekeeping**

Apply every retention window once, now.

Key permission: `admin`.

Response `202`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/admin/logs/tail

**Get Log Tail**

The last `lines` log entries, optionally filtered to `level` and above.

Key permission: `admin`.

Query parameters:

- `lines` (integer, optional, 1 to 5000, default 500)
- `level` (LogLevel, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/admin/restart

**Restart Application**

Restart the server process in place (admin only). No graceful drain - use sparingly.

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/settings

**Update Application Settings**

Update application settings with new configuration values.

Key permission: `admin`.

JSON body: a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/settings/{key}

**Update Setting by Key**

Update a specific setting by key. Creates user override if user is authenticated.

Key permission: `admin`.

Path parameters:

- `key` (string, required)

JSON body, `SettingUpdateRequest`:

- `value` (any, required)
- `description` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Stats

### GET /api/stats/breakdown

**Ranked counts for one dimension**

Key permission: `admin`.

Query parameters:

- `dimension` (DimensionParam, required)
- `limit` (integer, optional, 1 to 100, default 10)
- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/dimensions

**Dimensions valid for /breakdown**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/durations

**Duration histogram and percentiles**

Key permission: `admin`.

Query parameters:

- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/overview

**Aggregate usage totals**

Key permission: `admin`.

Query parameters:

- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/presets/resources

**Peak/average VRAM, RAM and CPU per preset (durable generation_stats store)**

Key permission: `admin`.

Query parameters:

- `limit` (integer, optional, 1 to 200, default 10)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/presets/timing

**Cold vs. warm start counts/averages per preset (durable generation_stats store)**

Key permission: `admin`.

Query parameters:

- `limit` (integer, optional, 1 to 200, default 10)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/spend

**Cloud spend per backend and per model**

Key permission: `admin`.

Query parameters:

- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/storage

**Output file sizes and resolutions**

Key permission: `admin`.

Query parameters:

- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD
- `bucket` (BucketParam, optional, default 'day')
- `limit` (integer, optional, 1 to 200, default 30)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/stats/timeseries

**A metric bucketed over time**

Key permission: `admin`.

Query parameters:

- `metric` (MetricParam, optional, default 'count')
- `bucket` (BucketParam, optional, default 'day')
- `from` (string, optional): Inclusive start date, YYYY-MM-DD
- `to` (string, optional): Inclusive end date, YYYY-MM-DD

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## System & Health

### GET /api/system/backends

**Get Per-Backend Hardware Snapshot**

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/system/monitoring/interval

**Set Monitoring Interval**

Set the system monitoring update interval in seconds.

Key permission: `admin`.

Query parameters:

- `interval` (number, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Tags

### POST /api/tags/models/bulk

**Bulk Add/Remove Model Tags**

Key permission: `admin`.

JSON body, `BulkModelTagsRequest`:

- `model_ids` (array of string, optional)
- `add` (array of string, optional)
- `remove` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/tags/models/selection

**Tags On Selected Models**

Key permission: `admin`.

JSON body, `ModelSelectionTagsRequest`:

- `model_ids` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## user-groups

### GET /api/user-groups

**List All Groups**

Get all user groups (admin only).

Key permission: `admin`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/user-groups

**Create Group**

Create a new user group (admin only).

Key permission: `admin`.

JSON body, `GroupCreate`:

- `name` (string, required)
- `description` (string, optional)
- `content_policy` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/user/{user_id}

**Get User's Groups**

Get all groups a user belongs to (admin only).

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/{group_id}

**Get Group**

Get group details with counts (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/user-groups/{group_id}

**Update Group**

Update a user group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `GroupUpdate`:

- `name` (string, optional)
- `description` (string, optional)
- `content_policy` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/user-groups/{group_id}

**Delete Group**

Delete a user group (admin only, cascades memberships and assignments).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/{group_id}/llms

**Get Group LLMs**

Get all LLM configurations assigned to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/user-groups/{group_id}/llms

**Assign LLMs**

Assign LLM configurations to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `LLMConfigIds`:

- `llm_config_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/user-groups/{group_id}/llms/{llm_config_id}

**Unassign LLM**

Unassign an LLM configuration from a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)
- `llm_config_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/{group_id}/members

**Get Group Members**

Get all members of a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/user-groups/{group_id}/members

**Add Members**

Add users to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `MemberIds`:

- `user_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/user-groups/{group_id}/members/{user_id}

**Remove Member**

Remove a user from a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)
- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/{group_id}/models

**Get Group Models**

Get all models assigned to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/user-groups/{group_id}/models

**Assign Models**

Assign models to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `ModelIds`:

- `model_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/user-groups/{group_id}/models/{model_id}

**Unassign Model**

Unassign a model from a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)
- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/user-groups/{group_id}/presets

**Get Group Presets**

Get all presets assigned to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/user-groups/{group_id}/presets

**Assign Presets**

Assign presets to a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)

JSON body, `PresetIds`:

- `preset_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/user-groups/{group_id}/presets/{preset_id}

**Unassign Preset**

Unassign a preset from a group (admin only).

Key permission: `admin`.

Path parameters:

- `group_id` (string, required)
- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## users

### POST /api/users

**Create User**

Create new user (admin only).

Key permission: `admin`.

JSON body, `src__features__users__dto__UserCreate`:

- `username` (string, required)
- `email` (string, required)
- `password` (string, required)
- `account_type` (string, optional, default 'USER')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/users/{user_id}

**Delete User**

Delete user (admin only, cannot delete self).

Key permission: `admin`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## WebSocket

### WS /ws/admin

**Open a WebSocket**

Key permission: `admin`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.

### WS /ws/automations

**Open a WebSocket**

Key permission: `admin`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.

### WS /ws/downloads

**Open a WebSocket**

Key permission: `admin`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.
