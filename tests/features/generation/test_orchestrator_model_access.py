"""Tests for GenerationOrchestrator's model-access wiring:
- `database_preset_repository`'s stored form overrides are threaded into `bind_form`.
- `_enforce_model_access` verifies every `model:<id>` ref the bound form carries,
  except refs living under an admin-pinned field name.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.generation import orchestrator as orch
from src.features.forms.binding import BoundForm
from src.features.models.form_refs import make_model_ref
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException


def _orchestrator(**overrides):
    instance = object.__new__(orch.GenerationOrchestrator)
    instance.database_preset_repository = overrides.get("database_preset_repository")
    instance.model_access_policy = overrides.get("model_access_policy")
    instance.user_repository = overrides.get("user_repository")
    return instance


class TestEnforceModelAccessNoOp:
    def test_no_op_without_policy_and_repository(self):
        instance = _orchestrator()
        bound = BoundForm(values={"checkpoint": make_model_ref("m1")}, form_name="custom")
        # Should not raise, and should not attempt to touch anything.
        instance._enforce_model_access(bound, "user_1")

    def test_no_op_when_form_carries_no_model_refs(self):
        policy = Mock()
        user_repo = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(values={"checkpoint": "plain/path.safetensors"}, form_name="custom")

        instance._enforce_model_access(bound, "user_1")

        user_repo.get_by_id.assert_not_called()
        policy.verify_model_access.assert_not_called()


class TestEnforceModelAccessVerifies:
    def test_verifies_every_referenced_model(self):
        policy = Mock()
        user_repo = Mock()
        user = Mock()
        user_repo.get_by_id.return_value = user
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={
                "checkpoint": make_model_ref("m1"),
                "loras": [{"model": make_model_ref("m2")}],
            },
            form_name="custom",
        )

        instance._enforce_model_access(bound, "user_1")

        user_repo.get_by_id.assert_called_once_with("user_1")
        checked_ids = {call.args[0] for call in policy.verify_model_access.call_args_list}
        assert checked_ids == {"m1", "m2"}
        for call in policy.verify_model_access.call_args_list:
            assert call.args[1] is user

    def test_denial_propagates(self):
        policy = Mock()
        policy.verify_model_access.side_effect = ModelAccessDeniedException("nope")
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(values={"checkpoint": make_model_ref("m1")}, form_name="custom")

        with pytest.raises(ModelAccessDeniedException):
            instance._enforce_model_access(bound, "user_1")

    def test_unknown_model_propagates(self):
        policy = Mock()
        policy.verify_model_access.side_effect = ModelNotFoundException("gone")
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(values={"checkpoint": make_model_ref("m1")}, form_name="custom")

        with pytest.raises(ModelNotFoundException):
            instance._enforce_model_access(bound, "user_1")

    def test_missing_user_raises_access_denied(self):
        policy = Mock()
        user_repo = Mock()
        user_repo.get_by_id.return_value = None
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(values={"checkpoint": make_model_ref("m1")}, form_name="custom")

        with pytest.raises(ModelAccessDeniedException):
            instance._enforce_model_access(bound, "user_1")

        policy.verify_model_access.assert_not_called()


class TestEnforceModelAccessLoraPickerShape:
    """`lora_picker` stores `[{model: "model:<id>", strength}, ...]` - a
    top-level string scan would MISS these refs. `collect_model_ids` (used
    per top-level field value, then unioned) already recurses into nested
    lists/dicts, so these are found regardless of nesting depth."""

    def test_finds_refs_nested_in_a_lora_picker_list(self):
        policy = Mock()
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={
                "loras": [
                    {"model": make_model_ref("lora_1"), "strength": 0.8},
                    {"model": make_model_ref("lora_2"), "strength": 0.5},
                ],
            },
            form_name="custom",
        )

        instance._enforce_model_access(bound, "user_1")

        checked_ids = {call.args[0] for call in policy.verify_model_access.call_args_list}
        assert checked_ids == {"lora_1", "lora_2"}

    def test_pinned_lora_picker_field_is_entirely_skipped(self):
        policy = Mock()
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={
                "loras": [{"model": make_model_ref("admin_pinned_lora"), "strength": 1.0}],
            },
            form_name="custom",
            admin_pinned=["loras"],
        )

        instance._enforce_model_access(bound, "user_1")

        policy.verify_model_access.assert_not_called()

    def test_denial_on_a_lora_nested_two_levels_deep_still_propagates(self):
        policy = Mock()
        policy.verify_model_access.side_effect = ModelAccessDeniedException("nope")
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={"loras": [{"model": make_model_ref("forbidden_lora"), "strength": 0.8}]},
            form_name="custom",
        )

        with pytest.raises(ModelAccessDeniedException):
            instance._enforce_model_access(bound, "user_1")


class TestEnforceModelAccessAdminPinnedBypass:
    def test_admin_pinned_field_is_never_checked(self):
        """An admin-pinned hidden/locked model default intentionally bypasses
        the user's own model-access checks."""
        policy = Mock()
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={"checkpoint": make_model_ref("admin_only_model")},
            form_name="custom",
            admin_pinned=["checkpoint"],
        )

        instance._enforce_model_access(bound, "user_1")

        policy.verify_model_access.assert_not_called()

    def test_non_pinned_refs_still_checked_alongside_pinned_ones(self):
        policy = Mock()
        user_repo = Mock()
        user_repo.get_by_id.return_value = Mock()
        instance = _orchestrator(model_access_policy=policy, user_repository=user_repo)
        bound = BoundForm(
            values={
                "checkpoint": make_model_ref("admin_only_model"),
                "lora": make_model_ref("user_chosen_model"),
            },
            form_name="custom",
            admin_pinned=["checkpoint"],
        )

        instance._enforce_model_access(bound, "user_1")

        checked_ids = {call.args[0] for call in policy.verify_model_access.call_args_list}
        assert checked_ids == {"user_chosen_model"}


@pytest.fixture(autouse=True)
def _bind_form_records_kwargs(monkeypatch):
    """Patch bind_form to a passthrough that records the `field_overrides`
    kwarg it was called with, so start_generation-level tests can assert on
    what the orchestrator threaded through."""
    calls = []

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        calls.append({"field_overrides": field_overrides})
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom")

    monkeypatch.setattr(orch, "bind_form", _passthrough)
    return calls


class TestFieldOverridesThreadedFromRepository:
    def _build_orchestrator(self, database_preset_repository):
        instance = object.__new__(orch.GenerationOrchestrator)
        instance.database_preset_repository = database_preset_repository
        instance.model_access_policy = None
        instance.user_repository = None
        instance.preset_template_loader = Mock()
        preset_template = Mock()
        preset_template.engine = "native"
        instance.preset_template_loader.load_preset_by_id = Mock(return_value=preset_template)
        instance.settings = Mock()
        instance.settings.get_file_storage_directory = Mock(return_value="/storage")
        instance.backend_registry = Mock()
        backend = Mock(backend_id="b1", name="Backend", engine="native")
        backend.start_generation = AsyncMock()
        instance.backend_registry.select_backend_for_generation = Mock(return_value=backend)
        instance.backend_registry.get_backends_for_engine = Mock(return_value=[backend])
        instance.plugin_registry = None
        instance.status_tracker = Mock()
        instance.status_tracker.create = Mock(return_value=Mock(model_dump=Mock(return_value={})))
        instance._queue_dispatcher = Mock()
        instance._queue_dispatcher.enqueue = AsyncMock()
        instance._queue_dispatcher.publish_positions = AsyncMock()
        instance._queue_dispatcher.position = Mock(return_value=None)
        return instance

    def _request(self):
        request = Mock()
        request.preset_id = "test_preset"
        request.form_data = {"steps": 20}
        request.mode = "txt2img"
        request.form_name = None
        request.backend_id = None
        request.tag_ids = None
        request.collection_ids = None
        request.segments = None
        return request

    @pytest.mark.asyncio
    async def test_stored_overrides_for_the_mode_are_passed_to_bind_form(self, _bind_form_records_kwargs):
        db_repo = Mock()
        db_repo.get_preset_form_overrides.return_value = {
            "txt2img": {"steps": {"editable": False}},
            "img2img": {"other": {"visible": False}},
        }
        instance = self._build_orchestrator(db_repo)

        with patch.object(orch, "generation_repo") as mock_gen_repo, \
             patch.object(orch, "resolve_form_model_refs", side_effect=lambda fd, bid: fd):
            mock_gen_repo.create = Mock()
            await instance.start_generation(self._request(), "user_1")

        assert _bind_form_records_kwargs[-1]["field_overrides"] == {"steps": {"editable": False}}

    @pytest.mark.asyncio
    async def test_no_repository_passes_none(self, _bind_form_records_kwargs):
        instance = self._build_orchestrator(None)

        with patch.object(orch, "generation_repo") as mock_gen_repo, \
             patch.object(orch, "resolve_form_model_refs", side_effect=lambda fd, bid: fd):
            mock_gen_repo.create = Mock()
            await instance.start_generation(self._request(), "user_1")

        assert _bind_form_records_kwargs[-1]["field_overrides"] is None
