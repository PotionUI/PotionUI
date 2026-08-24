"""Tests for src/core/automation/nodes/actions.py's execute() implementations."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.features.automation.context import AutomationServices, NodeExecutionContext, RunContext
from src.features.automation.nodes.actions import (
    _execute_assign_model,
    _execute_index_model,
    _execute_index_models,
    _guess_model_type,
)


def _ctx(config: dict, event: dict = None, upstream: dict = None,
         services: AutomationServices = None) -> NodeExecutionContext:
    run = RunContext(automation_id="auto1", run_id="run1", event=event or {}, services=services or AutomationServices())
    run.upstream = upstream or {}
    return NodeExecutionContext(run=run, node_id="n1", node_type="action.test", config=config)


class FakeModel:
    def __init__(self, id, filename):
        self.id = id
        self.filename = filename


class TestGuessModelType(unittest.TestCase):

    def test_configured_type_wins(self):
        self.assertEqual(_guess_model_type("/depot/loras/foo.safetensors", "lora"), "lora")

    def test_guesses_from_parent_directory(self):
        self.assertEqual(_guess_model_type("/depot/checkpoints/foo.safetensors"), "checkpoint")

    def test_guesses_llm_directory(self):
        self.assertEqual(_guess_model_type("/depot/llm/some-model.gguf"), "llm")

    def test_guesses_vfi_directory(self):
        self.assertEqual(_guess_model_type("/depot/vfi/foo.pth"), "vfi")

    def test_unknown_directory_falls_back(self):
        self.assertEqual(_guess_model_type("/depot/mystery/foo.bin"), "unknown")


class TestExecuteIndexModelOutput(unittest.IsolatedAsyncioTestCase):

    async def test_output_includes_model_id_and_name(self):
        indexer = MagicMock()
        indexer.index_single_model.return_value = FakeModel(id="model-123", filename="krea2_lora_v1.safetensors")
        services = AutomationServices(model_indexer=indexer)

        import tempfile, os
        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(
                config={"path": f.name, "model_type": "lora"},
                services=services,
            ))

        self.assertEqual(result.output["model_id"], "model-123")
        self.assertEqual(result.output["name"], "krea2_lora_v1.safetensors")
        self.assertEqual(result.output["filename"], "krea2_lora_v1.safetensors")

    async def test_output_is_none_safe_when_indexing_returns_none(self):
        indexer = MagicMock()
        indexer.index_single_model.return_value = None
        services = AutomationServices(model_indexer=indexer)

        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(config={"path": f.name}, services=services))

        self.assertIsNone(result.output["model_id"])
        self.assertIsNone(result.output["name"])

    async def test_raises_when_no_indexer_configured(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(RuntimeError):
                await _execute_index_model(_ctx(config={"path": f.name}, services=AutomationServices()))


def _fake_backend(backend_id: str, name: str, supports_listing: bool = True) -> MagicMock:
    from src.features.backends.base_backend import BaseBackend

    backend = MagicMock(spec=BaseBackend)
    backend.backend_id = backend_id
    backend.name = name
    backend.supports_model_listing.return_value = supports_listing
    return backend


def _index_models_services(backends: dict, results_by_id: dict = None) -> AutomationServices:
    """Services with a registry holding `backends` (id -> instance or None for
    'configured once, gone now') and an indexer answering with real IndexResults."""
    from unittest.mock import AsyncMock

    from src.features.backends.backend_registry import BackendRegistry
    from src.features.models.backend_indexer import BackendModelIndexer, IndexResult

    registry = MagicMock(spec=BackendRegistry)
    live = {bid: backend for bid, backend in backends.items() if backend is not None}
    registry.get_all_backends.return_value = live
    registry.get_backend.side_effect = lambda backend_id: live.get(backend_id)

    async def index_backend(backend):
        if results_by_id and isinstance(results_by_id.get(backend.backend_id), Exception):
            raise results_by_id[backend.backend_id]
        if results_by_id and backend.backend_id in results_by_id:
            return results_by_id[backend.backend_id]
        return IndexResult(backend_id=backend.backend_id, listed=2, created=1, matched=1)

    indexer = MagicMock(spec=BackendModelIndexer)
    indexer.index_backend = AsyncMock(side_effect=index_backend)
    return AutomationServices(backend_registry=registry, backend_model_indexer=indexer)


def _single_index_services(backends: dict, model, holders: list = None,
                           results_by_id: dict = None) -> AutomationServices:
    """Services for action.index_model: a file indexer answering `model`, plus
    the backend registry/indexer pair with `holders` as the backend ids the
    availability table reports for that model after reconciliation."""
    from src.features.models.availability_repository import ModelAvailabilityRepository

    services = _index_models_services(backends, results_by_id)

    scanner = MagicMock()
    scanner.index_single_model.return_value = model
    services.model_indexer = scanner

    availability_repo = MagicMock(spec=ModelAvailabilityRepository)
    availability_repo.backend_ids_by_model.return_value = (
        {model.id: list(holders or [])} if model is not None else {}
    )
    services.backend_model_indexer.availability = availability_repo
    return services


class TestIndexModelAvailability(unittest.IsolatedAsyncioTestCase):
    """The additive availability check `action.index_model` now runs after the
    file lands in the library. The pre-existing output keys keep their meaning."""

    async def test_checks_only_the_selected_backends(self):
        import tempfile

        native = _fake_backend("native", "Native")
        comfy = _fake_backend("comfy1", "ComfyUI Main")
        model = FakeModel(id="m1", filename="style.safetensors")
        services = _single_index_services(
            {"native": native, "comfy1": comfy}, model, holders=["comfy1"]
        )

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(
                config={"path": f.name, "model_type": "lora", "backends": ["comfy1"]},
                services=services,
            ))

        self.assertEqual(result.output["model_id"], "m1")
        self.assertEqual(result.output["availability"], {"ComfyUI Main": True})
        self.assertEqual(result.output["availability_notes"], [])
        services.backend_model_indexer.index_backend.assert_awaited_once_with(comfy)

    async def test_empty_selection_checks_all_backends(self):
        import tempfile

        native = _fake_backend("native", "Native")
        comfy = _fake_backend("comfy1", "ComfyUI Main")
        model = FakeModel(id="m1", filename="style.safetensors")
        services = _single_index_services(
            {"native": native, "comfy1": comfy}, model, holders=["native"]
        )

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(
                config={"path": f.name, "backends": []}, services=services,
            ))

        self.assertEqual(
            result.output["availability"],
            {"Native": True, "ComfyUI Main": False},
        )

    async def test_without_backend_services_the_node_still_indexes_the_file(self):
        """A partial services bundle (as before this feature) must not break."""
        import tempfile

        indexer = MagicMock()
        indexer.index_single_model.return_value = FakeModel(id="m1", filename="a.safetensors")
        services = AutomationServices(model_indexer=indexer)

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(config={"path": f.name}, services=services))

        self.assertEqual(result.output["model_id"], "m1")
        self.assertEqual(result.output["availability"], {})
        self.assertEqual(len(result.output["availability_notes"]), 1)

    async def test_deleted_backend_is_noted_and_the_rest_are_checked(self):
        import tempfile

        native = _fake_backend("native", "Native")
        model = FakeModel(id="m1", filename="style.safetensors")
        services = _single_index_services({"native": native}, model, holders=["native"])

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(
                config={"path": f.name, "backends": ["gone1", "native"]}, services=services,
            ))

        self.assertEqual(result.output["availability"], {"Native": True})
        self.assertEqual(len(result.output["availability_notes"]), 1)
        self.assertIn("gone1", result.output["availability_notes"][0])
        self.assertIn("no longer exists", result.output["availability_notes"][0])

    async def test_unreachable_backend_does_not_fail_the_indexed_file(self):
        import tempfile

        native = _fake_backend("native", "Native")
        broken = _fake_backend("broken1", "Broken Server")
        model = FakeModel(id="m1", filename="style.safetensors")
        services = _single_index_services(
            {"native": native, "broken1": broken}, model, holders=["native"],
            results_by_id={"broken1": ConnectionError("connection refused")},
        )

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(
                config={"path": f.name, "backends": []}, services=services,
            ))

        self.assertEqual(result.output["model_id"], "m1")
        self.assertEqual(result.output["availability"], {"Native": True})
        self.assertIn("Broken Server", result.output["availability_notes"][0])

    async def test_unindexed_file_skips_the_availability_check(self):
        import tempfile

        native = _fake_backend("native", "Native")
        services = _single_index_services({"native": native}, model=None)

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(config={"path": f.name}, services=services))

        self.assertIsNone(result.output["model_id"])
        self.assertEqual(result.output["availability"], {})
        self.assertEqual(len(result.output["availability_notes"]), 1)
        services.backend_model_indexer.index_backend.assert_not_awaited()

    async def test_availability_read_back_failure_is_a_note_not_a_failure(self):
        import tempfile

        native = _fake_backend("native", "Native")
        model = FakeModel(id="m1", filename="style.safetensors")
        services = _single_index_services({"native": native}, model)
        services.backend_model_indexer.availability.backend_ids_by_model.side_effect = (
            RuntimeError("db locked")
        )

        with tempfile.NamedTemporaryFile() as f:
            result = await _execute_index_model(_ctx(config={"path": f.name}, services=services))

        self.assertEqual(result.output["model_id"], "m1")
        self.assertEqual(result.output["availability"], {})
        self.assertTrue(any("db locked" in note for note in result.output["availability_notes"]))


class TestExecuteIndexModels(unittest.IsolatedAsyncioTestCase):

    async def test_indexes_only_the_selected_backends(self):
        native = _fake_backend("native", "Native")
        comfy = _fake_backend("comfy1", "ComfyUI Main")
        services = _index_models_services({"native": native, "comfy1": comfy})

        result = await _execute_index_models(_ctx(config={"backends": ["comfy1"]}, services=services))

        self.assertEqual(set(result.output["results"]), {"ComfyUI Main"})
        self.assertEqual(result.output["indexed_backends"], 1)
        self.assertEqual(result.output["skipped"], [])
        services.backend_model_indexer.index_backend.assert_awaited_once_with(comfy)

    async def test_empty_selection_indexes_all_backends(self):
        native = _fake_backend("native", "Native")
        comfy = _fake_backend("comfy1", "ComfyUI Main")
        services = _index_models_services({"native": native, "comfy1": comfy})

        result = await _execute_index_models(_ctx(config={"backends": []}, services=services))

        self.assertEqual(set(result.output["results"]), {"Native", "ComfyUI Main"})
        self.assertEqual(result.output["indexed_backends"], 2)

    async def test_absent_selection_indexes_all_backends(self):
        """A graph saved before the picker existed has no `backends` key at all."""
        native = _fake_backend("native", "Native")
        services = _index_models_services({"native": native})

        result = await _execute_index_models(_ctx(config={}, services=services))

        self.assertEqual(set(result.output["results"]), {"Native"})

    async def test_results_carry_the_indexers_counts(self):
        from src.features.models.backend_indexer import IndexResult

        native = _fake_backend("native", "Native")
        services = _index_models_services(
            {"native": native},
            results_by_id={"native": IndexResult(backend_id="native", listed=7, created=2, matched=5, removed=1)},
        )

        result = await _execute_index_models(_ctx(config={"backends": ["native"]}, services=services))

        self.assertEqual(
            result.output["results"]["Native"],
            {"listed": 7, "created": 2, "matched": 5, "removed": 1},
        )

    async def test_deleted_backend_is_skipped_with_a_note_and_the_rest_are_indexed(self):
        native = _fake_backend("native", "Native")
        services = _index_models_services({"native": native})  # "gone1" was deleted after being saved

        result = await _execute_index_models(
            _ctx(config={"backends": ["gone1", "native"]}, services=services)
        )

        self.assertEqual(set(result.output["results"]), {"Native"})
        self.assertEqual(len(result.output["skipped"]), 1)
        self.assertIn("gone1", result.output["skipped"][0])
        self.assertIn("no longer exists", result.output["skipped"][0])

    async def test_disabled_backend_note_uses_its_display_name(self):
        """Config row still exists (disabled), so the note can carry the real name."""
        from src.features.backends.backend_config import BackendConfigManager

        disabled_config = MagicMock()
        disabled_config.name = "Paused Server"
        config_manager = MagicMock(spec=BackendConfigManager)
        config_manager.get_backend.return_value = disabled_config

        native = _fake_backend("native", "Native")
        services = _index_models_services({"native": native})
        services.backend_config_manager = config_manager

        result = await _execute_index_models(
            _ctx(config={"backends": ["paused1", "native"]}, services=services)
        )

        self.assertEqual(set(result.output["results"]), {"Native"})
        self.assertIn("Paused Server", result.output["skipped"][0])
        self.assertIn("disabled", result.output["skipped"][0])

    async def test_backend_that_cannot_list_models_is_skipped(self):
        native = _fake_backend("native", "Native")
        mute = _fake_backend("mute1", "No Listing", supports_listing=False)
        services = _index_models_services({"native": native, "mute1": mute})

        result = await _execute_index_models(_ctx(config={"backends": []}, services=services))

        self.assertEqual(set(result.output["results"]), {"Native"})
        self.assertIn("No Listing", result.output["skipped"][0])

    async def test_one_unreachable_backend_does_not_fail_the_others(self):
        native = _fake_backend("native", "Native")
        broken = _fake_backend("broken1", "Broken Server")
        services = _index_models_services(
            {"native": native, "broken1": broken},
            results_by_id={"broken1": ConnectionError("connection refused")},
        )

        result = await _execute_index_models(
            _ctx(config={"backends": ["broken1", "native"]}, services=services)
        )

        self.assertEqual(set(result.output["results"]), {"Native"})
        self.assertIn("Broken Server", result.output["skipped"][0])
        self.assertIn("connection refused", result.output["skipped"][0])

    async def test_fails_when_nothing_was_indexed(self):
        services = _index_models_services({})

        with self.assertRaises(RuntimeError) as caught:
            await _execute_index_models(_ctx(config={"backends": ["gone1"]}, services=services))

        self.assertIn("gone1", str(caught.exception))

    async def test_fails_when_no_backends_are_configured_at_all(self):
        services = _index_models_services({})

        with self.assertRaises(RuntimeError):
            await _execute_index_models(_ctx(config={}, services=services))

    async def test_raises_when_no_backend_registry_configured(self):
        from unittest.mock import AsyncMock

        from src.features.models.backend_indexer import BackendModelIndexer

        indexer = MagicMock(spec=BackendModelIndexer)
        indexer.index_backend = AsyncMock()
        with self.assertRaises(RuntimeError):
            await _execute_index_models(
                _ctx(config={}, services=AutomationServices(backend_model_indexer=indexer))
            )

    async def test_raises_when_no_backend_model_indexer_configured(self):
        from src.features.backends.backend_registry import BackendRegistry

        registry = MagicMock(spec=BackendRegistry)
        with self.assertRaises(RuntimeError):
            await _execute_index_models(
                _ctx(config={}, services=AutomationServices(backend_registry=registry))
            )


class TestExecuteAssignModel(unittest.IsolatedAsyncioTestCase):

    async def test_assigns_model_to_selected_user(self):
        manager = MagicMock()
        manager.assign_model_to_user.return_value = {"assignment": {"id": "a1"}, "message": "ok"}
        services = AutomationServices(model_index_manager=manager)

        result = await _execute_assign_model(_ctx(
            config={"model_id": "model-123", "user": "user-456"},
            services=services,
        ))

        manager.assign_model_to_user.assert_called_once_with("model-123", "user-456")
        self.assertEqual(result.output["model_id"], "model-123")
        self.assertEqual(result.output["user_id"], "user-456")
        self.assertEqual(result.output["assignment"], {"id": "a1"})

    async def test_model_id_is_jinja_templated_against_upstream(self):
        manager = MagicMock()
        manager.assign_model_to_user.return_value = {"assignment": {}, "message": "ok"}
        services = AutomationServices(model_index_manager=manager)

        result = await _execute_assign_model(_ctx(
            config={"model_id": "{{ upstream.index_node.model_id }}", "user": "admin-1"},
            upstream={"index_node": {"model_id": "resolved-model-id"}},
            services=services,
        ))

        manager.assign_model_to_user.assert_called_once_with("resolved-model-id", "admin-1")
        self.assertEqual(result.output["model_id"], "resolved-model-id")

    async def test_raises_when_no_model_index_manager_configured(self):
        with self.assertRaises(RuntimeError):
            await _execute_assign_model(_ctx(config={"model_id": "m1", "user": "u1"}, services=AutomationServices()))

    async def test_propagates_assignment_exception(self):
        from src.features.models.exceptions import ModelAssignmentException

        manager = MagicMock()
        manager.assign_model_to_user.side_effect = ModelAssignmentException("already assigned")
        services = AutomationServices(model_index_manager=manager)

        with self.assertRaises(ModelAssignmentException):
            await _execute_assign_model(_ctx(config={"model_id": "m1", "user": "u1"}, services=services))


if __name__ == '__main__':
    unittest.main()


def _media_index_manager(sequences: dict, queue_counts: dict) -> MagicMock:
    """`sequences`: pass_type -> list of `process_pending` results, consumed in
    call order per pass type (the last entry repeats once exhausted)."""
    calls = []

    def process_pending(pass_type, batch_size):
        calls.append((pass_type, batch_size))
        seq = sequences[pass_type]
        idx = sum(1 for c in calls if c[0] == pass_type) - 1
        return seq[min(idx, len(seq) - 1)]

    manager = MagicMock()
    manager.process_pending.side_effect = process_pending
    manager.repository = MagicMock()
    manager.repository.queue_counts.side_effect = (
        lambda pass_type=None: {pass_type: queue_counts.get(pass_type, {})}
    )
    manager.calls = calls
    return manager


class TestExecuteIndexMediaQueue(unittest.IsolatedAsyncioTestCase):

    async def test_raises_when_no_media_index_manager_configured(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        with self.assertRaises(RuntimeError):
            await _execute_index_media_queue(_ctx(config={}, services=AutomationServices()))

    async def test_drains_a_single_partial_batch_per_pass_type(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [{"processed": 3, "failed": 0}], "clip_embed": [{"processed": 2, "failed": 1}]},
            queue_counts={"tags": {"pending": 0}, "clip_embed": {"pending": 0}},
        )

        result = await _execute_index_media_queue(_ctx(
            config={"pass_types": ["tags", "clip_embed"], "batch_size": 8, "max_items": 32},
            services=AutomationServices(media_index_manager=manager),
        ))

        self.assertEqual(result.output["processed_count"], 5)
        self.assertEqual(result.output["failed_count"], 1)
        self.assertEqual(result.output["remaining_count"], 0)
        self.assertEqual(len(manager.calls), 2)

    async def test_loops_batches_within_a_pass_until_the_queue_empties(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [
                {"processed": 2, "failed": 0}, {"processed": 2, "failed": 0}, {"processed": 1, "failed": 0},
            ]},
            queue_counts={"tags": {"pending": 4}},
        )

        result = await _execute_index_media_queue(_ctx(
            config={"pass_types": ["tags"], "batch_size": 2, "max_items": 0},
            services=AutomationServices(media_index_manager=manager),
        ))

        self.assertEqual(result.output["processed_count"], 5)
        self.assertEqual(result.output["remaining_count"], 4)
        self.assertEqual(len(manager.calls), 3)

    async def test_stops_at_the_max_items_cap(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [{"processed": 8, "failed": 0}] * 10},
            queue_counts={"tags": {"pending": 100}},
        )

        result = await _execute_index_media_queue(_ctx(
            config={"pass_types": ["tags"], "batch_size": 8, "max_items": 10},
            services=AutomationServices(media_index_manager=manager),
        ))

        self.assertEqual(result.output["processed_count"], 16)
        self.assertEqual(len(manager.calls), 2)

    async def test_max_items_zero_drains_until_empty_across_passes(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={
                "tags": [{"processed": 8, "failed": 0}, {"processed": 3, "failed": 0}],
                "clip_embed": [{"processed": 0, "failed": 0}],
            },
            queue_counts={"tags": {"pending": 0}, "clip_embed": {"pending": 0}},
        )

        result = await _execute_index_media_queue(_ctx(
            config={"pass_types": ["tags", "clip_embed"], "batch_size": 8, "max_items": 0},
            services=AutomationServices(media_index_manager=manager),
        ))

        self.assertEqual(result.output["processed_count"], 11)
        self.assertEqual(len(manager.calls), 3)

    async def test_default_pass_types_run_tags_clip_and_prompt_embed(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={
                "tags": [{"processed": 1, "failed": 0}],
                "clip_embed": [{"processed": 1, "failed": 0}],
                "prompt_embed": [{"processed": 1, "failed": 0}],
            },
            queue_counts={"tags": {"pending": 0}, "clip_embed": {"pending": 0}, "prompt_embed": {"pending": 0}},
        )

        result = await _execute_index_media_queue(_ctx(config={}, services=AutomationServices(media_index_manager=manager)))

        self.assertEqual({call[0] for call in manager.calls}, {"tags", "clip_embed", "prompt_embed"})
        self.assertEqual(result.output["processed_count"], 3)

    async def test_max_runtime_stops_between_batches_leaving_remainder_queued(self):
        """The deadline is only ever checked between `process_pending` calls, so a
        timed-out fire always stops at a batch boundary - never mid-batch."""
        from src.features.automation.nodes import actions as actions_module
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [{"processed": 8, "failed": 0}] * 20},
            queue_counts={"tags": {"pending": 50}},
        )

        # monotonic() is called once to set the deadline, then once per
        # pre-batch check; the check before the third batch reports the
        # deadline as already elapsed.
        with patch.object(actions_module, "monotonic", side_effect=[0, 0, 0, 100]):
            result = await _execute_index_media_queue(_ctx(
                config={"pass_types": ["tags"], "batch_size": 8, "max_items": 0, "max_runtime_s": 10},
                services=AutomationServices(media_index_manager=manager),
            ))

        self.assertTrue(result.output["timed_out"])
        self.assertEqual(len(manager.calls), 2)
        self.assertEqual(result.output["processed_count"], 16)
        # Nothing beyond the two settled batches was touched - the other 34
        # queued rows are exactly where `claim_batch` will find them on the
        # next fire, still `pending`.
        self.assertEqual(result.output["remaining_count"], 50)

    async def test_max_runtime_s_zero_disables_the_deadline(self):
        from src.features.automation.nodes import actions as actions_module
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [{"processed": 8, "failed": 0}, {"processed": 3, "failed": 0}]},
            queue_counts={"tags": {"pending": 0}},
        )

        with patch.object(actions_module, "monotonic", side_effect=RuntimeError("must not be called")):
            result = await _execute_index_media_queue(_ctx(
                config={"pass_types": ["tags"], "batch_size": 8, "max_items": 0, "max_runtime_s": 0},
                services=AutomationServices(media_index_manager=manager),
            ))

        self.assertFalse(result.output["timed_out"])
        self.assertEqual(result.output["processed_count"], 11)
        self.assertEqual(len(manager.calls), 2)

    async def test_max_items_and_batch_size_still_hold_alongside_a_runtime_deadline(self):
        from src.features.automation.nodes.actions import _execute_index_media_queue

        manager = _media_index_manager(
            sequences={"tags": [{"processed": 8, "failed": 0}] * 10},
            queue_counts={"tags": {"pending": 100}},
        )

        result = await _execute_index_media_queue(_ctx(
            config={"pass_types": ["tags"], "batch_size": 8, "max_items": 10, "max_runtime_s": 300},
            services=AutomationServices(media_index_manager=manager),
        ))

        self.assertEqual(result.output["processed_count"], 16)
        self.assertEqual(len(manager.calls), 2)
        self.assertFalse(result.output["timed_out"])


class TestAssignModelIsIdempotent(unittest.IsolatedAsyncioTestCase):
    """
    A file watcher fires again when the same file is touched or re-copied, and
    `action.index_model` dedups by SHA256 and returns the SAME model id. So the
    second run reaches `action.assign_model` with an already-assigned pair. That
    must not fail a workflow which has, in fact, already done its job.
    """

    async def test_already_assigned_succeeds_and_reports_assigned_false(self):
        from src.features.models.exceptions import ModelAlreadyAssignedException
        from src.features.automation.nodes.actions import _execute_assign_model

        existing = MagicMock()
        existing.to_dict.return_value = {"id": "assign-1"}

        manager = MagicMock()
        manager.assign_model_to_user.side_effect = ModelAlreadyAssignedException(
            "Model 'm1' is already assigned to user 'u1'", assignment=existing
        )

        result = await _execute_assign_model(_ctx(
            config={"model_id": "m1", "user": "u1"},
            services=AutomationServices(model_index_manager=manager),
        ))

        self.assertFalse(result.output["assigned"])
        self.assertEqual(result.output["assignment"], {"id": "assign-1"})
        self.assertEqual(result.output["model_id"], "m1")

    async def test_a_fresh_assignment_reports_assigned_true(self):
        from src.features.automation.nodes.actions import _execute_assign_model

        manager = MagicMock()
        manager.assign_model_to_user.return_value = {"assignment": {"id": "assign-2"}}

        result = await _execute_assign_model(_ctx(
            config={"model_id": "m1", "user": "u1"},
            services=AutomationServices(model_index_manager=manager),
        ))

        self.assertTrue(result.output["assigned"])
        self.assertEqual(result.output["assignment"], {"id": "assign-2"})

    async def test_a_real_assignment_failure_still_fails_the_node(self):
        """Only 'already assigned' is benign; an unknown model must still fail the run."""
        from src.features.models.exceptions import ModelAssignmentException
        from src.features.automation.nodes.actions import _execute_assign_model

        manager = MagicMock()
        manager.assign_model_to_user.side_effect = ModelAssignmentException("No model with id 'ghost'")

        with self.assertRaises(ModelAssignmentException):
            await _execute_assign_model(_ctx(
                config={"model_id": "ghost", "user": "u1"},
                services=AutomationServices(model_index_manager=manager),
            ))


def _backend_services(quick_actions=None, lifecycle=None) -> AutomationServices:
    """A services bundle whose backend manager returns one fake backend that
    self-declares `quick_actions` (default: clear-vram + clear-cache + restart-backend)."""
    if quick_actions is None:
        quick_actions = [
            {"id": "clear-vram", "label": "Clear VRAM"},
            {"id": "clear-cache", "label": "Clear VRAM & Cache (RAM)"},
            {"id": "restart-backend", "label": "Restart Backend"},
        ]
    backend = MagicMock()
    backend.device = "cuda"
    backend.quick_actions.return_value = quick_actions
    config_manager = MagicMock()
    config_manager.get_backend.return_value = backend
    return AutomationServices(
        backend_config_manager=config_manager,
        model_lifecycle_manager=lifecycle if lifecycle is not None else MagicMock(),
    )


class TestExecuteBackendAction(unittest.IsolatedAsyncioTestCase):

    async def test_clear_vram_offloads_gpu_residents_without_evicting_cache(self):
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action
        from src.platform.runtime.native.memory.residency import OffloadResult

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = []
        services = _backend_services(lifecycle=lifecycle)
        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = OffloadResult(["dit", "vae"], freed_gb=13.4)

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

        residency_manager.offload_all.assert_called_once_with("cuda", exclude=[])
        lifecycle.cleanup.assert_called_once_with(aggressive=False)
        lifecycle.invalidate.assert_not_called()
        self.assertEqual(result.output["backend_id"], "native")
        self.assertEqual(result.output["action_id"], "clear-vram")
        self.assertEqual(result.output["label"], "Clear VRAM")
        self.assertTrue(result.output["success"])
        self.assertEqual(result.output["status"], "cleared")
        self.assertEqual(result.output["offloaded_count"], 2)
        self.assertEqual(result.output["freed_gb"], 13.4)
        self.assertEqual(result.output["failed_count"], 0)

    async def test_clear_vram_reports_zero_when_nothing_resident(self):
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action
        from src.platform.runtime.native.memory.residency import OffloadResult

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = []
        services = _backend_services(lifecycle=lifecycle)
        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = OffloadResult()

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

        self.assertEqual(result.output["offloaded_count"], 0)
        self.assertEqual(result.output["freed_gb"], 0.0)
        self.assertEqual(result.output["failed_count"], 0)

    async def test_clear_vram_sweeps_unregistered_gpu_resident_cache_entries(self):
        """Registration-gap-proof fallback: a cached model that ended up
        GPU-resident WITHOUT registering with GpuResidencyManager (a future
        placement path that forgot to) is still offloaded, via
        `cached_values()` rather than the residency ledger."""
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action
        from src.platform.runtime.native.memory.residency import OffloadResult

        class FakeCachedModel:
            def __init__(self, device, estimated_vram_gb):
                self.device = device
                self.estimated_vram_gb = estimated_vram_gb
                self.offloaded = False

            def offload(self):
                self.offloaded = True
                self.device = "cpu"

        unregistered_gpu = FakeCachedModel("cuda:0", 6.0)
        cpu_resident = FakeCachedModel("cpu", 4.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = [unregistered_gpu, cpu_resident]
        services = _backend_services(lifecycle=lifecycle)
        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = OffloadResult()

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

        self.assertTrue(unregistered_gpu.offloaded)
        self.assertFalse(cpu_resident.offloaded)
        self.assertEqual(result.output["offloaded_count"], 1)
        self.assertEqual(result.output["freed_gb"], 6.0)
        self.assertEqual(result.output["failed_count"], 0)

    async def test_clear_vram_sweep_skips_leased_gpu_resident_entries(self):
        """A leased cache entry (an in-flight generation) must never be
        yanked, even by the fallback sweep."""
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action
        from src.platform.runtime.native.memory.residency import OffloadResult

        class FakeCachedModel:
            def __init__(self, device, estimated_vram_gb):
                self.device = device
                self.estimated_vram_gb = estimated_vram_gb
                self.offloaded = False

            def offload(self):
                self.offloaded = True
                self.device = "cpu"

        leased_gpu = FakeCachedModel("cuda:0", 9.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = [leased_gpu]
        lifecycle.cached_values.return_value = [leased_gpu]
        services = _backend_services(lifecycle=lifecycle)
        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = OffloadResult()

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

        residency_manager.offload_all.assert_called_once_with("cuda", exclude=[leased_gpu])
        self.assertFalse(leased_gpu.offloaded)
        self.assertEqual(result.output["offloaded_count"], 0)

    async def test_clear_vram_sweep_does_not_double_count_already_offloaded(self):
        """A cached model the residency-ledger sweep already offloaded reads
        back as CPU-resident by the time the lifecycle sweep runs, so it must
        not be counted (or offloaded) a second time."""
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action
        from src.platform.runtime.native.memory.residency import OffloadResult

        class FakeCachedModel:
            def __init__(self, device, estimated_vram_gb):
                self.device = device
                self.estimated_vram_gb = estimated_vram_gb
                self.offload_calls = 0

            def offload(self):
                self.offload_calls += 1
                self.device = "cpu"

        # Simulate: offload_all already moved this one to cpu.
        already_offloaded = FakeCachedModel("cpu", 12.0)

        lifecycle = MagicMock()
        lifecycle.leased_values.return_value = []
        lifecycle.cached_values.return_value = [already_offloaded]
        services = _backend_services(lifecycle=lifecycle)
        residency_manager = MagicMock()
        residency_manager.offload_all.return_value = OffloadResult([already_offloaded], freed_gb=12.0)

        with patch(
            "src.platform.runtime.native.memory.residency.get_residency_manager",
            return_value=residency_manager,
        ):
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

        self.assertEqual(already_offloaded.offload_calls, 0)
        self.assertEqual(result.output["offloaded_count"], 1)
        self.assertEqual(result.output["freed_gb"], 12.0)

    async def test_clear_cache_invalidates_lifecycle_cache(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        lifecycle = MagicMock()
        services = _backend_services(lifecycle=lifecycle)

        result = await _execute_backend_action(_ctx(
            config={"backend": "native", "action": "clear-cache"}, services=services,
        ))

        lifecycle.invalidate.assert_called_once_with()
        self.assertEqual(result.output["backend_id"], "native")
        self.assertEqual(result.output["action_id"], "clear-cache")
        self.assertEqual(result.output["label"], "Clear VRAM & Cache (RAM)")
        self.assertTrue(result.output["success"])
        self.assertEqual(result.output["status"], "cleared")

    async def test_raises_when_clear_cache_has_no_lifecycle_manager(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        backend = MagicMock()
        backend.quick_actions.return_value = [{"id": "clear-cache", "label": "Clear VRAM & Cache (RAM)"}]
        config_manager = MagicMock()
        config_manager.get_backend.return_value = backend
        services = AutomationServices(backend_config_manager=config_manager)

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-cache"}, services=services,
            ))

    async def test_restart_backend_schedules_the_shared_restart(self):
        from unittest.mock import patch
        from src.features.automation.nodes.actions import _execute_backend_action

        services = _backend_services()

        # Patch where actions.py looks it up (imported inside the function).
        with patch("src.features.settings.app_lifecycle.schedule_app_restart") as scheduler:
            result = await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "restart-backend"}, services=services,
            ))

        scheduler.assert_called_once_with()
        self.assertEqual(result.output["action_id"], "restart-backend")
        self.assertTrue(result.output["success"])
        self.assertEqual(result.output["status"], "restarting")

    async def test_raises_when_no_backend_config_manager_configured(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"},
                services=AutomationServices(model_lifecycle_manager=MagicMock()),
            ))

    async def test_raises_when_backend_not_found(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        config_manager = MagicMock()
        config_manager.get_backend.return_value = None
        services = AutomationServices(
            backend_config_manager=config_manager, model_lifecycle_manager=MagicMock(),
        )

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "ghost", "action": "clear-vram"}, services=services,
            ))

    async def test_raises_when_action_not_declared_by_backend(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        # Backend declares only clear-vram; asking for restart-backend must fail.
        services = _backend_services(quick_actions=[{"id": "clear-vram", "label": "Clear VRAM"}])

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "restart-backend"}, services=services,
            ))

    async def test_raises_when_clear_vram_has_no_lifecycle_manager(self):
        from src.features.automation.nodes.actions import _execute_backend_action

        backend = MagicMock()
        backend.quick_actions.return_value = [{"id": "clear-vram", "label": "Clear VRAM"}]
        config_manager = MagicMock()
        config_manager.get_backend.return_value = backend
        services = AutomationServices(backend_config_manager=config_manager)

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "clear-vram"}, services=services,
            ))

    async def test_declared_but_unbound_action_fails_loudly(self):
        """A plugin-declared action with no core binding must not silently no-op."""
        from src.features.automation.nodes.actions import _execute_backend_action

        services = _backend_services(quick_actions=[{"id": "reindex", "label": "Reindex"}])

        with self.assertRaises(RuntimeError):
            await _execute_backend_action(_ctx(
                config={"backend": "native", "action": "reindex"}, services=services,
            ))


class TestExecuteScanFiles(unittest.IsolatedAsyncioTestCase):

    def _tree(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = tmp.name
        os.makedirs(os.path.join(base, "loras", "krea2"))
        os.makedirs(os.path.join(base, "checkpoints"))
        with open(os.path.join(base, "loras", "krea2", "style.safetensors"), "w") as f:
            f.write("a")
        with open(os.path.join(base, "loras", "notes.txt"), "w") as f:
            f.write("b")
        with open(os.path.join(base, "checkpoints", "base.safetensors"), "w") as f:
            f.write("c")
        return base

    async def test_recursive_walk_emits_one_item_per_file(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": True, "resolve_models": False},
        ))

        self.assertEqual(result.output, {"scanned": 3, "emitted": 3, "truncated": False})
        paths = {item["path"] for item in result.items}
        self.assertEqual(paths, {
            os.path.join(base, "loras", "krea2", "style.safetensors"),
            os.path.join(base, "loras", "notes.txt"),
            os.path.join(base, "checkpoints", "base.safetensors"),
        })

    async def test_non_recursive_walk_skips_subdirectories(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": False, "resolve_models": False},
        ))

        self.assertEqual(result.output["emitted"], 0)
        self.assertEqual(result.items, [])

    async def test_extension_filter_only_emits_matching_files(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": True, "extensions": "safetensors", "resolve_models": False},
        ))

        self.assertEqual(result.output["emitted"], 2)
        self.assertTrue(all(item["ext"] == ".safetensors" for item in result.items))

    async def test_max_files_caps_and_reports_truncation(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": True, "max_files": 1, "resolve_models": False},
        ))

        self.assertEqual(result.output, {"scanned": 3, "emitted": 1, "truncated": True})
        self.assertEqual(len(result.items), 1)

    async def test_rel_parts_are_relative_to_the_scanned_directory(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": True, "extensions": "safetensors", "resolve_models": False},
        ))

        item = next(i for i in result.items if i["path"].endswith("style.safetensors"))
        self.assertEqual(item["rel_parts"], ["loras", "krea2", "style.safetensors"])
        self.assertEqual(item["rel_path"], "loras/krea2/style.safetensors")

    async def test_resolve_models_fills_model_fields_when_a_match_exists(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        base = self._tree()
        target = os.path.join(base, "loras", "krea2", "style.safetensors")

        def get_by_file_path(path):
            return SimpleNamespace(id="m1", filename="style.safetensors", model_type="lora") if path == target else None

        model_repo = MagicMock()
        model_repo.get_by_file_path = MagicMock(side_effect=get_by_file_path)

        settings_manager = MagicMock()
        settings_manager.get_models_dir.return_value = base

        services = AutomationServices(model_repository=model_repo, settings_manager=settings_manager)
        result = await _execute_scan_files(_ctx(
            config={"directory": base, "recursive": True, "extensions": "safetensors", "resolve_models": True},
            services=services,
        ))

        resolved = next(i for i in result.items if i["path"] == target)
        self.assertEqual(resolved["model_id"], "m1")
        unresolved = next(i for i in result.items if i["path"] != target)
        self.assertIsNone(unresolved["model_id"])

    async def test_raises_when_directory_does_not_exist(self):
        from src.features.automation.nodes.actions import _execute_scan_files

        with self.assertRaises(RuntimeError):
            await _execute_scan_files(_ctx(config={"directory": "/does/not/exist", "resolve_models": False}))


class TestExecuteAddToCollection(unittest.IsolatedAsyncioTestCase):

    async def test_adds_model_and_reports_added_true(self):
        from src.features.automation.nodes.actions import _execute_add_to_collection

        repo = MagicMock()
        repo.get_by_id.return_value = SimpleNamespace(id="c1", user_id="u1")
        repo.add_members.return_value = 1
        services = AutomationServices(model_collection_repository=repo)

        result = await _execute_add_to_collection(_ctx(
            config={"collection": "c1", "model_id": "m1"}, services=services,
        ))

        self.assertEqual(result.output, {"collection_id": "c1", "model_id": "m1", "added": True, "reason": None})
        repo.add_members.assert_called_once_with("c1", ["m1"], "u1")

    async def test_empty_model_id_skips_with_a_reason_and_does_not_touch_the_repository(self):
        from src.features.automation.nodes.actions import _execute_add_to_collection

        repo = MagicMock()
        services = AutomationServices(model_collection_repository=repo)

        result = await _execute_add_to_collection(_ctx(
            config={"collection": "c1", "model_id": ""}, services=services,
        ))

        self.assertFalse(result.output["added"])
        self.assertEqual(result.output["reason"], "no model_id")
        repo.get_by_id.assert_not_called()

    async def test_unknown_collection_skips_with_a_reason(self):
        from src.features.automation.nodes.actions import _execute_add_to_collection

        repo = MagicMock()
        repo.get_by_id.return_value = None
        services = AutomationServices(model_collection_repository=repo)

        result = await _execute_add_to_collection(_ctx(
            config={"collection": "missing", "model_id": "m1"}, services=services,
        ))

        self.assertFalse(result.output["added"])
        self.assertEqual(result.output["reason"], "collection not found")

    async def test_model_id_is_rendered_through_jinja(self):
        from src.features.automation.nodes.actions import _execute_add_to_collection

        repo = MagicMock()
        repo.get_by_id.return_value = SimpleNamespace(id="c1", user_id="u1")
        repo.add_members.return_value = 1
        services = AutomationServices(model_collection_repository=repo)

        result = await _execute_add_to_collection(_ctx(
            config={"collection": "c1", "model_id": "{{ upstream.scan.model_id }}"},
            upstream={"scan": {"model_id": "m-from-upstream"}},
            services=services,
        ))

        self.assertEqual(result.output["model_id"], "m-from-upstream")
        repo.add_members.assert_called_once_with("c1", ["m-from-upstream"], "u1")

    async def test_raises_without_a_configured_repository(self):
        from src.features.automation.nodes.actions import _execute_add_to_collection

        with self.assertRaises(RuntimeError):
            await _execute_add_to_collection(_ctx(config={"collection": "c1", "model_id": "m1"}))
