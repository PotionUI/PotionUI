---
title: "Reference: Presets, forms and models"
order: 42
category: API
category_order: 90
---

# Reference: Presets, forms and models

What can be generated with: presets and their forms, models, backends and downloads.

Generated from the routes of the running application. Do not edit by hand.

## Backends

### GET /api/backends

**Get All Backends**

Get all configured backends including local and cloud instances.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/default

**Get Default Backend For Engine**

Get the default backend for an engine.

Key permission: `read`.

Query parameters:

- `engine` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/enabled

**Get Enabled Backends**

Get all enabled backends sorted by priority for load balancing.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/engines

**Get Registered Engines**

Get all registered engines (built-in + plugin-provided).

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}

**Get Backend Details**

Get detailed configuration for a specific backend by ID.

Key permission: `read`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/backends/{backend_id}/stats

**Get Backend Model Stats**

Indexed model count, total disk size, and last-indexed time for one backend.

Key permission: `read`.

Path parameters:

- `backend_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Cloud

### GET /api/cloud/models/{model_id}/capabilities

**Get a cloud model's capabilities**

Key permission: `read`.

Path parameters:

- `model_id` (string, required)

Query parameters:

- `driver` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Fields

### GET /api/fields/types

**Get Field Types**

Get the frontend manifest of every registered form field type.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Forms

### GET /api/form/defaults/{preset_id}

**Get Default Values**

Get the default form values for a specific preset configuration.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/form/options

**Get Field Options**

Get available options for a specific field type based on its configuration.

Key permission: `read`.

JSON body, `FormFieldOptionsRequest`:

- `field_type` (string, required)
- `field_config` (object, optional, default {})

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/form/validate

**Validate Form Data**

Validate submitted form data against the schema and return validation results.

Key permission: `read`.

JSON body, `FormValidationRequest`:

- `form_schema` (object, required)
- `form_data` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Model Collections

### GET /api/models/collections

**List Model Collections**

List all model collections owned by the current user with model counts.

Key permission: `read`.

Query parameters:

- `include_descendants` (boolean, optional, default true): Roll sub-collection items up into each count
- `all_models` (boolean, optional, default false): Count every model (admin only), like the models list

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/collections

**Create Model Collection**

Create a new model collection.

Key permission: `content`.

JSON body, `CreateModelCollectionRequest`:

- `name` (string, required)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/collections/bulk-move

**Bulk Move Model Collections**

Reparent several model collections in one call (parent_id null = move to root).

Key permission: `content`.

JSON body, `BulkMoveModelCollectionsRequest`:

- `collection_ids` (array of string, required)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/collections/{collection_id}

**Rename Model Collection**

Rename a model collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `UpdateModelCollectionRequest`:

- `name` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/collections/{collection_id}

**Delete Model Collection**

Delete a model collection (cascade removes all memberships).

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/models/collections/{collection_id}/members

**Add Model Collection Members**

Add models to a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `ModelCollectionMembersRequest`:

- `model_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/models/collections/{collection_id}/members

**Remove Model Collection Members**

Remove models from a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `ModelCollectionMembersRequest`:

- `model_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/collections/{collection_id}/move

**Move Model Collection**

Reparent a model collection in the folder tree (parent_id null = move to root).

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `MoveModelCollectionRequest`:

- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Models

### GET /api/models

**List Models**

List all indexed models with optional filtering.

Key permission: `read`.

Query parameters:

- `model_type` (string, optional): Filter by model type
- `tag_ids` (string, optional): Comma-separated tag IDs to filter by
- `search` (string, optional): Search models by name (case-insensitive)
- `sort_by` (string, optional, default 'indexed_at'): Field to sort by
- `sort_order` (string, optional, default 'desc'): Sort order: 'asc' or 'desc'
- `limit` (integer, optional, default 20): Limit number of results
- `offset` (integer, optional, default 0): Offset for pagination
- `include_tags` (boolean, optional, default true): Include tags
- `all_models` (boolean, optional, default false): Return all models (admin only)
- `assignment_filter` (string, optional): Filter by assignment: 'assigned' or 'unassigned'
- `assigned_user_id` (string, optional): User ID for assignment filtering
- `assigned_group_id` (string, optional): Group ID for assignment filtering
- `favorites_only` (boolean, optional, default false): Only return models favorited by the current user
- `collection_id` (string, optional): Only return models in this model collection
- `in_any_collection` (boolean, optional, default false): Only return models that belong to any of the current user's collections
- `include_descendants` (boolean, optional, default true): With collection_id: also include items of its sub-collections
- `unsorted` (boolean, optional, default false): Only models in none of the current user's collections
- `q_mode` (string, optional): How `search` matches: 'substring' (default) or 'regex'
- `indexed_from` (string, optional): Indexed on or after this day (YYYY-MM-DD, UTC)
- `indexed_to` (string, optional): Indexed on or before this day (YYYY-MM-DD, UTC)
- `used` (string, optional): Usage filter: 'any', 'used' or 'never'
- `min_uses` (integer, optional): Only models used in at least this many generations
- `last_used_from` (string, optional): Last used on or after this day (YYYY-MM-DD, UTC)
- `last_used_to` (string, optional): Last used on or before this day (YYYY-MM-DD, UTC)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/attributes

**List Attribute Definitions**

List model attribute definitions (core + plugin + admin-authored). `admin_only` definitions are filtered out for non-admins.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/downloads/{download_id}

**Get Recommendation Download Status**

Poll a recommendation download's status/progress by ID.

Key permission: `read`.

Path parameters:

- `download_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/hash/{sha256}

**Get Model by Hash**

Get model by SHA256 hash.

Key permission: `read`.

Path parameters:

- `sha256` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/stats

**Get Model Stats**

Get model indexing statistics.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/type-definitions

**List Model Type Definitions**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/types

**Get Model Types**

Get available model types and their counts.

Key permission: `read`.

Query parameters:

- `user_scoped` (boolean, optional, default false): When true, counts only models assigned to the current user
- `include_empty` (boolean, optional, default false): When true (admin, non-user_scoped only), also include known model types with zero indexed models
- `model_type` (string, optional): Narrows `tag_counts` only; type counts always ignore it
- `tag_ids` (string, optional): Comma-separated tag IDs every counted model must carry
- `search` (string, optional): Same as the list endpoint's `search`
- `assignment_filter` (string, optional): Filter by assignment: 'assigned' or 'unassigned'
- `assigned_user_id` (string, optional): User ID for assignment filtering
- `assigned_group_id` (string, optional): Group ID for assignment filtering
- `favorites_only` (boolean, optional, default false): Only count models favorited by the current user
- `collection_id` (string, optional): Only count models in this model collection
- `in_any_collection` (boolean, optional, default false): Only count models in any of the current user's collections
- `include_descendants` (boolean, optional, default true): With collection_id: also include items of its sub-collections
- `unsorted` (boolean, optional, default false): Only models in none of the current user's collections
- `q_mode` (string, optional): How `search` matches: 'substring' (default) or 'regex'
- `indexed_from` (string, optional): Indexed on or after this day (YYYY-MM-DD, UTC)
- `indexed_to` (string, optional): Indexed on or before this day (YYYY-MM-DD, UTC)
- `used` (string, optional): Usage filter: 'any', 'used' or 'never'
- `min_uses` (integer, optional): Only models used in at least this many generations
- `last_used_from` (string, optional): Last used on or after this day (YYYY-MM-DD, UTC)
- `last_used_to` (string, optional): Last used on or before this day (YYYY-MM-DD, UTC)
- `include_tag_counts` (boolean, optional, default false): Also return `tag_counts`: matching models per tag id

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/{model_id}

**Get Model by ID**

Get specific model by ID.

Key permission: `read`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/attributes/user

**Update Model User Attributes**

Update the caller's per-user attribute value overlay for a model.

Key permission: `content`.

Path parameters:

- `model_id` (string, required)

JSON body, `UpdateModelUserAttributesRequest`:

- `values` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/favorite

**Set Model Favorite**

Set or clear the favorite flag for a model (scoped to the current user).

Key permission: `content`.

Path parameters:

- `model_id` (string, required)

JSON body, `ModelFavoriteRequest`:

- `is_favorite` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/{model_id}/generations

**Get Model Generations**

Get generations that used a specific model.

Key permission: `read`.

Path parameters:

- `model_id` (string, required)

Query parameters:

- `limit` (integer, optional, default 20): Limit number of results
- `offset` (integer, optional, default 0): Offset for pagination

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/models/{model_id}/library-name

**Set Model Library Name**

Set or clear a per-user custom display name for a model.

Key permission: `content`.

Path parameters:

- `model_id` (string, required)

JSON body, `ModelLibraryNameRequest`:

- `name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/models/{model_id}/previews

**List Model Previews**

List a model's admin-set previews, ordered (position 0 = primary).

Key permission: `read`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Presets

### GET /api/presets

**List Presets**

The presets you may generate with, each with its id, name, modes and engine.

Key permission: `read`.

Query parameters:

- `include_uninstalled` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/mode-labels

**Get Mode Display Names**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/picker-state

**Get Pinned and Recent Presets**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/presets/picker-state/pinned/{preset_id}

**Pin a Preset**

Key permission: `content`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/presets/picker-state/pinned/{preset_id}

**Unpin a Preset**

Key permission: `content`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/picker-state/recent/{preset_id}

**Record a Recently Chosen Preset**

Key permission: `content`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}

**Get Preset Details**

Get detailed information about a specific preset including its configuration.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/form

**Get Form Schema**

The form of one preset for a mode: every field with its name, type and default. The field names are the keys of form_data when you start a generation.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `mode` (string, optional)
- `form_name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/models

**Get Preset Models**

List models loadable by this preset's engine, each badged with the backends holding it.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `model_type` (string, optional)
- `search` (string, optional)
- `limit` (integer, optional)
- `offset` (integer, optional, default 0)
- `tag_ids` (string, optional)
- `any_tag_ids` (string, optional)
- `favorites_only` (boolean, optional, default false)
- `tasks` (string, optional)
- `model_types` (string, optional)
- `unquantized_only` (boolean, optional, default false)
- `families` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/modes

**Get Preset Modes**

Get available generation modes (txt2img, img2img, etc.) for a specific preset.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/presets/{preset_id}/pipes

**Get Pipeline Configuration**

Get the pipeline configuration and pipe connections for a preset and mode.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `mode` (string, optional, default 'txt2img')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/presets/{preset_id}/pipes

**Get Pipeline with Form Data**

Get pipeline configuration evaluated with specific form data for dynamic pipe configuration.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Query parameters:

- `mode` (string, optional, default 'txt2img')

JSON body: a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Providers

### GET /api/providers

**List marketplace providers**

List all registered marketplace providers.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/providers/{provider_id}

**Get a provider's details**

Get details for a specific provider.

Key permission: `read`.

Path parameters:

- `provider_id` (string, required)

Response `200`, `ProviderInfo`:

- `id` (string, required)
- `name` (string, required)
- `description` (string, required)
- `website` (string, required)
- `capabilities` (array of string, required)
- `version` (string, required)
- `initialized` (boolean, required)
- `icon` (string, optional)

### GET /api/providers/{provider_id}/lookup/{sha256}

**Look up a model by file hash**

Look up model information by SHA256 hash.

Key permission: `read`.

Path parameters:

- `provider_id` (string, required)
- `sha256` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/providers/{provider_id}/settings/schema

**Get a provider's settings schema**

Get the JSON schema for a provider's settings.

Key permission: `read`.

Path parameters:

- `provider_id` (string, required)

Response `200`: the body is not described in the schema.

## Sampling

### GET /api/sampling/catalog

**Get Sampling Catalog**

Get the sampler/schedule catalog, optionally narrowed to a model family.

Key permission: `read`.

Query parameters:

- `family` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
