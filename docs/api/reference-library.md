---
title: "Reference: History, library and prompts"
order: 43
category: API
category_order: 90
---

# Reference: History, library and prompts

Your own content: the library, kits, collections, tags, saved prompts and segments, phrasebook and workspaces.

Generated from the routes of the running application. Do not edit by hand.

## Auto-organize

### GET /api/organize/activity

**What the rules did**

Key permission: `read`.

Query parameters:

- `subject` (string, optional)
- `rule_id` (string, optional)
- `before` (string, optional)
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/catalog

**Facts and actions a rule can use**

Key permission: `read`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/collections/{scope}/{collection_id}/rules

**Rules that fill a collection**

Key permission: `read`.

Path parameters:

- `scope` (string, required)
- `collection_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/facts/{key}/options

**Choices for a fact**

Key permission: `read`.

Path parameters:

- `key` (string, required)

Query parameters:

- `subject` (string, optional)
- `q` (string, optional, default '')
- `limit` (integer, optional, 1 to 200, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/jobs

**Apply-to-existing jobs**

Key permission: `read`.

Query parameters:

- `active` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/jobs/{job_id}

**One job**

Key permission: `read`.

Path parameters:

- `job_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/jobs/{job_id}/cancel

**Cancel a job**

Key permission: `content`.

Path parameters:

- `job_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/preview

**Count what a rule would match**

Key permission: `read`.

JSON body, `PreviewBody`:

- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `rule_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/preview/matches

**Page through what a rule would match**

Key permission: `read`.

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

### GET /api/organize/provenance/{item_type}/{item_id}

**Which rules filed an item**

Key permission: `read`.

Path parameters:

- `item_type` (string, required)
- `item_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/rules

**List rules**

Key permission: `read`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/rules

**Create a rule**

Key permission: `content`.

JSON body, `RuleBody`:

- `name` (any, optional)
- `subject` (string, optional)
- `match` (string, optional)
- `conditions` (array of any, optional)
- `actions` (array of any, optional)
- `enabled` (boolean, optional)
- `stop_after` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/rules/reorder

**Reorder rules**

Key permission: `content`.

JSON body, `ReorderBody`:

- `subject` (string, required)
- `rule_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/rules/{rule_id}

**Get a rule**

Key permission: `read`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/organize/rules/{rule_id}

**Replace a rule**

Key permission: `content`.

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

### PATCH /api/organize/rules/{rule_id}

**Switch, rename or set stop on a rule**

Key permission: `content`.

Path parameters:

- `rule_id` (string, required)

JSON body, `RulePatch`:

- `name` (any, optional)
- `enabled` (boolean, optional)
- `stop_after` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/organize/rules/{rule_id}

**Delete a rule**

Key permission: `content`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/rules/{rule_id}/apply-existing

**Apply a rule to existing items**

Key permission: `content`.

Path parameters:

- `rule_id` (string, required)

JSON body (optional): ApplyExistingBody.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/rules/{rule_id}/duplicate

**Duplicate a rule**

Key permission: `content`.

Path parameters:

- `rule_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/runs/{run_id}/items

**What a run did, item by item**

Key permission: `read`.

Path parameters:

- `run_id` (string, required)

Query parameters:

- `status` (string, optional)
- `page` (integer, optional, at least 1, default 1)
- `page_size` (integer, optional, 1 to 100, default 25)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/runs/{run_id}/retry

**Try the waiting items of a run again**

Key permission: `content`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/organize/runs/{run_id}/undo

**Undo a run**

Key permission: `content`.

Path parameters:

- `run_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/summary

**Rule counts for the sidebar**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/organize/templates

**Starter rules**

Key permission: `read`.

Query parameters:

- `subject` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Collections

### GET /api/collections

**List Collections**

List all of the current user's collections within scope, with generation counts.

Key permission: `read`.

Query parameters:

- `scope` (one of history, library, prompts, required): Which folder tree to list: 'history', 'library', or 'prompts'
- `include_descendants` (boolean, optional, default true): Roll sub-collection items up into each parent's item_count

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections

**Create Collection**

Create a new collection.

Key permission: `content`.

JSON body, `CreateCollectionRequest`:

- `name` (string, required)
- `scope` (one of history, library, prompts, required)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections/bulk-move

**Bulk Move Collections**

Reparent several collections in one call (parent_id null = move to root).

Key permission: `content`.

JSON body, `BulkMoveCollectionsRequest`:

- `collection_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/collections/{collection_id}

**Rename Collection**

Rename a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `UpdateCollectionRequest`:

- `name` (string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/collections/{collection_id}

**Delete Collection**

Delete a collection (cascade removes all memberships).

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

Query parameters:

- `scope` (one of history, library, prompts, required): Which folder tree the collection belongs to

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections/{collection_id}/delete-entries

**Delete Collection Entries**

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections/{collection_id}/members

**Add Collection Members**

Add generations to a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionMembersRequest`:

- `generation_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/collections/{collection_id}/members

**Remove Collection Members**

Remove generations from a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionMembersRequest`:

- `generation_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/collections/{collection_id}/move

**Move Collection**

Reparent a collection in the folder tree (parent_id null = move to root).

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `MoveCollectionRequest`:

- `scope` (one of history, library, prompts, required)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections/{collection_id}/prompts

**Add Collection Prompts**

Add saved prompts to a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionPromptMembersRequest`:

- `prompt_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/collections/{collection_id}/prompts

**Remove Collection Prompts**

Remove saved prompts from a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionPromptMembersRequest`:

- `prompt_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/collections/{collection_id}/uploads

**Add Collection Library Items**

Add library uploads to a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionUploadMembersRequest`:

- `upload_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/collections/{collection_id}/uploads

**Remove Collection Library Items**

Remove library uploads from a collection.

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `CollectionUploadMembersRequest`:

- `upload_ids` (array of string, required)
- `scope` (one of history, library, prompts, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Dictionaries

### GET /api/dictionaries/models

**List supported model types**

Get available model types

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Filters

### GET /api/filters

**Available filters**

Key permission: `read`.

Response `200`: the body is not described in the schema.

### GET /api/filters/mine

**List my filters**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/filters/mine

**Create a filter of my own**

Key permission: `content`.

JSON body, `CreateFilterRequest`:

- `name` (string, required, 1 to 64 characters)
- `description` (string, optional, 0 to 240 characters)
- `group` (string, optional, 1 to 24 characters)
- `intensity` (integer, optional, 0.0 to 100.0, default 100)
- `steps` (array of object, required)
- `lut` (string, optional)
- `has_lut` (boolean, optional, default false)
- `source_id` (string, optional, 0 to 120 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/filters/mine/{filter_id}

**Update a filter of my own**

Key permission: `content`.

Path parameters:

- `filter_id` (string, required)

JSON body, `UpdateFilterRequest`:

- `name` (string, optional, 1 to 64 characters)
- `description` (string, optional, 0 to 240 characters)
- `group` (string, optional, 1 to 24 characters)
- `intensity` (integer, optional, 0.0 to 100.0)
- `steps` (array of object, optional)
- `lut` (string, optional)
- `has_lut` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/filters/mine/{filter_id}

**Delete a filter of my own**

Key permission: `content`.

Path parameters:

- `filter_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/filters/schema

**The filter.yml JSON schema**

Key permission: `read`.

Response `200`: the body is not described in the schema.

### GET /api/filters/{filter_id}/lut

**A filter's .cube LUT**

Key permission: `read`.

Path parameters:

- `filter_id` (string, required)

Response `200`: the body is not described in the schema.

## Formulas

### GET /api/formulas

**List Formulas**

Key permission: `read`.

Query parameters:

- `preset_id` (string, required, at least 1 characters)
- `mode` (string, required, at least 1 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/formulas

**Create Formula**

Key permission: `content`.

JSON body, `CreateFormulaRequest`:

- `variant` (string, optional, 0 to 200 characters)
- `groups` (array of FormulaGroupSelection, required)
- `values` (object, required)
- `signatures` (object of object, optional)
- `preset_version` (string, optional, 0 to 64 characters, default '')
- `preset_id` (string, required, 1 to 200 characters)
- `mode` (string, required, 1 to 200 characters)
- `name` (string, required, 0 to 120 characters)
- `note` (string, optional, 0 to 1000 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/formulas/{formula_id}

**Update Formula**

Key permission: `content`.

Path parameters:

- `formula_id` (string, required)

JSON body, `UpdateFormulaRequest`:

- `name` (string, optional, 0 to 120 characters)
- `note` (string, optional, 0 to 1000 characters)
- `content` (FormulaContent, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/formulas/{formula_id}

**Delete Formula**

Key permission: `content`.

Path parameters:

- `formula_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/formulas/{formula_id}/duplicate

**Duplicate Formula**

Key permission: `content`.

Path parameters:

- `formula_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/formulas/{formula_id}/plan

**Plan Applying a Formula**

Key permission: `read`.

Path parameters:

- `formula_id` (string, required)

JSON body, `PlanFormulaRequest`:

- `form_name` (string, optional)
- `current_values` (object, optional)
- `lora_mode` (one of replace, add, optional, default 'replace')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Inspirations

### GET /api/inspirations

**List Inspirations Feed**

Key permission: `read`.

Query parameters:

- `query` (string, optional)
- `limit` (integer, optional, default 50)
- `offset` (integer, optional, default 0)
- `collection_id` (string, optional)
- `author_id` (string, optional)
- `saved` (boolean, optional)
- `include_descendants` (boolean, optional, default true)
- `unsorted` (boolean, optional, default false)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/inspirations

**Publish Inspiration**

Key permission: `content`.

JSON body, `PublishInspirationRequest`:

- `generation_id` (string, required)
- `filenames` (array of string, required)
- `title` (string, required, 1 to 200 characters)
- `description` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/inspirations/collections

**List My Inspiration Collections**

Key permission: `read`.

Query parameters:

- `include_descendants` (boolean, optional, default true)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/inspirations/collections

**Create Inspiration Collection**

Key permission: `content`.

JSON body, `CreateInspirationCollectionRequest`:

- `name` (string, required, at least 1 characters)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/inspirations/collections/{collection_id}

**Update Inspiration Collection**

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `UpdateInspirationCollectionRequest`:

- `name` (string, optional)
- `parent_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/inspirations/collections/{collection_id}

**Delete Inspiration Collection**

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/inspirations/collections/{collection_id}/items

**Add Inspiration To Collection**

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)

JSON body, `InspirationCollectionItemRequest`:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/inspirations/collections/{collection_id}/items/{inspiration_id}

**Remove Inspiration From Collection**

Key permission: `content`.

Path parameters:

- `collection_id` (string, required)
- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/inspirations/{inspiration_id}

**Get Inspiration**

Key permission: `read`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/inspirations/{inspiration_id}

**Delete Inspiration**

Key permission: `content`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/inspirations/{inspiration_id}/comments

**List Inspiration Comments**

Key permission: `read`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/inspirations/{inspiration_id}/comments

**Add Inspiration Comment**

Key permission: `content`.

Path parameters:

- `inspiration_id` (string, required)

JSON body, `CommentCreateRequest`:

- `body` (string, required, 1 to 2000 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/inspirations/{inspiration_id}/comments/{comment_id}

**Delete Inspiration Comment**

Key permission: `content`.

Path parameters:

- `inspiration_id` (string, required)
- `comment_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/inspirations/{inspiration_id}/params

**Get Inspiration Params Snapshot**

Key permission: `read`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/inspirations/{inspiration_id}/save

**Unsave Inspiration**

Key permission: `content`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/inspirations/{inspiration_id}/save-to-library

**Save Inspiration To Library**

Key permission: `content`.

Path parameters:

- `inspiration_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/media/inspirations/{inspiration_id}/{filename}

**Serve Inspiration Media**

Key permission: `read` or `mcp`.

Path parameters:

- `inspiration_id` (string, required)
- `filename` (string, required)

Response `200`: the body is not described in the schema.

## Keybindings

### GET /api/keybindings

**Get Effective Keybindings**

Get effective keybindings for the current user.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/keybindings/defaults

**Get Default Keybindings**

Get all default keybindings.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/keybindings/reset

**Reset All Keybindings**

Reset all user keybinding overrides to defaults.

Key permission: `content`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/keybindings/{action_id}

**Update Keybinding**

Set a user keybinding override.

Key permission: `content`.

Path parameters:

- `action_id` (string, required)

JSON body, `UpdateKeybindingRequest`:

- `key` (string, optional)
- `modifiers` (string, optional, default '')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/keybindings/{action_id}

**Reset Keybinding**

Reset a single keybinding to default.

Key permission: `content`.

Path parameters:

- `action_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Kits

### GET /api/kits

**List Kits**

Key permission: `read`.

Query parameters:

- `label` (string, optional)
- `search` (string, optional)
- `limit` (integer, optional, 1 to 200, default 50)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits

**Create Kit**

Key permission: `content`.

JSON body, `CreateKitRequest`:

- `name` (string, required)
- `handle` (string, optional)
- `label` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/add-history-items

**Add History Items To A Kit**

Key permission: `content`.

JSON body, `AddHistoryItemsRequest`:

- `file_ids` (array of string, required)
- `kit_id` (string, optional)
- `new_kit` (NewKitSpec, optional)
- `names` (object of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/add-library-items

**Add Library Items To A Kit**

Key permission: `content`.

JSON body, `AddLibraryItemsRequest`:

- `upload_ids` (array of string, required)
- `kit_id` (string, optional)
- `new_kit` (NewKitSpec, optional)
- `names` (object of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/kits/order

**Reorder Kits**

Key permission: `content`.

JSON body, `OrderRequest`:

- `ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/resolve

**Resolve Kit Citations**

Key permission: `read`.

JSON body, `ResolveRequest`:

- `text` (string, optional, default '')
- `cites` (array of string, optional)
- `loaded` (array of string, optional)
- `field` (string, optional)
- `preset_id` (string, optional)
- `mode` (string, optional)
- `form_data` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/kits/usage

**Kits Using Library Items**

Key permission: `read`.

Query parameters:

- `upload_ids` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/kits/{kit_id}

**Get Kit**

Key permission: `read`.

Path parameters:

- `kit_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/kits/{kit_id}

**Update Kit**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

JSON body, `UpdateKitRequest`:

- `name` (string, optional)
- `handle` (string, optional)
- `label` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/kits/{kit_id}

**Delete Kit**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/{kit_id}/duplicate

**Duplicate Kit**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/{kit_id}/items

**Add Kit Item**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

JSON body, `AddItemRequest`:

- `upload_id` (string, optional)
- `text` (string, optional)
- `name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/kits/{kit_id}/items/upload

**Upload A File Into A Kit**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

Form upload (multipart):

- `file` (file, required)
- `name` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/kits/{kit_id}/items/{item_id}

**Update Kit Item**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)
- `item_id` (string, required)

JSON body, `UpdateItemRequest`:

- `name` (string, optional)
- `text` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/kits/{kit_id}/items/{item_id}

**Remove Kit Item**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)
- `item_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/kits/{kit_id}/order

**Reorder Kit Items**

Key permission: `content`.

Path parameters:

- `kit_id` (string, required)

JSON body, `OrderRequest`:

- `ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/kits/{kit_id}/usage

**Where A Kit Is Cited**

Key permission: `read`.

Path parameters:

- `kit_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Library

### POST /api/library/bulk-delete-by-criteria

**Bulk Delete Library Items by Criteria**

Delete every library item of the current user matching criteria, AND-ed.

Key permission: `content`.

JSON body, `BulkDeleteLibraryByCriteriaRequest`:

- `tag_ids` (array of string, optional)
- `older_than_days` (integer, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `media_type` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/library/count-by-criteria

**Count Library Items by Criteria**

Count the current user's library items matching criteria, AND-ed (for confirmation preview).

Key permission: `read`.

JSON body, `BulkDeleteLibraryByCriteriaRequest`:

- `tag_ids` (array of string, optional)
- `older_than_days` (integer, optional)
- `created_from` (string, optional)
- `created_to` (string, optional)
- `media_type` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/library/export

**Export Library Items as Zip**

Export the given library items as a single zip.

Key permission: `read`.

JSON body, `LibraryExportRequest`:

- `item_ids` (array of string, required)

Response `200`: the body is not described in the schema.

### GET /api/library/facets

**Library Facets**

Per-media-type counts for the current user's library view.

Key permission: `read`.

Query parameters:

- `collection_id` (string, optional): Count only items in this collection
- `include_descendants` (boolean, optional, default true): With collection_id: include sub-collections' items
- `unsorted` (boolean, optional, default false): Count only items in no collection
- `favorites_only` (boolean, optional, default false): Count favorite items only

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/library/items

**List Library Items**

List the current user's library items, newest first.

Key permission: `read`.

Query parameters:

- `media_type` (string, optional): 'image' | 'video' | 'audio'
- `tag_ids` (string, optional): Comma-separated tag ids; an item must have ALL of them
- `collection_id` (string, optional): Only items in this collection
- `search` (string, optional): Substring match on the item's original filename
- `include_descendants` (boolean, optional, default true): With collection_id: include sub-collections' items
- `unsorted` (boolean, optional, default false): Only items in no collection
- `favorites_only` (boolean, optional, default false): Only favorite items
- `limit` (integer, optional, default 50)
- `offset` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/library/items/from-generation

**Copy Generated File Into Library**

Copy a generated file into the library as a standalone resource.

Key permission: `content`.

JSON body, `CopyFromGenerationRequest`:

- `file_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/library/items/{item_id}

**Get Library Item**

Get one library item.

Key permission: `read`.

Path parameters:

- `item_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/library/items/{item_id}

**Delete Library Item**

Delete one library item (row, file and memberships).

Key permission: `content`.

Path parameters:

- `item_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/library/items/{item_id}/favorite

**Set Library Item Favorite**

Key permission: `content`.

Path parameters:

- `item_id` (string, required)

JSON body, `SetLibraryFavoriteRequest`:

- `is_favorite` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/library/items/{item_id}/tags

**Get Library Item Tags**

List a library item's tags.

Key permission: `read`.

Path parameters:

- `item_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/library/items/{item_id}/tags

**Set Library Item Tags**

Replace a library item's tags.

Key permission: `content`.

Path parameters:

- `item_id` (string, required)

JSON body, `SetLibraryTagsRequest`:

- `tag_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Notifications

### GET /api/notifications/

**List Notifications**

List the current user's notifications.

Key permission: `read`.

Query parameters:

- `limit` (integer, optional, default 50): Max results
- `before` (string, optional): Keyset cursor: id of the oldest already-seen notification
- `unread_only` (boolean, optional, default false): Only return unread notifications

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/notifications/

**Create Notification**

Create a notification for the current user.

Key permission: `content`.

JSON body, `CreateNotificationRequest`:

- `level` (NotificationLevel, required)
- `title` (string, required)
- `message` (string, optional, default '')
- `category` (string, optional, default 'frontend')
- `transient` (boolean, optional, default false)
- `show_toast` (boolean, optional, default true)
- `metadata` (object, optional)
- `type` (string, optional, default '')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/notifications/

**Clear Notifications**

Delete all of the current user's notifications.

Key permission: `content`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/notifications/preferences

**Update Notification Preferences**

Partially update the current user's notification preferences (types and/or sound).

Key permission: `content`.

JSON body, `UpdateNotificationPreferencesRequest`:

- `types` (object of boolean, optional)
- `sound` (boolean, optional)
- `chat_sound` (boolean, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/notifications/read-all

**Mark All Notifications Read**

Mark all of the current user's notifications as read.

Key permission: `content`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/notifications/types

**List Notification Types + Preferences**

List all registered notification types with the current user's effective preferences.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/notifications/{notification_id}

**Delete Notification**

Delete a single notification.

Key permission: `content`.

Path parameters:

- `notification_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/notifications/{notification_id}/read

**Mark Notification Read**

Mark a single notification as read.

Key permission: `content`.

Path parameters:

- `notification_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Phrasebook

### GET /api/phrasebook/batch-ops

**List Batch Operations**

Registered batch operations (core and plugin) for the Find & replace selection bar.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/phrasebook/categories

**Get Phrasebook Categories**

Get phrasebook categories for the current user with optional state filtering.

Key permission: `read`.

Query parameters:

- `root_only` (boolean, optional, default false)
- `state` (PhrasebookStateFilter, optional, default 'all')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/categories

**Create Phrasebook Category**

Create a new phrasebook category.

Key permission: `content`.

JSON body, `PhrasebookCategoryRequest`:

- `name` (string, required)
- `path` (string, required)
- `parent_id` (string, optional)
- `description` (string, optional, default '')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/phrasebook/categories/{category_id}

**Get Phrasebook Category**

Get a specific phrasebook category with its values.

Key permission: `read`.

Path parameters:

- `category_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/phrasebook/categories/{category_id}

**Update Phrasebook Category**

Update an phrasebook category.

Key permission: `content`.

Path parameters:

- `category_id` (string, required)

JSON body, `PhrasebookCategoryRequest`:

- `name` (string, required)
- `path` (string, required)
- `parent_id` (string, optional)
- `description` (string, optional, default '')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/phrasebook/categories/{category_id}

**Delete Phrasebook Category**

Delete an phrasebook category and all its values.

Key permission: `content`.

Path parameters:

- `category_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/phrasebook/categories/{category_id}/active

**Toggle Category Active State**

Toggle the active state of an phrasebook category.

Key permission: `content`.

Path parameters:

- `category_id` (string, required)

JSON body, `ToggleActiveRequest`:

- `is_active` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/phrasebook/categories/{category_id}/children

**Get Category Children**

Get direct children of a category.

Key permission: `read`.

Path parameters:

- `category_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/categories/{category_id}/generate-previews

**Generate Preview Images**

Generate preview images for values in a category.

Key permission: `generate`.

Path parameters:

- `category_id` (string, required)

JSON body, `GeneratePreviewRequest`:

- `session_id` (string, required)
- `prompt_template` (string, required): Prompt template containing &lt;&lt; value &gt;&gt; placeholder
- `mode` (string, required): Session mode to use (e.g., 'txt2img', 'img2img')
- `negative_prompt` (string, optional): Override session's negative prompt. Leave None to use session's default.
- `seed` (integer, optional): Fixed seed for all generations. None = random seed per generation.
- `value_ids` (array of string, optional): Specific value IDs to generate for. None = all active values in category.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/phrasebook/export/{category_id}

**Export Category as YAML**

Export a category and its values as YAML.

Key permission: `read`.

Path parameters:

- `category_id` (string, required)

Response `200`: the body is not described in the schema.

### GET /api/phrasebook/find

**Find Phrasebook Text**

Text search across categories and values: contains / whole word / regex, optional case sensitivity, scope, subtree and field limits. Every hit carries `matches` spans per field.

Key permission: `read`.

Query parameters:

- `q` (string, optional, default '')
- `mode` (PhrasebookFindMode, optional, default 'contains')
- `case_sensitive` (boolean, optional, default false)
- `scope` (PhrasebookFindScope, optional, default 'all')
- `include_inactive` (boolean, optional, default true)
- `path_prefix` (string, optional, default '')
- `fields` (string, optional)
- `limit` (integer, optional, default 200)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/import

**Import YAML File**

Import a YAML file to create phrasebook categories and values.

Key permission: `content`.

Query parameters:

- `root_category` (string, optional)

Form upload (multipart):

- `file` (file, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/phrasebook/search

**Search Phrasebook (GET)**

Search for phrasebook suggestions by path prefix (GET method) with state filtering.

Key permission: `read`.

Query parameters:

- `path` (string, required)
- `limit` (integer, optional, default 50)
- `state` (PhrasebookStateFilter, optional, default 'active')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/search

**Search Phrasebook**

Search for phrasebook suggestions by path prefix with state filtering.

Key permission: `read`.

Query parameters:

- `state` (PhrasebookStateFilter, optional, default 'active')

JSON body, `PhrasebookSearchRequest`:

- `path` (string, required)
- `limit` (integer, optional, default 50)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/values

**Create Phrasebook Value**

Create a new phrasebook value.

Key permission: `content`.

JSON body, `PhrasebookValueRequest`:

- `category_id` (string, required)
- `label` (string, required)
- `value` (string, required)
- `sort_order` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/values/batch

**Run Batch Operation**

Run the registered operation `op` over `value_ids` with `params`, all-or-nothing, between the phrasebook.batch.before/after hooks.

Key permission: `content`.

JSON body, `BatchRequest`:

- `op` (string, required)
- `value_ids` (array of string, required)
- `params` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/phrasebook/values/batch/preview

**Preview Batch Operation**

Dry-run `op` over `value_ids`; 400 `no_preview` when the operation has none.

Key permission: `read`.

JSON body, `BatchRequest`:

- `op` (string, required)
- `value_ids` (array of string, required)
- `params` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/phrasebook/values/{value_id}

**Update Phrasebook Value**

Update an phrasebook value.

Key permission: `content`.

Path parameters:

- `value_id` (string, required)

JSON body, `PhrasebookValueRequest`:

- `category_id` (string, required)
- `label` (string, required)
- `value` (string, required)
- `sort_order` (integer, optional, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/phrasebook/values/{value_id}

**Delete Phrasebook Value**

Delete an phrasebook value.

Key permission: `content`.

Path parameters:

- `value_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PATCH /api/phrasebook/values/{value_id}/active

**Toggle Value Active State**

Toggle the active state of an phrasebook value.

Key permission: `content`.

Path parameters:

- `value_id` (string, required)

JSON body, `ToggleActiveRequest`:

- `is_active` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Prompt resources

### GET /api/prompt-resources/sources/{source_id}/members

**Get an item's members**

Key permission: `read`.

Path parameters:

- `source_id` (string, required)

Query parameters:

- `item` (string, required, at least 1 characters)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Prompts

### GET /api/prompts

**List saved prompts**

Key permission: `read`.

Query parameters:

- `limit` (integer, optional, 1 to 100, default 20)
- `offset` (integer, optional, at least 0, default 0)
- `source_provider` (string, optional)
- `base_model` (string, optional)
- `model_id` (string, optional)
- `usage_hint` (string, optional)
- `collection_id` (string, optional): Only prompts in this 'prompts'-scope collection
- `include_descendants` (boolean, optional, default true): With collection_id, include sub-collections
- `unsorted` (boolean, optional, default false): Only prompts in none of the user's collections
- `favorites_only` (boolean, optional, default false): Only favorite prompts
- `q` (string, optional)
- `tags` (string, optional): Comma-separated, any-of exact tag match
- `used` (one of any, used, never, optional, default 'any')
- `used_after` (string, optional)
- `has_variables` (boolean, optional)
- `nsfw` (one of exclude, include, optional, default 'exclude')
- `sort_by` (string, optional, default 'created_at')
- `sort_order` (string, optional, default 'desc')
- `semantic` (boolean, optional, default false): With q, append filtered semantic matches after the last text-match page

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/prompts

**Create a prompt**

Key permission: `content`.

JSON body, `PromptRequest`:

- `source_provider` (string, optional)
- `source_id` (string, optional)
- `source_url` (string, optional)
- `source_group_id` (string, optional)
- `model_id` (string, optional)
- `model_name` (string, optional)
- `base_model` (string, optional)
- `cfg_scale` (number, optional)
- `steps` (integer, optional)
- `sampler` (string, optional)
- `width` (integer, optional)
- `height` (integer, optional)
- `heart_count` (integer, optional, default 0)
- `like_count` (integer, optional, default 0)
- `laugh_count` (integer, optional, default 0)
- `cry_count` (integer, optional, default 0)
- `comment_count` (integer, optional, default 0)
- `tags` (array of string, optional)
- `nsfw` (boolean, optional, default false)
- `metadata` (object, optional)
- `variables` (object, optional)
- `name` (string, optional)
- `usage_hint` (one of positive, negative, optional)
- `segments` (array of RichSegment, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/prompts/bulk-delete

**Delete multiple prompts**

Key permission: `content`.

JSON body, `PromptBulkDeleteRequest`:

- `prompt_ids` (array of string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/prompts/embed-pending

**Embed prompts awaiting vectorization**

Key permission: `content`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/export

**Export saved prompts**

Key permission: `read`.

Query parameters:

- `format` (string, optional, default 'styles-csv')
- `collection_id` (string, optional): Only prompts in this 'prompts'-scope collection
- `include_descendants` (boolean, optional, default true): With collection_id, include sub-collections

Response `200`: the body is not described in the schema.

### POST /api/prompts/find-duplicates

**Find near-duplicate prompts**

Key permission: `read`.

Query parameters:

- `threshold` (number, optional, 0.01 to 1.0, default 0.1)
- `model_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/prompts/import

**Import prompts from one or more files**

Auto-detects styles.csv, Fooocus-style JSON, dynamicprompts wildcard YAML, one-prompt-per-line text, or generation metadata embedded in an image, unless `format` overrides detection. Pasted text arrives as an ordinary file part (conventionally named `pasted.txt`) - nothing special-cases it here.

Key permission: `content`.

Form upload (multipart):

- `files` (array of file, required)
- `format` (string, optional)
- `model_id` (string, optional)
- `base_model` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/prompts/import/{importer_id}

**Run a prompt import source**

Key permission: `content`.

Path parameters:

- `importer_id` (string, required)

JSON body (optional): a free-form object.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/importers

**List available prompt import sources**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/prompts/purge-model/{model_id}

**Delete all prompts for a model**

Key permission: `content`.

Path parameters:

- `model_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/search

**Semantic-search saved prompts**

Key permission: `read`.

Query parameters:

- `q` (string, required, at least 1 characters)
- `limit` (integer, optional, 1 to 100, default 20)
- `base_model` (string, optional)
- `model_id` (string, optional)
- `source_provider` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/tags

**Tag counts across saved prompts**

Key permission: `read`.

Query parameters:

- `nsfw` (one of exclude, include, optional, default 'exclude')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/{prompt_id}

**Get a prompt**

Key permission: `read`.

Path parameters:

- `prompt_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/prompts/{prompt_id}

**Replace a prompt**

Key permission: `content`.

Path parameters:

- `prompt_id` (string, required)

JSON body, `PromptRequest`:

- `source_provider` (string, optional)
- `source_id` (string, optional)
- `source_url` (string, optional)
- `source_group_id` (string, optional)
- `model_id` (string, optional)
- `model_name` (string, optional)
- `base_model` (string, optional)
- `cfg_scale` (number, optional)
- `steps` (integer, optional)
- `sampler` (string, optional)
- `width` (integer, optional)
- `height` (integer, optional)
- `heart_count` (integer, optional, default 0)
- `like_count` (integer, optional, default 0)
- `laugh_count` (integer, optional, default 0)
- `cry_count` (integer, optional, default 0)
- `comment_count` (integer, optional, default 0)
- `tags` (array of string, optional)
- `nsfw` (boolean, optional, default false)
- `metadata` (object, optional)
- `variables` (object, optional)
- `name` (string, optional)
- `usage_hint` (one of positive, negative, optional)
- `segments` (array of RichSegment, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/prompts/{prompt_id}

**Delete a prompt**

Key permission: `content`.

Path parameters:

- `prompt_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/prompts/{prompt_id}/favorite

**Set a prompt's favorite flag**

Key permission: `content`.

Path parameters:

- `prompt_id` (string, required)

JSON body, `PromptFavoriteRequest`:

- `is_favorite` (boolean, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/prompts/{prompt_id}/generations

**Generations that used this prompt**

Completed generations submitted from this library prompt, newest first.

Key permission: `read`.

Path parameters:

- `prompt_id` (string, required)

Query parameters:

- `limit` (integer, optional, 1 to 100, default 20)
- `offset` (integer, optional, at least 0, default 0)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Segment Categories

### GET /api/segment-categories

**List segment categories**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/segment-categories

**Create a segment category**

Key permission: `content`.

JSON body, `SegmentCategoryRequest`:

- `name` (string, required)
- `description` (string, optional, default '')
- `color` (string, optional, default '#3B82F6')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/segment-categories/{category_id}

**Update a segment category**

Key permission: `content`.

Path parameters:

- `category_id` (string, required)

JSON body, `SegmentCategoryRequest`:

- `name` (string, required)
- `description` (string, optional, default '')
- `color` (string, optional, default '#3B82F6')

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/segment-categories/{category_id}

**Delete a segment category**

Key permission: `content`.

Path parameters:

- `category_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Segment Templates

### GET /api/segment-templates

**List segment templates**

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/segment-templates

**Create a segment template**

Key permission: `content`.

JSON body, `SegmentTemplateRequest`:

- `name` (string, required)
- `description` (string, optional, default '')
- `tags` (array of string, optional)
- `segments` (array of RichSegment, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/segment-templates/{template_id}

**Get a segment template**

Key permission: `read`.

Path parameters:

- `template_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/segment-templates/{template_id}

**Update a segment template**

Key permission: `content`.

Path parameters:

- `template_id` (string, required)

JSON body, `SegmentTemplateRequest`:

- `name` (string, required)
- `description` (string, optional, default '')
- `tags` (array of string, optional)
- `segments` (array of RichSegment, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/segment-templates/{template_id}

**Delete a segment template**

Key permission: `content`.

Path parameters:

- `template_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Segments

### GET /api/segments

**List saved segments**

Key permission: `read`.

Query parameters:

- `category_id` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/segments

**Create a saved segment**

Key permission: `content`.

JSON body, `SavedSegmentRequest`:

- `name` (string, required)
- `category_id` (string, required)
- `type` (string, optional, default 'content')
- `content` (string, optional, default '')
- `chips` (object of PhrasebookChip, optional)
- `resources` (object of PromptResourceRef, optional)
- `enabled` (boolean, optional, default true)
- `color` (string, optional)
- `description` (string, optional)
- `prefix` (string, optional)
- `suffix` (string, optional)
- `tags` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/segments/{segment_id}

**Get a saved segment**

Key permission: `read`.

Path parameters:

- `segment_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/segments/{segment_id}

**Update a saved segment**

Key permission: `content`.

Path parameters:

- `segment_id` (string, required)

JSON body, `SavedSegmentRequest`:

- `name` (string, required)
- `category_id` (string, required)
- `type` (string, optional, default 'content')
- `content` (string, optional, default '')
- `chips` (object of PhrasebookChip, optional)
- `resources` (object of PromptResourceRef, optional)
- `enabled` (boolean, optional, default true)
- `color` (string, optional)
- `description` (string, optional)
- `prefix` (string, optional)
- `suffix` (string, optional)
- `tags` (array of string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/segments/{segment_id}

**Delete a saved segment**

Key permission: `content`.

Path parameters:

- `segment_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Tags

### GET /api/tags

**List Tags**

List all tags of specified type with usage counts.

Key permission: `read`.

Query parameters:

- `type` (TagType, required): Tag type: MODEL or GENERATION

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/tags

**Create Tag**

Create a new tag.

Key permission: `content`.

JSON body, `CreateTagRequest`:

- `name` (string, required)
- `type` (TagType, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/tags/search

**Search Tags**

Search tags for autocomplete.

Key permission: `read`.

Query parameters:

- `q` (string, required): Search query
- `type` (TagType, required): Tag type: MODEL or GENERATION
- `limit` (integer, optional, default 10): Max results

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/tags/{tag_id}

**Update Tag**

Update tag name.

Key permission: `content`.

Path parameters:

- `tag_id` (string, required)

JSON body, `UpdateTagRequest`:

- `name` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/tags/{tag_id}

**Delete Tag**

Delete tag (cascade removes all associations).

Key permission: `content`.

Path parameters:

- `tag_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

## Workspaces

### GET /api/workspaces

**Get Workspaces**

Get all workspaces for the current user.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/workspaces

**Save Workspace**

Save a new workspace.

Key permission: `content`.

JSON body, `SaveWorkspaceRequest`:

- `name` (string, required)
- `data` (object, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/workspaces/{workspace_id}

**Get Workspace by ID**

Get a specific workspace by ID.

Key permission: `read`.

Path parameters:

- `workspace_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### PUT /api/workspaces/{workspace_id}

**Update Workspace**

Update an existing workspace.

Key permission: `content`.

Path parameters:

- `workspace_id` (string, required)

JSON body, `UpdateWorkspaceRequest`:

- `name` (string, optional)
- `data` (object, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### DELETE /api/workspaces/{workspace_id}

**Delete Workspace**

Delete a workspace.

Key permission: `content`.

Path parameters:

- `workspace_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
