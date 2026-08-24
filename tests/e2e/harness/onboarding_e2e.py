#!/usr/bin/env python3
"""Onboarding iteration harness: an ephemeral end-to-end run of the
first-run journey, driven entirely over HTTP against a throwaway backend.

What this proves, every time it's run: a fresh PotionUI instance can be
claimed, can see the models already present on disk (the "depot"),
can walk a Phase-3 setup recipe through to completion - including the
owner-consent gate in front of any download - and can produce a real image on
a real GPU. It is the fast inner loop for iterating on the onboarding flow
without spinning up Docker and without ever touching a real running
instance (ports 8005/3001) or its data.

Everything below runs against a BRAND NEW, throwaway instance:

  - a fresh SQLite database in a temp directory (nothing shared with any real
    install)
  - a temp "storage" directory (generation outputs, thumbnails, the setup
    claim token, ...)
  - a real models directory, mirrored read-only (each file is
    symlinked in, never copied - the depot itself is never written to; new
    downloads land as ordinary new files in the mirror)
  - a backend subprocess on its own port (default 8055), never 8005

Usage:

    python tests/e2e/harness/onboarding_e2e.py --models-dir /path/to/models
    python tests/e2e/harness/onboarding_e2e.py --models-dir /path/to/models --no-gpu
    python tests/e2e/harness/onboarding_e2e.py --models-dir /path/to/models --fresh-download
    python tests/e2e/harness/onboarding_e2e.py --models-dir /path/to/models --keep

See `--help` for the full flag list. Every stage below is a plain importable
function (`stage_*`) taking a `Journey`, so a future pytest suite can drive
the same journey against a backend it boots its own way, or replay a single
stage in isolation - this script's `main()` is just one particular way of
sequencing them.

The throwaway-instance lifecycle itself (subprocess boot/teardown, the port
guard, health/claim/login) lives in `tests/e2e/harness/e2e_harness.py`, shared
with `tests/e2e/journeys/`; this script owns everything specific to driving
a setup recipe (recipe mutation, the dummy-download server, the consent
loop).
"""

from __future__ import annotations

import argparse
import http.server
import os
import secrets
import sys
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements.txt
    print(
        "The 'requests' package is required to run this script "
        "(pip install -r requirements.txt), or run it with the project venv's "
        "PYTHONPATH - see CLAUDE.md's Testing section.",
        file=sys.stderr,
    )
    raise

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships in requirements.txt
    print(
        "PyYAML is required to run this script (pip install -r requirements.txt).",
        file=sys.stderr,
    )
    raise

from e2e_harness import (
    DEFAULT_PORT,
    EphemeralInstance,
    SetupClient,
    StageError,
    claim_owner as harness_claim_owner,
    login as harness_login,
    mirror_models_dir_readonly,
    port_is_free,
    raise_for_status,
    reject_forbidden_port,
    spawn_backend,
    stage_wait_for_health,
    teardown_backend,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_RECIPE = "sdxl-starter"

# The recipe declares this artifact's identity (model_type, filename) - used
# to seed the depot mirror sanity check and to make targeted assertions about
# "did artifacts.plan actually skip the download". Read from the recipe file
# itself at runtime (see `_primary_artifact`) rather than hardcoded twice;
# this constant is only the fallback filename shown in error messages before
# the recipe has been parsed.
SDXL_STARTER_CHECKPOINT = "cyberrealisticPony_v125.safetensors"

# Generous: this bounds both the mutating action call itself (fast - it no
# longer waits on `drive()`, see `SetupRunManager.drive_async`) AND
# the poll loop `_drive_with_progress` runs afterward to catch up with
# whatever the background drive does (a real generation, a small download
# can legitimately take a while).
RUN_ACTION_TIMEOUT_SECONDS = 360.0
MODEL_INDEX_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0


def log(message: str) -> None:
    print(f"[onboarding-e2e] {message}", flush=True)


def stage_log(stage: str, message: str) -> None:
    print(f"[onboarding-e2e] [{stage}] {message}", flush=True)


# ---------------------------------------------------------------------------
# --fresh-download: a tiny local HTTP server for a dummy artifact
# ---------------------------------------------------------------------------


class DummyArtifactServer:
    """Serves one small, randomly-generated file over plain HTTP on
    127.0.0.1, so `artifacts.fetch` has something real - but tiny - to
    download for `--fresh-download`. The backend subprocess reaches this over
    loopback exactly like it would reach any other HTTP download source; no
    production download code is touched or faked."""

    FILENAME = "onboarding-e2e-dummy.safetensors"
    SIZE_BYTES = 4096

    def __init__(self, serve_dir: Path):
        self.serve_dir = serve_dir
        self.serve_dir.mkdir(parents=True, exist_ok=True)
        (self.serve_dir / self.FILENAME).write_bytes(secrets.token_bytes(self.SIZE_BYTES))
        handler = _make_quiet_handler(self.serve_dir)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}/{self.FILENAME}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _make_quiet_handler(directory: Path):
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass  # silence per-request access logs; the journey log is enough

    return _Handler


# ---------------------------------------------------------------------------
# Ephemeral recipes
# ---------------------------------------------------------------------------


def prepare_ephemeral_recipes(
    dest_dir: Path,
    *,
    no_gpu: bool,
    dummy_artifact_url: Optional[str],
) -> None:
    """Copy the repo's real `content/recipes/marketplace/*.yml` into
    `dest_dir / "marketplace"` (`dest_dir` is the catalog *base* dir, what
    `POTIONUI_RECIPES_DIR` points at), one of them (`sdxl-starter`) lightly
    mutated for this run's flags:

    - `--no-gpu`: drops the `generation.smoke` and `workspace.activate`
      steps. Both `plugins.ensure`/`backend.ensure`/`artifacts.plan`/
      `artifacts.fetch`/`models.index`/`preset.ensure`/`pipeline.render`
      genuinely need no GPU (`pipeline.render` is a dry-run through
      `PipelineBuilder` - see its own docstring); only `generation.smoke`
      does. There is no API seam to pause a run mid-drive (`drive()` runs
      every already-approved step in one call - see run_manager.py), so the
      only clean way to "stop right before the smoke step" is to hand it a
      recipe that ends one step earlier.
    - `--fresh-download`: declares one extra, tiny artifact (a `lora`, so it
      can never collide with the real SDXL checkpoint's identity) whose
      `provider_hint.download_url` points at the local `DummyArtifactServer`,
      and adds its id to the `artifacts.plan`/`artifacts.fetch` steps'
      `artifact_ids` alongside the real checkpoint. Since the checkpoint is
      already present (via the depot mirror) and the dummy never is, this
      exercises the full "parks on awaiting_consent -> grant_consent ->
      really downloads something" path against a few KB, not a 7GB file -
      without touching the real `sdxl-checkpoint` artifact entry at all.

    Every other recipe file (e.g. `comfyui-detect.yml`) is copied verbatim,
    so `GET /api/setup/recipes` still lists everything a real instance would.
    """
    source_dir = REPO_ROOT / "content" / "recipes" / "marketplace"
    dest_marketplace = dest_dir / "marketplace"
    dest_marketplace.mkdir(parents=True, exist_ok=True)
    if not source_dir.is_dir():
        raise StageError("recipes", f"No recipes directory found at {source_dir}")

    for source_path in sorted(source_dir.glob("*.yml")):
        data = yaml.safe_load(source_path.read_text()) or {}
        if data.get("id") == "sdxl-starter":
            _mutate_sdxl_starter(data, no_gpu=no_gpu, dummy_artifact_url=dummy_artifact_url)
        (dest_marketplace / source_path.name).write_text(yaml.safe_dump(data, sort_keys=False))
    stage_log("recipes", f"Prepared ephemeral recipe catalog at {dest_marketplace}")


def _mutate_sdxl_starter(data: dict, *, no_gpu: bool, dummy_artifact_url: Optional[str]) -> None:
    if dummy_artifact_url:
        dummy_id = "e2e-dummy-lora"
        data.setdefault("artifacts", []).append(
            {
                "id": dummy_id,
                "kind": "lora",
                "model_type": "lora",
                "filename": DummyArtifactServer.FILENAME,
                "display_name": "Onboarding E2E dummy artifact (not a real model - --fresh-download only)",
                "required": False,
                "size_bytes": DummyArtifactServer.SIZE_BYTES,
                "provider_hint": {"download_url": dummy_artifact_url},
            }
        )
        for step in data.get("steps", []):
            if step.get("kind") in ("artifacts.plan", "artifacts.fetch"):
                ids = list(step.setdefault("params", {}).get("artifact_ids") or [])
                if dummy_id not in ids:
                    ids.append(dummy_id)
                step["params"]["artifact_ids"] = ids

    if no_gpu:
        data["steps"] = [
            s for s in data.get("steps", []) if s.get("kind") not in ("generation.smoke", "workspace.activate")
        ]


def primary_artifact(recipe: dict) -> Optional[dict]:
    """The recipe's first declared artifact - used to sanity-check the depot
    mirror actually contains what the recipe expects before we bother
    starting the backend."""
    artifacts = recipe.get("artifacts") or []
    return artifacts[0] if artifacts else None


TYPE_DIR_MAP = {
    "checkpoint": "checkpoints",
    "diffusion_model": "diffusion_models",
    "lora": "loras",
    "embedding": "embeddings",
    "upscaler": "upscalers",
    "controlnet": "controlnet",
    "vae": "vae",
    "text_encoder": "text_encoders",
    "unet": "unet",
}


# ---------------------------------------------------------------------------
# Journey state
# ---------------------------------------------------------------------------


@dataclass
class Journey:
    """Everything the stages need, threaded through explicitly rather than
    hung off module globals - a future pytest can build one of these against
    a backend it boots its own way and call the `stage_*` functions directly."""

    client: SetupClient
    instance_dir: Path
    storage_dir: Path
    recipe_id: str
    no_gpu: bool
    fresh_download: bool
    username: str = "onboarding-e2e"
    email: str = "onboarding-e2e@example.com"
    password: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    run_view: Optional[Dict[str, Any]] = None
    started_at: float = field(default_factory=time.monotonic)
    # Tracks the last-logged status per step_key across every `_drive_with_progress`
    # call in this journey, so a step already reported as "succeeded" in an earlier
    # call (e.g. before a grant_consent) isn't re-printed unchanged on the next one.
    seen_step_status: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_claim_owner(journey: Journey) -> None:
    stage = "claim-owner"
    account_type = harness_claim_owner(
        journey.client, username=journey.username, email=journey.email, password=journey.password
    )
    if account_type != "ADMIN":
        raise StageError(
            stage,
            f"Expected the first registration on a fresh instance to become the owner (ADMIN), got {account_type!r}.",
        )


def stage_login(journey: Journey) -> None:
    harness_login(journey.client, username=journey.username, password=journey.password)


def stage_readiness(journey: Journey) -> Dict[str, Any]:
    stage = "readiness"
    resp = journey.client.get("/api/readiness")
    body = raise_for_status(stage, resp, "Readiness check")
    for row in body.get("checks", []) or []:
        stage_log(stage, f"  {row.get('area')}: {row.get('status')} - {row.get('message')}")
    stage_log(stage, f"Readiness reported ({len(body.get('checks', []) or [])} facet(s))")
    return body


def stage_index_models(journey: Journey, *, expect_filename: Optional[str], expect_model_type: Optional[str]) -> None:
    """Trigger a model-directory scan and wait for it to land in the DB
    index. Needed because `artifacts.plan`/`artifacts.fetch` look the model
    up by its indexed identity (`ModelRepository.get_by_identity`), and the
    recipe's own `models.index` step only runs AFTER `artifacts.plan`/
    `artifacts.fetch` (see recipes/sdxl-starter.yml's step order) - a file
    that's only ever been dropped on disk, never indexed, would otherwise
    read as "missing" and trigger a real download even though it's sitting
    right there in the depot mirror."""
    stage = "index-models"
    resp = journey.client.post("/api/models/index")
    raise_for_status(stage, resp, "Model index trigger")
    if not expect_filename:
        stage_log(stage, "Triggered a model directory scan (no specific file to wait for)")
        return

    deadline = time.monotonic() + MODEL_INDEX_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = journey.client.get(
            "/api/models",
            # `all_models=true`: without it, even an admin's model list is scoped to
            # models already ASSIGNED to them (ModelAccessPolicy.get_allowed_model_ids) -
            # a freshly indexed file has no assignment yet, so it would never show up
            # here and this poll would spin until it timed out.
            params={"search": expect_filename, "model_type": expect_model_type, "limit": 5, "all_models": "true"},
        )
        body = raise_for_status(stage, resp, "Model list poll")
        models = ((body.get("data") or {}).get("models")) or []
        if any(m.get("filename") == expect_filename for m in models):
            stage_log(stage, f"Depot scan indexed '{expect_filename}' - artifacts.plan will see it as present")
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise StageError(
        stage,
        f"'{expect_filename}' never showed up in the model index within "
        f"{MODEL_INDEX_TIMEOUT_SECONDS:.0f}s - check it actually exists under "
        f"<models-dir>/{TYPE_DIR_MAP.get(expect_model_type or '', expect_model_type)}/",
    )


def stage_list_recipes(journey: Journey) -> List[Dict[str, Any]]:
    stage = "list-recipes"
    resp = journey.client.get("/api/setup/recipes")
    body = raise_for_status(stage, resp, "List recipes")
    recipes = body.get("recipes") or []
    ids = [r.get("id") for r in recipes]
    stage_log(stage, f"Available recipes: {ids}")
    if journey.recipe_id not in ids:
        raise StageError(stage, f"Recipe '{journey.recipe_id}' not found among {ids}")
    return recipes


def _run_action_in_background(fn: Callable[[], requests.Response]) -> "concurrent.futures.Future":
    executor = ThreadPoolExecutor(max_workers=1)
    return executor.submit(fn)


def _drive_with_progress(journey: Journey, stage: str, action: Callable[[], requests.Response]) -> Dict[str, Any]:
    """Issue a mutating setup-run call (create / grant_consent / resume) on a
    background thread, and poll `GET /runs/{id}` from the main thread every
    couple of seconds so long-running steps (a real generation, a download)
    show live progress - mirroring how the real frontend polls a run that's
    still mid-drive server-side (see registry.py's `_report_progress`
    comment).

    `drive()` runs on a background thread server-side (see
    `SetupRunManager.drive_async`), so the mutating call's own HTTP response
    returns almost immediately and reflects whatever the run's status was at
    that instant - typically still pending/running, NOT the driven-forward
    state. So the harness catches up the same way the real frontend does: after the
    action call returns, keep polling `GET /runs/{id}` until the run reaches
    a state that needs the caller again (`awaiting_consent`) or a terminal
    one, and treat THAT as the source of truth instead of the action
    response.
    """
    future = _run_action_in_background(action)
    run_id = (journey.run_view or {}).get("id")
    while not future.done():
        if run_id:
            try:
                resp = journey.client.get(f"/api/setup/runs/{run_id}", timeout=10.0)
                if resp.status_code == 200:
                    _log_step_transitions(stage, resp.json(), journey.seen_step_status)
            except requests.RequestException:
                pass  # the primary action call is what we actually wait on
        time.sleep(POLL_INTERVAL_SECONDS)
    resp = future.result()
    body = raise_for_status(stage, resp, "Setup-run action")
    run_id = body.get("id") or run_id
    _log_step_transitions(stage, body, journey.seen_step_status)

    deadline = time.monotonic() + RUN_ACTION_TIMEOUT_SECONDS
    while body.get("status") in ("pending", "running") and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        resp = journey.client.get(f"/api/setup/runs/{run_id}", timeout=10.0)
        body = raise_for_status(stage, resp, "Poll run after background drive")
        _log_step_transitions(stage, body, journey.seen_step_status)

    return body


def _log_step_transitions(stage: str, run_view: Dict[str, Any], seen: Dict[str, str]) -> None:
    for step in run_view.get("steps") or []:
        key, status = step.get("step_key"), step.get("status")
        if seen.get(key) != status:
            seen[key] = status
            extra = ""
            attempts = step.get("attempts") or []
            if attempts and attempts[-1].get("progress_total"):
                a = attempts[-1]
                extra = f" ({a.get('progress_current')}/{a.get('progress_total')} {a.get('progress_unit') or ''})"
            stage_log(stage, f"  step '{key}' [{step.get('kind')}] -> {status}{extra}")


def stage_start_run(journey: Journey) -> Dict[str, Any]:
    stage = "start-run"
    stage_log(stage, f"Starting setup run for recipe '{journey.recipe_id}'")

    def _create() -> requests.Response:
        return journey.client.post(
            "/api/setup/runs",
            json={"recipe_id": journey.recipe_id, "recipe_version": 1, "safe_input": {}},
            timeout=RUN_ACTION_TIMEOUT_SECONDS,
        )

    journey.run_view = _drive_with_progress(journey, stage, _create)
    return journey.run_view


def stage_drive_to_completion(journey: Journey) -> Dict[str, Any]:
    """Repeatedly grant consent for whatever the run parks on, driving it
    forward until it reaches a terminal status (completed/failed/cancelled)
    or genuinely stops making progress."""
    stage = "consent-loop"
    run = journey.run_view
    assert run is not None
    seen_steps: set = set()

    while run.get("status") == "awaiting_consent":
        current_step_key = run.get("current_step")
        attempt = _current_step_attempt(run, current_step_key)
        consent = (attempt or {}).get("safe_output", {}).get("consent_request") if attempt else None
        if consent is None:
            raise StageError(stage, f"Run is awaiting_consent on '{current_step_key}' but no consent_request was found")

        artifacts = consent.get("artifacts") or []
        total = consent.get("total_bytes")
        names = ", ".join(a.get("display_name") or a.get("id") for a in artifacts)
        stage_log(
            stage,
            f"Step '{current_step_key}' wants to download: {names} "
            f"(total {total if total is not None else 'unknown'} bytes) - granting consent",
        )

        loop_guard = (current_step_key, len(seen_steps))
        if current_step_key in seen_steps:
            raise StageError(stage, f"Run is stuck re-parking on '{current_step_key}' - not making progress")
        seen_steps.add(current_step_key)

        def _grant(step_key=current_step_key) -> requests.Response:
            return journey.client.post(
                f"/api/setup/runs/{run['id']}/actions/grant_consent",
                json={"step_key": step_key},
                timeout=RUN_ACTION_TIMEOUT_SECONDS,
            )

        run = _drive_with_progress(journey, stage, _grant)
        journey.run_view = run

    if run.get("status") == "running":
        # `drive()` stopped mid-recipe without parking (its max_steps circuit
        # breaker, or current_step didn't advance) - nudge it forward with an
        # explicit resume rather than silently declaring victory. Routed
        # through `_drive_with_progress` like every other action, since
        # `drive_async` means this resume's own response is no longer the
        # driven-forward state either.
        stage_log(stage, "Run is still 'running' with no consent pending - resuming to keep driving it")

        def _resume() -> requests.Response:
            return journey.client.post(
                f"/api/setup/runs/{run['id']}/actions/resume",
                timeout=RUN_ACTION_TIMEOUT_SECONDS,
            )

        run = _drive_with_progress(journey, stage, _resume)
        journey.run_view = run

    return run


def _current_step_attempt(run: Dict[str, Any], step_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not step_key:
        return None
    for step in run.get("steps") or []:
        if step.get("step_key") == step_key:
            attempts = step.get("attempts") or []
            return attempts[-1] if attempts else None
    return None


def _step_by_kind(run: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    for step in run.get("steps") or []:
        if step.get("kind") == kind:
            return step
    return None


def stage_assert_no_unexpected_download(journey: Journey, checkpoint_filename: str) -> None:
    """Assert `artifacts.plan` saw the depot's checkpoint as already present
    and never asked for it - the whole point of mirroring --models-dir into
    the ephemeral instance before starting the run."""
    stage = "assert-no-download"
    run = journey.run_view
    assert run is not None
    plan_step = _step_by_kind(run, "artifacts.plan")
    if plan_step is None:
        stage_log(stage, "Recipe has no artifacts.plan step - nothing to assert here")
        return
    attempts = plan_step.get("attempts") or []
    if not attempts:
        raise StageError(stage, "artifacts.plan never ran")

    # Walk every attempt (a consent grant records a fresh SUCCEEDED attempt
    # after the AWAITING_CONSENT one) looking for the checkpoint in a
    # "wants to download this" list - it must never appear in one.
    for attempt in attempts:
        safe_output = attempt.get("safe_output") or {}
        consent = safe_output.get("consent_request") or {}
        approved = safe_output.get("approved") or {}
        for bucket_name, bucket in (("consent_request.artifacts", consent.get("artifacts") or []),
                                     ("approved.artifacts", approved.get("artifacts") or [])):
            for artifact in bucket:
                display = artifact.get("display_name") or artifact.get("id") or ""
                if checkpoint_filename in display or artifact.get("id") == "sdxl-checkpoint":
                    raise StageError(
                        stage,
                        f"artifacts.plan asked to download the SDXL checkpoint (found in {bucket_name}) "
                        "even though it should already be present via the depot mirror.",
                    )
    stage_log(stage, f"Confirmed artifacts.plan never asked to download '{checkpoint_filename}' - depot mirror worked")


def stage_assert_smoke_output(journey: Journey) -> str:
    """Assert `generation.smoke` produced a real file on disk, and return its
    absolute path in the ephemeral storage directory."""
    stage = "assert-smoke-output"
    run = journey.run_view
    assert run is not None
    smoke_step = _step_by_kind(run, "generation.smoke")
    if smoke_step is None:
        raise StageError(stage, "Recipe has no generation.smoke step to assert against")
    if smoke_step.get("status") != "succeeded":
        raise StageError(stage, f"generation.smoke did not succeed (status={smoke_step.get('status')})")

    attempts = smoke_step.get("attempts") or []
    if not attempts:
        raise StageError(stage, "generation.smoke has no recorded attempt")
    output = attempts[-1].get("safe_output") or {}
    output_count = output.get("output_count")
    file_path = output.get("file_path")
    if not output_count or output_count < 1:
        raise StageError(stage, f"generation.smoke reported output_count={output_count}")
    if not file_path:
        raise StageError(stage, "generation.smoke succeeded but recorded no file_path")

    absolute = (journey.storage_dir / file_path).resolve()
    if not absolute.is_file():
        raise StageError(stage, f"generation.smoke's recorded output does not exist on disk: {absolute}")
    size = absolute.stat().st_size
    stage_log(stage, f"Real generation produced {output_count} output(s); first file: {absolute} ({size} bytes)")
    return str(absolute)


# ---------------------------------------------------------------------------
# Step-table / run summary printing
# ---------------------------------------------------------------------------


def print_step_table(run_view: Optional[Dict[str, Any]]) -> None:
    if not run_view:
        print("(no run was ever created)")
        return
    steps = run_view.get("steps") or []
    if not steps:
        print("(run has no recipe-backed step manifest)")
        return
    width_key = max((len(s.get("step_key", "")) for s in steps), default=10)
    width_kind = max((len(s.get("kind", "")) for s in steps), default=10)
    print(f"\nSetup run {run_view.get('id')} - overall status: {run_view.get('status')}")
    print(f"  {'step':<{width_key}}  {'kind':<{width_kind}}  status")
    print(f"  {'-' * width_key}  {'-' * width_kind}  ------")
    for step in steps:
        print(f"  {step.get('step_key', ''):<{width_key}}  {step.get('kind', ''):<{width_kind}}  {step.get('status')}")
    print()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ephemeral onboarding end-to-end harness (claim -> readiness -> setup recipe -> real generation).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--models-dir",
        default=os.environ.get("POTIONUI_E2E_MODELS_DIR"),
        help="Path to a real models directory (the 'depot') to mirror read-only into the "
        "ephemeral instance. Required unless POTIONUI_E2E_MODELS_DIR is set.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port for the ephemeral backend.")
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Stop cleanly right before the smoke-generation step (no GPU work happens) and "
        "report everything up to it as the result. For CI/CPU-only machines.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Skip teardown: leave the backend running and the temp dir on disk, and print the "
        "base URL + owner credentials so a human/agent can poke the live instance.",
    )
    parser.add_argument("--recipe", default=DEFAULT_RECIPE, help="Recipe id to run.")
    parser.add_argument(
        "--fresh-download",
        action="store_true",
        help="Exercise the consent+fetch path for real: adds a tiny dummy artifact (served over "
        "a local HTTP server, a few KB) that is never present, so the run genuinely parks on "
        "awaiting_consent and genuinely downloads something small - without touching the real "
        "multi-GB SDXL checkpoint.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if not args.models_dir:
        print(
            "--models-dir is required (or set POTIONUI_E2E_MODELS_DIR).",
            file=sys.stderr,
        )
        return 2
    models_source = Path(args.models_dir).expanduser().resolve()

    try:
        reject_forbidden_port(args.port)
    except StageError as exc:
        print(f"ERROR [{exc.stage}] {exc.message}", file=sys.stderr)
        return 1
    if not port_is_free(args.port):
        print(f"ERROR [args] Port {args.port} is already in use. Pick a different --port.", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:8]
    instance_dir = Path(tempfile.mkdtemp(prefix=f"potionui-onboarding-e2e-{run_id}-"))
    instance = EphemeralInstance(
        instance_dir=instance_dir,
        storage_dir=instance_dir / "storage",
        db_path=instance_dir / "storage" / "db.sqlite",
        models_dir=instance_dir / "models",
        recipes_dir=instance_dir / "recipes",
        port=args.port,
    )
    instance.storage_dir.mkdir(parents=True, exist_ok=True)

    dummy_server: Optional[DummyArtifactServer] = None
    current_stage = "setup"
    start_time = time.monotonic()
    journey: Optional[Journey] = None

    try:
        stage_log(current_stage, f"Ephemeral instance dir: {instance_dir}")

        current_stage = "models-dir"
        mirror_models_dir_readonly(models_source, instance.models_dir)

        current_stage = "recipes"
        if args.fresh_download:
            dummy_server = DummyArtifactServer(instance_dir / "dummy-download-source")
            stage_log(current_stage, f"Dummy artifact server: {dummy_server.url}")
        prepare_ephemeral_recipes(
            instance.recipes_dir,
            no_gpu=args.no_gpu,
            dummy_artifact_url=dummy_server.url if dummy_server else None,
        )
        recipe_path = instance.recipes_dir / "marketplace" / f"{args.recipe}.yml"
        if not recipe_path.is_file():
            raise StageError(current_stage, f"Recipe '{args.recipe}' has no file at {recipe_path}")
        recipe_data = yaml.safe_load(recipe_path.read_text()) or {}
        artifact = primary_artifact(recipe_data)
        if artifact is not None:
            expected = instance.models_dir / TYPE_DIR_MAP.get(artifact["model_type"], "checkpoints") / artifact["filename"]
            if not expected.exists():
                raise StageError(
                    current_stage,
                    f"The depot at {models_source} has no '{artifact['filename']}' under "
                    f"{TYPE_DIR_MAP.get(artifact['model_type'], 'checkpoints')}/ - the recipe '{args.recipe}' needs it "
                    "already on disk for this harness's 'skip download' assertion to mean anything.",
                )

        current_stage = "subprocess-boot"
        spawn_backend(instance, REPO_ROOT)

        base_url = f"http://127.0.0.1:{args.port}"
        client = SetupClient(base_url)

        current_stage = "health"
        stage_wait_for_health(client)

        journey = Journey(
            client=client,
            instance_dir=instance_dir,
            storage_dir=instance.storage_dir,
            recipe_id=args.recipe,
            no_gpu=args.no_gpu,
            fresh_download=args.fresh_download,
        )

        current_stage = "claim-owner"
        stage_claim_owner(journey)

        current_stage = "login"
        stage_login(journey)

        current_stage = "readiness"
        stage_readiness(journey)

        current_stage = "index-models"
        if artifact is not None:
            stage_index_models(journey, expect_filename=artifact["filename"], expect_model_type=artifact["model_type"])
        else:
            stage_index_models(journey, expect_filename=None, expect_model_type=None)

        current_stage = "list-recipes"
        stage_list_recipes(journey)

        current_stage = "start-run"
        stage_start_run(journey)

        current_stage = "consent-loop"
        stage_drive_to_completion(journey)

        current_stage = "assert-no-download"
        if artifact is not None:
            stage_assert_no_unexpected_download(journey, artifact["filename"])

        run_status = (journey.run_view or {}).get("status")
        if run_status == "failed":
            failing = journey.run_view.get("current_step")
            detail = journey.run_view.get("safe_error_detail")
            raise StageError("run-failed", f"Setup run failed on step '{failing}': {detail}")

        if args.no_gpu:
            stage_log("no-gpu", "Stopping before generation.smoke, as requested by --no-gpu")
            print_step_table(journey.run_view)
            elapsed = time.monotonic() - start_time
            log(f"SUCCESS (--no-gpu, stopped before smoke) in {elapsed:.1f}s")
            return 0

        if run_status != "completed":
            raise StageError(
                "run-incomplete",
                f"Expected the run to reach 'completed', got '{run_status}' "
                f"(current_step={journey.run_view.get('current_step')})",
            )

        current_stage = "assert-smoke-output"
        output_path = stage_assert_smoke_output(journey)

        print_step_table(journey.run_view)
        elapsed = time.monotonic() - start_time
        log(f"First real generated output: {output_path}")
        log(f"SUCCESS in {elapsed:.1f}s")
        return 0

    except StageError as exc:
        print(f"\nFAILED at stage [{exc.stage}]: {exc.message}", file=sys.stderr)
        if instance.log_path and instance.log_path.exists():
            print(f"Backend log: {instance.log_path}", file=sys.stderr)
        if journey is not None:
            print_step_table(journey.run_view)
        return 1
    except Exception:
        print(f"\nUNEXPECTED FAILURE at stage [{current_stage}]:", file=sys.stderr)
        traceback.print_exc()
        if instance.log_path and instance.log_path.exists():
            print(f"Backend log: {instance.log_path}", file=sys.stderr)
        return 1
    finally:
        if dummy_server is not None:
            dummy_server.stop()
        if args.keep:
            base_url = f"http://127.0.0.1:{args.port}"
            print("\n--keep set: leaving the instance running.")
            print(f"  URL:      {base_url}")
            print(f"  Username: {journey.username if journey else 'onboarding-e2e'}")
            print(f"  Password: {journey.password if journey else '(registration never completed)'}")
            print(f"  Instance dir: {instance_dir}")
            print("  Stop it yourself when done: kill the backend process and rm -rf the instance dir above.\n")
        teardown_backend(instance, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
