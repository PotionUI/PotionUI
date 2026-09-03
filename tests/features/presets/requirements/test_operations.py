"""`src.features.presets.operations.get_preset_requirements` and the
`requirements_summary` peek wired into `list_presets`/`get_preset`.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
from src.features.presets.requirements.contracts import RequirementResult
from src.features.presets.requirements.evaluator import RequirementsCache
from src.features.presets.templates import PresetTemplate
from src.platform.plugins.requirement_checkers import requirement_checker_registry


def setup_module(module):
    # This module's assertions rely on the shared `requirement_checker_registry`
    # singleton carrying the core checkers - lazily seed it if some earlier
    # test module hasn't already (mirrors `PresetLinter._requirement_checker_registry`'s
    # guard), so this file's results don't depend on collection order.
    if not requirement_checker_registry.all():
        register_builtin_requirement_checkers(requirement_checker_registry)


def _preset(preset_id="preset-1", requirements=None):
    return PresetTemplate(
        id=preset_id, name="Preset", version="1.0.0", path="/tmp/preset", modes={},
        engine="native", requirements=requirements or [],
    )


def _collaborators(**overrides):
    defaults = dict(
        preset_loader=MagicMock(),
        preset_processor=MagicMock(),
        template_processor=MagicMock(),
        file_repo=MagicMock(),
        db_repo=MagicMock(),
        user_repo=MagicMock(),
        group_repo=MagicMock(),
        pipeline_builder=MagicMock(),
        pipe_catalog=MagicMock(),
        plugins=MagicMock(),
        settings=MagicMock(),
    )
    defaults.update(overrides)
    with patch('src.features.presets.collaborators.PresetFormSerializer'):
        return PresetCollaborators(**defaults)


class TestGetPresetRequirements:
    @pytest.mark.asyncio
    async def test_raises_when_preset_not_found(self):
        collaborators = _collaborators(file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=None)))

        with pytest.raises(PresetNotFoundException):
            await operations.get_preset_requirements(collaborators, "missing-preset")

    @pytest.mark.asyncio
    async def test_returns_results_and_summary(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=None,
            model_index=None,
            gpu_monitor=None,
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"]["ok"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"
        assert "checked_at" in data

    @pytest.mark.asyncio
    async def test_optional_miss_counts_as_optional_missing_not_missing(self):
        preset = _preset(requirements=[
            {"type": "platform", "os": ["linux"]},  # ok on this host
            {"type": "python_package", "name": "definitely-not-a-real-package-xyz", "optional": True},
            {"type": "python_package", "name": "also-not-a-real-package-xyz"},  # non-optional miss
        ])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"] == {"ok": 1, "missing": 1, "unknown": 0, "optional_missing": 1}
        optional_item = data["results"][1]
        assert optional_item["optional"] is True
        assert optional_item["status"] == "missing"

    @pytest.mark.asyncio
    async def test_result_carries_type_name_and_optional(self):
        preset = _preset(requirements=[
            {"type": "platform", "os": ["linux"], "optional": True},
        ])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        item = data["results"][0]
        assert item["type"] == "platform"
        assert item["name"] == "linux"  # PlatformRequirementChecker.describe()
        assert item["optional"] is True

    @pytest.mark.asyncio
    async def test_optional_defaults_to_false(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["results"][0]["optional"] is False

    @pytest.mark.asyncio
    async def test_unregistered_type_falls_back_to_first_string_field(self):
        preset = _preset(requirements=[{"type": "custom_check", "node": "MyCustomNode"}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        item = data["results"][0]
        assert item["type"] == "custom_check"
        assert item["name"] == "MyCustomNode"
        assert item["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_unregistered_type_with_no_string_field_falls_back_to_type(self):
        preset = _preset(requirements=[{"type": "custom_check", "count": 3}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["results"][0]["name"] == "custom_check"

    @pytest.mark.asyncio
    async def test_no_cache_still_evaluates(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=None,
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"]["ok"] == 1

    @pytest.mark.asyncio
    async def test_refresh_flag_forces_reevaluation(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        cache = RequirementsCache()
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=cache,
        )

        first = await operations.get_preset_requirements(collaborators, preset.id)
        second = await operations.get_preset_requirements(collaborators, preset.id, refresh=True)

        assert first["checked_at"] <= second["checked_at"]


class TestRequirementsSummaryPeek:
    def test_get_preset_summary_is_none_before_evaluation(self):
        preset = _preset()
        collaborators = _collaborators(
            file_repo=MagicMock(
                find_preset_by_id=MagicMock(return_value=preset),
                preset_to_info=MagicMock(return_value=MagicMock(dict=lambda: {
                    "id": preset.id, "name": preset.name, "version": preset.version,
                })),
            ),
            requirements_cache=RequirementsCache(),
        )

        data = operations.get_preset(collaborators, preset.id)

        assert data["requirements_summary"] is None

    @pytest.mark.asyncio
    async def test_get_preset_summary_populated_after_evaluation(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        cache = RequirementsCache()
        collaborators = _collaborators(
            file_repo=MagicMock(
                find_preset_by_id=MagicMock(return_value=preset),
                preset_to_info=MagicMock(return_value=MagicMock(dict=lambda: {
                    "id": preset.id, "name": preset.name, "version": preset.version,
                })),
            ),
            requirements_cache=cache,
        )

        await operations.get_preset_requirements(collaborators, preset.id)
        data = operations.get_preset(collaborators, preset.id)

        assert data["requirements_summary"] == {"ok": 1, "missing": 0, "unknown": 0, "optional_missing": 0}
