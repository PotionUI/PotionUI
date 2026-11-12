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
