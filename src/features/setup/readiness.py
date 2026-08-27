"""The readiness aggregate: can this instance actually generate?

`GET /api/setup/status` answers the *process* question a fresh client asks
before it can route (does the instance need an owner?). Readiness answers the
*capability* question that comes after: the API is up, but can a real
generation run end to end? A healthy process with no enabled backend, no
assigned presets, or no models is "up" and still useless - readiness is the
contract that separates those.

Four independent facets, each reported as one row whose shape mirrors the
`./potionui doctor` registry ({area/code/status/message/action}):

- ``service``          - the API answered, its database is reachable, and its
                         migrations are current.
- ``execution``        - at least one enabled backend reports ``healthy`` (it
                         can actually load a model), not merely enabled.
- ``content``          - the caller has installed/assigned presets AND at least
                         one of them resolves to a model that is indexed and
                         visible to them. The optional ``recipe_id`` param is the
                         seam per-recipe readiness plugs into; until recipes exist
                         it reports a clear not-implemented-yet row rather than
                         erroring.
- ``generation_proven`` - some generation has actually completed on this
                         instance. Per the onboarding audit, setup is complete
                         only after a real output, not merely a green config.

Every row carries both an admin-facing and a user-facing phrasing; the report is
rendered per caller role so a regular user gets the status and an "ask your
administrator" nudge, never admin-only internals (backend names, device
reasons, migration state) or admin-shaped repair actions.

The manager owns no probing logic of its own: it composes the collaborators
that already know each answer (backend registry health, the presets
`PresetCollaborators` bundle, model repository/availability, generation
repository) plus a trivial DB/migration sanity check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel

from src.platform.security.user import AccountType

if TYPE_CHECKING:
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.generation.repository import GenerationRepository
    from src.features.models.repository import ModelRepository
    from src.features.presets.collaborators import PresetCollaborators
    from src.features.setup.repository import InstanceClaimRepository
    from src.platform.security.user import User


# --- Wire contract ----------------------------------------------------------

class ReadinessCheck(BaseModel):
    """One facet of readiness, in the doctor-registry row shape.

    ``message`` and ``action`` are already role-resolved by the time they reach
    the wire: an admin sees the operator phrasing and a concrete repair action; a
    regular user sees a plain-language status and an "ask your administrator"
    nudge (``action`` is always null for users).
    """

    area: str
    status: str  # "ready" | "not_ready" | "degraded"
    code: str
    message: str
    action: Optional[str] = None


class ReadinessReport(BaseModel):
    """The aggregate: an overall verdict plus the four facet rows.

    ``overall`` is ``ready`` only when every facet is ready; ``not_ready`` when
    any facet is not_ready (a hard blocker); otherwise ``degraded`` (something
    works but can't be fully confirmed).
    """

    overall: str
    checks: List[ReadinessCheck]


# --- Internal row (carries both role phrasings) -----------------------------

READY = "ready"
NOT_READY = "not_ready"
DEGRADED = "degraded"


@dataclass
class _Row:
    area: str
    status: str
    code: str
    admin_message: str
    user_message: str
    admin_action: Optional[str] = None

    def render(self, is_admin: bool) -> ReadinessCheck:
        if is_admin:
            return ReadinessCheck(
                area=self.area,
                status=self.status,
                code=self.code,
                message=self.admin_message,
                action=self.admin_action,
            )
        return ReadinessCheck(
            area=self.area,
            status=self.status,
            code=self.code,
            message=self.user_message,
            action=None,
        )


class ReadinessAggregator:
    """Computes the four-facet readiness aggregate from existing collaborators."""

    def __init__(
        self,
        backend_registry: "BackendRegistry",
        preset_manager: "PresetCollaborators",
        model_repository: "ModelRepository",
        generation_repository: "GenerationRepository",
        migration_runner=None,
        instance_claim_repository: "Optional[InstanceClaimRepository]" = None,
    ):
        self.backend_registry = backend_registry
        self.preset_manager = preset_manager
        self.model_repository = model_repository
        self.generation_repository = generation_repository
        # Migration sanity is a process-global concern; injectable so tests need
        # no real database.
        if migration_runner is None:
            from src.platform.database.migration_runner import MigrationRunner
            migration_runner = MigrationRunner()
        if instance_claim_repository is None:
            from src.features.setup.repository import InstanceClaimRepository
            instance_claim_repository = InstanceClaimRepository()
        self.migration_runner = migration_runner
        self.instance_claim_repository = instance_claim_repository

    async def evaluate(self, user: "User", recipe_id: Optional[str] = None) -> ReadinessReport:
        """Assemble the report for `user`, filtered to their role."""
        is_admin = user.account_type == AccountType.ADMIN
        rows = [
            self._service(),
            await self._execution(),
            self._content(user, is_admin, recipe_id),
            self._generation_proven(),
        ]
        return ReadinessReport(
            overall=self._overall(rows),
            checks=[row.render(is_admin) for row in rows],
        )

    # --- overall verdict ----------------------------------------------------

    @staticmethod
    def _overall(rows: List[_Row]) -> str:
        statuses = {row.status for row in rows}
        if statuses == {READY}:
            return READY
        if NOT_READY in statuses:
            return NOT_READY
        return DEGRADED

    # --- service ------------------------------------------------------------

    def _service(self) -> _Row:
        area = "service"
        try:
            self.instance_claim_repository.check_connection()
        except Exception as exc:  # pragma: no cover - defensive; DB is up if we answered
            return _Row(
                area=area,
                status=NOT_READY,
                code="DB_UNREACHABLE",
                admin_message=f"The service is running but its database is not reachable ({exc}).",
                user_message="The service is having trouble right now. Try again shortly.",
                admin_action="Check the database file/permissions under ./storage and the server logs.",
            )
        if self.migration_runner.has_pending_migrations():
            return _Row(
                area=area,
                status=DEGRADED,
                code="MIGRATIONS_PENDING",
                admin_message="The database is reachable but has migrations that have not been applied.",
                user_message="The service is finishing an update. Try again shortly.",
                admin_action="Restart the backend (migrations run on startup) or run the migration step.",
            )
        return _Row(
            area=area,
            status=READY,
            code="SERVICE_OK",
            admin_message="The service is running and its database is reachable and current.",
            user_message="The service is running.",
        )

    # --- execution ----------------------------------------------------------

    async def _execution(self) -> _Row:
        area = "execution"
        backends = self.backend_registry.get_all_backends()
        if not backends:
            return _Row(
                area=area,
                status=NOT_READY,
                code="NO_EXECUTION_BACKEND",
                admin_message="No generation backend is enabled yet, so nothing can run a generation.",
                user_message="Generation isn't available yet. Ask your administrator to set it up.",
                admin_action="Open Administration -> Backends and enable and test a backend.",
            )

        healths = []
        for backend in backends.values():
            try:
                info = await backend.health_check()
            except Exception as exc:
                info = {"status": "error", "error": str(exc)}
            healths.append((backend, info))

        healthy = [(b, i) for b, i in healths if i.get("status") == "healthy"]
        if healthy:
            backend, _ = healthy[0]
            return _Row(
                area=area,
                status=READY,
                code="EXECUTION_READY",
                admin_message=f"Backend '{backend.name}' (engine {backend.engine}) is healthy and can run generations.",
                user_message="Generation is available.",
            )

        degraded = [(b, i) for b, i in healths if i.get("status") == "degraded"]
        if degraded:
            backend, info = degraded[0]
            reason = info.get("reason") or "the backend reported a degraded state"
            return _Row(
                area=area,
                status=DEGRADED,
                code="EXECUTION_DEGRADED",
                admin_message=f"Backend '{backend.name}' is enabled but not healthy: {reason}",
                user_message="Generation isn't available yet. Ask your administrator to check the setup.",
                admin_action="Open Administration -> Backends and fix the backend flagged above.",
            )

        backend, info = healths[0]
        reason = info.get("error") or "the backend reported an error"
        return _Row(
            area=area,
            status=NOT_READY,
            code="EXECUTION_UNHEALTHY",
            admin_message=f"No enabled backend is healthy. Backend '{backend.name}' reported: {reason}",
            user_message="Generation isn't available yet. Ask your administrator to check the setup.",
            admin_action="Open Administration -> Backends and repair or replace the failing backend.",
        )

    # --- content (degenerate recipe check) ----------------------------------

    def _content(self, user: "User", is_admin: bool, recipe_id: Optional[str]) -> _Row:
        area = "content"

        # Seam: per-recipe readiness plugs in here. Until recipes exist, return a
        # clear not-implemented-yet row rather than erroring, so callers can
        # already pass ?recipe_id= and get a stable shape back.
        if recipe_id is not None:
            return _Row(
                area=area,
                status=DEGRADED,
                code="RECIPE_NOT_IMPLEMENTED",
                admin_message=(
                    f"Per-recipe readiness for '{recipe_id}' is not available yet; "
                    "recipes arrive in a later phase. Showing no recipe verdict."
                ),
                user_message="Per-recipe readiness isn't available yet.",
            )

        from src.features.presets import operations as preset_operations

        presets = preset_operations.list_presets(self.preset_manager, user)
        if not presets:
            return _Row(
                area=area,
                status=NOT_READY,
                code="NO_PRESETS_ASSIGNED",
                admin_message="No presets are installed and assigned to this user, so there is nothing to generate.",
                user_message="You don't have any presets yet. Ask your administrator to assign one.",
                admin_action="Open Administration -> Presets, install a preset and assign it to this user.",
            )

        if self._any_preset_has_models(presets, user, is_admin):
            return _Row(
                area=area,
                status=READY,
                code="CONTENT_READY",
                admin_message="This user has presets whose models resolve to indexed, visible models.",
                user_message="You have presets ready to generate with.",
            )

        return _Row(
            area=area,
            status=DEGRADED,
            code="PRESETS_WITHOUT_MODELS",
            admin_message=(
                "This user has assigned presets, but none of them resolve to a model that is "
                "indexed on a matching backend and visible to the user."
            ),
            user_message="Your presets don't have any usable models yet. Ask your administrator.",
            admin_action="Index models on the matching backend and assign the models to this user.",
        )

    def _any_preset_has_models(self, presets, user: "User", is_admin: bool) -> bool:
        """True if any assigned preset's engine yields at least one model the
        user can actually use (indexed on a matching backend and visible to
        them). Composes `models_for_engine`, the same availability path the
        model picker and generation routing use."""
        from src.features.models.availability import models_for_engine

        # Visibility restriction: None for an admin (unrestricted), else the
        # user's own assigned model ids (STRICT - an empty list means "sees
        # nothing", mirroring ModelAccessPolicy).
        user_allowed_model_ids = (
            None if is_admin else self.model_repository.get_available_model_ids_for_user(user.id)
        )

        engines = {preset.get("engine") for preset in presets if preset.get("engine")}
        for engine in engines:
            models = models_for_engine(
                engine,
                self.backend_registry,
                model_repository=self.model_repository,
                user_allowed_model_ids=user_allowed_model_ids,
                limit=1,
                include_providers=False,
                include_tags=False,
            )
            if models:
                return True
        return False

    # --- generation proven --------------------------------------------------

    def _generation_proven(self) -> _Row:
        area = "generation_proven"
        completed = self.generation_repository.count_by_status(status="completed")
        if completed > 0:
            return _Row(
                area=area,
                status=READY,
                code="GENERATION_PROVEN",
                admin_message="This instance has completed at least one generation.",
                user_message="This instance has produced generations before.",
            )
        return _Row(
            area=area,
            status=NOT_READY,
            code="NO_GENERATION_YET",
            admin_message="No generation has completed on this instance yet; setup is complete only after a real output.",
            user_message="No generation has finished on this instance yet. Run one to finish setup.",
            admin_action="Run a generation end to end to confirm the instance really works.",
        )
