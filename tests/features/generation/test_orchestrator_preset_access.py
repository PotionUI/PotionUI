from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.generation import orchestrator as orch
from src.features.presets.exceptions import PresetNotFoundException
from src.platform.security.user import AccountType


def _request(preset_id="family/variant"):
    return SimpleNamespace(preset_id=preset_id, form_data={}, mode="txt2img", form_name=None)


def _orchestrator(available, account_type=AccountType.USER, user_known=True):
    instance = object.__new__(orch.GenerationOrchestrator)
    repo = Mock()
    repo.get_available_preset_ids_for_user.return_value = list(available)
    users = Mock()
    users.get_by_id.return_value = SimpleNamespace(id="u1", account_type=account_type) if user_known else None
    instance.database_preset_repository = repo
    instance.user_repository = users
    instance.limit_guard = None
    instance.output_broadcaster = None
    instance.preset_template_loader = Mock()
    instance.preset_template_loader.load_preset_by_id.return_value = None
    return instance


@pytest.mark.asyncio
async def test_start_generation_refuses_an_unassigned_preset_before_loading_it():
    instance = _orchestrator([])

    with pytest.raises(PresetNotFoundException):
        await instance.start_generation(_request(), "u1")

    instance.preset_template_loader.load_preset_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_preview_memory_refuses_an_unassigned_preset_before_loading_it():
    instance = _orchestrator(["family/other"])

    with pytest.raises(PresetNotFoundException):
        await instance.preview_memory(_request(), "u1")

    instance.preset_template_loader.load_preset_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_user_is_refused():
    instance = _orchestrator(["family/variant"], user_known=False)

    with pytest.raises(PresetNotFoundException):
        await instance.start_generation(_request(), "u1")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", ["start_generation", "preview_memory"])
@pytest.mark.parametrize(
    "available,account_type",
    [(["family/variant"], AccountType.USER), ([], AccountType.ADMIN)],
    ids=["assigned", "admin"],
)
async def test_assigned_and_admin_users_get_past_the_gate(entry_point, available, account_type):
    instance = _orchestrator(available, account_type)

    with pytest.raises(ValueError, match="not found"):
        await getattr(instance, entry_point)(_request(), "u1")

    instance.preset_template_loader.load_preset_by_id.assert_called_once_with("family/variant")


@pytest.mark.asyncio
async def test_an_unwired_orchestrator_does_not_gate():
    instance = _orchestrator([])
    instance.user_repository = None

    with pytest.raises(ValueError, match="not found"):
        await instance.start_generation(_request(), "u1")
