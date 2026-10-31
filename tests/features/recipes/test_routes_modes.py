import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.executors.base import StepResult
from src.features.recipes.executors.modes_plan import ModesPlanExecutor
from src.features.recipes.executors.registry import RecipeExecutorRegistry
from src.features.recipes.mode_plans import ModePlanner
from src.features.recipes.preset_links import RecipePresetLinks
from src.features.recipes.removal import ModeFileRemoval
from src.features.recipes.routes import build_router
from src.features.recipes.runner import RecipeRunner
from src.features.recipes.targets import InstallTargets
from src.platform.database.database import db as global_db
from src.platform.runtime.gpu_profile import build_gpu_profile
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.recipes.mode_fixtures import GIB, FakeDbRepo, FakeLoader, FakeModelRepo, video_preset
from tests.fixtures.db_template import copy_template_db

RECIPE = "modes-vid-1-0"
BASE = [("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors")]


@pytest.fixture
def file_db(tmp_path):
    db_path = tmp_path / "recipe_modes_routes.db"
    copy_template_db(db_path)
    original_path = global_db.db_path
    global_db.db_path = db_path
    try:
        yield global_db
    finally:
        global_db.db_path = original_path


class Recorder:
    def __init__(self):
        self.plans = []

    def execute(self, context):
        self.plans.append(context.plans(context.step.params.get("plan")))
        return StepResult.ok()


def _admin():
    return User(username="a", email="a@example.com", password_hash="x", account_type=AccountType.ADMIN, id="admin-1")


def _client(tmp_path, repo):
    loader = FakeLoader(video_preset())
    catalog = RecipeCatalog(str(tmp_path / "recipes"))
    catalog.attach_presets(loader)
    roots = SimpleNamespace(write_dir=lambda t: SimpleNamespace(path=tmp_path, root_id="home"))
    targets = InstallTargets(
        model_roots=roots,
        gpu_profile_provider=lambda *a: build_gpu_profile((8, 9), 24.0, name="RTX 4090"),
        disk_usage=lambda p: SimpleNamespace(free=900 * GIB, total=1000 * GIB, used=0),
    )
    db = FakeDbRepo()
    planner = ModePlanner(loader, repo, preset_db_repo=db, targets=targets)
    recorder = Recorder()
    runner = RecipeRunner()
    executors = {kind: recorder for kind in ("backend.ensure", "artifacts.fetch", "models.index", "preset.ensure", "preset.defaults", "pipeline.render")}
    executors["modes.plan"] = ModesPlanExecutor(planner)
    runner.register_executor_registry(RecipeExecutorRegistry(catalog, executors))
    locator = SimpleNamespace(locations=lambda model_id: [])
    container = SimpleNamespace(
        recipe_runner=runner,
        recipe_catalog=catalog,
        recipe_preset_links=RecipePresetLinks(catalog, runner, preset_loader=loader, preset_db_repo=db, mode_planner=planner),
        mode_file_removal=ModeFileRemoval(catalog, planner, repo, locator, roots, preset_db_repo=db, preset_loader=loader),
    )
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = _admin
    return TestClient(app), runner, recorder


def _poll(client, run_id, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/api/recipes/runs/{run_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run never reached {status}: {body}")


def test_list_and_detail_show_mode_counts_and_bytes(file_db, tmp_path):
    client, _, _ = _client(tmp_path, FakeModelRepo(files=BASE))

    row = next(r for r in client.get("/api/recipes").json()["recipes"] if r["id"] == RECIPE)
    assert (row["source"], row["preset_id"], row["modes_total"], row["modes_ready"]) == ("derived", "VID", 5, 1)
    assert row["missing_bytes"] == 0
    detail = client.get(f"/api/recipes/{RECIPE}").json()
    assert [m["id"] for m in detail["modes"]] == ["video", "cinemagraph", "add_water", "restore", "motion"]
    assert detail["modes"][0]["ready"] is True


def test_dry_run_returns_the_consent_payload_and_creates_no_run(file_db, tmp_path):
    client, runner, _ = _client(tmp_path, FakeModelRepo(files=BASE))

    body = client.post(f"/api/recipes/{RECIPE}/plan", json={"preselect_modes": ["cinemagraph"]}).json()

    assert body["consent_request"]["kind"] == "modes"
    assert body["totals"] == {"download_bytes": 200 * 1024 ** 2, "file_count": 1}
    plan_step = next(s for s in body["steps"] if s["kind"] == "modes.plan")
    assert plan_step["preview"] == {"summary": "5 modes, 1 ready"}
    assert runner.list_runs() == []


def test_start_with_preselected_modes_then_grant_modes_and_extras(file_db, tmp_path):
    client, runner, recorder = _client(tmp_path, FakeModelRepo(files=BASE))

    run = client.post(f"/api/recipes/{RECIPE}/runs", json={"preselect_modes": ["cinemagraph"]}).json()
    assert run["safe_input"] == {"preselect_modes": ["cinemagraph"]}
    parked = _poll(client, run["id"], "awaiting_consent")
    consent = parked["steps"][1]["attempts"][-1]["safe_output"]["consent_request"]
    assert [m["id"] for m in consent["modes"] if m["selected"]] == ["cinemagraph"]

    rejected = client.post(f"/api/recipes/runs/{run['id']}/consent/modes.plan", json={"modes": ["ghost"]})
    assert rejected.status_code == 400
    granted = client.post(
        f"/api/recipes/runs/{run['id']}/consent/modes.plan",
        json={"modes": ["cinemagraph", "restore"], "extras": ["deblur_lora"], "target": "local"},
    )
    assert granted.status_code == 200
    _poll(client, run["id"], "completed")
    plan = recorder.plans[-1][0]
    assert plan["modes"] == ["cinemagraph", "restore"]
    assert [f["slot_id"] for f in plan["files"]] == ["effect_lora@cinemagraph", "deblur_lora"]


def test_removal_endpoints_preview_and_refuse_when_nothing_is_deletable(file_db, tmp_path):
    client, _, _ = _client(tmp_path, FakeModelRepo(files=BASE + [("lora", "cine.safetensors")]))

    preview = client.get(f"/api/recipes/{RECIPE}/modes/cinemagraph/removal").json()
    assert preview["delete"] == []
    assert preview["keep"][-1]["reason_code"] == "not_on_disk"
    assert client.post(f"/api/recipes/{RECIPE}/modes/cinemagraph/removal", json={"model_ids": ["m3"]}).status_code == 409
    assert client.get(f"/api/recipes/{RECIPE}/modes/ghost/removal").status_code == 404
