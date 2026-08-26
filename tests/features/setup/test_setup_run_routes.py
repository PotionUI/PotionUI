"""Route gating and flow for the durable setup-run endpoints.

The status route stays public; the run routes are admin-only with the
404-not-403 idiom (a non-admin cannot even confirm a run exists). A real
`SetupRunManager` backed by a temp file exercises the wiring end to end.

`POST /runs` and the actions now drive the run forward on a
background thread (`SetupRunManager.drive_async`) instead of inline, so a
response no longer necessarily reflects steps that ran as a side effect of
the same request - tests that need to observe a driven-forward state poll
`GET /runs/{id}` with `_poll_until`, exactly like the real frontend does.
"""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationManager
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.features.setup.routes import build_router
from src.features.setup.run_manager import SetupRunManager
from src.features.setup.executors.base import StepResult
from src.features.setup.executors.registry import SetupExecutorRegistry
from src.features.setup.recipe_schema import Recipe, RecipeStep


@pytest.fixture
def file_db(tmp_path):
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "setup_run_routes.db"
    try:
        MigrationManager().run_migrations()
        yield global_db
    finally:
        global_db.db_path = original_path


def _user(account_type: AccountType) -> User:
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=account_type,
        id="user-1",
    )


class _AwaitsConsentExecutor:
    def execute(self, context):
        return StepResult.awaiting({"artifacts": [{"id": "a", "display_name": "A", "size_bytes": 1, "kind": "checkpoint"}], "total_bytes": 1})


class _FakeCatalog:
    def __init__(self, recipes):
        self._recipes = {r.id: r for r in recipes}

    def list_recipes(self):
        return list(self._recipes.values())

    def get_recipe(self, recipe_id, version=None):
        return self._recipes.get(recipe_id)


def _consent_recipe(recipe_id="consent-recipe"):
    return Recipe(
        id=recipe_id, schema_version=1, version=1, name="Consent Recipe", engine="native",
        steps=[
            RecipeStep(key="artifacts.plan", kind="artifacts.plan", title="Plan"),
            RecipeStep(key="artifacts.fetch", kind="artifacts.fetch", title="Fetch"),
        ],
    )


def _client(current_user: User, *, recipe_catalog=None, executor_registry=None) -> TestClient:
    run_manager = SetupRunManager()
    if executor_registry is not None:
        run_manager.register_executor_registry(executor_registry)
    container = SimpleNamespace(
        setup_run_manager=run_manager,
        # readiness deps are resolved lazily and never touched here.
        backend_registry=Mock(),
        preset_manager=Mock(),
        model_repository=Mock(),
        generation_repository=Mock(),
        instance_claim_repository=Mock(),
        claim_token_manager=Mock(),
        settings_manager=Mock(),
    )
    if recipe_catalog is not None:
        container.recipe_catalog = recipe_catalog
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: current_user
    return TestClient(app)


def _poll_until(client, run_id, predicate, timeout=5.0):
    """Poll `GET /runs/{run_id}` until `predicate(body)` is true, mirroring
    the frontend's own ~2.5s poll of the same endpoint (see
    `frontend/src/lib/services/websocket.ts` callers) - now load-bearing in
    tests too, since `drive_async` no longer finishes before the triggering
    request returns."""
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        body = client.get(f"/api/setup/runs/{run_id}").json()
        if predicate(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for run '{run_id}' to satisfy predicate; last body: {body}")


def test_non_admin_gets_404_on_create(file_db):
    client = _client(_user(AccountType.USER))
    resp = client.post("/api/setup/runs", json={"recipe_id": "r"})
    assert resp.status_code == 404


def test_non_admin_gets_404_on_get(file_db):
    # Seed a real run as admin so we prove the 404 hides an existing run.
    run = SetupRunManager().create_run("r")
    client = _client(_user(AccountType.USER))
    resp = client.get(f"/api/setup/runs/{run.id}")
    assert resp.status_code == 404


def test_admin_create_and_get_flow(file_db):
    client = _client(_user(AccountType.ADMIN))

    created = client.post(
        "/api/setup/runs",
        json={"recipe_id": "native-image-starter", "safe_input": {"api_key": "sk"}},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["recipe_id"] == "native-image-starter"
    # secret stripped by redaction before persistence
    assert "api_key" not in (body["safe_input"] or {})

    run_id = body["id"]
    fetched = client.get(f"/api/setup/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run_id


def test_create_is_idempotent_under_active_run(file_db):
    client = _client(_user(AccountType.ADMIN))
    first = client.post("/api/setup/runs", json={"recipe_id": "r1"}).json()
    second = client.post("/api/setup/runs", json={"recipe_id": "r2"}).json()
    assert second["id"] == first["id"]  # returns the existing active run


def test_admin_actions_transition_state(file_db):
    client = _client(_user(AccountType.ADMIN))
    run_id = client.post("/api/setup/runs", json={"recipe_id": "r"}).json()["id"]

    # pending -> running (via a run manager call is Phase 3; here drive actions)
    paused = client.post(f"/api/setup/runs/{run_id}/actions/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/setup/runs/{run_id}/actions/resume")
    assert resumed.json()["status"] == "running"


def test_illegal_action_returns_409(file_db):
    client = _client(_user(AccountType.ADMIN))
    run_id = client.post("/api/setup/runs", json={"recipe_id": "r"}).json()["id"]
    # retry_step on a pending (never-failed) run is illegal.
    resp = client.post(f"/api/setup/runs/{run_id}/actions/retry_step")
    assert resp.status_code == 409


def test_unknown_action_returns_400(file_db):
    client = _client(_user(AccountType.ADMIN))
    run_id = client.post("/api/setup/runs", json={"recipe_id": "r"}).json()["id"]
    resp = client.post(f"/api/setup/runs/{run_id}/actions/frobnicate")
    assert resp.status_code == 400


def test_action_on_missing_run_returns_404(file_db):
    client = _client(_user(AccountType.ADMIN))
    resp = client.post("/api/setup/runs/nonexistent/actions/pause")
    assert resp.status_code == 404


# --- GET /runs/active --------------------------------------------------


def test_active_run_404_when_none_active(file_db):
    client = _client(_user(AccountType.ADMIN))
    resp = client.get("/api/setup/runs/active")
    assert resp.status_code == 404


def test_active_run_is_read_only_unlike_post(file_db):
    """Unlike POST /runs (idempotent, but creates on first call), GET
    /runs/active never creates anything."""
    client = _client(_user(AccountType.ADMIN))
    resp = client.get("/api/setup/runs/active")
    assert resp.status_code == 404
    # Still nothing active - proves the GET had no side effect.
    resp_again = client.get("/api/setup/runs/active")
    assert resp_again.status_code == 404


def test_active_run_returns_the_active_run(file_db):
    client = _client(_user(AccountType.ADMIN))
    created = client.post("/api/setup/runs", json={"recipe_id": "r"}).json()

    resp = client.get("/api/setup/runs/active")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_active_run_non_admin_gets_404(file_db):
    admin_client = _client(_user(AccountType.ADMIN))
    admin_client.post("/api/setup/runs", json={"recipe_id": "r"})

    user_client = _client(_user(AccountType.USER))
    resp = user_client.get("/api/setup/runs/active")
    assert resp.status_code == 404


# --- grant_consent -------------------------------------------------------


def test_grant_consent_advances_a_parked_run(file_db):
    recipe = _consent_recipe()
    registry = SetupExecutorRegistry(_FakeCatalog([recipe]), {"artifacts.plan": _AwaitsConsentExecutor()})
    client = _client(_user(AccountType.ADMIN), recipe_catalog=_FakeCatalog([recipe]), executor_registry=registry)

    created_id = client.post("/api/setup/runs", json={"recipe_id": recipe.id}).json()["id"]
    # `POST /runs` now only drives in the background - poll for the
    # run to park on its consent gate instead of asserting on the response.
    created = _poll_until(client, created_id, lambda b: b["status"] == "awaiting_consent")
    assert created["current_step"] == "artifacts.plan"
    plan_attempt = next(a for a in created["attempts"] if a["step_key"] == "artifacts.plan")
    assert plan_attempt["safe_output"]["consent_request"]["total_bytes"] == 1

    granted = client.post(
        f"/api/setup/runs/{created['id']}/actions/grant_consent", json={"step_key": "artifacts.plan"}
    )
    assert granted.status_code == 200
    # artifacts.fetch has no registered executor in this fake registry, so
    # driving stops there with a clear STEP_NOT_IMPLEMENTED failure - proves
    # grant_consent both recorded the approval AND advanced past the step
    # (the run reached artifacts.fetch at all, rather than staying parked).
    # Also driven in the background now, so poll rather than assert on the
    # grant_consent response itself.
    body = _poll_until(client, created["id"], lambda b: b["status"] == "failed")
    assert body["current_step"] == "artifacts.fetch"
    assert body["error_code"] == "STEP_NOT_IMPLEMENTED"


def test_grant_consent_without_step_key_is_400(file_db):
    recipe = _consent_recipe()
    registry = SetupExecutorRegistry(_FakeCatalog([recipe]), {"artifacts.plan": _AwaitsConsentExecutor()})
    client = _client(_user(AccountType.ADMIN), recipe_catalog=_FakeCatalog([recipe]), executor_registry=registry)
    run_id = client.post("/api/setup/runs", json={"recipe_id": recipe.id}).json()["id"]

    resp = client.post(f"/api/setup/runs/{run_id}/actions/grant_consent", json={})
    assert resp.status_code == 400


def test_grant_consent_on_non_consent_run_is_409(file_db):
    client = _client(_user(AccountType.ADMIN))
    run_id = client.post("/api/setup/runs", json={"recipe_id": "r"}).json()["id"]

    resp = client.post(f"/api/setup/runs/{run_id}/actions/grant_consent", json={"step_key": "artifacts.plan"})
    assert resp.status_code == 409


# --- GET /recipes ----------------------------------------------------------


def test_list_recipes_admin_only(file_db):
    catalog = _FakeCatalog([_consent_recipe()])
    user_client = _client(_user(AccountType.USER), recipe_catalog=catalog)
    resp = user_client.get("/api/setup/recipes")
    assert resp.status_code == 404


def test_list_recipes_returns_summaries(file_db):
    catalog = _FakeCatalog([_consent_recipe()])
    client = _client(_user(AccountType.ADMIN), recipe_catalog=catalog)

    resp = client.get("/api/setup/recipes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recipes"][0]["id"] == "consent-recipe"
    assert body["recipes"][0]["engine"] == "native"
