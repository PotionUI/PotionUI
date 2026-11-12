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
