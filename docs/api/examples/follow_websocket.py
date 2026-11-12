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
