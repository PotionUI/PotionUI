"""The four-facet readiness aggregate and its role filtering.

Every collaborator the manager composes is faked, so these are pure unit tests
of the aggregation logic: which facet flips to which status under which
condition, how the overall verdict combines them, and what an admin sees versus
a regular user.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.features.setup.readiness import (
    DEGRADED,
    NOT_READY,
    READY,
    ReadinessManager,
)
from src.platform.security.user import AccountType, User

AVAILABILITY = "src.features.models.availability.models_for_engine"


# --- fakes ------------------------------------------------------------------

def _user(admin=False):
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=AccountType.ADMIN if admin else AccountType.USER,
        id="u1",
    )


class _OkClaimRepository:
    def check_connection(self):
        return None


class _FailClaimRepository:
    def check_connection(self):
        raise RuntimeError("database is locked")


def _backend(name="native-1", engine="native", status="healthy", reason=None, error=None):
    info = {"status": status}
    if reason is not None:
        info["reason"] = reason
    if error is not None:
        info["error"] = error
    backend = MagicMock()
    backend.name = name
    backend.engine = engine
    backend.health_check = AsyncMock(return_value=info)
    return backend


def _manager(
    backends=None,
    presets=None,
    completed=1,
    pending=False,
    instance_claim_repository=None,
):
    backend_registry = MagicMock()
    backend_registry.get_all_backends.return_value = (
        {"native-1": _backend()} if backends is None else backends
    )
    preset_manager = MagicMock()
    preset_manager.list_presets.return_value = (
        [{"id": "p1", "engine": "native"}] if presets is None else presets
    )
    model_repository = MagicMock()
    model_repository.get_available_model_ids_for_user.return_value = ["m1"]
    generation_repository = MagicMock()
    generation_repository.count_by_status.return_value = completed
    migration_manager = MagicMock()
    migration_manager.has_pending_migrations.return_value = pending
    return ReadinessManager(
        backend_registry=backend_registry,
        preset_manager=preset_manager,
        model_repository=model_repository,
        generation_repository=generation_repository,
        migration_manager=migration_manager,
        instance_claim_repository=instance_claim_repository or _OkClaimRepository(),
    )


def _run(manager, user, recipe_id=None, models_result=None):
    """Evaluate with `models_for_engine` stubbed to control content resolution."""
    result = [{"id": "m1"}] if models_result is None else models_result
    with patch(AVAILABILITY, return_value=result):
        return asyncio.run(manager.evaluate(user, recipe_id=recipe_id))


def _by_area(report):
    return {check.area: check for check in report.checks}


# --- happy path -------------------------------------------------------------

def test_all_green_is_ready():
    report = _run(_manager(), _user(admin=True))
    assert report.overall == READY
    areas = _by_area(report)
    assert areas["service"].code == "SERVICE_OK"
    assert areas["execution"].code == "EXECUTION_READY"
    assert areas["content"].code == "CONTENT_READY"
    assert areas["generation_proven"].code == "GENERATION_PROVEN"
    assert all(c.status == READY for c in report.checks)


def test_report_rows_use_the_doctor_registry_shape():
    report = _run(_manager(), _user(admin=True))
    for check in report.checks:
        assert set(check.model_dump().keys()) == {"area", "status", "code", "message", "action"}


# --- execution facet --------------------------------------------------------

def test_no_backend_is_not_ready():
    report = _run(_manager(backends={}), _user(admin=True))
    execution = _by_area(report)["execution"]
    assert execution.status == NOT_READY
    assert execution.code == "NO_EXECUTION_BACKEND"
    assert report.overall == NOT_READY


def test_degraded_backend_is_degraded():
    backends = {"b": _backend(status="degraded", reason="no CUDA GPU is visible")}
    report = _run(_manager(backends=backends), _user(admin=True))
    execution = _by_area(report)["execution"]
    assert execution.status == DEGRADED
    assert execution.code == "EXECUTION_DEGRADED"
    assert "no CUDA GPU is visible" in execution.message
    # no facet is not_ready -> overall settles at degraded, not not_ready
    assert report.overall == DEGRADED


def test_erroring_backend_is_not_ready():
    backends = {"b": _backend(status="error", error="driver exploded")}
    report = _run(_manager(backends=backends), _user(admin=True))
    execution = _by_area(report)["execution"]
    assert execution.status == NOT_READY
    assert execution.code == "EXECUTION_UNHEALTHY"


def test_one_healthy_among_degraded_wins():
    backends = {
        "bad": _backend(name="bad", status="degraded", reason="misconfigured"),
        "good": _backend(name="good", status="healthy"),
    }
    report = _run(_manager(backends=backends), _user(admin=True))
    execution = _by_area(report)["execution"]
    assert execution.status == READY
    assert "good" in execution.message


# --- content facet ----------------------------------------------------------

def test_no_presets_assigned_is_not_ready():
    report = _run(_manager(presets=[]), _user(admin=True))
    content = _by_area(report)["content"]
    assert content.status == NOT_READY
    assert content.code == "NO_PRESETS_ASSIGNED"
    assert report.overall == NOT_READY


def test_presets_without_resolvable_models_is_degraded():
    report = _run(_manager(), _user(admin=True), models_result=[])
    content = _by_area(report)["content"]
    assert content.status == DEGRADED
    assert content.code == "PRESETS_WITHOUT_MODELS"


def test_recipe_id_seam_returns_not_implemented_without_erroring():
    report = _run(_manager(), _user(admin=True), recipe_id="some-recipe")
    content = _by_area(report)["content"]
    assert content.code == "RECIPE_NOT_IMPLEMENTED"
    assert content.status == DEGRADED
    assert "some-recipe" in content.message  # admin phrasing names the recipe


def test_user_visibility_restricts_model_resolution():
    """A regular user's model resolution is scoped to their assigned ids."""
    manager = _manager()
    with patch(AVAILABILITY, return_value=[{"id": "m1"}]) as models_for_engine:
        asyncio.run(manager.evaluate(_user(admin=False)))
    # STRICT scoping: the user's own allowed ids are passed through, not None.
    _, kwargs = models_for_engine.call_args
    assert kwargs["user_allowed_model_ids"] == ["m1"]


def test_admin_model_resolution_is_unrestricted():
    manager = _manager()
    with patch(AVAILABILITY, return_value=[{"id": "m1"}]) as models_for_engine:
        asyncio.run(manager.evaluate(_user(admin=True)))
    _, kwargs = models_for_engine.call_args
    assert kwargs["user_allowed_model_ids"] is None


# --- generation-proven facet ------------------------------------------------

def test_no_completed_generation_is_not_ready():
    report = _run(_manager(completed=0), _user(admin=True))
    proven = _by_area(report)["generation_proven"]
    assert proven.status == NOT_READY
    assert proven.code == "NO_GENERATION_YET"
    assert report.overall == NOT_READY


# --- service facet ----------------------------------------------------------

def test_unreachable_db_is_not_ready():
    report = _run(_manager(instance_claim_repository=_FailClaimRepository()), _user(admin=True))
    service = _by_area(report)["service"]
    assert service.status == NOT_READY
    assert service.code == "DB_UNREACHABLE"
    assert report.overall == NOT_READY


def test_pending_migrations_is_degraded():
    report = _run(_manager(pending=True), _user(admin=True))
    service = _by_area(report)["service"]
    assert service.status == DEGRADED
    assert service.code == "MIGRATIONS_PENDING"


# --- role filtering ---------------------------------------------------------

def test_admin_sees_actions_and_internals():
    report = _run(_manager(backends={}), _user(admin=True))
    execution = _by_area(report)["execution"]
    assert execution.action is not None
    assert "Administration" in execution.action
    assert "backend" in execution.message.lower()


def test_user_gets_status_but_no_admin_action_or_internals():
    backends = {"b": _backend(name="secret-backend", status="degraded", reason="GPU index 3 does not exist")}
    manager = _manager(backends=backends)
    report = _run(manager, _user(admin=False))
    execution = _by_area(report)["execution"]
    # status is still reported to the user...
    assert execution.status == DEGRADED
    # ...but no admin-shaped action and no operator internals leak.
    assert execution.action is None
    assert "secret-backend" not in execution.message
    assert "GPU index 3" not in execution.message
    assert "administrator" in execution.message.lower()


def test_user_and_admin_agree_on_status_and_code():
    """Role filtering changes phrasing, never the verdict."""
    backends = {"b": _backend(status="degraded", reason="whatever")}
    admin_report = _run(_manager(backends=backends), _user(admin=True))
    user_report = _run(_manager(backends=backends), _user(admin=False))
    assert admin_report.overall == user_report.overall
    admin_areas = _by_area(admin_report)
    user_areas = _by_area(user_report)
    for area, admin_check in admin_areas.items():
        assert user_areas[area].status == admin_check.status
        assert user_areas[area].code == admin_check.code
