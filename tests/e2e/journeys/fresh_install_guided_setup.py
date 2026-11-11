from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

import yaml

from e2e_harness import (
    REPO_ROOT,
    JourneyResult,
    SetupClient,
    ThrowawayApp,
    login,
    spawn_backend,
    stage_wait_for_health,
    stop_backend_process,
)
from fake_huggingface import FakeHuggingFace

SOURCE_RECIPE = REPO_ROOT / "content" / "recipes" / "marketplace" / "flux2-klein-starter.yml"
RECIPE_ID = "fresh-install-hf-starter"
ARTIFACT_ID = "flux2-vae"
KEPT_STEPS = (
    "plugins.ensure",
    "backend.ensure",
    "models.index",
    "artifacts.plan",
    "artifacts.fetch",
    "models.index.downloads",
    "preset.ensure",
    "workspace.activate",
)
PLUGIN_ID = "huggingface-provider"
RUN_TIMEOUT_SECONDS = 180.0


def build_recipe(payload: bytes) -> dict:
    recipe = yaml.safe_load(SOURCE_RECIPE.read_text(encoding="utf-8"))
    artifact = next(a for a in recipe["artifacts"] if a["id"] == ARTIFACT_ID)
    artifact["size_bytes"] = len(payload)
    artifact["source_host"] = "huggingface.co"
    artifact["checksum"]["value"] = hashlib.sha256(payload).hexdigest()
    recipe["id"] = RECIPE_ID
    recipe["name"] = "Fresh install Hugging Face starter"
    recipe["first_run"] = True
    recipe["artifacts"] = [artifact]
    recipe["steps"] = [s for s in recipe["steps"] if s["key"] in KEPT_STEPS]
    for step in recipe["steps"]:
        if "artifact_ids" in step.get("params", {}):
            step["params"]["artifact_ids"] = [ARTIFACT_ID]
    return recipe


def resolve_path(artifact: dict) -> str:
    hint = artifact["provider_hint"]
    revision, _, filepath = hint["version_id"].partition("@")
    return f"{hint['model_id']}/resolve/{revision}/{filepath}"


def plugin_row(client: SetupClient) -> dict:
    resp = client.get(f"/api/plugins/{PLUGIN_ID}")
    return (resp.json().get("data") or {}) if resp.status_code == 200 else {}


def drive_run(client: SetupClient, recipe_id: str, evidence: list) -> dict:
    resp = client.post("/api/setup/runs", json={"recipe_id": recipe_id, "safe_input": {}})
    run = resp.json()
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    granted = set()
    while time.monotonic() < deadline:
        status = run.get("status")
        if status in ("completed", "failed", "cancelled"):
            return run
        if status == "awaiting_consent" and run.get("current_step") not in granted:
            step_key = run["current_step"]
            granted.add(step_key)
            evidence.append(f"granted consent on {step_key}")
            client.post(f"/api/setup/runs/{run['id']}/actions/grant_consent", json={"step_key": step_key})
        time.sleep(1.0)
        resp = client.get(f"/api/setup/runs/{run['id']}", timeout=10.0)
        if resp.status_code == 200:
            run = resp.json()
    return run


def step_summary(run: dict) -> list:
    rows = []
    for step in run.get("steps") or []:
        attempts = step.get("attempts") or []
        last = attempts[-1] if attempts else {}
        message = (last.get("error") or {}).get("message") if isinstance(last.get("error"), dict) else last.get("error")
        rows.append(f"{step.get('step_key')} -> {step.get('status')}" + (f" ({message})" if message else ""))
    return rows


def restart(app: ThrowawayApp) -> SetupClient:
    stop_backend_process(app.instance)
    spawn_backend(app.instance, app.repo_root, extra_env=app.extra_env)
    client = SetupClient(app.base_url)
    stage_wait_for_health(client)
    login(client, username=app.username, password=app.password)
    return client


def run(_shared: ThrowawayApp) -> JourneyResult:
    evidence = []
    failures = []
    payload = b"fresh-install-vae-" + os.urandom(64)
    recipe = build_recipe(payload)
    artifact = recipe["artifacts"][0]
    served_path = resolve_path(artifact)

    server = FakeHuggingFace({served_path: payload})
    try:
        with tempfile.TemporaryDirectory(prefix="potionui-fresh-install-") as scratch:
            scratch_dir = Path(scratch)
            recipes_dir = scratch_dir / "recipes"
            recipes_dir.mkdir()
            (recipes_dir / f"{RECIPE_ID}.yml").write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
            extra_env = {"HF_ENDPOINT": server.url}
            app_ctx = ThrowawayApp(
                models_dir=scratch_dir / "no-models",
                recipes_dir=recipes_dir,
                username="fresh-owner",
                extra_env=extra_env,
            )
            with app_ctx as app:
                before = plugin_row(app.client)
                evidence.append(f"{PLUGIN_ID} at boot: enabled={before.get('enabled')}")
                if not before.get("enabled"):
                    failures.append(f"{PLUGIN_ID} must be enabled by default on a fresh install")

                final = drive_run(app.client, RECIPE_ID, evidence)
                evidence.extend(f"  {line}" for line in step_summary(final))
                if final.get("status") != "completed":
                    failures.append(f"setup run ended '{final.get('status')}', expected completed")

                after = plugin_row(app.client)
                evidence.append(f"{PLUGIN_ID} after run: enabled={after.get('enabled')}")
                if not after.get("enabled"):
                    failures.append(f"plugins.ensure left {PLUGIN_ID} not persisted as enabled")

                fetched = [r for r in server.requests if r[0] == "GET" and served_path in r[1]]
                evidence.append(f"fake Hugging Face served {len(fetched)} download request(s)")
                if not fetched:
                    failures.append("the download step never fetched the artifact from the resolved source")

                on_disk = list(app.instance.models_dir.rglob(artifact["filename"]))
                if not on_disk or on_disk[0].read_bytes() != payload:
                    failures.append("downloaded file missing or content mismatch under the models dir")

                listing = app.client.get(
                    "/api/models",
                    params={"search": artifact["filename"], "limit": 5, "all_models": "true"},
                ).json()
                models = ((listing.get("data") or {}).get("models")) or []
                evidence.append(f"model index lists {len(models)} match(es) for {artifact['filename']}")
                if not any(m.get("filename") == artifact["filename"] for m in models):
                    failures.append("Register downloaded models did not index the downloaded file")

                client = restart(app)
                persisted = plugin_row(client)
                evidence.append(f"{PLUGIN_ID} after restart: enabled={persisted.get('enabled')}")
                if not persisted.get("enabled"):
                    failures.append(f"{PLUGIN_ID} not enabled after restarting on the same database")
    finally:
        server.stop()

    if failures:
        return JourneyResult.fail(*evidence, *failures)
    return JourneyResult.ok(*evidence)
