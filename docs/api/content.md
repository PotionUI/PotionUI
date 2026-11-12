---
title: "Working with your content"
order: 15
category: API
category_order: 90
---

# Working with your content

This page covers the routes a script most often needs for the things you keep in PotionUI: History, the Library, Kits, collections and tags. Reading needs a key with **Can read**; creating, changing and deleting needs **Can change and delete your content**. Everything is yours alone: a key only ever sees and changes its owner's content.

```bash
BASE="${POTIONUI_URL:-http://localhost:26730}"
```

```powershell
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
```

## History

Every generation you make is kept in History.

`GET /api/generations/history` lists generations with their files. Page through it with `limit` and `offset`; the answer carries `generations`, `total`, `limit` and `offset`. Narrow it with `status`, `mode`, `preset_id`, `model_name`, `media_type`, `tag_ids` (comma-separated), `collection_id`, `favorites_only`, `min_rating`, `created_from`, `created_to`, `search` and more; every filter is listed in the endpoint reference.

```bash
curl -sS "$BASE/api/generations/history?status=completed&favorites_only=true&limit=20" \
  -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/generations/history?status=completed&favorites_only=true&limit=20" -Headers $headers
```

`GET /api/generations/history/{generation_id}` returns one generation with its `form_data`, its `parameters`, and its `files`. `GET /api/generations/history/facets` returns the modes, presets and models you have used, with counts.

To download everything that has finished, page through the list and fetch each file with `GET /api/media/files/{file_id}`. This script saves new files into a `history` folder and skips the ones it already has, so you can run it again later:

`examples/download_history.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"
mkdir -p history

curl -sS --fail-with-body "$BASE/api/generations/history?status=completed&limit=20&offset=0" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data.generations[] | .id as $generation | .files[] | "\($generation) \(.id) \(.mime_type)"' \
  | while read -r GENERATION FILE_ID MIME; do
      NAME="history/$GENERATION-$FILE_ID.${MIME#*/}"
      [ -e "$NAME" ] && continue
      curl -sS --fail-with-body "$BASE/api/media/files/$FILE_ID" \
        -H "Authorization: Bearer $POTIONUI_KEY" -o "$NAME"
      echo "saved $NAME"
    done
```

`examples/download_history.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
New-Item -ItemType Directory -Force -Path history | Out-Null

$offset = 0
do {
    $page = (Invoke-RestMethod -Uri "$base/api/generations/history?status=completed&limit=20&offset=$offset" -Headers $headers).data
    foreach ($generation in $page.generations) {
        foreach ($file in $generation.files) {
            $target = "history/$($generation.id)-$($file.id).$($file.mime_type.Split('/')[1])"
            if (-not (Test-Path $target)) {
                Invoke-WebRequest -Uri "$base/api/media/files/$($file.id)" -Headers $headers -OutFile $target
                Write-Output "saved $target"
            }
        }
    }
    $offset += $page.limit
} while ($offset -lt $page.total)
```

`examples/download_history.py`
```python
import os
from pathlib import Path

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=60,
)
folder = Path("history")
folder.mkdir(exist_ok=True)

offset = 0
while True:
    page = client.get(
        "/api/generations/history",
        params={"status": "completed", "limit": 20, "offset": offset},
    ).json()["data"]
    for generation in page["generations"]:
        for file in generation["files"]:
            name = f"{generation['id']}-{file['id']}.{file['mime_type'].split('/')[-1]}"
            target = folder / name
            if not target.exists():
                target.write_bytes(client.get(f"/api/media/files/{file['id']}").content)
                print("saved", target)
    offset += page["limit"]
    if offset >= page["total"]:
        break
```

Changing a generation needs `content`:

- `PUT /api/generations/{generation_id}/favorite` with `{"is_favorite": true}`.
- `PUT /api/generations/{generation_id}/rating` with `{"rating": 4}`, a whole number from 0 (unrated) to 5.
- `DELETE /api/generations/history/{generation_id}` deletes the generation and its files for good.

```bash
curl -sS -X PUT "$BASE/api/generations/GENERATION_ID/favorite" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"is_favorite": true}'
```

```powershell
Invoke-RestMethod -Method Put -Uri "$base/api/generations/GENERATION_ID/favorite" -Headers $headers -ContentType "application/json" -Body '{"is_favorite": true}'
```

## Tags

Tags label your generations. `GET /api/tags?type=GENERATION` lists them with how often each is used. `POST /api/tags` creates one, and the answer carries its `id`:

```bash
curl -sS -X POST "$BASE/api/tags" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "keeper", "type": "GENERATION"}'
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/tags" -Headers $headers -ContentType "application/json" -Body '{"name": "keeper", "type": "GENERATION"}'
```

`PUT /api/generations/{generation_id}/tags` sets the tags of a generation to exactly the ids you send, so an empty list removes them all. Find tagged generations with `tag_ids` on the history list.

```bash
curl -sS -X PUT "$BASE/api/generations/GENERATION_ID/tags" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tag_ids": ["TAG_ID"]}'
```

```powershell
Invoke-RestMethod -Method Put -Uri "$base/api/generations/GENERATION_ID/tags" -Headers $headers -ContentType "application/json" -Body '{"tag_ids": ["TAG_ID"]}'
```

## Collections

A collection is a folder. Collections of type `history` hold generations, `library` hold Library items and `prompts` hold saved prompts. Every request that names a collection also names its `scope`.

```bash
curl -sS -X POST "$BASE/api/collections" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Lighthouses", "scope": "history"}'
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/collections" -Headers $headers -ContentType "application/json" -Body '{"name": "Lighthouses", "scope": "history"}'
```

The answer has the new collection under `data.collection`, with its `id`. Put generations into it with `POST /api/collections/{collection_id}/members`:

```bash
curl -sS -X POST "$BASE/api/collections/COLLECTION_ID/members" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"generation_ids": ["GENERATION_ID"], "scope": "history"}'
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/collections/COLLECTION_ID/members" -Headers $headers -ContentType "application/json" -Body '{"generation_ids": ["GENERATION_ID"], "scope": "history"}'
```

`GET /api/collections?scope=history` lists your collections with their `item_count`, and `collection_id` on the history list returns the generations in one.

## Library

The Library holds your own images, video and audio, and the files you uploaded or copied there. `GET /api/library/items` lists them, filtered by `media_type`, `tag_ids`, `collection_id`, `search` (a part of the original file name) and `favorites_only`, with `limit` and `offset`.

```bash
curl -sS "$BASE/api/library/items?media_type=image&limit=20" -H "Authorization: Bearer $POTIONUI_KEY"
```

```powershell
Invoke-RestMethod -Uri "$base/api/library/items?media_type=image&limit=20" -Headers $headers
```

To keep a generated result in the Library, copy it with the file's id from the generation:

```bash
curl -sS -X POST "$BASE/api/library/items/from-generation" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"file_id": "FILE_ID"}'
```

```powershell
Invoke-RestMethod -Method Post -Uri "$base/api/library/items/from-generation" -Headers $headers -ContentType "application/json" -Body '{"file_id": "FILE_ID"}'
```

The answer is the new item under `data.item`.

## Kits

A kit is a named list of named items that you build once and cite in prompts: a character's pictures, a location's clips, a few lines of text. Each item is an image, a video, an audio file or a piece of text. A kit holds up to 50 items, and a text item up to 4,000 characters.

Create a kit with `POST /api/kits`. Its `handle` is made from the name (lowercase letters, digits and `_`, up to 32 characters); a name that is already taken gets a numbered handle such as `mara2`. The answer carries the kit's `id`, its `handle` and its `path`, which is how prompts cite it.

Add a text item with `POST /api/kits/{kit_id}/items`, and a file with `POST /api/kits/{kit_id}/items/upload` (a multipart `file` and an optional `name`). To add files you already have, use `POST /api/kits/add-library-items` with `upload_ids` and `POST /api/kits/add-history-items` with `file_ids`; either names an existing `kit_id` or a `new_kit` to create.

This script makes a kit, adds a text item and shows how a citation resolves:

`examples/make_kit.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"

KIT=$(curl -sS --fail-with-body -X POST "$BASE/api/kits" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "'"${POTIONUI_KIT:-Mara}"'", "label": "Actor"}')
KIT_ID=$(echo "$KIT" | jq -r '.data.kit.id')
KIT_PATH=$(echo "$KIT" | jq -r '.data.kit.path')

curl -sS --fail-with-body -X POST "$BASE/api/kits/$KIT_ID/items" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "bio", "text": "Mara is a sailor with a red scarf."}' > /dev/null

curl -sS --fail-with-body -X POST "$BASE/api/kits/resolve" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "A portrait of @'"$KIT_PATH"'.bio at sea"}' \
  | jq -r '.data.expanded'
```

`examples/make_kit.ps1`
```powershell
$ErrorActionPreference = "Stop"
$base = if ($env:POTIONUI_URL) { $env:POTIONUI_URL } else { "http://localhost:26730" }
$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }
$name = if ($env:POTIONUI_KIT) { $env:POTIONUI_KIT } else { "Mara" }

$kit = (Invoke-RestMethod -Method Post -Uri "$base/api/kits" -Headers $headers -ContentType "application/json" -Body (@{ name = $name; label = "Actor" } | ConvertTo-Json)).data.kit

$item = @{ name = "bio"; text = "Mara is a sailor with a red scarf." } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/api/kits/$($kit.id)/items" -Headers $headers -ContentType "application/json" -Body $item | Out-Null

$resolve = @{ text = "A portrait of @$($kit.path).bio at sea" } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri "$base/api/kits/resolve" -Headers $headers -ContentType "application/json" -Body $resolve).data.expanded
```

`examples/make_kit.py`
```python
import os

import httpx

client = httpx.Client(
    base_url=os.environ.get("POTIONUI_URL", "http://localhost:26730"),
    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},
    timeout=30,
)

created = client.post("/api/kits", json={"name": os.environ.get("POTIONUI_KIT", "Mara"), "label": "Actor"})
created.raise_for_status()
kit = created.json()["data"]["kit"]

added = client.post(
    f"/api/kits/{kit['id']}/items",
    json={"name": "bio", "text": "Mara is a sailor with a red scarf."},
)
added.raise_for_status()

resolved = client.post("/api/kits/resolve", json={"text": f"A portrait of @{kit['path']}.bio at sea"})
resolved.raise_for_status()
print(kit["path"], "->", resolved.json()["data"]["expanded"])
```

It prints the prompt with the cited text written in: `kits.mara -> A portrait of Mara is a sailor with a red scarf. at sea`. `POST /api/kits/resolve` needs only `read`, because it changes nothing: use it to see what a prompt would turn into.

### Cite a kit in a generation

A kit reaches a generation in two steps, and only what the prompt cites is used:

1. Cite items in the prompt. `@kits.mara.bio` cites one item and `@kits.mara` cites every item of the kit.
2. Load the kit into a reference field of the preset by adding `{"kit": "<kit id>"}` as a row of that field in `form_data`, next to any image paths the field already holds.

```bash
curl -sS -X POST "$BASE/api/generations/start" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"preset_id": "PRESET_ID", "mode": "edit", "prompt": "make it blue, in the style of @kits.mara.bio", "form_data": {"references": ["uploads/red.png", {"kit": "KIT_ID"}]}}'
```

```powershell
$body = '{"preset_id": "PRESET_ID", "mode": "edit", "prompt": "make it blue, in the style of @kits.mara.bio", "form_data": {"references": ["uploads/red.png", {"kit": "KIT_ID"}]}}'
Invoke-RestMethod -Method Post -Uri "$base/api/generations/start" -Headers $headers -ContentType "application/json" -Body $body
```

A cited text item is written into the prompt where the citation stands. A cited image, video or audio item goes to the model as a reference when the preset's field takes that kind. A citation never stops a generation: an item that cannot be used is skipped, and the answer to `/start` says so in `kits.notices`, each with a `code` such as `kit_not_loaded`, `unsupported_kind`, `file_missing` or `kit_deleted`. `kits.used` lists the items that were used.

Other kit routes: `GET /api/kits` and `GET /api/kits/{kit_id}` read, `PATCH /api/kits/{kit_id}` renames, `POST /api/kits/{kit_id}/duplicate` copies, `DELETE /api/kits/{kit_id}` removes a kit without deleting the Library files its items point to, and `PATCH` and `DELETE` on `/api/kits/{kit_id}/items/{item_id}` change and remove an item.
