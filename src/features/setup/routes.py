"""First-run setup + readiness endpoints.

`GET /api/setup/status` is unauthenticated by design - a fresh client has no
account yet and needs to know whether to show a "create the owner" screen. It
returns only the three-boolean `SetupStatus` and nothing that describes the
host.

`GET /api/readiness` is authenticated and answers the question that comes after
the process is up: can this instance *actually* generate? It reports four facets
(service, execution, content, generation_proven) in the `./potionui doctor`
row shape, filtered to the caller's role - see `readiness.py`.
"""

from typing import TYPE_CHECKING, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.features.setup import operations
from src.features.setup.dto import SetupStatus
from src.features.setup.readiness import ReadinessAggregator, ReadinessReport
from src.features.setup.recipe_dto import RecipeSummary
from src.features.setup.run_dto import (
    CreateSetupRunRequest,
    SetupRunActionRequest,
    SetupRunView,
)
from src.features.setup.runner import (
    IllegalSetupTransition,
    SetupRunError,
    SetupRunner,
    SetupRunNotFound,
    VALID_ACTIONS,
)
from src.features.setup.run_repository import SetupRunRepository
from src.platform.http.origin import is_loopback_host
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _require_admin_or_404(user: User) -> None:
    """Gate setup-run routes. Setup mutates the whole instance, so it is
    admin-only; a non-admin gets 404 (not 403) so the surface is invisible
    rather than merely forbidden - the house 404-not-403 idiom (see
    GenerationPolicy)."""
    if user is None or user.account_type != AccountType.ADMIN:
        raise HTTPException(status_code=404, detail="Not found")


def _run_view(runner: SetupRunner, recipe_catalog, run) -> SetupRunView:
    """Build the wire view for `run`, enriched with its recipe's ordered step
    manifest when the recipe can still be resolved (see
    `SetupRunView.from_record`'s `recipe_steps` param)."""
    recipe_steps = None
    if recipe_catalog is not None:
        recipe = recipe_catalog.get_recipe(run.recipe_id, run.recipe_version)
        if recipe is not None:
            recipe_steps = [(s.key, s.kind, s.title) for s in recipe.steps]
    return SetupRunView.from_record(run, runner.list_attempts(run.id), recipe_steps=recipe_steps)


def build_router(container: "AppContainer") -> APIRouter:
    instance_claim_repository = container.instance_claim_repository
    claim_token_store = container.claim_token_store
    settings = container.settings
    # Stateless (wraps the process-wide `db` singleton, see run_repository.py),
    # so a fresh instance here for reads costs nothing and follows the
    # route -> repository house rule without threading it through the manager.
    run_repository = SetupRunRepository()

    def _runner() -> SetupRunner:
        # Resolved per request (not at build time) so the public /status route
        # and its minimal test container stay independent of the run manager.
        return container.setup_runner

    def _recipe_catalog():
        # Optional: a minimal test container built without a recipe catalog
        # still gets the old flat/unordered `attempts` shape rather than an
        # AttributeError.
        return getattr(container, "recipe_catalog", None)

    # No prefix on the parent: setup status lives under /api/setup, readiness at
    # /api/readiness. Both ship through the one `build_setup_router` registration
    # in src/bootstrap/routers.py.
    router = APIRouter(tags=["System & Health"])

    setup_router = APIRouter(prefix="/api/setup")

    @setup_router.get("/status", response_model=SetupStatus, summary="First-run setup status")
    async def get_setup_status(request: Request) -> SetupStatus:
        """Report whether the instance needs an owner and how it may be claimed."""
        client_host = request.client.host if request.client else None
        return operations.status(
            instance_claim_repository,
            claim_token_store,
            settings,
            is_loopback=is_loopback_host(client_host),
        )

    # Built lazily on first readiness request so the (public, minimal) setup
    # status route stays independent of the rest of the container.
    _readiness: dict = {}

    def _readiness_aggregator() -> ReadinessAggregator:
        if "mgr" not in _readiness:
            _readiness["mgr"] = ReadinessAggregator(
                backend_registry=container.backend_registry,
                preset_manager=container.preset_manager,
                model_repository=container.model_repository,
                generation_repository=container.generation_repository,
                instance_claim_repository=container.instance_claim_repository,
            )
        return _readiness["mgr"]

    @router.get("/api/readiness", response_model=ReadinessReport, summary="Instance readiness aggregate")
    async def get_readiness(
        recipe_id: Optional[str] = None,
        current_user: User = Depends(get_current_active_user),
    ) -> ReadinessReport:
        """Report whether the instance can actually generate, filtered to the
        caller's role. `recipe_id` is the Phase-3 recipe seam (not yet
        implemented; returns a clear not-implemented-yet content row)."""
        return await _readiness_aggregator().evaluate(current_user, recipe_id=recipe_id)

    # --- durable setup runs (admin-only) ----------------------------------
    # The Phase-3 wizard executes against these. All are gated to admins with
    # the 404-not-403 idiom; the actions endpoint only transitions state today -
    # real step execution is the Phase-3 executor seam on the manager.

    @setup_router.post(
        "/runs",
        response_model=SetupRunView,
        status_code=201,
        summary="Start (or return the active) setup run",
    )
    async def create_setup_run(
        body: CreateSetupRunRequest,
        current_user: User = Depends(get_current_active_user),
    ) -> SetupRunView:
        _require_admin_or_404(current_user)
        runner = _runner()
        run = runner.create_run(
            body.recipe_id,
            recipe_version=body.recipe_version,
            safe_input=body.safe_input,
            created_by=current_user.id,
        )
        # Drive it forward in the background (see `SetupRunner.
        # drive_async`): a brand-new run is PENDING and nothing else will
        # ever call `execute_current_step` on its behalf. This kicks off
        # every step the recipe already has an executor for, stopping the
        # moment one needs the owner (awaiting_consent) or fails - but never
        # blocks this request waiting for it: a big `models.index` scan or
        # artifact download can run well past any client timeout.
        # The response below reflects whatever the run's status is *right
        # now* (typically still pending) - the frontend's existing ~2.5s
        # poll of `GET /runs/{id}` is what shows progress as it happens.
        runner.drive_async(run.id)
        run = runner.get_run_or_raise(run.id)
        return _run_view(runner, _recipe_catalog(), run)

    @setup_router.get(
        "/runs/active",
        response_model=SetupRunView,
        summary="The currently active setup run, if any",
    )
    async def get_active_setup_run(
        current_user: User = Depends(get_current_active_user),
    ) -> SetupRunView:
        """Read-only discovery for a display-only page: unlike `POST /runs`
        (idempotent, but CREATES a pending run as a side effect the first
        time), this never creates anything - 404 when no run is active, per
        the house 404-not-403 idiom. Registered ahead of `GET /runs/{run_id}`
        so "active" is never swallowed as a literal run id."""
        _require_admin_or_404(current_user)
        runner = _runner()
        run = run_repository.get_active_run()
        if run is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _run_view(runner, _recipe_catalog(), run)

    @setup_router.get(
        "/runs/{run_id}",
        response_model=SetupRunView,
        summary="Durable setup-run detail",
    )
    async def get_setup_run(
        run_id: str,
        current_user: User = Depends(get_current_active_user),
    ) -> SetupRunView:
        _require_admin_or_404(current_user)
        runner = _runner()
        run = runner.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _run_view(runner, _recipe_catalog(), run)

    @setup_router.post(
        "/runs/{run_id}/actions/{action}",
        response_model=SetupRunView,
        summary="Apply a setup-run action (pause|resume|cancel|retry_step|grant_consent)",
    )
    async def act_on_setup_run(
        run_id: str,
        action: str,
        body: Optional[SetupRunActionRequest] = None,
        current_user: User = Depends(get_current_active_user),
    ) -> SetupRunView:
        _require_admin_or_404(current_user)
        runner = _runner()
        if action not in VALID_ACTIONS:
            raise HTTPException(status_code=400, detail="Unknown action")
        try:
            if action == "grant_consent":
                step_key = body.step_key if body else None
                if not step_key:
                    raise HTTPException(
                        status_code=400, detail="'step_key' is required to grant consent"
                    )
                run = runner.grant_consent(run_id, step_key, granted_by=current_user.id)
                # Keep going in the background: the step just approved is
                # done, so the run may already have more not-yet-gated steps
                # to run through (e.g. `artifacts.fetch` right after
                # `artifacts.plan`'s consent) - possibly a long one (a real
                # download), so this must not block the response either (see
                # `create_setup_run`'s comment).
                runner.drive_async(run_id)
                run = runner.get_run_or_raise(run_id)
            else:
                run = runner.apply_action(run_id, action)
                if action in ("resume", "retry_step"):
                    runner.drive_async(run_id)
                    run = runner.get_run_or_raise(run_id)
        except SetupRunNotFound:
            raise HTTPException(status_code=404, detail="Not found")
        except IllegalSetupTransition as e:
            # The run exists but is not in a state that permits this action.
            raise HTTPException(status_code=409, detail=str(e))
        except SetupRunError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _run_view(runner, _recipe_catalog(), run)

    @setup_router.get(
        "/recipes",
        summary="Available Phase-3 setup recipes",
    )
    async def list_setup_recipes(
        current_user: User = Depends(get_current_active_user),
    ) -> Dict[str, List[RecipeSummary]]:
        _require_admin_or_404(current_user)
        catalog = _recipe_catalog()
        recipes = catalog.list_recipes() if catalog is not None else []
        preset_loader = getattr(container, "preset_template_loader", None)
        runner = _runner()
        summaries: List[RecipeSummary] = []
        for recipe in recipes:
            preset_name = None
            if preset_loader is not None and recipe.presets:
                template = preset_loader.load_preset_by_id(recipe.presets[0].preset_id)
                preset_name = template.name if template is not None else None
            completed_run = runner.get_latest_completed_run(recipe.id)
            summaries.append(
                RecipeSummary.from_recipe(
                    recipe,
                    preset_name=preset_name,
                    last_completed_at=completed_run.completed_at if completed_run else None,
                )
            )
        return {"recipes": summaries}

    router.include_router(setup_router)
    return router
