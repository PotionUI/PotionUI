---
title: "Quickstart"
order: 11
category: API
category_order: 90
---

# Quickstart

Here you create a key, make your first authenticated call, generate one image from a preset, wait for it and download it. Every step is given for bash, PowerShell and Python.

## 1. Create an API key

Open **Settings**, choose **API keys** and press **Create key**. Give the key a name, choose when it expires and tick what it may do:

- **Can read**: look at your presets, gallery, prompts, library and generation status, and download files.
- **Can generate**: start and cancel generations, and upload input files.
- **Can change and delete your content**: edit and delete your gallery, prompts, library, kits, collections and tags.
- **Full administration**: users, settings, plugins and backups. Only an administrator can add it, and such a key lasts 90 days at most.
- **Use with MCP clients**: lets an MCP assistant connect with the key. See the MCP page.

For this quickstart tick **Can read** and **Can generate**. Reading is needed to check on a generation and download the result.

The key is shown once, in a box with a **Copy key** button. It starts with `pui_`. Copy it now: PotionUI keeps only a fingerprint of it and cannot show it again. If you lose it, revoke it and create another.

Put the key and your instance address in environment variables, so no script has to contain a secret. Replace `pui_YOUR_KEY` with your key.

```bash
export POTIONUI_URL=http://localhost:26730
export POTIONUI_KEY=pui_YOUR_KEY
```

```powershell
$env:POTIONUI_URL = "http://localhost:26730"
$env:POTIONUI_KEY = "pui_YOUR_KEY"
```

## 2. Make the first call

List the presets you can generate with. Each preset is one model with its own form, and it lists the modes it supports (`txt2img`, `edit`, and so on).

`examples/first_call.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"

curl -sS --fail-with-body "$BASE/api/presets" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data[] | "\(.id)  \(.name)  \(.modes | join(","))"'
```

`examples/first_call.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }

$presets = Invoke-RestMethod -Uri "$base/api/presets" -Headers $headers
foreach ($preset in $presets.data) {
    Write-Output "$($preset.id)  $($preset.name)  $($preset.modes -join ',')"
}
```

`examples/first_call.py`
```python
import os

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=30,
)

response = client.get("/api/presets")
response.raise_for_status()
for preset in response.json()["data"]:
    print(preset["id"], preset["name"], ",".join(preset["modes"]))
```

Each line printed is a preset id, its name and its modes. If you get `401`, the key is wrong or revoked; if you get `403`, it lacks a permission. The Authentication page lists every answer.

## 3. Generate an image and download it

Pick a preset id from the list and put it in `POTIONUI_PRESET`. Then set `POTIONUI_FORM` to the settings you want as a JSON object. These are the form's fields, by name: for example `{"aspect_ratio": "1:1", "count": 1}`. A field you leave out takes the preset's default. If the preset needs a field you left out, the answer is `422` and `field_errors` names it.

The easiest way to learn the field names and values is to make one generation in the app and read it back: `GET /api/generations/history/{generation_id}` returns the `form_data` that generation used. The form of a preset is also available as `GET /api/presets/{preset_id}/form`.

```bash
export POTIONUI_PRESET=PRESET_ID_FROM_STEP_2
export POTIONUI_FORM='{"count": 1, "aspect_ratio": "1:1"}'
```

```powershell
$env:POTIONUI_PRESET = "PRESET_ID_FROM_STEP_2"
$env:POTIONUI_FORM = '{"count": 1, "aspect_ratio": "1:1"}'
```

The script starts the generation, checks its status once a second until it finishes, and saves every file of the result into the current folder.

`examples/generate_and_wait.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"
FORM="${POTIONUI_FORM:-}"
[ -n "$FORM" ] || FORM='{}'

STARTED=$(curl -sS --fail-with-body -X POST "$BASE/api/generations/start" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"preset_id": "'"$POTIONUI_PRESET"'", "mode": "txt2img", "prompt": "a lighthouse at dusk, oil painting", "form_data": '"$FORM"'}')
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

`examples/generate_and_wait.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$form = if ($env:POTIONUI_FORM) { $env:POTIONUI_FORM } else { "{}" }

$body = @"
{"preset_id": "$($env:POTIONUI_PRESET)", "mode": "txt2img", "prompt": "a lighthouse at dusk, oil painting", "form_data": $form}
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

`examples/generate_and_wait.py`
```python
import json
import os
import time
from pathlib import Path

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=60,
)

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

You now have the whole loop: `POST /api/generations/start` answers at once with a `generation_id`, `GET /api/generations/{id}/status` tells you how it is going, `GET /api/generations/history/{id}` lists the result files, and `GET /api/media/files/{file_id}` returns a file.

## Where next

- To get progress while it runs instead of polling, use the WebSocket described on the Generating page.
- To send only a prompt and keep every other setting from the app, use Generate from a saved session.
- To see what a key may do, and every error it can get, read Authentication and permissions.
- To revoke the key, open **Settings**, **API keys**, and revoke it from its row. It stops working at once.
