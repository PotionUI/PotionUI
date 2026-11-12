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
