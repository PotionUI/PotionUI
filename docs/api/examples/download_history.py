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
