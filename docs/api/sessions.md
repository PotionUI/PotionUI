---
title: "Generate from a saved session"
order: 14
category: API
category_order: 90
---

# Generate from a saved session

Configure a generation once in the app, then send only the prompt from a script. A **session** is what the Generate page saves for a preset: the mode, the prompt, every form value, the prompt variables and the selected models. The session routes start a generation from it and let you change just the parts you name, so a script never has to know the preset's form.

This page is self-contained. You need a key with **Can read** (to find a session and read its template) and **Can generate** (to start a generation), and a session you saved in the app.

```bash
BASE="${POTIONUI_URL:-http://localhost:26730}"
```

```powershell
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
```

## The whole script

This script prints the fields the session lets you change, generates from it with a new prompt and a new seed, waits for the result and saves it. Put the session id in `POTIONUI_SESSION`.

`examples/generate_from_session.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"

curl -sS --fail-with-body "$BASE/api/sessions/$POTIONUI_SESSION/request" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data.fields[] | "\(.name)  \(.type)  \(.value)"'

STARTED=$(curl -sS --fail-with-body -X POST "$BASE/api/sessions/$POTIONUI_SESSION/generate" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"overrides": {"prompt": "a glass potion bottle on a shelf", "seed": -1}}')
ID=$(echo "$STARTED" | jq -r '.data.generation_id')

while true; do
  STATUS=$(curl -sS --fail-with-body "$BASE/api/generations/$ID/status" \
    -H "Authorization: Bearer $POTIONUI_KEY" | jq -r '.data.status')
  case "$STATUS" in completed|failed|cancelled) break ;; esac
  sleep 1
done
[ "$STATUS" = "completed" ] || { echo "generation $STATUS"; exit 1; }

curl -sS --fail-with-body "$BASE/api/generations/history/$ID" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data.files[] | "\(.id) \(.mime_type)"' \
  | while read -r FILE_ID MIME; do
      NAME="$ID-$FILE_ID.${MIME#*/}"
      curl -sS --fail-with-body "$BASE/api/media/files/$FILE_ID" \
        -H "Authorization: Bearer $POTIONUI_KEY" -o "$NAME"
      echo "saved $NAME"
    done
```

`examples/generate_from_session.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$session = $env:POTIONUI_SESSION

$template = Invoke-RestMethod -Uri "$base/api/sessions/$session/request" -Headers $headers
foreach ($item in $template.data.fields) {
    Write-Output "$($item.name)  $($item.type)  $($item.value)"
}

$body = '{"overrides": {"prompt": "a glass potion bottle on a shelf", "seed": -1}}'
$started = Invoke-RestMethod -Method Post -Uri "$base/api/sessions/$session/generate" -Headers $headers -ContentType "application/json" -Body $body
$id = $started.data.generation_id

do {
    Start-Sleep -Seconds 1
    $state = (Invoke-RestMethod -Uri "$base/api/generations/$id/status" -Headers $headers).data
} while ($state.status -in @("pending", "running"))

if ($state.status -ne "completed") { throw "generation $($state.status)" }

$generation = (Invoke-RestMethod -Uri "$base/api/generations/history/$id" -Headers $headers).data
foreach ($file in $generation.files) {
    $name = "$id-$($file.id).$($file.mime_type.Split('/')[1])"
    Invoke-WebRequest -Uri "$base/api/media/files/$($file.id)" -Headers $headers -OutFile $name
    Write-Output "saved $name"
}
```

`examples/generate_from_session.py`
```python
import os
import time
from pathlib import Path

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=60,
)
session_id = os.environ["POTIONUI_SESSION"]

template = client.get(f"/api/sessions/{session_id}/request")
template.raise_for_status()
for item in template.json()["data"]["fields"]:
    print(item["name"], item["type"], item.get("value"))

started = client.post(
    f"/api/sessions/{session_id}/generate",
    json={"overrides": {"prompt": "a glass potion bottle on a shelf", "seed": -1}},
)
started.raise_for_status()
generation_id = started.json()["data"]["generation_id"]

while True:
    state = client.get(f"/api/generations/{generation_id}/status").json()["data"]
    if state["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(1)

if state["status"] != "completed":
    raise SystemExit(f"generation {state['status']}")

generation = client.get(f"/api/generations/history/{generation_id}").json()["data"]
for file in generation["files"]:
    name = f"{generation_id}-{file['id']}.{file['mime_type'].split('/')[-1]}"
    Path(name).write_bytes(client.get(f"/api/media/files/{file['id']}").content)
    print("saved", name)
```

The rest of this page explains each step.

## Find the session

`GET /api/sessions` (needs `read`) lists your sessions across all presets, pinned first and then newest first. Filter with `preset_id`, `name` (the exact name) or `q` (sessions whose name contains the text, without regard to case). You only ever see your own sessions.

```bash
curl -sS "$BASE/api/sessions?q=portrait" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/sessions?q=portrait" -Headers $headers
```

Each item is:

```json
{
  "id": "01JC0SESSION",
  "name": "Portrait",
  "preset_id": "flux-test",
  "preset_name": "Flux Test",
  "modes": ["txt2img"],
  "prompts": {"txt2img": "a lighthouse"},
  "pinned": false,
  "updated_at": "2026-11-12T09:30:00Z",
  "version": 3
}
```

A session can hold several modes. When it does, the other routes need `mode`; without it the answer is `400` with the error `mode_required` and a `modes` list.

## Read the template

`GET /api/sessions/{session_id}/request?mode=txt2img` (needs `read`) tells you what a script may change, what the session holds, and the request it resolves to. `mode` is only needed when the session has several.

```bash
curl -sS "$BASE/api/sessions/SESSION_ID/request" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/sessions/SESSION_ID/request" -Headers $headers
```

```json
{
  "success": true,
  "data": {
    "session": {"id": "01JC0SESSION", "name": "Portrait", "preset_id": "flux-test", "preset_name": "Flux Test",
                "modes": ["txt2img"], "mode": "txt2img", "version": 3,
                "settings_hash": "cae464182ffc56da...", "updated_at": "2026-11-12T09:30:00Z"},
    "prompt": {"positive": "a lighthouse", "negative": "blur", "batch": 1,
               "variables": [{"name": "mood", "type": "choice", "options": ["noir", "sunlit"], "mode": "shuffle"}]},
    "shortcuts": {"seed": "seed", "count": "quantity"},
    "fields": [
      {"name": "steps", "label": "Steps", "type": "slider", "kind": "value", "value": 30, "default": 20,
       "min": 1, "max": 60, "locked": false, "media": false, "required": false},
      {"name": "resolution", "label": "Resolution", "type": "select", "kind": "value", "value": "1024x1024",
       "options": ["1024x1024", "1216x832"], "locked": false, "media": false, "required": false},
      {"name": "image", "label": "Image", "type": "image", "kind": "media", "value": "uploads/a.png",
       "locked": false, "media": true, "required": false}
    ],
    "resolved_request": {"preset_id": "flux-test", "prompts": [{"positive": "a lighthouse", "negative": "blur"}],
                         "mode": "txt2img", "form_data": {"seed": 5, "quantity": 2, "steps": 30}},
    "problems": [],
    "editor_only": null
  }
}
```

- `fields` is the session's form, flattened. `kind` is `value`, `media` or `model`. A field the administrator locked has `locked: true` and no `value` or `default`; fields the administrator hid are not listed, and neither their values nor any hidden text appears in any answer.
- `shortcuts` names the form fields that `seed` and `count` set (`null` when the form has none).
- `problems` lists what would stop a generation, with a `200` answer: `session_preset_unavailable`, `preset_not_found`, `session_model_unavailable`, `input_missing`, `field_locked`, `mode_not_available`, `no_prompt`.
- `resolved_request` is the request the session resolves to, ready for `POST /api/generations/start`. Shuffled choices and variables are drawn again on each call, like a click on Generate. Sending it to `/start` makes a frozen copy that no longer depends on the session.

## Generate with overrides

`POST /api/sessions/{session_id}/generate` (needs `generate`) starts a generation. Every key of the body is optional, and `{}` generates exactly what the session says. An unknown key anywhere is refused with `422`.

```bash
curl -sS -X POST "$BASE/api/sessions/SESSION_ID/generate" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode": "txt2img", "overrides": {"prompt": "a glass potion bottle, paper label that reads ELIXIR", "negative_prompt": "blurry", "seed": -1, "count": 4, "variables": {"mood": "noir"}, "form": {"resolution": "1216x832"}}, "idempotency_key": "nightly-2026-11-12"}'
```

```powershell
$body = '{"mode": "txt2img", "overrides": {"prompt": "a glass potion bottle, paper label that reads ELIXIR", "negative_prompt": "blurry", "seed": -1, "count": 4, "variables": {"mood": "noir"}, "form": {"resolution": "1216x832"}}, "idempotency_key": "nightly-2026-11-12"}'
Invoke-RestMethod -Method Post -Uri "$base/api/sessions/SESSION_ID/generate" -Headers $headers -ContentType "application/json" -Body $body
```

| Override | Effect |
| --- | --- |
| `prompt` | Replaces the whole positive side: the session's segments and prompt tabs are dropped. It expands like any prompt (`{a|b}`, `${var}`, kit citations). |
| `negative_prompt` | Replaces the negative side. |
| `prompts` | A batch: `[{"positive": "...", "negative": "..."}]`, up to 20. Cannot be combined with `prompt`. More than one needs a preset that supports batches. |
| `variables` | Values for variables the session defines, as `{name: template}`. |
| `seed` | Sets the form's seed field; `-1` picks a new seed. Refused with `seed_not_supported` when the form has none. |
| `count` | Sets the form's `quantity` field. A form whose count field has another name (see `shortcuts` in the template) refuses it with `count_not_supported`; set that field through `form` instead. |
| `form` | Any field of the session's form, by name, applied after `seed` and `count` and checked like `/start` checks a form. |

Other keys of the body: `mode`, `if_settings_hash` (see below), `idempotency_key`, `tag_ids`, `collection_ids` and `dry_run`. You cannot override the preset, the form variant, the layout or a Director document.

The answer is the answer of `/api/generations/start` plus what was resolved:

```json
{
  "success": true,
  "data": {
    "generation_id": "01JC...",
    "status": {"status": "pending"},
    "queue_position": null,
    "details_url": "https://host/history?generation=01JC...",
    "session": {"id": "01JC0SESSION", "mode": "txt2img", "version": 3, "settings_hash": "cae464182ffc56da..."},
    "resolved_request": {"preset_id": "flux-test", "prompts": [{"positive": "a glass potion bottle...", "negative": "blurry"}],
                         "mode": "txt2img", "form_data": {"seed": -1, "quantity": 4, "steps": 30, "resolution": "1216x832"}},
    "notes": []
  }
}
```

Follow the generation with the routes on the Generating page. `notes` lists saved form keys that the preset's form no longer has and were ignored.

## Dry run

Send `"dry_run": true` to check a request without starting anything. It needs the same `generate` permission, validates everything, and returns `{"dry_run": true, "session": {...}, "resolved_request": {...}, "notes": []}`. Use it to test overrides.

## When the session changed

Pin the settings you tested by sending the `settings_hash` from the template as `if_settings_hash`. If someone has changed the session since, the answer is `409` with the error `session_changed`, the current `settings_hash`, and `changed_fields` (a best-effort list: the prompt, the negative prompt, the mode or the names of form fields that changed). The hash covers the settings of the mode without layout, so saving only a layout change, or saving nothing new, does not trip it. Without `if_settings_hash` the script always uses the session as it is now, so editing the session in the app changes what the script generates.

## What is refused

Sessions of **Video Director** and **Music Director** modes cannot be started from a script this way, because their timeline lives in the editor. The template shows `editor_only` with a message, empty `fields` and a `null` `resolved_request`, and generating answers `422` with the error `session_needs_editor`. To generate such a video from a script, send a full request to `/api/generations/start` with the Director document in `form_data.video_director`.

Errors of the session routes, in the usual `{"detail": {"success": false, "error": "<code>", "message": "..."}}` shape:

| Status | Error | Extra fields |
| --- | --- | --- |
| 400 | `mode_required` | `modes` |
| 404 | `session_not_found`, `preset_not_found` | |
| 404 | `session_mode_not_found` | `modes` |
| 503 | `storage_unavailable` | Your storage location could not be resolved, so input files cannot be checked; try again later |
| 403 | `session_preset_unavailable` | `preset_id`, `preset_name` |
| 403 | `session_model_unavailable` | `models`: a list of `field`, `model_id`, `name` |
| 403 | `insufficient_scope` | The `WWW-Authenticate` header names the permission |
| 409 | `session_changed` | `settings_hash`, `changed_fields` |
| 422 | `session_needs_editor` | `supported` |
| 422 | `input_missing` | `inputs`: a list of `field`, `path` |
| 422 | `unknown_field`, `unknown_variable` | `names`, `known` |
| 422 | `field_locked` | `names` |
| 422 | `seed_not_supported`, `count_not_supported`, `conflicting_overrides`, `too_many_prompts`, `batch_not_supported`, `no_prompt`, `mode_not_available`, `session_empty` | |
| 422 | `form_validation_failed` | `field_errors`, `coercions`, `stripped` |

Errors that `/api/generations/start` can give, such as `model_not_found`, `idempotency_key_conflict` and plan limits, pass through unchanged. Someone else's session id always answers `404` with `session_not_found`, whatever the key's permissions.

## From an MCP assistant

The same session can be used from MCP. The `list_sessions` tool lists your sessions, or describes one when you give its `session_id`. The `start_generation` tool takes `session_id` instead of `preset_id`, together with `prompt`, `seed`, `count`, `variables` and `form_overrides` (the `form` override); the two ids cannot be combined. Giving both ids is refused with `conflicting_arguments`. `start_generation` spends, so it stays off until you allow it in your MCP tool settings. A refusal comes back as a tool error with the same text as the `message` above.
