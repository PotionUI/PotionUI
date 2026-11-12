from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from e2e_harness import (
    REPO_ROOT,
    JourneyResult,
    SetupClient,
    ThrowawayApp,
    kill_group,
    login,
    spawn_backend,
    stage_wait_for_health,
)
from fake_huggingface import FakeHuggingFace

SOURCE_RECIPE = REPO_ROOT / "content" / "recipes" / "marketplace" / "sdxl-starter.yml"
RECIPE_ID = "sdxl-starter"
ARTIFACT_ID = "sdxl-checkpoint"
APP_MODULE = "tests.e2e.harness.stub_engine_app:app"
OPT_IN = "E2E_STUB_ENGINE"
RUN_TIMEOUT_SECONDS = 240.0
RAW_FAILURE_TEXT = "CUDA out of memory. Tried to allocate 2.00 GiB (GPU 0; 8.00 GiB total capacity)"
PAYLOAD_BYTES = 3 * 1024 * 1024


def build_recipe(payload: bytes) -> dict:
    recipe = yaml.safe_load(SOURCE_RECIPE.read_text(encoding="utf-8"))
    artifact = next(a for a in recipe["artifacts"] if a["id"] == ARTIFACT_ID)
    artifact["size_bytes"] = len(payload)
    artifact["checksum"]["value"] = hashlib.sha256(payload).hexdigest()
    return recipe


def resolve_path(artifact: dict) -> str:
    hint = artifact["provider_hint"]
    revision, _, filepath = hint["version_id"].partition("@")
    return f"{hint['model_id']}/resolve/{revision}/{filepath}"


def write_control(path: Path, text: str) -> None:
    path.write_text(text + "\n", encoding="utf-8")


def open_the_wizard(client: SetupClient, evidence: List[str]) -> None:
    readiness = client.get("/api/readiness")
    machine = client.get("/api/setup/machine")
    recipes = client.get("/api/setup/recipes")
    ids = [r["id"] for r in (recipes.json().get("recipes") or [])]
    evidence.append(
        f"wizard opened: readiness={readiness.status_code} machine={machine.status_code} "
        f"recipes={recipes.status_code} {ids}"
    )


def start_run(client: SetupClient) -> dict:
    return client.post("/api/setup/runs", json={"recipe_id": RECIPE_ID, "safe_input": {}}).json()


def poll(client: SetupClient, run: dict, evidence: List[str], until: Callable[[dict], bool], timeout: float = RUN_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    granted = set()
    while time.monotonic() < deadline:
        if until(run):
            return run
        if run.get("status") in ("completed", "failed", "cancelled"):
            return run
        if run.get("status") == "awaiting_consent" and run.get("current_step") not in granted:
            step_key = run["current_step"]
            granted.add(step_key)
            evidence.append(f"granted consent on {step_key}")
            client.post(
                f"/api/setup/runs/{run['id']}/actions/grant_consent",
                json={"step_key": step_key, "selections": {}},
            )
        time.sleep(1.0)
        response = client.get(f"/api/setup/runs/{run['id']}", timeout=10.0)
        if response.status_code == 200:
            run = response.json()
    return run


def step(run: dict, kind: str) -> dict:
    return next((s for s in run.get("steps") or [] if s.get("kind") == kind), {})


def latest(run: dict, kind: str) -> dict:
    attempts = step(run, kind).get("attempts") or []
    return attempts[-1] if attempts else {}


def readiness_area(client: SetupClient, area: str) -> Optional[str]:
    checks = client.get("/api/readiness").json().get("checks") or []
    return next((c.get("status") for c in checks if c.get("area") == area), None)


def boot(scratch: Path, payload: bytes, server: FakeHuggingFace, control: Path) -> ThrowawayApp:
    recipes_dir = scratch / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    (recipes_dir / f"{RECIPE_ID}.yml").write_text(yaml.safe_dump(build_recipe(payload), sort_keys=False), encoding="utf-8")
    return ThrowawayApp(
        models_dir=scratch / "no-models",
        recipes_dir=recipes_dir,
        username="stub-owner",
        extra_env={"HF_ENDPOINT": server.url, "E2E_STUB_ENGINE_CONTROL": str(control)},
        app_module=APP_MODULE,
    )


def variant_success(scratch: Path, evidence: List[str], failures: List[str]) -> None:
    payload = b"stub-checkpoint-" + os.urandom(64)
    recipe = build_recipe(payload)
    server = FakeHuggingFace({resolve_path(recipe["artifacts"][0]): payload})
    control = scratch / "control"
    write_control(control, "ok")
    try:
        with boot(scratch, payload, server, control) as app:
            open_the_wizard(app.client, evidence)
            run = poll(app.client, start_run(app.client), evidence, lambda r: False)
            bad = [s["step_key"] for s in run.get("steps") or [] if s.get("status") != "succeeded"]
            evidence.append(f"success: run {run.get('status')}; steps not succeeded: {bad}")
            if run.get("status") != "completed" or bad:
                failures.append(f"success: expected a completed run with every step succeeded, got {run.get('status')} {bad}")
            if readiness_area(app.client, "generation_proven") != "ready":
                failures.append("success: generation_proven is not ready after the test picture")
            smoke = latest(run, "generation.smoke")
            filename = (smoke.get("safe_output") or {}).get("filename")
            if not filename or not list(app.instance.storage_dir.rglob(filename)):
                failures.append(f"success: the test picture {filename!r} is not on disk")
    finally:
        server.stop()


def variant_failed_test(scratch: Path, evidence: List[str], failures: List[str]) -> None:
    payload = b"stub-checkpoint-" + os.urandom(64)
    recipe = build_recipe(payload)
    server = FakeHuggingFace({resolve_path(recipe["artifacts"][0]): payload})
    control = scratch / "control"
    write_control(control, f"fail {RAW_FAILURE_TEXT}")
    try:
        with boot(scratch, payload, server, control) as app:
            open_the_wizard(app.client, evidence)
            run = poll(app.client, start_run(app.client), evidence, lambda r: False)
            smoke = latest(run, "generation.smoke")
            sentence = smoke.get("safe_error_detail") or ""
            evidence.append(f"failed test: run {run.get('status')}; smoke {smoke.get('status')}: {sentence!r}")
            if run.get("status") != "completed":
                failures.append(f"failed test: the run must complete, got {run.get('status')}")
            if smoke.get("status") != "action_required":
                failures.append(f"failed test: the smoke row must need attention, got {smoke.get('status')}")
            for leaked in ("CUDA out of memory", "GiB", "RuntimeError", "Traceback"):
                if leaked in sentence:
                    failures.append(f"failed test: the sentence leaks exception text ({leaked!r}): {sentence!r}")
            if RAW_FAILURE_TEXT not in (smoke.get("safe_technical_detail") or ""):
                failures.append("failed test: the raw text must be kept in safe_technical_detail")
            if latest(run, "workspace.activate").get("status") != "succeeded":
                failures.append("failed test: activation must still succeed")
            if readiness_area(app.client, "generation_proven") == "ready":
                failures.append("failed test: generation_proven must stay not ready until a picture is made")

            write_control(control, "ok")
            again = app.client.post(f"/api/recipes/{RECIPE_ID}/runs", json={}).json()
            again = poll_admin(app.client, again, evidence)
            evidence.append(f"failed test: run again from Admin -> Recipes ended {again.get('status')}")
            if again.get("status") != "completed":
                failures.append(f"failed test: the second run must complete, got {again.get('status')}")
            if readiness_area(app.client, "generation_proven") != "ready":
                failures.append("failed test: generation_proven must become ready after a good picture")
    finally:
        server.stop()


def poll_admin(client: SetupClient, run: dict, evidence: List[str], timeout: float = RUN_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    granted = set()
    while time.monotonic() < deadline:
        if run.get("status") in ("completed", "failed", "cancelled"):
            return run
        if run.get("status") == "awaiting_consent" and run.get("current_step") not in granted:
            granted.add(run["current_step"])
            client.post(f"/api/recipes/runs/{run['id']}/consent/{run['current_step']}", json={"selections": {}})
        time.sleep(1.0)
        response = client.get(f"/api/recipes/runs/{run['id']}", timeout=10.0)
        if response.status_code == 200:
            run = response.json()
    return run


def variant_restart_during_download(scratch: Path, evidence: List[str], failures: List[str]) -> None:
    payload = b"stub-checkpoint-" + os.urandom(PAYLOAD_BYTES)
    recipe = build_recipe(payload)
    server = FakeHuggingFace({resolve_path(recipe["artifacts"][0]): payload}, chunk_delay=0.08)
    control = scratch / "control"
    write_control(control, "ok")
    try:
        with boot(scratch, payload, server, control) as app:
            open_the_wizard(app.client, evidence)
            run = poll(
                app.client,
                start_run(app.client),
                evidence,
                lambda r: r.get("current_step") == "artifacts.fetch" and (latest(r, "artifacts.fetch").get("progress_current") or 0) > 0,
            )
            seen = (latest(run, "artifacts.fetch").get("progress_current") or 0)
            if seen <= 0:
                failures.append("restart: the download never started, so there was nothing to interrupt")
                return
            evidence.append(f"restart: killing the backend with {seen} bytes downloaded")
            kill_group(app.instance.process, force=True)
            app.instance.process.wait(timeout=30)
            spawn_backend(app.instance, app.repo_root, extra_env=app.extra_env, app_module=app.app_module)
            client = SetupClient(app.base_url)
            stage_wait_for_health(client)
            login(client, username=app.username, password=app.password)
            final = poll(client, run, evidence, lambda r: False)
            evidence.append(f"restart: run {final.get('status')} with no further call; range requests: {server.range_requests}")
            if final.get("status") != "completed":
                failures.append(f"restart: the run must finish by itself, got {final.get('status')} ({final.get('error_code')})")
            if not server.range_requests:
                failures.append("restart: the download did not resume with a Range request")
    finally:
        server.stop()


def run(_shared: ThrowawayApp) -> JourneyResult:
    if os.environ.get(OPT_IN) != "1":
        return JourneyResult.skip(f"set {OPT_IN}=1 to run the stub-engine fresh-setup journeys")
    evidence: List[str] = []
    failures: List[str] = []
    for name, variant in (
        ("success", variant_success),
        ("failed test", variant_failed_test),
        ("restart during download", variant_restart_during_download),
    ):
        scratch = Path(tempfile.mkdtemp(prefix="potionui-stub-setup-"))
        try:
            variant(scratch, evidence, failures)
        except Exception as exc:
            failures.append(f"{name}: unhandled {exc!r}")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    if failures:
        return JourneyResult.fail(*evidence, *failures)
    return JourneyResult.ok(*evidence)
