---
title: "Reference: Generation and media"
order: 41
category: API
category_order: 90
---

# Reference: Generation and media

Starting, following, cancelling and fetching generations, uploads and media files, saved sessions and media tools.

Generated from the routes of the running application. Do not edit by hand.

## Elixirs

### GET /api/elixirs

**List Elixirs**

Key permission: `read`.

Response `200`: the body is not described in the schema.

### POST /api/elixirs

**Create Elixir**

Key permission: `content`.

JSON body, `CreateElixirRequest`:

- `name` (string, required, 1 to 120 characters)
- `run_mode` (one of automatic, review, optional, default 'review')
- `definition` (ElixirDefinition, optional)

Response `201`: the body is not described in the schema.

### GET /api/elixirs/presets

**List Presets**

Key permission: `read`.

Response `200`: the body is not described in the schema.

### GET /api/elixirs/runs/{run_id}

**Get Brew**

Key permission: `read`.

Path parameters:

- `run_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/runs/{run_id}/cancel

**Cancel Brew**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/runs/{run_id}/edges/{edge_id}/continue

**Continue Edge**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)
- `edge_id` (string, required)

JSON body, `SelectionRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `selected` (array of string, optional)

Response `200`: the body is not described in the schema.

### PUT /api/elixirs/runs/{run_id}/edges/{edge_id}/selection

**Save Selection**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)
- `edge_id` (string, required)

JSON body, `SelectionRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `selected` (array of string, optional)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/runs/{run_id}/nodes/{node_id}/retry-failed

**Retry Failed**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)
- `node_id` (string, required)

Response `200`: the body is not described in the schema.

### PUT /api/elixirs/runs/{run_id}/texts/{text_id}

**Edit Text**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)
- `text_id` (string, required)

JSON body, `EditTextRequest`:

- `text` (string, required, 1 to 8000 characters)

Response `200`: the body is not described in the schema.

### GET /api/elixirs/{elixir_id}

**Get Elixir**

Key permission: `read`.

Path parameters:

- `elixir_id` (string, required)

Response `200`: the body is not described in the schema.

### PUT /api/elixirs/{elixir_id}

**Update Elixir**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)

JSON body, `UpdateElixirRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `name` (string, optional, 1 to 120 characters)
- `run_mode` (one of automatic, review, optional)
- `definition` (ElixirDefinition, optional)

Response `200`: the body is not described in the schema.

### DELETE /api/elixirs/{elixir_id}

**Delete Elixir**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/elixirs/{elixir_id}/followed-sessions

**Followed Sessions**

Key permission: `read`.

Path parameters:

- `elixir_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/nodes/{node_id}/runs

**Run Node**

Key permission: `generate`.

Path parameters:

- `elixir_id` (string, required)
- `node_id` (string, required)

JSON body, `NodeRunRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `idempotency_key` (string, required, 8 to 120 characters)
- `template` (object, required)
- `session_hashes` (object, optional)

Response `201`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/nodes/{node_id}/session

**Import Node Session**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)
- `node_id` (string, required)

JSON body, `ImportSessionRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `session_id` (string, required, 1 to 64 characters)
- `version` (integer, optional, at least 1.0)
- `mode` (string, optional, 1 to 64 characters)
- `overrides` (one of keep, discard, optional)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/copy

**Copy Node Session**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)
- `node_id` (string, required)

JSON body, `NodeRevisionRequest`:

- `expected_revision` (integer, required, at least 1.0)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/follow

**Follow Node Session**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)
- `node_id` (string, required)

JSON body, `FollowSessionRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `session_id` (string, required, 1 to 64 characters)
- `mode` (string, optional, 1 to 64 characters)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/new

**Own Node Settings**

Key permission: `content`.

Path parameters:

- `elixir_id` (string, required)
- `node_id` (string, required)

JSON body, `NodeRevisionRequest`:

- `expected_revision` (integer, required, at least 1.0)

Response `200`: the body is not described in the schema.

### GET /api/elixirs/{elixir_id}/runs

**List Brews**

Key permission: `read`.

Path parameters:

- `elixir_id` (string, required)

Query parameters:

- `status` (one of all, active, finished, failed, cancelled, optional, default 'all')
- `from` (string, optional)
- `to` (string, optional)
- `run_mode` (one of review, automatic, optional)
- `scope` (one of elixir, node, optional)
- `q` (string, optional, 0 to 200 characters)
- `limit` (integer, optional, 1 to 200, default 50)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/runs

**Start Brew**

Key permission: `generate`.

Path parameters:

- `elixir_id` (string, required)

JSON body, `StartBrewRequest`:

- `expected_revision` (integer, required, at least 1.0)
- `idempotency_key` (string, required, 8 to 120 characters)
- `templates` (object, optional)
- `session_hashes` (object, optional)
- `run_mode` (one of automatic, review, optional)

Response `201`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/runs/cancel

**Cancel Brews**

Key permission: `generate`.

Path parameters:

- `elixir_id` (string, required)

JSON body, `CancelBrewsRequest`:

- `run_ids` (array of string, optional)
- `all_active` (boolean, optional, default false)

Response `200`: the body is not described in the schema.

### POST /api/elixirs/{elixir_id}/validate

**Validate Elixir**

Key permission: `read`.

Path parameters:

- `elixir_id` (string, required)

Response `200`: the body is not described in the schema.

## Generation

### POST /api/generations/export

**Export Generations as Zip**

Export the final image/video files of multiple generations as a single zip.

Key permission: `read`.

JSON body, `ExportRequest`:

- `generation_ids` (array of string, required)
- `strip_metadata` (boolean, optional, default false)

Response `200`: the body is not described in the schema.

### POST /api/generations/grids

**Start X/Y Compare Grid**

Key permission: `generate`.

JSON body, `CreateGridRequest`:

- `request` (GenerationRequest, required)
- `x_axis` (Axis, required)
- `y_axis` (Axis, optional)
- `lock_seed` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/grids/settings

**Get Compare Grid Settings**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/grids/{grid_id}

**Get Compare Grid**

Key permission: `read`.

Path parameters:

- `grid_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/generations/grids/{grid_id}

**Delete Compare Grid**

Key permission: `content`.

Path parameters:

- `grid_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/grids/{grid_id}/retry-failed

**Retry Failed Compare Cells**

Key permission: `generate`.

Path parameters:

- `grid_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/history

**Get Generation History**

Your generations with their files, a page at a time (limit and offset). Narrow the list with status, mode, preset_id, tag_ids, collection_id, favorites_only, min_rating, created_from, created_to, media_type or search.

Key permission: `read`.

Query parameters:

- `limit` (integer, optional, default 50)
- `offset` (integer, optional, default 0)
- `include_tags` (boolean, optional, default true)
- `sort_by` (string, optional)
- `sort_dir` (string, optional)
- `status` (string, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `completed_from` (string, optional)
- `completed_to` (string, optional)
- `tag_ids` (string, optional)
- `media_type` (string, optional)
- `search` (string, optional)
- `mode` (string, optional)
- `preset_id` (string, optional)
- `model_name` (string, optional)
- `min_rating` (integer, optional)
- `favorites_only` (boolean, optional, default false)
- `collection_id` (string, optional)
- `include_descendants` (boolean, optional, default true)
- `unsorted` (boolean, optional, default false)
- `used_phrasebook_value_id` (string, optional)
- `system_tag` (string, optional)
- `semantic_query` (string, optional)
- `grid_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/history/bulk-delete

**Bulk Delete Generations**

Delete multiple generations from history including all associated files.

Key permission: `content`.

JSON body, `BulkDeleteRequest`:

- `generation_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/history/bulk-delete-by-criteria

**Bulk Delete by Criteria**

Delete every generation of the current user matching criteria, AND-ed.

Key permission: `content`.

JSON body, `BulkDeleteByCriteriaRequest`:

- `tag_ids` (array of string, optional)
- `older_than_days` (integer, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `without_media` (boolean, optional, default false)
- `statuses` (array of string, optional)
- `keep_favorites` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/history/count-by-criteria

**Count Generations by Criteria**

Count the current user's generations matching criteria, AND-ed (for confirmation preview).

Key permission: `read`.

JSON body, `BulkDeleteByCriteriaRequest`:

- `tag_ids` (array of string, optional)
- `older_than_days` (integer, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `without_media` (boolean, optional, default false)
- `statuses` (array of string, optional)
- `keep_favorites` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/history/facets

**Get History Filter Facets**

Get distinct modes, presets and models (with counts) for history filter controls.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/history/version

**Get History Version Token**

A change-detection token for the caller's history. Clients poll this and refetch the list only when the token differs from the last one seen.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/history/{generation_id}

**Get Generation by ID**

One generation with its parameters, its error if it failed, and its files. Each file has an id that GET /api/media/files/{file_id} returns as bytes.

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)

Query parameters:

- `include_files` (boolean, optional, default true)
- `include_batch` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/generations/history/{generation_id}

**Delete Generation**

Delete a generation from history including all associated files.

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/history/{generation_id}/export-bundle

**Export Generation Bundle**

Portable bundle (preset, resolved seed, models, output listing) for reproducing this generation on another PotionUI instance.

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/generations/import-bundle

**Import Generation Bundle**

Parse an exported generation bundle (zip or bare generation.json) into a reuse payload for a pre-filled generate tab. Never creates a generation.

Key permission: `content`.

Form upload (multipart):

- `file` (file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/list

**List Active Generations**

List all currently active generations with their current statuses.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/memory-preview

**Preview Generation Memory Advisory**

Estimate the GPU memory a request would need, without starting it.

Key permission: `read`.

JSON body, `GenerationRequest`:

- `preset_id` (string, required)
- `prompt` (string, optional, default '')
- `negative_prompt` (string, optional, default '')
- `prompts` (array of PromptPair, optional)
- `batch_prompts` (boolean, optional, default false)
- `prompt_state` (object, optional)
- `segments` (array of SegmentInput, optional)
- `variables` (object of string, optional)
- `mode` (string, optional, default 'txt2img')
- `form_name` (string, optional)
- `form_data` (object, optional, default {})
- `source_prompt_id` (string, optional)
- `idempotency_key` (string, optional, 1 to 200 characters)
- `tag_ids` (array of string, optional)
- `collection_ids` (array of string, optional)
- `tab_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/queue

**Get Generation Queue**

Your pending generations in the order they will start, each with its queue_position (0 starts next), and your running generations.

Key permission: `read`.

Query parameters:

- `tab_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/queue/clear

**Clear A Tab's Queue**

Cancel every generation this tab has queued but not yet started.

Key permission: `generate`.

JSON body, `ClearTabQueueRequest`:

- `tab_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/start

**Start Generation**

Starts a generation from a preset and answers at once with its generation_id; the run continues in the background. Follow it with the status route or the /ws/generation socket. Repeating a request with the same idempotency_key returns the generation already started, and a different request under that key is refused with 409 idempotency_key_conflict.

Key permission: `generate`.

JSON body, `GenerationRequest`:

- `preset_id` (string, required)
- `prompt` (string, optional, default '')
- `negative_prompt` (string, optional, default '')
- `prompts` (array of PromptPair, optional)
- `batch_prompts` (boolean, optional, default false)
- `prompt_state` (object, optional)
- `segments` (array of SegmentInput, optional)
- `variables` (object of string, optional)
- `mode` (string, optional, default 'txt2img')
- `form_name` (string, optional)
- `form_data` (object, optional, default {})
- `source_prompt_id` (string, optional)
- `idempotency_key` (string, optional, 1 to 200 characters)
- `tag_ids` (array of string, optional)
- `collection_ids` (array of string, optional)
- `tab_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/upload

**Upload Generations**

Upload image files as completed generations with optional tags.

Key permission: `content`.

Query parameters:

- `tag_ids` (array of string, optional, default [])

Form upload (multipart):

- `files` (array of file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/generations/{generation_id}/cancel

**Cancel Generation**

Cancels a pending or running generation. A generation that has already finished answers 400 cancel_failed.

Key permission: `generate`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/generations/{generation_id}/favorite

**Set Generation Favorite**

Set the favorite flag for a generation.

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)

JSON body, `FavoriteRequest`:

- `is_favorite` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/{generation_id}/params/{index}

**Get Generation Parameters by Index**

Get parameters for a specific generation and image index. `data` carries `parameters` ({name: value}), `models`, `parameter_meta` ({name: {label?, type?, unit?}}, only names with recorded meta) and `parameter_order` (names in the order the preset emitted them, unordered names after them alphabetically); the last two are empty for generations recorded before parameter meta existed.

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)
- `index` (integer, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/generations/{generation_id}/rating

**Set Generation Rating**

Set the star rating (0-5, 0 = unrated) for a generation.

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)

JSON body, `RatingRequest`:

- `rating` (integer, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/{generation_id}/run-report/artifacts/{name}

**Serve Run Report Artifact**

Key permission: `read` or `mcp`.

Path parameters:

- `generation_id` (string, required)
- `name` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/generations/{generation_id}/status

**Get Generation Status**

The state of one of your generations: pending, running, completed, failed or cancelled, with its progress. Someone else's generation answers 404.

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/generations/{generation_id}/tags

**Get Generation Tags**

Get all tags for a generation

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/generations/{generation_id}/tags

**Update Generation Tags**

Replace all tags for a generation

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)

JSON body, `src__features__generation__dto__UpdateTagsRequest`:

- `tag_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/generations/{generation_id}/tags/{tag_id}

**Remove Tag from Generation**

Remove a single tag from a generation

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)
- `tag_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Media

### GET /api/media/files/{file_id}

**Serve File by ID**

The bytes of a generated file or an upload, by the file id that history returns. Optional width, height or size ask for a smaller version.

Key permission: `read` or `mcp`.

Path parameters:

- `file_id` (string, required)

Query parameters:

- `width` (integer, optional)
- `height` (integer, optional)
- `size` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/media/files/{file_id}/blob

**Get File as Blob**

Get a file as blob data for frontend to create blob URLs

Key permission: `read`.

Path parameters:

- `file_id` (string, required)

Query parameters:

- `width` (integer, optional)
- `height` (integer, optional)

Response `200`: the body is not described in the schema.

### GET /api/media/files/{file_id}/params

**Get File Parameters**

Get parameters associated with a generated file

Key permission: `read`.

Path parameters:

- `file_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/generations/{generation_id}

**List Generation Media**

List all media files for a generation

Key permission: `read`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/media/generations/{generation_id}

**Delete Generation Media**

Delete all media files for a generation

Key permission: `content`.

Path parameters:

- `generation_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/generations/{generation_id}/{filename}

**Serve Generation Media**

Serve media file from a generation

Key permission: `read` or `mcp`.

Path parameters:

- `generation_id` (string, required)
- `filename` (string, required)

Query parameters:

- `size` (string, optional)
- `animated` (boolean, optional, default false)

Response `200`: the body is not described in the schema.

### GET /api/media/presets/{preset_id}/{file_path}

**Serve Preset File**

Serve static media files from a preset's `public/` directory

No sign-in needed.

Path parameters:

- `preset_id` (string, required)
- `file_path` (string, required)

Query parameters:

- `size` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/media/tmp/{filename}

**Serve Temporary Media**

Key permission: `read` or `mcp`.

Path parameters:

- `filename` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/media/upload

**Upload Media**

Uploads an input file (multipart field file). The answer carries relative_path, which goes into a media field of form_data when you start a generation.

Key permission: `generate`.

Form upload (multipart):

- `file` (file, required)
- `purpose` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/uploads

**List My Uploads**

List the current user's media-loader uploads, newest first

Key permission: `read`.

Query parameters:

- `media_type` (string, optional)
- `limit` (integer, optional, default 20)
- `offset` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/uploads/{filename}

**Serve Uploaded Media**

Key permission: `read` or `mcp`.

Path parameters:

- `filename` (string, required)

Query parameters:

- `size` (string, optional)
- `animated` (boolean, optional, default false)

Response `200`: the body is not described in the schema.

### DELETE /api/media/uploads/{filename}

**Delete My Upload**

Delete one of the current user's uploads

Key permission: `content`.

Path parameters:

- `filename` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/uploads/{filename}/info

**Get Uploaded Media Info**

Get best-effort metadata for an already-uploaded file

Key permission: `read`.

Path parameters:

- `filename` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Media Tools

### GET /api/media-tools

**List Media Tools**

Key permission: `read`.

Query parameters:

- `kind` (string, optional)
- `scope` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-tools/analyse/panels

**Detect Panels In A Picture**

Key permission: `generate`.

JSON body, `PanelsRequest`:

- `input` (InputRef, required)
- `tolerance` (integer, optional, 0.0 to 64.0, default 12)
- `grid` (array of integer, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-tools/artifacts

**Upload An Editor Artifact**

Key permission: `generate`.

Form upload (multipart):

- `file` (file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-tools/effects/analysis

**Measure An Effect Over A Clip**

Key permission: `generate`.

JSON body, `EffectAnalysisRequest`:

- `input` (InputRef, required)
- `steps` (array of StepRequest, optional)
- `step_index` (integer, optional, at least 0.0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media-tools/effects/{tool_id}/shader

**Get An Effect Preview Shader**

Key permission: `read`.

Path parameters:

- `tool_id` (string, required)

Headers:

- `if-none-match` (string, optional)

Response `200`: the body is not described in the schema.

### GET /api/media-tools/looks/table

**Get A Look For The Live Preview**

Key permission: `read`.

Query parameters:

- `ref` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/media-tools/luts/table

**Get A LUT For The Live Preview**

Key permission: `read`.

Query parameters:

- `ref` (string, required)

Response `200`: the body is not described in the schema.

### POST /api/media-tools/preview

**Preview A Media Tool**

Key permission: `generate`.

JSON body, `PreviewRequest`:

- `tool` (string, required)
- `params` (object, optional)
- `input` (InputRef, required)
- `time_seconds` (number, optional)

Response `200`: the body is not described in the schema.

### POST /api/media-tools/preview/clip

**Render A Short Preview Clip Of An Edit Stack**

Key permission: `generate`.

JSON body, `ClipPreviewRequest`:

- `input` (InputRef, required)
- `steps` (array of StepRequest, optional)
- `start_seconds` (number, optional, at least 0.0, default 0.0)
- `duration_seconds` (number, optional, at most 10.0, default 3.0)
- `height` (integer, optional, 16.0 to 2160.0, default 480)

Response `202`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-tools/preview/frame

**Preview One Frame Of An Edit Stack**

Key permission: `generate`.

JSON body, `FramePreviewRequest`:

- `input` (InputRef, required)
- `steps` (array of StepRequest, optional)
- `time_seconds` (number, optional, default 0.0)
- `max_side` (integer, optional, 16.0 to 8192.0, default 1280)
- `format` (one of webp, png, optional, default 'webp')

Response `200`: the body is not described in the schema.

### GET /api/media-tools/previews/{preview_id}

**Get A Preview Clip**

Key permission: `read` or `mcp`.

Path parameters:

- `preview_id` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/media-tools/runs

**List My Media Tool Runs**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/media-tools/runs

**Start A Media Tool Run**

Key permission: `generate`.

JSON body, `StartRunRequest`:

- `steps` (array of StepRequest, required)
- `inputs` (array of InputRef, required)
- `output` (OutputSpec, optional)

Response `202`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media-tools/runs/{run_id}

**Get A Media Tool Run**

Key permission: `read`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/media-tools/runs/{run_id}

**Cancel A Media Tool Run**

Key permission: `generate`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Sessions

### GET /api/sessions

**List Sessions**

List the caller's sessions across presets.

Key permission: `read`.

Query parameters:

- `preset_id` (string, optional)
- `name` (string, optional)
- `q` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/sessions/preset/{preset_id}

**Get Sessions for Preset**

Get all sessions for the current user and specified preset.

Key permission: `read`.

Path parameters:

- `preset_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/sessions/save

**Save Session**

Save a new session or update existing one with same name.

Key permission: `content`.

JSON body, `SaveSessionRequest`:

- `preset_id` (string, required)
- `name` (string, required)
- `data` (object, required)
- `mode` (string, optional)
- `base_version` (integer, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/sessions/{session_id}

**Get Session by ID**

Get a specific session by ID.

Key permission: `read`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/sessions/{session_id}

**Update Session**

Update an existing session.

Key permission: `content`.

Path parameters:

- `session_id` (string, required)

JSON body, `src__features__sessions__dto__UpdateSessionRequest`:

- `name` (string, required)
- `data` (object, required)
- `mode` (string, optional)
- `base_version` (integer, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/sessions/{session_id}

**Delete Session**

Delete a session.

Key permission: `content`.

Path parameters:

- `session_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/sessions/{session_id}/generate

**Generate From A Session**

Start a generation from a saved session, sending only what changes.

Key permission: `generate`.

Path parameters:

- `session_id` (string, required)

JSON body, `GenerateFromSessionRequest`:

- `mode` (string, optional)
- `overrides` (SessionOverrides, optional)
- `if_settings_hash` (string, optional)
- `idempotency_key` (string, optional, 1 to 200 characters)
- `tag_ids` (array of string, optional)
- `collection_ids` (array of string, optional)
- `dry_run` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/sessions/{session_id}/pin

**Pin or Unpin Session**

Key permission: `content`.

Path parameters:

- `session_id` (string, required)

JSON body, `PinSessionRequest`:

- `pinned` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/sessions/{session_id}/request

**Describe What A Session Accepts**

Machine-readable template: the fields a script may change and the request they resolve to.

Key permission: `read`.

Path parameters:

- `session_id` (string, required)

Query parameters:

- `mode` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/sessions/{session_id}/versions

**List Session Versions**

List a session's version history, newest first (no payloads).

Key permission: `read`.

Path parameters:

- `session_id` (string, required)

Query parameters:

- `limit` (integer, optional, 1 to 200)
- `before` (integer, optional, at least 1)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/sessions/{session_id}/versions/{version_number}

**Get Session Version**

Get a single version's full payload.

Key permission: `read`.

Path parameters:

- `session_id` (string, required)
- `version_number` (integer, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Tab drafts

### GET /api/tab-drafts

**List Tab Drafts**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/tab-drafts/{tab_id}

**Get Tab Draft**

Key permission: `read`.

Path parameters:

- `tab_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/tab-drafts/{tab_id}

**Replace Tab Draft**

Key permission: `content`.

Path parameters:

- `tab_id` (string, required)

JSON body, `ReplaceTabDraftRequest`:

- `doc` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/tab-drafts/{tab_id}

**Delete Tab Draft**

Key permission: `content`.

Path parameters:

- `tab_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/tab-drafts/{tab_id}/ops

**Apply Tab Draft Operations**

Key permission: `content`.

Path parameters:

- `tab_id` (string, required)

JSON body, `TabDraftOperationsRequest`:

- `ops` (array of any, required)
- `base_rev` (integer, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
