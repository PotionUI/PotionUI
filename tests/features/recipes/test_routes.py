"""The admin `/api/recipes` surface: catalog, detail, runs, step kinds.

A real `RecipeRunner` backed by a temp file exercises the wiring end to end,
so an admin run really does skip the steps a recipe marks `onboarding_only`
rather than merely reporting that it would.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.recipes.executors.base import StepResult
from src.features.recipes.executors.registry import RecipeExecutorRegistry
from src.features.recipes.preset_links import RecipePresetLinks
from src.features.recipes.routes import build_router
from src.features.recipes.runner import RecipeRunner
from src.features.recipes.schema import (
    Recipe,
    RecipeArtifact,
    RecipePresetRef,
    RecipeSmokeRef,
    RecipeStep,
)
from src.platform.database.database import db as global_db
from tests.fixtures.db_template import copy_template_db
from src.platform.plugins.recipe_steps import (
    RecipeStepKindRegistration,
    RecipeStepKindRegistry,
)
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


@pytest.fixture
def file_db(tmp_path):
    db_path = tmp_path / "recipe_routes.db"
    copy_template_db(db_path)
    original_path = global_db.db_path
    global_db.db_path = db_path
    try:
        yield global_db
    finally:
        global_db.db_path = original_path


def _user(account_type: AccountType, user_id: str = "user-1") -> User:
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=account_type,
        id=user_id,
    )


class _OkExecutor:
    """Records which steps actually ran."""

    def __init__(self, seen):
        self.seen = seen

    def execute(self, context):
        self.seen.append(context.step.key)
        return StepResult.ok({"ran": context.step.key})


class _FakeCatalog:
    def __init__(self, recipes, load_errors=None):
        self._recipes = {r.id: r for r in recipes}
        self.load_errors = dict(load_errors or {})

    def list_recipes(self):
        return sorted(self._recipes.values(), key=lambda r: r.id)

    def get_recipe(self, recipe_id, version=None):
        return self._recipes.get(recipe_id)


def _recipe(recipe_id="demo"):
    return Recipe(
        id=recipe_id,
        schema_version=1,
        version=3,
        name="Demo Recipe",
        engine="native",
        summary="A demo",
        description="Longer copy",
        category="image",
        source="marketplace",
        artifacts=[
            RecipeArtifact(
                id="ckpt",
                kind="checkpoint",
                model_type="checkpoint",
                filename="demo.safetensors",
                display_name="Demo checkpoint",
                size_bytes=17,
                gated=True,
                license_url="https://huggingface.co/demo/model",
            )
        ],
        presets=[RecipePresetRef(preset_id="PRESET1", path_hint="marketplace/Demo")],
        smoke=RecipeSmokeRef(preset_id="PRESET1", mode="txt2img"),
        steps=[
            RecipeStep(key="backend.ensure", kind="backend.ensure", title="Backend"),
            RecipeStep(
                key="workspace.activate",
                kind="workspace.activate",
                title="Finish onboarding",
                onboarding_only=True,
            ),
        ],
    )


def _client(
    current_user: User,
    *,
    recipes=None,
    executors=None,
    step_kind_registry=None,
    load_errors=None,
    preset_loader=None,
    preset_db_repo=None,
):
    catalog = _FakeCatalog(recipes if recipes is not None else [_recipe()], load_errors)
    runner = RecipeRunner()
    if executors is not None:
        runner.register_executor_registry(
            RecipeExecutorRegistry(catalog, executors, step_kind_registry=step_kind_registry)
        )
    # Readiness collaborators are pinned to their empty answers so the
    # recipe-readiness route reports a deterministic report instead of
    # probing anything.
    backend_registry = MagicMock()
    backend_registry.get_all_backends.return_value = {}
    preset_collaborators = MagicMock()
    preset_collaborators.list_presets.return_value = []
    generation_repository = MagicMock()
    generation_repository.count_by_status.return_value = 0
    instance_claim_repository = MagicMock()
    instance_claim_repository.check_connection.return_value = None

    container = SimpleNamespace(
        recipe_runner=runner,
        recipe_catalog=catalog,
        recipe_preset_links=RecipePresetLinks(
            catalog, runner, preset_loader=preset_loader, preset_db_repo=preset_db_repo
        ),
        backend_registry=backend_registry,
        preset_collaborators=preset_collaborators,
        model_repository=MagicMock(),
        generation_repository=generation_repository,
        instance_claim_repository=instance_claim_repository,
        migration_runner=Mock(has_pending_migrations=Mock(return_value=False)),
    )
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: current_user
    return TestClient(app), runner


def _poll_until(client, run_id, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        body = client.get(f"/api/recipes/runs/{run_id}").json()
        if predicate(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"Timed out on run '{run_id}'; last body: {body}")


# --- gating ----------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/recipes"),
        ("get", "/api/recipes/demo"),
        ("get", "/api/recipes/demo/readiness"),
        ("get", "/api/recipes/runs"),
        ("get", "/api/recipes/step-kinds"),
    ],
)
def test_non_admin_is_forbidden(file_db, method, path):
    client, _ = _client(_user(AccountType.USER))
    assert getattr(client, method)(path).status_code == 403


def test_non_admin_cannot_start_a_run(file_db):
    client, _ = _client(_user(AccountType.USER))
    assert client.post("/api/recipes/demo/runs", json={}).status_code == 403


# --- catalog ---------------------------------------------------------------


def _preset_loader(presets):
    return SimpleNamespace(load_preset_by_id=lambda preset_id: presets.get(preset_id))


def _preset_db_repo(installed_ids):
    return SimpleNamespace(
        get_all_installed_presets=lambda: [SimpleNamespace(preset_id=i) for i in installed_ids]
    )


def test_list_reports_source_step_count_and_linked_presets(file_db):
    client, _ = _client(
        _user(AccountType.ADMIN),
        preset_loader=_preset_loader(
            {"PRESET1": SimpleNamespace(name="Demo Preset", media={"cover": "public/cover.webp"})}
        ),
        preset_db_repo=_preset_db_repo(["PRESET1"]),
    )

    body = client.get("/api/recipes").json()

    assert [r["id"] for r in body["recipes"]] == ["demo"]
    row = body["recipes"][0]
    assert row["source"] == "marketplace"
    assert row["plugin_id"] is None
    assert row["step_count"] == 2
    assert row["presets"] == [
        {
            "id": "PRESET1",
            "name": "Demo Preset",
            "cover_url": "/api/media/presets/PRESET1/public/cover.webp?size=small",
            "installed": True,
        }
    ]
    assert row["total_download_bytes"] == 17
    assert row["artifact_count"] == 1


def test_detail_lists_steps_artifacts_presets_and_smoke(file_db):
    recipe = _recipe()
    client, _ = _client(
        _user(AccountType.ADMIN),
        recipes=[recipe],
        load_errors={recipe.source_path: ["something odd"]},
        preset_loader=_preset_loader({"PRESET1": SimpleNamespace(name="Demo Preset", media=None)}),
        preset_db_repo=_preset_db_repo([]),
    )

    body = client.get("/api/recipes/demo").json()

    assert [(s["key"], s["onboarding_only"]) for s in body["steps"]] == [
        ("backend.ensure", False),
        ("workspace.activate", True),
    ]
    assert body["artifacts"][0]["filename"] == "demo.safetensors"
    assert body["artifacts"][0]["gated"] is True
    assert body["artifacts"][0]["license_url"] == "https://huggingface.co/demo/model"
    assert body["presets"] == [
        {"id": "PRESET1", "name": "Demo Preset", "cover_url": None, "installed": False}
    ]
    assert body["smoke"] == {"preset_id": "PRESET1", "mode": "txt2img"}
    assert body["load_errors"] == ["something odd"]


def test_linked_preset_missing_from_disk_is_left_out(file_db):
    client, _ = _client(
        _user(AccountType.ADMIN),
        preset_loader=_preset_loader({}),
        preset_db_repo=_preset_db_repo([]),
    )

    assert client.get("/api/recipes/demo").json()["presets"] == []


def test_detail_of_unknown_recipe_is_404(file_db):
    client, _ = _client(_user(AccountType.ADMIN))
    assert client.get("/api/recipes/nope").status_code == 404


def test_readiness_reports_the_four_facets(file_db):
    client, _ = _client(_user(AccountType.ADMIN))

    response = client.get("/api/recipes/demo/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["overall"] in {"ready", "not_ready", "degraded"}
    assert {c["area"] for c in body["checks"]} == {
        "service",
        "execution",
        "content",
        "generation_proven",
    }


# --- step kinds ------------------------------------------------------------


def test_step_kinds_reports_core_and_plugin_sources(file_db):
    registry = RecipeStepKindRegistry()
    registry.register(
        RecipeStepKindRegistration(
            kind="collections.ensure", executor=_OkExecutor([]), source="my-plugin"
        )
    )
    client, _ = _client(
        _user(AccountType.ADMIN),
        executors={"backend.ensure": _OkExecutor([])},
        step_kind_registry=registry,
    )

    kinds = {k["kind"]: k for k in client.get("/api/recipes/step-kinds").json()["kinds"]}

    assert kinds["backend.ensure"] == {
        "kind": "backend.ensure",
        "source": "core",
        "plugin_id": None,
    }
    assert kinds["collections.ensure"] == {
        "kind": "collections.ensure",
        "source": "plugin",
        "plugin_id": "my-plugin",
    }


# --- runs ------------------------------------------------------------------


def test_admin_run_skips_onboarding_only_steps(file_db):
    seen = []
    client, _ = _client(
        _user(AccountType.ADMIN),
        executors={
            "backend.ensure": _OkExecutor(seen),
            "workspace.activate": _OkExecutor(seen),
        },
    )

    created = client.post("/api/recipes/demo/runs", json={})
    assert created.status_code == 201
    assert created.json()["mode"] == "admin"
    run_id = created.json()["id"]

    body = _poll_until(client, run_id, lambda b: b["status"] == "completed")

    assert seen == ["backend.ensure"]
    assert [s["step_key"] for s in body["steps"]] == ["backend.ensure"]


def test_second_run_while_one_is_active_is_409(file_db):
    client, runner = _client(_user(AccountType.ADMIN))
    runner.create_run("demo", recipe_version=3)

    response = client.post("/api/recipes/demo/runs", json={})

    assert response.status_code == 409


def test_409_detail_carries_the_blocking_run(file_db):
    client, runner = _client(
        _user(AccountType.ADMIN), recipes=[_recipe("demo"), _recipe("other")]
    )
    blocking = runner.create_run("demo", recipe_version=3)

    response = client.post("/api/recipes/other/runs", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"]
    assert detail["active_run"] == {
        "id": blocking.id,
        "recipe_id": "demo",
        "recipe_name": "Demo Recipe",
        "status": "pending",
        "current_step_key": None,
    }


def test_runs_listing_is_newest_first_and_filters_by_recipe(file_db):
    client, runner = _client(
        _user(AccountType.ADMIN), recipes=[_recipe("demo"), _recipe("other")]
    )
    first = runner.create_run("demo", recipe_version=3)
    runner.apply_action(first.id, "cancel")
    second = runner.create_run("other", recipe_version=3)

    all_runs = client.get("/api/recipes/runs").json()["runs"]
    assert [r["id"] for r in all_runs] == [second.id, first.id]

    only_demo = client.get("/api/recipes/runs", params={"recipe_id": "demo"}).json()["runs"]
    assert [r["id"] for r in only_demo] == [first.id]


def test_run_action_pauses_and_resumes(file_db):
    client, runner = _client(_user(AccountType.ADMIN))
    run = runner.create_run("demo", recipe_version=3)

    paused = client.post(f"/api/recipes/runs/{run.id}/actions", json={"action": "pause"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/recipes/runs/{run.id}/actions", json={"action": "resume"})
    assert resumed.json()["status"] == "running"


def test_unknown_run_action_is_400(file_db):
    client, runner = _client(_user(AccountType.ADMIN))
    run = runner.create_run("demo", recipe_version=3)

    response = client.post(f"/api/recipes/runs/{run.id}/actions", json={"action": "nope"})

    assert response.status_code == 400


def test_run_detail_of_missing_run_is_404(file_db):
    client, _ = _client(_user(AccountType.ADMIN))
    assert client.get("/api/recipes/runs/nope").status_code == 404


class _AwaitsVariantConsent:
    def execute(self, context):
        return StepResult.awaiting(
            {
                "artifacts": [],
                "total_bytes": 0,
                "slots": [{"id": "dit", "recommended_variant_id": "fp8", "variants": [{"id": "fp8"}, {"id": "nvfp4"}]}],
            }
        )


class _RecordSelections:
    def __init__(self, seen):
        self.seen = seen

    def execute(self, context):
        self.seen.append(dict(context.selections))
        return StepResult.ok({})


def _variant_recipe():
    return Recipe(
        id="demo",
        schema_version=1,
        version=3,
        name="Demo",
        engine="native",
        category="image",
        steps=[
            RecipeStep(key="artifacts.plan", kind="artifacts.plan", title="Plan"),
            RecipeStep(key="artifacts.fetch", kind="artifacts.fetch", title="Fetch"),
        ],
    )


def test_admin_consent_accepts_variant_selections(file_db):
    seen = []
    client, _ = _client(
        _user(AccountType.ADMIN),
        recipes=[_variant_recipe()],
        executors={"artifacts.plan": _AwaitsVariantConsent(), "artifacts.fetch": _RecordSelections(seen)},
    )
    run_id = client.post("/api/recipes/demo/runs", json={}).json()["id"]
    _poll_until(client, run_id, lambda b: b["status"] == "awaiting_consent")

    bad = client.post(f"/api/recipes/runs/{run_id}/consent/artifacts.plan", json={"selections": {"dit": "bf16"}})
    assert bad.status_code == 400

    ok = client.post(f"/api/recipes/runs/{run_id}/consent/artifacts.plan", json={"selections": {"dit": "nvfp4"}})
    assert ok.status_code == 200
    _poll_until(client, run_id, lambda b: b["status"] == "completed")
    assert seen == [{"dit": "nvfp4"}]


def test_admin_consent_without_a_body_still_works(file_db):
    seen = []
    client, _ = _client(
        _user(AccountType.ADMIN),
        recipes=[_variant_recipe()],
        executors={"artifacts.plan": _AwaitsVariantConsent(), "artifacts.fetch": _RecordSelections(seen)},
    )
    run_id = client.post("/api/recipes/demo/runs", json={}).json()["id"]
    _poll_until(client, run_id, lambda b: b["status"] == "awaiting_consent")

    assert client.post(f"/api/recipes/runs/{run_id}/consent/artifacts.plan").status_code == 200
    _poll_until(client, run_id, lambda b: b["status"] == "completed")
    assert seen == [{"dit": "fp8"}]


def test_detail_lists_artifact_variants(file_db):
    from src.features.recipes.schema import RecipeArtifactVariant, RecipeVariantRule

    recipe = _recipe()
    slot = RecipeArtifact(
        id="dit",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="fp8.safetensors",
        display_name="DiT",
        size_bytes=12,
        variants=(
            RecipeArtifactVariant(
                id="fp8",
                label="Balanced",
                precision="fp8",
                filename="fp8.safetensors",
                size_bytes=12,
                uploader="org",
                source_url="https://huggingface.co/org/repo",
                recommended_for=(RecipeVariantRule(min_vram_gb=20, generations=("ada",)),),
                default=True,
            ),
        ),
    )
    recipe.artifacts.append(slot)
    client, _ = _client(_user(AccountType.ADMIN), recipes=[recipe])

    body = client.get("/api/recipes/demo").json()

    dit = next(a for a in body["artifacts"] if a["id"] == "dit")
    assert dit["variants"] == [
        {
            "id": "fp8",
            "label": "Balanced",
            "precision": "fp8",
            "filename": "fp8.safetensors",
            "size_bytes": 12,
            "gated": False,
            "license_url": None,
            "uploader": "org",
            "source_url": "https://huggingface.co/org/repo",
            "default": True,
            "recommended_for": [{"min_vram_gb": 20.0, "generations": ["ada"]}],
        }
    ]
    ckpt = next(a for a in body["artifacts"] if a["id"] == "ckpt")
    assert ckpt["variants"] == []
