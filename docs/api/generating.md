---
title: "Generating"
order: 13
category: API
category_order: 90
---

# Generating

A generation is started with one request, runs in the background, and is followed by polling its status or by listening on a WebSocket. This page covers starting, following, the queue, cancelling, results, errors and limits.

The examples assume the two variables from the Quickstart, `POTIONUI_URL` and `POTIONUI_KEY`, and a key with **Can read** and **Can generate**.

```bash
BASE="${POTIONUI_URL:-http://localhost:26730}"
```

```powershell
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
```

## Find what to generate with

`GET /api/presets` lists the presets you can use. Each has an `id`, a `name`, the `modes` it supports and its `engine`.

`GET /api/presets/{preset_id}/form?mode=txt2img` returns the form for one preset and mode: the fields with their `name`, `type` and `default`. The field names are the keys of `form_data` when you start a generation.

```bash
curl -sS "$BASE/api/presets/PRESET_ID/form?mode=txt2img" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/presets/PRESET_ID/form?mode=txt2img" -Headers $headers
```

The quickest way to learn good values is to make a generation in the app and read it back with `GET /api/generations/history/{generation_id}`: its `form_data` is what you would send.

## Start a generation

`POST /api/generations/start` needs the `generate` permission.

```bash
curl -sS -X POST "$BASE/api/generations/start" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"preset_id": "PRESET_ID", "mode": "txt2img", "prompt": "a lighthouse at dusk", "negative_prompt": "blurry", "form_data": {"count": 1}, "idempotency_key": "nightly-2026-11-12"}'
```

```powershell
$body = '{"preset_id": "PRESET_ID", "mode": "txt2img", "prompt": "a lighthouse at dusk", "negative_prompt": "blurry", "form_data": {"count": 1}, "idempotency_key": "nightly-2026-11-12"}'
Invoke-RestMethod -Method Post -Uri "$base/api/generations/start" -Headers $headers -ContentType "application/json" -Body $body
```

The body:

| Field | Meaning |
| --- | --- |
| `preset_id` | Required. The preset to run. |
| `mode` | The mode, `txt2img` when omitted. |
| `prompt`, `negative_prompt` | The prompt text. A prompt can use the same template syntax as in the app, such as `{a|b}` choices. |
| `variables` | Values for `${name}` variables used in the prompt, as an object of strings. |
| `form_data` | The form's fields by name. Fields you leave out take the preset's defaults. |
| `form_name` | The form variant to use, when a mode has more than one. |
| `idempotency_key` | A string of 1 to 200 characters that makes retries safe. See below. |
| `tag_ids`, `collection_ids` | Tags and collections to put the result into when it is created. |

The answer is immediate:

```json
{
  "success": true,
  "data": {
    "generation_id": "01M784T12AM8PB480H6B8CTY66",
    "status": {"id": "01M784T12AM8PB480H6B8CTY66", "status": "running", "preset_id": "01M3SVA35XT0E7CG5JZTS9P21S", "progress": null},
    "queue_position": null
  },
  "message": null,
  "error": null
}
```

`status.status` is `running` when the generation started at once and `pending` when it waits for a free slot; then `queue_position` says where it stands. A prompt that cites a kit adds a `kits` object that lists what was used and what was skipped. See the Content page.

### Retrying safely

Send the same `idempotency_key` with a request you may repeat, such as a nightly job that could run twice. A second request with the same key returns the generation that was already started instead of starting another. If the key is reused with a different request, the answer is `409` with the error `idempotency_key_conflict`.

## Follow a generation

A generation moves through `pending` (waiting for a slot), `running`, and then ends as `completed`, `failed` or `cancelled`.

### Poll the status

`GET /api/generations/{generation_id}/status` needs `read`. Check once a second or so until `status` is one of the three final values.

```bash
curl -sS "$BASE/api/generations/GENERATION_ID/status" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/generations/GENERATION_ID/status" -Headers $headers
```

```json
{
  "success": true,
  "data": {
    "id": "01M784T12AM8PB480H6B8CTY66",
    "status": "running",
    "preset_id": "01M3SVA35XT0E7CG5JZTS9P21S",
    "progress": 0.6,
    "current_step": "Generating <<TIME:0s>>",
    "created_at": "1794495083.6033256",
    "completed_at": null
  },
  "message": null,
  "error": null
}
```

`progress` is a number from 0 to 1, or `null` when the step has no measure. `current_step` is a short description that can contain markers such as `<<TIME:0s>>` or `<<PIPE:name>>` which the app renders; strip them or ignore them in a script. Asking for a generation that is not yours answers `404` with the error `generation_not_found`.

### Listen on the WebSocket

The socket `/ws/generation` pushes progress and results as they happen, so a script does not have to poll. It needs the `read` permission. There are two ways to authenticate.

**With the key in a header** (scripts and servers). Most WebSocket libraries outside the browser can send headers when they connect:

`examples/follow_websocket.py`
```python
import json
import os

import httpx
from websockets.sync.client import connect

base = os.environ.get("POTIONUI_URL", "http://localhost:26730")
headers = {"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]}
client = httpx.Client(base_url=base, headers=headers, timeout=60)

started = client.post(
    "/api/generations/start",
    json={
        "preset_id": os.environ["POTIONUI_PRESET"],
        "mode": "txt2img",
        "prompt": "a lighthouse at dusk, oil painting",
        "form_data": json.loads(os.environ.get("POTIONUI_FORM", "{}")),
    },
)
started.raise_for_status()
generation_id = started.json()["data"]["generation_id"]

finished = ("completed", "failed", "cancelled")
with connect(base.replace("http", "ws", 1) + "/ws/generation", additional_headers=headers) as socket:
    socket.send(json.dumps({"type": "subscribe_generation", "generation_id": generation_id}))
    for raw in socket:
        message = json.loads(raw)
        kind = message["type"]
        if kind == "generation_status" and message.get("progress") is not None:
            print(f"{message['progress']:.0%}", message.get("current_step"))
        elif kind == "status_update" and message["data"]["status"] in finished:
            print(message["data"]["status"])
            break
        elif kind in ("generation_complete", "generation_error", "generation_cancelled"):
            print(kind)
            break
```

**With a ticket** (browsers, which cannot set headers on a WebSocket). Ask for a ticket, then pass it in the URL:

```bash
curl -sS -X POST "$BASE/api/auth/ws-ticket" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"path": "/ws/generation"}'
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/auth/ws-ticket" -Headers $headers -ContentType "application/json" -Body '{"path": "/ws/generation"}'
```

```json
{"success": true, "data": {"ticket": "YOUR_TICKET", "expires_in": 30}, "message": null, "error": null}
```

Then connect to `ws://localhost:26730/ws/generation?ticket=TICKET` (`wss://` when the instance is served over HTTPS). A ticket works for one connection to that path only and expires after 30 seconds, so ask for it just before you connect. A ticket for an unknown path answers `404` (`unknown_socket`); a key that lacks the socket's permission answers `403` (`insufficient_scope`). By default you may ask for up to 60 tickets in any 60 seconds.

A browser can also pass the session token in the URL as `?token=`. That still works so that older clients keep working, but it is deprecated: use a header or a ticket. An API key is never accepted in the URL.

When authentication fails the server accepts the connection and closes it at once with code `4001` and a reason such as `Invalid or expired ticket`.

**Messages.** Right after connecting the server sends `connection_established`. To follow a generation, send a message to subscribe; you can only subscribe to your own generations:

```json
{"type": "subscribe_generation", "generation_id": "01M784T12AM8PB480H6B8CTY66"}
```

The server answers `subscribed`, followed by a `status_update` with the current status, so you do not miss anything that happened before you subscribed. Check that snapshot: a generation that already ended produces no further messages. Asking for a generation that does not exist or is not yours gives `subscription_error` with a message and no details of whose it is.

| Message `type` | When | Main fields |
| --- | --- | --- |
| `connection_established` | After connecting | `client_id` |
| `subscribed` | After `subscribe_generation` | `generation_id` |
| `subscription_error` | The subscription was refused | `generation_id`, `message` |
| `status_update` | Right after subscribing | `data`: the generation status, as in `/status` |
| `generation_status` | Progress of a step | `generation_id`, `pipe_id`, `pipe_name`, `status`, `current_step`, `message`, `progress` |
| `timer_update` | A step or the whole run was timed | `generation_id`, `timer_name`, `timer_value`, `timer_unit` (`s`), `formatted_time` |
| `generation_update` | A step reported a parameter | `generation_id`, `pipe_id`, `pipe_name`, `output_type` |
| `workbench_update` | An in-progress preview image | `generation_id`, `pipe_id`, the preview image |
| `workbench_preview_clip` | A video preview of the sampling so far | `generation_id`, `pipe_id` |
| `gallery_update` | The final images | `generation_id`, `pipe_id`, `images`: base64 data |
| `pipe_artifact` | An artifact such as the seed or the models used | `generation_id`, `pipe_id` |
| `queue_update` | The generation's place in the queue changed | `generation_id`, `status`, `queue_position` (`null` once running) |
| `generation_complete` | It finished | `data`: the final status |
| `generation_error` | It failed | `generation_id`, `status`, `error_code`, `message`, `hint`, `error_id` |
| `generation_cancelled` | It was cancelled | `data`: the final status |
| `heartbeat` | Every 15 seconds | `timestamp` |
| `ping` (you send) and `pong` | Keep-alive and latency check | `timestamp` in `pong` |

The final images arrive in `gallery_update` as base64 data, which suits a quick preview. To get the real files, use the results routes below.

### Queue position

When all slots are busy a new generation waits. The answer to `POST /api/generations/start` carries `queue_position` (0 is the next one to start) and `status.status` is `pending`. While it waits, the socket sends `queue_update` messages when its place changes and when it starts. `GET /api/generations/queue` lists your pending generations in the order they will start, each with its `queue_position`, and your running ones:

```json
{
  "success": true,
  "data": {
    "pending": [{"generation_id": "01M783XQGEYRY6P8FXGXMEE198", "backend_id": "ed479d79-2d17-4ad9-976f-5d0c7b71f56e", "tab_id": null, "queue_position": 0}],
    "running": [{"generation_id": "01M783XPYMB1EFRM6DY0GCWQHY", "backend_id": "ed479d79-2d17-4ad9-976f-5d0c7b71f56e", "preset_id": "01M3SVA35XT0E7CG5JZTS9P21S", "tab_id": null, "progress": null}]
  },
  "message": null,
  "error": null
}
```

How many generations run at once depends on the backend that serves the preset. `GET /api/generations/list` shows your active generations.

## Cancel

`POST /api/generations/{generation_id}/cancel` needs `generate`. It cancels a pending or running generation and answers with `"message": "Generation cancelled successfully"` and `data` of `{"cancel_notice": null}`. The status becomes `cancelled`, and a `generation_cancelled` message goes to listeners. A generation that already finished answers `400` with the error `cancel_failed`.

```bash
curl -sS -X POST "$BASE/api/generations/GENERATION_ID/cancel" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/generations/GENERATION_ID/cancel" -Headers $headers
```

## Results and files

When the status is `completed`, `GET /api/generations/history/{generation_id}` (needs `read`) returns the generation with its parameters and its `files`. Each file has:

| Field | Meaning |
| --- | --- |
| `id` | The file id used to download it. |
| `file_type` | `IMAGE`, `VIDEO` and so on. |
| `mime_type` | For example `image/png`. |
| `file_size` | Bytes. |
| `width`, `height` | Pixels, for images and video. |
| `duration_seconds`, `fps` | For video and audio. |
| `is_final` | Whether this is a final result rather than an intermediate file. |

Download a file with `GET /api/media/files/{file_id}`, which returns the bytes. `GET /api/media/generations/{generation_id}` lists the files of a generation with a `url` for each, and `GET /api/media/generations/{generation_id}/{filename}` downloads one by name. All of these need the key in the `Authorization` header, which a browser's `<img>` tag cannot send: fetch the file in your program instead.

```bash
curl -sS "$BASE/api/media/files/FILE_ID" -H "Authorization: Bearer $POTIONUI_KEY" -o result.png
```

```powershell
Invoke-WebRequest -Uri "$base/api/media/files/FILE_ID" -Headers $headers -OutFile result.png
```

To use an image you have on disk as an input, upload it first and pass its path in the form:

`examples/upload_and_use.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"
FORM="${POTIONUI_FORM:-}"
[ -n "$FORM" ] || FORM='{}'
FIELD="${POTIONUI_INPUT_FIELD:-references}"

UPLOADED=$(curl -sS --fail-with-body -X POST "$BASE/api/media/upload" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -F "file=@$1")
RELATIVE=$(echo "$UPLOADED" | jq -r '.data.relative_path')
FORM=$(echo "$FORM" | jq --arg field "$FIELD" --arg path "$RELATIVE" '.[$field] = [$path]')

STARTED=$(curl -sS --fail-with-body -X POST "$BASE/api/generations/start" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"preset_id": "'"$POTIONUI_PRESET"'", "mode": "'"${POTIONUI_MODE:-edit}"'", "prompt": "make the liquid deep blue", "form_data": '"$FORM"'}')
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

`examples/upload_and_use.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$form = if ($env:POTIONUI_FORM) { $env:POTIONUI_FORM | ConvertFrom-Json } else { [pscustomobject]@{} }
$field = if ($env:POTIONUI_INPUT_FIELD) { $env:POTIONUI_INPUT_FIELD } else { "references" }
$mode = if ($env:POTIONUI_MODE) { $env:POTIONUI_MODE } else { "edit" }

$uploaded = curl.exe -sS --fail-with-body -X POST "$base/api/media/upload" -H "Authorization: Bearer $($env:POTIONUI_KEY)" -F "file=@$($args[0])" | ConvertFrom-Json
$form | Add-Member -NotePropertyName $field -NotePropertyValue @($uploaded.data.relative_path) -Force
$formJson = $form | ConvertTo-Json -Depth 10 -Compress

$body = @"
{"preset_id": "$($env:POTIONUI_PRESET)", "mode": "$mode", "prompt": "make the liquid deep blue", "form_data": $formJson}
"@
$started = Invoke-RestMethod -Method Post -Uri "$base/api/generations/start" -Headers $headers -ContentType "application/json" -Body $body
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

`examples/upload_and_use.py`
```python
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=60,
)

image = Path(sys.argv[1])
with image.open("rb") as handle:
    uploaded = client.post(
        "/api/media/upload",
        files={"file": (image.name, handle, mimetypes.guess_type(image.name)[0] or "application/octet-stream")},
    )
uploaded.raise_for_status()
relative_path = uploaded.json()["data"]["relative_path"]

form = json.loads(os.environ.get("POTIONUI_FORM", "{}"))
form[os.environ.get("POTIONUI_INPUT_FIELD", "references")] = [relative_path]
started = client.post(
    "/api/generations/start",
    json={
        "preset_id": os.environ["POTIONUI_PRESET"],
        "mode": os.environ.get("POTIONUI_MODE", "edit"),
        "prompt": "make the liquid deep blue",
        "form_data": form,
    },
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

Run it with the path of an image as its argument. `POTIONUI_INPUT_FIELD` is the name of the preset's image field (look it up in the form of the preset; it defaults to `references`) and `POTIONUI_MODE` is the mode (it defaults to `edit`). `POST /api/media/upload` needs `generate` and answers with `relative_path`, `url`, `width` and `height`. A field that takes several images (the form marks it `multi`) takes a list of paths, and a field that takes one image takes a single path string. Your uploads are listed by `GET /api/media/uploads`.

## Errors

Starting a generation can be refused with:

| Status | Error | Meaning |
| --- | --- | --- |
| 400 | `validation_error` | The request is wrong in a way the message explains, for example `Preset 'x' not found`. |
| 401, 403 | | The key is missing, invalid or lacks `generate`. See the Authentication page. |
| 403 | `insufficient_scope` | The preset is restricted to administrators and the key does not have `admin`. |
| 403 | `limit_exceeded` | A plan limit was reached. See below. |
| 404 | `preset_not_found`, `form_not_found`, `model_not_found` | The preset, its form or a chosen model is not available to you. A model you may not use answers the same as one that does not exist. |
| 409 | `idempotency_key_conflict` | The `idempotency_key` was used for a different request. |
| 422 | `form_validation_failed` | `form_data` does not fit the form. `field_errors` names each field and what is wrong; `stripped` lists keys the form does not have. |
| 422 | `validation_error` | The body does not fit the schema. |

A generation that was accepted can still fail while running. Its status becomes `failed`, and the status and the history record carry `error_code`, `error_message` and `error_user_message` (a readable text with a hint), and `error_id`:

```json
{
  "id": "01M784T12AM8PB480H6B8CTY66",
  "status": "failed",
  "error_code": "cloud_refused",
  "error_message": "The online generation service refused this request.",
  "error_user_message": "The online generation service refused this request.\n\n- Try a different prompt or input",
  "error_id": "01M784T12AM8PB480H6B8CTY66"
}
```

On the socket the same failure arrives as a `generation_error` message.

## Limits and quotas

Administrators can attach a plan to a person that limits what they use. A request that goes over a limit is refused with `403` and the error `limit_exceeded`:

```json
{"detail": {"success": false, "error": "limit_exceeded", "code": "daily_generations_exceeded", "message": "...", "kind": "generations_per_day", "used": 20, "limit": 20, "resets_at": "2026-11-13T00:00:00+00:00"}}
```

`code` says which limit; `message` is a sentence you can show.

| `code` | Limit | Where it applies |
| --- | --- | --- |
| `daily_generations_exceeded` | Generations per day. A batch counts once. | Starting a generation |
| `storage_quota_exceeded` | Storage space for generations and uploads | Starting a generation and uploading |
| `cloud_budget_exceeded` | Cloud spend per month. Values are hidden from people who are not administrators. | Starting a generation on a cloud preset |
| `upload_file_size_exceeded` | The largest single upload | Uploading |

Other limits that apply to scripts:

- More than five invalid API keys from one address within 60 seconds are refused for a while (`429`).
- By default at most 60 WebSocket tickets in any 60 seconds, and a ticket lives for 30 seconds.
- A kit holds up to 50 items.
