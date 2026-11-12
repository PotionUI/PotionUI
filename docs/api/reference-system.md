---
title: "Reference: System"
order: 46
category: API
category_order: 90
---

# Reference: System

Health, settings, plugins, recipes, documentation and statistics.

Generated from the routes of the running application. Do not edit by hand.

## Documentation

### GET /api/docs/content

**Get Documentation Content**

Get the raw markdown content for a doc id.

Key permission: `read`.

Query parameters:

- `id` (string, required): Doc id from the tree, e.g. 'user/getting-started'

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/docs/tree

**Get Documentation Tree**

Get the role-filtered documentation tree.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Plugins

### GET /api/plugins/frontend-extensions

**Get Frontend Extensions**

Get manifest-declared renderers + extension slot contributions from enabled plugins.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/hooks/catalog

**Get Hooks Catalog**

Get the full catalog of declared hook points (core + plugin-provided).

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/hooks/frontend

**Get Frontend Hooks**

Get all frontend hooks grouped by hook name for frontend component loading.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/pages

**Get All Plugin Pages**

Get all active plugin pages sorted by sidebar_order.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/pages/{route}

**Get Plugin Page by Route**

Get page info for a specific route.

Key permission: `read`.

Path parameters:

- `route` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/quick-actions

**Get Plugin Quick Actions**

Get quick actions from enabled plugins.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/sidebar

**Get Sidebar Items**

Get sidebar items from active plugins.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/sidebar-widgets

**Get Sidebar Widgets**

Get sidebar widgets from enabled plugins.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/plugins/{plugin_id}/assets/{file_path}

**Get Plugin Asset**

Serve static assets (JS, CSS) from plugin's frontend/dist directory.

No sign-in needed.

Path parameters:

- `plugin_id` (string, required)
- `file_path` (string, required)

Response `200`: the body is not described in the schema.

## Settings

### GET /api/settings

**Get Application Settings**

Get current application settings and configuration for the authenticated user.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/settings/all

**Get All Settings Detailed**

Get all settings with detailed information, optionally filtered by type (USER/SYSTEM).

Key permission: `read`.

Query parameters:

- `setting_type` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/settings/user

**Get User Setting Overrides**

Get all user-specific setting overrides for the authenticated user.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/settings/{key}

**Get Setting by Key**

Get a specific setting value by key for the authenticated user.

Key permission: `read`.

Path parameters:

- `key` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## System & Health

### GET /api/setup/status

**First-run setup status**

Report whether the instance needs an owner and how it may be claimed.

No sign-in needed.

Response `200`, `SetupStatus`:

- `needs_owner` (boolean, required)
- `registration_open` (boolean, required)
- `claim_requires_token` (boolean, required)

### GET /api/system/gpu-profile

**Get GPU Profile**

Key permission: `read`.

Response `200`: the body is not described in the schema.

### GET /api/system/stats

**Get System Statistics**

Get current system statistics including GPU, RAM, and CPU usage.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/system/vram

**Get Total GPU Memory**

Key permission: `read`.

Response `200`: the body is not described in the schema.

## WebSocket

### WS /ws/generation

**Open a WebSocket**

Key permission: `read`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.

### WS /ws/notifications

**Open a WebSocket**

Key permission: `read`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.

### WS /ws/system

**Open a WebSocket**

Key permission: `read`.

Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.
