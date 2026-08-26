"""Marketing-capture seed data.

Populates a throwaway PotionUI instance (see `e2e_harness.ThrowawayApp` under
tests/e2e/harness/)
so it looks like a genuinely used install instead of an empty first-boot: a
history/gallery built from the preset example media PotionUI already ships
(`content/presets/marketplace/<Preset>/public/examples/`), a couple of demo
users and a group, and the plugin catalog scanned so the admin screens have
something to show.

Every seeded generation goes through the REAL upload code path
(`POST /api/generations/upload` - see `GenerationHistoryArchive.upload_generations`)
so file storage, thumbnails and `File` rows are exactly what a real upload
produces. `upload_generations` always writes `preset_id=None`, `form_data={}`
and "now" as the timestamp (it doesn't know which preset a bare file upload
came from) - this module patches those three things afterward with a direct
sqlite UPDATE against the ephemeral instance's own scratch DB (WAL mode, safe
to write from a second connection - see `src/platform/database/database.py`),
using the preset's own shipped example prompt/defaults so nothing shown in the
UI is invented. `generation_parameters` rows are inserted the same way so the
history detail modal's Parameters panel (which reads a dedicated table, not
`form_data` - see `GenerationHistoryQuery.get_params`) has real content; no
`models` rows are inserted since the throwaway `models/tests` depot ships no
real model files to link honestly.

Deterministic: fixed slot order, fixed relative timestamps (anchored to the
capture run's start date), fixed seeds/ratings - two runs a day apart produce
byte-identical seed state modulo the anchor date itself, so a diff between two
capture runs is trustworthy evidence, not run-to-run noise.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_ROOT = REPO_ROOT / "content" / "presets" / "marketplace"

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}


@dataclass
class SeedGeneration:
    """One history row to seed: a shipped example asset uploaded as a
    completed generation, then backdated and attributed to a real preset."""

    slot: int
    preset_dir: str  # under content/presets/marketplace/
    asset: str  # filename under <preset_dir>/public/examples/
    preset_id: str
    preset_version: str
    mode: str
    prompt: str
    parameters: Dict[str, Any]
    days_ago: int = 0
    duration_ms: int = 0
    rating: int = 0
    tag_names: List[str] = field(default_factory=list)
    negative_prompt: Optional[str] = None

    @property
    def asset_path(self) -> Path:
        return EXAMPLES_ROOT / self.preset_dir / "public" / "examples" / self.asset


# Real prompts/defaults lifted verbatim from each preset's own `preset.yml`
# (media.gallery[0].prompt and vars.default_*) - never invented copy, since a
# fabricated prompt sitting next to a real shipped image is the one thing this
# module must not do.
_FLUX_PROMPT = (
    "A photorealistic, highly detailed cinematic macro photograph of a single, "
    "intricately hand-blown glass potion flask filled with a vibrant, intensely "
    "glowing cobalt blue bioluminescent liquid, on a weathered dark wooden "
    "alchemist's bench surrounded by vials, bottles and dried herbs."
)
_SDXL_PROMPT = (
    "masterpiece, best quality, ultra-detailed, macro photography, focal point on "
    "glowing ornate glass potion bottle, alchemical artifact, swirling "
    "bioluminescent liquid, deep ultraviolet, electric cyan, bright gold, "
    "suspended microscopic clockwork mechanism, intricate brass gears, sapphire "
    "jewels, cinematic lighting, 8k resolution, sharp focus, extreme depth of field"
)
_QWEN_PROMPT = (
    "A photorealistic, highly detailed macro photograph of an ornate spherical "
    "glass potion vessel on an aged dark oak alchemist's bench, a swirling "
    "pearlescent bioluminescent liquid nebula inside holding a miniature "
    "functional clockwork mechanism, a young alchemist studying it in close-up "
    "portrait, cinematic lighting, extreme depth of field."
)
_KREA_PROMPT = (
    "An extreme macro photograph of an antique hand-blown glass potion bottle on "
    "a chipped stone alchemist's bench, a violently swirling vortex of "
    "bioluminescent ultraviolet, cyan and gold liquid holding a tiny clockwork "
    "mechanism, volumetric mist, sharp raytraced reflections, 8k photorealistic."
)
_ZIMAGE_PROMPT = (
    "A hyper-realistic ultra-high-speed macro photograph of an ornate glass "
    "potion bottle frozen mid-shatter, glowing neon-emerald and magenta "
    "bioluminescent liquid splashing outward with suspended droplets, dramatic "
    "rim lighting against a pitch-black studio void, 8k raw photography."
)
_ANIMA_PROMPT = (
    "masterpiece, best quality, anime, 1girl, young alchemist, serious "
    "expression, braided chestnut hair, alchemist vest, holding an ornate glass "
    "bottle glowing with swirling deep ultraviolet and gold bioluminescent "
    "liquid, arcane runes, cinematic lighting, dramatic rim lighting, bloom."
)
_WAN_PROMPT = (
    "Cinematic macro slow-motion video of an ornate hand-blown glass potion "
    "bottle on a weathered oak bench, camera orbiting slowly, a bioluminescent "
    "ultraviolet and gold liquid swirling in a hypnotic vortex inside, wispy "
    "volumetric mist rising from the neck, extreme shallow depth of field."
)
_LTX_PROMPT = (
    "Cinematic hyper-detailed macro tracking shot of an ornate glass potion "
    "bottle on an alchemist's desk, camera gliding backward and upward in a "
    "crane shot revealing the workshop, viscous neon-purple and gold "
    "bioluminescent liquid inside pulsing with light, seamless physics."
)

# (days_ago, duration_ms, rating, tags) per slot - fixed so reruns land on the
# same relative schedule regardless of which real day the capture runs.
_SCHEDULE = [
    (1, 42_000, 5, ["showcase"]),
    (2, 38_500, 0, []),
    (3, 51_000, 4, []),
    (4, 29_000, 0, []),
    (5, 46_500, 0, ["showcase"]),
    (6, 33_000, 3, []),
    (7, 60_000, 0, []),
    (8, 27_500, 0, []),
    (9, 71_000, 5, ["showcase"]),
    (10, 24_000, 0, []),
    (11, 118_000, 0, []),
    (12, 96_500, 4, []),
    (13, 143_000, 0, []),
    (14, 22_000, 0, ["restoration"]),
    (16, 88_000, 5, ["restoration"]),
    (18, 31_000, 0, []),
    (20, 26_500, 0, []),
]


def _seed_generations() -> List[SeedGeneration]:
    entries = [
        SeedGeneration(0, "Flux2", "potion.png", "01KX46YCC5RB5EGYY38SBMVKR5", "1.0.1", "txt2img",
                       _FLUX_PROMPT, {"model": "flux1-dev.safetensors", "steps": 30, "cfg_scale": 3.5,
                                      "sampler": "euler", "width": 1072, "height": 1920, "seed": 1000}),
        SeedGeneration(1, "Flux2", "potion.png", "01KX46YCC5RB5EGYY38SBMVKR5", "1.0.1", "txt2img",
                       _FLUX_PROMPT, {"model": "flux1-dev.safetensors", "steps": 28, "cfg_scale": 3.5,
                                      "sampler": "euler", "width": 1072, "height": 1920, "seed": 1001}),
        SeedGeneration(2, "SDXL", "potion.png", "01K0W24A3RADXXABH16YQ7KE90", "1.0.1", "txt2img",
                       _SDXL_PROMPT, {"model": "cyberrealisticPony_v180Coreshift.safetensors", "steps": 32,
                                      "cfg_scale": 6.5, "sampler": "DPMPP_2M", "width": 1024, "height": 1536,
                                      "seed": 1002}),
        SeedGeneration(3, "SDXL", "potion.png", "01K0W24A3RADXXABH16YQ7KE90", "1.0.1", "txt2img",
                       _SDXL_PROMPT, {"model": "cyberrealisticPony_v180Coreshift.safetensors", "steps": 30,
                                      "cfg_scale": 7.0, "sampler": "DPMPP_2M", "width": 1024, "height": 1536,
                                      "seed": 1003}),
        SeedGeneration(4, "QwenImage", "potion.png", "01K0W24A3RADXXABH16YQ7KF00", "2.0.1", "txt2img",
                       _QWEN_PROMPT, {"model": "qwen-image-bf16.safetensors", "steps": 24, "cfg_scale": 4.0,
                                      "shift": 1.15, "width": 1104, "height": 1472, "seed": 1004}),
        SeedGeneration(5, "QwenImage", "potion.png", "01K0W24A3RADXXABH16YQ7KF00", "2.0.1", "txt2img",
                       _QWEN_PROMPT, {"model": "qwen-image-bf16.safetensors", "steps": 24, "cfg_scale": 4.0,
                                      "shift": 1.15, "width": 1104, "height": 1472, "seed": 1005}),
        SeedGeneration(6, "Krea2", "potion.png", "4TK1KBQZ2XMB8ME0PTMXS1YJQP", "1.1.0", "txt2img",
                       _KREA_PROMPT, {"model": "flux1-krea-dev.safetensors", "steps": 28, "cfg_scale": 4.5,
                                      "width": 1024, "height": 1536, "seed": 1006}),
        SeedGeneration(7, "Krea2", "bar-portrait.webp", "4TK1KBQZ2XMB8ME0PTMXS1YJQP", "1.1.0", "txt2img",
                       "A moody bar interior portrait, cinematic rim lighting, shallow depth of field.",
                       {"model": "flux1-krea-dev.safetensors", "steps": 28, "cfg_scale": 4.5, "width": 896,
                        "height": 1344, "seed": 1007}),
        SeedGeneration(8, "ZImage", "potion.png", "01KX5GS4P8HHCB63FY8SA7QSBH", "1.0.1", "txt2img",
                       _ZIMAGE_PROMPT, {"model": "z-image-turbo.safetensors", "steps": 9, "cfg_scale": 1.0,
                                        "shift": 3.0, "width": 1088, "height": 1440, "seed": 1008}),
        SeedGeneration(9, "Anima", "potion.png", "01KX5GRNWFC9S2F6T15155H41C", "1.0.1", "txt2img",
                       _ANIMA_PROMPT, {"model": "anima-cosmos-predict2.safetensors", "steps": 24, "cfg_scale": 6.0,
                                       "shift": 3.0, "sampler": "euler", "width": 960, "height": 1440,
                                       "seed": 1009}),
        SeedGeneration(10, "Wan", "potion.mp4", "01KX47WANVIDEO0000000000TV", "1.0.1", "txt2vid",
                       _WAN_PROMPT, {"model": "wan2.2-t2v-a14b.safetensors", "steps": 20, "cfg_scale": 5.0,
                                     "fps": 16, "num_frames": 81, "width": 832, "height": 480, "seed": 1010}),
        SeedGeneration(11, "Wan", "potion.mp4", "01KX47WANVIDEO0000000000TV", "1.0.1", "txt2vid",
                       _WAN_PROMPT, {"model": "wan2.2-t2v-a14b.safetensors", "steps": 20, "cfg_scale": 5.0,
                                     "fps": 16, "num_frames": 81, "width": 832, "height": 480, "seed": 1011}),
        SeedGeneration(12, "LTX-2", "potion.mp4", "01KX47LTXVIDEO0000000000TV", "1.0.1", "txt2vid",
                       _LTX_PROMPT, {"model": "ltx-2-13b.safetensors", "steps": 30, "cfg_scale": 4.0,
                                     "fps": 25, "num_frames": 49, "width": 768, "height": 512, "seed": 1012}),
        SeedGeneration(13, "LTX-2", "potion.mp4", "01KX47LTXVIDEO0000000000TV", "1.0.1", "txt2vid",
                       _LTX_PROMPT, {"model": "ltx-2-13b.safetensors", "steps": 30, "cfg_scale": 4.0,
                                     "fps": 25, "num_frames": 49, "width": 768, "height": 512, "seed": 1013}),
        SeedGeneration(14, "SeedVR2", "bar-restore.jpg", "01KXB7C553THYMSMKY1QSYESFM", "1.1.0", "upscale",
                       "Restoration pass: bar photo, scale x2, denoise low.",
                       {"model": "seedvr2-3b.safetensors", "scale_factor": 2.0, "denoise_strength": 0.2,
                        "seed": 1014}, negative_prompt=None),
        SeedGeneration(15, "SeedVR2", "bar-restore.mp4", "01KXB7C553THYMSMKY1QSYESFM", "1.1.0", "video_upscale",
                       "Restoration pass: bar footage, scale x2, denoise low.",
                       {"model": "seedvr2-3b.safetensors", "scale_factor": 2.0, "denoise_strength": 0.2,
                        "fps": 24, "seed": 1015}),
        SeedGeneration(16, "SeedVR2", "moon-portrait.webp", "01KXB7C553THYMSMKY1QSYESFM", "1.1.0", "upscale",
                       "Restoration pass: portrait, scale x4, denoise medium.",
                       {"model": "seedvr2-3b.safetensors", "scale_factor": 4.0, "denoise_strength": 0.35,
                        "seed": 1016}),
    ]
    for entry, (days_ago, duration_ms, rating, tags) in zip(entries, _SCHEDULE):
        entry.days_ago = days_ago
        entry.duration_ms = duration_ms
        entry.rating = rating
        entry.tag_names = tags
    return entries


@dataclass
class SeedResult:
    generation_ids: List[str] = field(default_factory=list)
    skipped_assets: List[str] = field(default_factory=list)
    tag_ids: Dict[str, str] = field(default_factory=dict)
    plugin_ids: List[str] = field(default_factory=list)
    user_ids: Dict[str, str] = field(default_factory=dict)
    group_id: Optional[str] = None
    library_item_ids: List[str] = field(default_factory=list)
    collection_ids: Dict[str, str] = field(default_factory=dict)
    phrasebook_category_ids: Dict[str, str] = field(default_factory=dict)


def seed_marketing_data(app, *, anchor: Optional[datetime] = None) -> SeedResult:
    """Seed history, tags, users/group, the plugin catalog, Library
    collections and phrasebook categories onto an already-booted
    `ThrowawayApp`. Deterministic given `anchor` (defaults to "now" at call
    time, truncated to the second).

    Does NOT create a `comfyui`-engine backend row: that engine is only
    registered by the comfyui-backend plugin's `backend.register` hook at
    process boot (`_sync_enabled_plugins` in `src.bootstrap.container`), so
    enabling the plugin over HTTP after boot cannot make `POST /api/backends`
    accept `engine: "comfyui"` without restarting the backend - out of scope
    here. The admin Backends scene works with the real `native` row alone."""
    anchor = (anchor or datetime.now(timezone.utc)).replace(microsecond=0)
    result = SeedResult()

    _seed_preset_installs(app)
    result.tag_ids = _seed_tags(app)
    (
        result.generation_ids, result.skipped_assets, file_ids, generation_ids_by_tag,
    ) = _seed_history(app, anchor, result.tag_ids)
    result.user_ids, result.group_id = _seed_users_and_group(app)
    result.plugin_ids = _seed_plugin_catalog(app)
    result.library_item_ids, result.collection_ids = _seed_library_and_collections(
        app, file_ids, generation_ids_by_tag, result.tag_ids
    )
    result.phrasebook_category_ids = _seed_phrasebook(app)
    return result


# The native presets scenes 1/3/4/5 depend on being visible in the /generate
# picker - which requires each to be both installed AND assigned to the
# owner user (an uninstalled/unassigned preset never appears there, matching
# frontend/tests/e2e/fe73-camera-orbit.spec.ts's same install+assign dance).
NATIVE_PRESET_IDS = [
    "01KX46YCC5RB5EGYY38SBMVKR5",  # Flux2
    "01K0W24A3RADXXABH16YQ7KE90",  # SDXL
    "01K0W24A3RADXXABH16YQ7KF00",  # QwenImage
    "4TK1KBQZ2XMB8ME0PTMXS1YJQP",  # Krea2
    "01KX5GS4P8HHCB63FY8SA7QSBH",  # ZImage
    "01KX5GRNWFC9S2F6T15155H41C",  # Anima
    "01KX47WANVIDEO0000000000TV",  # Wan
    "01KX47LTXVIDEO0000000000TV",  # LTX-2
    "01KXB7C553THYMSMKY1QSYESFM",  # SeedVR2
]


def _seed_preset_installs(app) -> None:
    me = app.client.get("/api/auth/me")
    if me.status_code != 200:
        return
    user_id = me.json()["data"]["id"]
    for preset_id in NATIVE_PRESET_IDS:
        app.client.post(f"/api/presets/{preset_id}/install")
        app.client.post(f"/api/presets/{preset_id}/assign", json={"user_ids": [user_id]})


def _seed_tags(app) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    for name in ("showcase", "restoration"):
        resp = app.client.post("/api/tags/", json={"name": name, "type": "GENERATION"})
        if resp.status_code == 200:
            ids[name] = resp.json()["data"]["tag"]["id"]
    return ids


def _seed_history(
    app, anchor: datetime, tag_ids: Dict[str, str]
) -> tuple[List[str], List[str], Dict[str, str], Dict[str, List[str]]]:
    generation_ids: List[str] = []
    skipped: List[str] = []
    file_ids: Dict[str, str] = {}  # generation_id -> its (only) file's id
    generation_ids_by_tag: Dict[str, List[str]] = {name: [] for name in tag_ids}
    db_path = Path(app.instance.db_path)

    for entry in _seed_generations():
        if not entry.asset_path.is_file():
            skipped.append(f"{entry.preset_dir}/{entry.asset} (missing on disk: {entry.asset_path})")
            continue

        mime = MIME_TYPES.get(entry.asset_path.suffix.lower(), "application/octet-stream")
        tag_id_list = [tag_ids[name] for name in entry.tag_names if name in tag_ids]
        with entry.asset_path.open("rb") as fh:
            resp = app.client.post(
                "/api/generations/upload",
                files=[("files", (entry.asset, fh, mime))],
                params={"tag_ids": tag_id_list} if tag_id_list else None,
            )
        if resp.status_code != 200:
            skipped.append(f"{entry.preset_dir}/{entry.asset} (upload failed: {resp.status_code} {resp.text[:200]})")
            continue

        upload_data = resp.json()["data"]
        generation_id = upload_data["generation_id"]
        generation_ids.append(generation_id)
        if upload_data.get("files"):
            file_ids[generation_id] = upload_data["files"][0]["id"]
        for name in entry.tag_names:
            if name in generation_ids_by_tag:
                generation_ids_by_tag[name].append(generation_id)

        created_at = anchor - timedelta(days=entry.days_ago, minutes=entry.slot)
        completed_at = created_at + timedelta(milliseconds=entry.duration_ms)
        form_data = {"prompt": entry.prompt, **entry.parameters}
        if entry.negative_prompt:
            form_data["negative_prompt"] = entry.negative_prompt

        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            conn.execute(
                "UPDATE generations SET preset_id=?, preset_version=?, form_data=?, mode=?, "
                "created_at=?, completed_at=?, updated_at=?, duration_ms=? WHERE id=?",
                (
                    entry.preset_id, entry.preset_version, json.dumps(form_data), entry.mode,
                    created_at.isoformat(), completed_at.isoformat(), completed_at.isoformat(),
                    entry.duration_ms, generation_id,
                ),
            )
            for name, value in {**entry.parameters, "prompt": entry.prompt}.items():
                conn.execute(
                    "INSERT INTO generation_parameters (id, generation_id, parameter_name, parameter_value, "
                    "parameter_index) VALUES (?, ?, ?, ?, 0)",
                    (f"{generation_id[:20]}{name[:6].upper()}", generation_id, name, json.dumps(value)),
                )
            conn.commit()
        finally:
            conn.close()

        if entry.rating:
            app.client.put(f"/api/generations/{generation_id}/rating", json={"rating": entry.rating})

    return generation_ids, skipped, file_ids, generation_ids_by_tag


def _seed_users_and_group(app) -> tuple[Dict[str, str], Optional[str]]:
    user_ids: Dict[str, str] = {}
    for username in ("art-lead", "reviewer"):
        resp = app.client.post(
            "/api/users/",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "marketing-capture-demo-pw-1!",
                "account_type": "USER",
            },
        )
        if resp.status_code == 200:
            user_ids[username] = resp.json()["data"]["id"]

    group_id = None
    resp = app.client.post("/api/user-groups/", json={"name": "Creative Team", "description": "Illustration + video review"})
    if resp.status_code == 200:
        group_id = resp.json()["data"]["id"]
        member_ids = list(user_ids.values())
        if member_ids:
            app.client.post(f"/api/user-groups/{group_id}/members", json={"user_ids": member_ids})
        preset_ids = NATIVE_PRESET_IDS[:2]
        app.client.post(f"/api/user-groups/{group_id}/presets", json={"preset_ids": preset_ids})

    return user_ids, group_id


def _seed_plugin_catalog(app) -> List[str]:
    resp = app.client.post("/api/plugins/scan")
    if resp.status_code != 200:
        return []
    listing = app.client.get("/api/plugins")
    if listing.status_code != 200:
        return []
    return [p["id"] for p in listing.json().get("data", [])]


def _seed_library_and_collections(
    app,
    file_ids: Dict[str, str],
    generation_ids_by_tag: Dict[str, List[str]],
    tag_ids: Dict[str, str],
) -> tuple[List[str], Dict[str, str]]:
    """Copy a few "showcase"-tagged generation files into the Library (the
    real `POST /api/library/items/from-generation` path - a copy, the source
    generation is untouched), file them into collections (History and Library
    each have their own collection tree, migration 137 - a collection can no
    longer mix generation members and library-upload members), and tag one
    library item so the Library's own tag filter has something to show."""
    showcase_ids = generation_ids_by_tag.get("showcase", [])
    restoration_ids = generation_ids_by_tag.get("restoration", [])

    library_item_ids: List[str] = []
    for generation_id in showcase_ids:
        file_id = file_ids.get(generation_id)
        if not file_id:
            continue
        resp = app.client.post("/api/library/items/from-generation", json={"file_id": file_id})
        if resp.status_code == 200:
            library_item_ids.append(resp.json()["data"]["item"]["id"])

    # Library item tags are a distinct TagType (UPLOAD, not GENERATION) -
    # src/features/library/manager.py's set_tags rejects a GENERATION-typed
    # id with "Invalid tag ID", so this needs its own tag row.
    if library_item_ids:
        upload_tag = app.client.post("/api/tags/", json={"name": "featured", "type": "UPLOAD"})
        if upload_tag.status_code == 200:
            upload_tag_id = upload_tag.json()["data"]["tag"]["id"]
            app.client.put(f"/api/library/items/{library_item_ids[0]}/tags", json={"tag_ids": [upload_tag_id]})

    collection_ids: Dict[str, str] = {}
    resp = app.client.post("/api/collections", json={"name": "Potion Showcase", "scope": "history"})
    if resp.status_code == 200:
        collection_id = resp.json()["data"]["collection"]["id"]
        collection_ids["Potion Showcase"] = collection_id
        if showcase_ids:
            app.client.post(
                f"/api/collections/{collection_id}/members",
                json={"generation_ids": showcase_ids, "scope": "history"},
            )

    resp = app.client.post("/api/collections", json={"name": "Potion Showcase", "scope": "library"})
    if resp.status_code == 200:
        collection_id = resp.json()["data"]["collection"]["id"]
        collection_ids["Potion Showcase (Library)"] = collection_id
        if library_item_ids:
            app.client.post(
                f"/api/collections/{collection_id}/uploads",
                json={"upload_ids": library_item_ids, "scope": "library"},
            )

    resp = app.client.post("/api/collections", json={"name": "Restorations", "scope": "history"})
    if resp.status_code == 200:
        collection_id = resp.json()["data"]["collection"]["id"]
        collection_ids["Restorations"] = collection_id
        if restoration_ids:
            app.client.post(
                f"/api/collections/{collection_id}/members",
                json={"generation_ids": restoration_ids, "scope": "history"},
            )

    return library_item_ids, collection_ids


# Two top-level categories (no dots - a bare `#camera`/`#lighting` matches the
# category path exactly, so `src.features.phrasebook.operations.search_phrasebook`
# returns the category's own values immediately instead of requiring a
# `#camera.` navigation step first - see its "exact_category" branch). Values are real
# prompt-craft vocabulary, on theme with the potion prompts above rather than
# placeholder text, since the prompt-segments-phrasebook marketing scene
# shows these labels on screen. 8 values each so a picked chip's alternate
# values (InlineChip's shuffle/AUTO controls only render when
# `allValues.length > 1`) has something to shuffle through.
_PHRASEBOOK_CATEGORIES = {
    "camera": (
        "Camera angles",
        [
            ("Low angle", "low angle shot"),
            ("High angle", "high angle shot"),
            ("Dutch angle", "dutch tilt"),
            ("Extreme close-up", "extreme close-up"),
            ("Wide establishing shot", "wide establishing shot"),
            ("Over-the-shoulder", "over-the-shoulder shot"),
            ("Bird's-eye view", "bird's-eye view"),
            ("Macro lens", "macro lens photography"),
        ],
    ),
    "lighting": (
        "Lighting",
        [
            ("Golden hour", "golden hour lighting"),
            ("Dramatic rim light", "dramatic rim lighting"),
            ("Soft studio light", "soft studio lighting"),
            ("Volumetric god rays", "volumetric god rays"),
            ("Neon accent", "neon accent lighting"),
            ("Chiaroscuro", "chiaroscuro high contrast lighting"),
            ("Backlit silhouette", "backlit silhouette"),
            ("Bioluminescent glow", "bioluminescent glow"),
        ],
    ),
}


def _seed_phrasebook(app) -> Dict[str, str]:
    category_ids: Dict[str, str] = {}
    for path, (name, values) in _PHRASEBOOK_CATEGORIES.items():
        resp = app.client.post("/api/phrasebook/categories", json={"name": name, "path": path})
        if resp.status_code != 200:
            continue
        category_id = resp.json()["data"]["id"]
        category_ids[path] = category_id
        for sort_order, (label, value) in enumerate(values):
            app.client.post(
                "/api/phrasebook/values",
                json={"category_id": category_id, "label": label, "value": value, "sort_order": sort_order},
            )
    return category_ids

