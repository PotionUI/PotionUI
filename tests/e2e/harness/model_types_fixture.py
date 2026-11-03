import os
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from e2e_harness import REPO_ROOT, StageError, ThrowawayApp, raise_for_status, stage_log

import cloud_fake

PLUGINS_ROOT = REPO_ROOT / "tests" / "e2e" / "model_type_plugins"
PLUGIN_ID = "e2e-model-types"
MODEL_TYPE = "e2e_rig"
FOLDER = "e2e_rigs"
FILENAME = "fixture-rig.onnx"
SPEC_PREFIXES = ("plugin-model-type",)
INDEX_TIMEOUT_SECONDS = 90.0


def wants_model_types(chunk_names: Iterable[str]) -> bool:
    return any(Path(name).name.startswith(SPEC_PREFIXES) for name in chunk_names)


def plugin_env() -> Dict[str, str]:
    return {cloud_fake.EXTRA_PLUGIN_DIRS_ENV: str(PLUGINS_ROOT)}


def merge_env(*envs: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    merged: Dict[str, str] = {}
    for env in envs:
        for key, value in (env or {}).items():
            if key == cloud_fake.EXTRA_PLUGIN_DIRS_ENV and merged.get(key):
                merged[key] = os.pathsep.join([merged[key], value])
            else:
                merged[key] = value
    return merged or None


def _enable_plugin(app: ThrowawayApp) -> None:
    client = app.client
    raise_for_status("model-types", client.post("/api/plugins/scan"), "Plugin scan")
    listing = raise_for_status("model-types", client.get("/api/plugins"), "Plugin list")
    row = next((p for p in listing.get("data") or [] if p.get("id") == PLUGIN_ID), None)
    if row is None:
        raise StageError("model-types", f"Plugin '{PLUGIN_ID}' was not discovered; is {cloud_fake.EXTRA_PLUGIN_DIRS_ENV} set?")
    if not row.get("enabled"):
        raise_for_status("model-types", client.post(f"/api/plugins/{PLUGIN_ID}/enable"), "Plugin enable")


def _indexed_rows(app: ThrowawayApp) -> list:
    body = raise_for_status(
        "model-types",
        app.client.get("/api/models", params={"model_type": MODEL_TYPE, "all_models": "true"}),
        "Model list",
    )
    return [m.get("filename") for m in (body.get("data") or {}).get("models") or []]


def prepare(app: ThrowawayApp) -> None:
    folder = app.instance.models_dir / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FILENAME).write_bytes(b"fixture rig for the plugin model type spec")
    _enable_plugin(app)
    cloud_fake.restart_backend(app)
    deadline = time.monotonic() + INDEX_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if FILENAME in _indexed_rows(app):
            stage_log("model-types", f"'{FILENAME}' indexed as '{MODEL_TYPE}' by the startup scan")
            return
        time.sleep(0.5)
    raise StageError("model-types", f"'{FILENAME}' was not indexed as '{MODEL_TYPE}' within {INDEX_TIMEOUT_SECONDS:.0f}s")
