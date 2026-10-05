from pathlib import Path
from typing import Any, Dict, Optional

from e2e_harness import (
    REPO_ROOT,
    StageError,
    ThrowawayApp,
    login,
    raise_for_status,
    spawn_backend,
    stage_log,
    stop_backend_process,
    stage_wait_for_health,
)

PLUGINS_ROOT = REPO_ROOT / "tests" / "e2e" / "plugins"
PLUGIN_ID = "e2e-cloud-fake"
DRIVER = "cloud.fake"
EXTRA_PLUGIN_DIRS_ENV = "POTIONUI_EXTRA_PLUGIN_DIRS"
SPEC_PREFIXES = ("cloud-fake-", "xy-compare-")

PRESET_ID = "01M3SVA35XT0E7CG5JZTS9P21S"
PRESET_NAME = "Fake Studio"
VIDEO_PRESET_ID = "01M3XPKGJQPA3AJKW32ST4GEK1"
VIDEO_PRESET_NAME = "Fake Video"
PRESET_IDS = (PRESET_ID, VIDEO_PRESET_ID)
BACKEND_NAME = "E2E Fake Cloud"

FULL_MODEL = "fake/image-1"
LITE_MODEL = "fake/lite-1"
FULL_SLUG = "fake~fake~image-1"
LITE_SLUG = "fake~fake~lite-1"
FULL_LABEL = "Fake Image"
LITE_LABEL = "Fake Lite"
DIRECTOR_MODEL = "fake/director-1"
DIRECTOR_START_MODEL = "fake/director-start-1"
DIRECTOR_TEXT_MODEL = "fake/director-text-1"
DIRECTOR_SLUG = "fake~fake~director-1"
DIRECTOR_START_SLUG = "fake~fake~director-start-1"
DIRECTOR_TEXT_SLUG = "fake~fake~director-text-1"
DIRECTOR_LABEL = "Fake Director"
DIRECTOR_START_LABEL = "Fake Director Start"
DIRECTOR_TEXT_LABEL = "Fake Director Text"

DEFAULT_KNOBS: Dict[str, Any] = {
    "mode": "async",
    "queue_seconds": 0.0,
    "duration_seconds": 2.0,
    "poll_seconds": 0.25,
    "fail_kind": "",
    "cost_usd": 0.04,
}


def plugin_env() -> Dict[str, str]:
    return {EXTRA_PLUGIN_DIRS_ENV: str(PLUGINS_ROOT)}


def wants_cloud_fake(chunk_names) -> bool:
    return any(Path(name).name.startswith(SPEC_PREFIXES) for name in chunk_names)


def restart_backend(app: ThrowawayApp) -> None:
    stop_backend_process(app.instance, "cloud-fake")
    spawn_backend(app.instance, app.repo_root, extra_env=app.extra_env)
    stage_wait_for_health(app.client)
    login(app.client, username=app.username, password=app.password)


def enable_plugin(app: ThrowawayApp) -> None:
    client = app.client
    raise_for_status("cloud-fake", client.post("/api/plugins/scan"), "Plugin scan")
    listing = raise_for_status("cloud-fake", client.get("/api/plugins"), "Plugin list")
    row = next((p for p in listing.get("data") or [] if p.get("id") == PLUGIN_ID), None)
    if row is None:
        raise StageError("cloud-fake", f"Plugin '{PLUGIN_ID}' was not discovered; is {EXTRA_PLUGIN_DIRS_ENV} set?")
    if not row.get("enabled"):
        raise_for_status("cloud-fake", client.post(f"/api/plugins/{PLUGIN_ID}/enable"), "Plugin enable")


def create_backend(app: ThrowawayApp, knobs: Optional[Dict[str, Any]] = None) -> str:
    body = {
        "name": BACKEND_NAME,
        "engine": "cloud",
        "driver": DRIVER,
        "enabled": True,
        "timeout_seconds": 300,
        **DEFAULT_KNOBS,
        **(knobs or {}),
    }
    created = raise_for_status("cloud-fake", app.client.post("/api/backends", json=body), "Backend create")
    return created["data"]["id"]


def refresh_catalog(app: ThrowawayApp, backend_id: str) -> Dict[str, Any]:
    result = raise_for_status(
        "cloud-fake", app.client.post(f"/api/cloud/backends/{backend_id}/catalog/refresh"), "Catalog refresh"
    )
    return result["data"]


def install_preset(app: ThrowawayApp, preset_id: str = PRESET_ID) -> None:
    client = app.client
    listing = raise_for_status(
        "cloud-fake", client.get("/api/presets", params={"include_uninstalled": "true"}), "Preset list"
    )
    row = next((p for p in listing.get("data") or [] if p.get("id") == preset_id), None)
    if row is None:
        raise StageError("cloud-fake", f"Preset {preset_id} is not listed after the plugin was enabled")
    if not row.get("installed", True):
        raise_for_status("cloud-fake", client.post(f"/api/presets/{preset_id}/install"), "Preset install")
    me = raise_for_status("cloud-fake", client.get("/api/auth/me"), "Who am I")
    raise_for_status(
        "cloud-fake",
        client.post(f"/api/presets/{preset_id}/assign", json={"user_ids": [me["data"]["id"]]}),
        "Preset assign",
    )


def prepare(app: ThrowawayApp, knobs: Optional[Dict[str, Any]] = None) -> str:
    enable_plugin(app)
    restart_backend(app)
    engines = raise_for_status("cloud-fake", app.client.get("/api/backends/engines"), "Engine list")
    drivers = [d.get("driver") for d in engines.get("data") or []]
    if DRIVER not in drivers:
        raise StageError("cloud-fake", f"Driver {DRIVER} is not registered after restart (drivers: {drivers})")
    for preset_id in PRESET_IDS:
        install_preset(app, preset_id)
    backend_id = create_backend(app, knobs)
    refresh_catalog(app, backend_id)
    stage_log("cloud-fake", f"Cloud backend {backend_id} created; catalog refreshed; nothing enabled")
    return backend_id
