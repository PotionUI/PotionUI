"""
Drift guard for the declared node output contract.

Every condition/action `NodeTypeSpec` declares `outputs` - the keys downstream
nodes may read as `upstream.<node_id>.<key>`. That declaration is what the
canvas renders and what the variable picker offers, so if it drifts from the
dict `execute()` actually returns, the UI starts lying.

This module executes every condition and action against fakes and asserts

    set(result.output.keys()) == {f.key for f in spec.outputs}

`test_every_condition_and_action_is_covered` is the part that keeps this honest
over time: registering a new condition/action without adding a fixture here
fails, so a node type cannot be added without declaring its outputs.

Triggers have no `execute()` (the concrete `TriggerSource` classes are built by
`AutomationManager`), so they're covered differently: `trigger.filesystem` is
checked against its pure `build_event_payload`, and the rest are checked for the
declare-or-mark-dynamic invariant.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.features.models.indexer import ModelScanner
from src.features.automation.context import AutomationServices, NodeExecutionContext, RunContext
from src.features.automation.nodes import register_builtin_nodes
from src.platform.plugins.automation_nodes import NodeTypeRegistry
from src.features.automation.triggers.filesystem import build_event_payload
from src.features.models.assignments import ModelAssignmentService
from src.features.models.provider_info import ProviderInfoFetcher
from src.features.notifications import operations as notification_operations
from src.platform.runtime.gpu import GpuManager
from src.features.tags.repository import TagRepository


def _registry() -> NodeTypeRegistry:
    """A fresh registry, so this test never depends on global import order."""
    registry = NodeTypeRegistry()
    register_builtin_nodes(registry)
    return registry


def _ctx(config: dict, event: dict = None, services: AutomationServices = None) -> NodeExecutionContext:
    run = RunContext(
        automation_id="auto1",
        run_id="run1",
        event=event or {},
        services=services or AutomationServices(),
    )
    return NodeExecutionContext(run=run, node_id="n1", node_type="test", config=config)


def _index_model_case():
    """`_execute_index_model` calls `os.stat`, so it needs a file that exists."""
    # `spec=` matters: a bare MagicMock invents any attribute you touch, so it
    # happily answered `index_single_model()` even while the app was injecting a
    # directory scanner that has no such method (two classes were once both named
    # `ModelIndexer`; see `test_service_contracts.py`). Spec'd mocks fail the same
    # way production does.
    indexer = MagicMock(spec=ModelScanner)
    indexer.index_single_model.return_value = SimpleNamespace(id="m1", filename="style.safetensors")
    handle = tempfile.NamedTemporaryFile(suffix=".safetensors")
    return (
        {"path": handle.name, "model_type": "lora"},
        {},
        AutomationServices(model_indexer=indexer),
        handle,  # kept alive by the caller until execute() has run
    )


def _tag_repo() -> MagicMock:
    repo = MagicMock(spec=TagRepository)
    repo.get_tag_by_name.return_value = SimpleNamespace(id="tag1")
    repo.add_tag_to_model.return_value = True
    return repo


def _user_group_repo() -> MagicMock:
    from src.features.user_groups.repository import UserGroupRepository
    repo = MagicMock(spec=UserGroupRepository)
    repo.get_group_by_id.return_value = SimpleNamespace(id="g1", name="All Users")
    repo.add_user_to_group.return_value = SimpleNamespace(id="m1")
    return repo


def _model_index_manager() -> SimpleNamespace:
    """A `ModelIndexCollaborators`-shaped double: `action.assign_model`/
    `action.fetch_provider_metadata` reach through `.assignments`/
    `.provider_info` respectively, not through the bundle directly."""
    assignments = MagicMock(spec=ModelAssignmentService)
    assignments.assign_model_to_user.return_value = {"assignment": {"id": "a1"}}
    provider_info = MagicMock(spec=ProviderInfoFetcher)
    provider_info.run_provider_fetch = AsyncMock()
    return SimpleNamespace(assignments=assignments, provider_info=provider_info)


def _notification_manager() -> MagicMock:
    # A bound callable (`functools.partial(operations.notify, collaborators)`),
    # not a class instance - see src.bootstrap.container.
    manager = MagicMock(spec=notification_operations.notify)
    manager.return_value = ["n1"]
    return manager


def _gpu_manager() -> MagicMock:
    manager = MagicMock(spec=GpuManager)
    # Above any threshold, so `_execute_wait_for_gpu`'s poll loop exits immediately.
    manager.get_free_vram.return_value = 999_999
    return manager


def _backend_config_manager() -> MagicMock:
    from src.features.backends.backend_config import BackendConfigManager, NativeBackendConfig
    backend = MagicMock(spec=NativeBackendConfig)
    backend.quick_actions.return_value = [{"id": "clear-vram", "label": "Clear VRAM"}]
    manager = MagicMock(spec=BackendConfigManager)
    manager.get_backend.return_value = backend
    return manager


def _model_lifecycle_manager() -> MagicMock:
    from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager
    manager = MagicMock(spec=ModelLifecycleManager)
    manager.leased_values.return_value = []
    return manager


def _media_index_manager() -> MagicMock:
    from src.features.media_index.manager import MediaIndexManager
    manager = MagicMock(spec=MediaIndexManager)
    manager.process_pending.return_value = {"processed": 2, "failed": 0}
    manager.repository = MagicMock()
    manager.repository.queue_counts.return_value = {"tags": {"pending": 1}}
    return manager


def _index_models_services() -> AutomationServices:
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.backends.base_backend import BaseBackend
    from src.features.models.backend_indexer import BackendModelIndexer, IndexResult

    backend = MagicMock(spec=BaseBackend)
    backend.name = "Native"
    backend.supports_model_listing.return_value = True

    registry = MagicMock(spec=BackendRegistry)
    registry.get_backend.return_value = backend
    registry.get_all_backends.return_value = {"native": backend}

    indexer = MagicMock(spec=BackendModelIndexer)
    indexer.index_backend = AsyncMock(
        return_value=IndexResult(backend_id="native", listed=2, created=1, matched=1)
    )
    return AutomationServices(backend_registry=registry, backend_model_indexer=indexer)


# node key -> (config, event, services). `action.index_model` is special-cased
# because it needs a live tempfile handle held open across execute().
EXECUTABLE_CASES = {
    "condition.compare": ({"field": "event.x", "operator": "equals", "value": "1"}, {"x": "1"}, None),
    "condition.path_match": ({"field": "event.path", "match_type": "contains", "value": "lora"},
                             {"path": "/models/loras/a.safetensors"}, None),
    "condition.jinja_expression": ({"expression": "1 == 1"}, {}, None),
    "condition.switch": ({"field": "event.x", "cases": "a, b"}, {"x": "a"}, None),
    "action.add_tag": ({"model_id": "m1", "tag_name": "krea2"}, {},
                       lambda: AutomationServices(tag_repository=_tag_repo())),
    "action.assign_model": ({"model_id": "m1", "user": "u1"}, {},
                            lambda: AutomationServices(model_index_manager=_model_index_manager())),
    "action.assign_user_to_group": ({"user_id": "u1", "group": "g1"}, {},
                                    lambda: AutomationServices(user_group_repository=_user_group_repo())),
    "action.fetch_provider_metadata": ({"model_id": "m1", "provider": "civitai"}, {},
                                       lambda: AutomationServices(model_index_manager=_model_index_manager())),
    "action.send_notification": ({"title": "t", "message": "m", "level": "info"}, {},
                                 lambda: AutomationServices(notification_manager=_notification_manager())),
    "action.wait_for_gpu": ({"threshold_mb": 1, "poll_interval_s": 0.5}, {},
                            lambda: AutomationServices(gpu_manager=_gpu_manager())),
    "action.backend_action": ({"backend": "native", "action": "clear-vram"}, {},
                              lambda: AutomationServices(
                                  backend_config_manager=_backend_config_manager(),
                                  model_lifecycle_manager=_model_lifecycle_manager())),
    "action.index_models": ({"backends": ["native"]}, {}, _index_models_services),
    "action.index_media_queue": ({"pass_types": ["tags", "clip_embed"], "batch_size": 8, "max_items": 32}, {},
                                 lambda: AutomationServices(media_index_manager=_media_index_manager())),
}

SPECIAL_CASES = {"action.index_model", "action.scan_files", "action.add_to_collection"}


class TestDeclaredOutputsMatchExecution(unittest.IsolatedAsyncioTestCase):

    async def test_every_condition_and_action_is_covered(self):
        """A new condition/action must add a fixture here - which forces it to declare `outputs`."""
        registry = _registry()
        executable = {
            spec.key
            for spec in registry.all()
            if spec.kind in ("condition", "action")
        }
        covered = set(EXECUTABLE_CASES) | SPECIAL_CASES

        self.assertEqual(
            executable - covered,
            set(),
            "Node type(s) registered without a fixture in this drift guard - "
            "add one so their declared `outputs` stay honest.",
        )
        self.assertEqual(covered - executable, set(), "Fixture for a node type that no longer exists.")

    async def test_declared_outputs_match_returned_keys(self):
        registry = _registry()

        for key, (config, event, services_factory) in EXECUTABLE_CASES.items():
            with self.subTest(node_type=key):
                spec = registry.get(key)
                services = services_factory() if services_factory else None
                result = await spec.execute(_ctx(config, event=event, services=services))

                self.assertEqual(
                    set(result.output.keys()),
                    {field.key for field in spec.outputs},
                    f"{key}: declared outputs do not match the dict execute() returned",
                )

    async def test_index_model_declared_outputs_match_returned_keys(self):
        registry = _registry()
        spec = registry.get("action.index_model")
        config, event, services, handle = _index_model_case()

        try:
            result = await spec.execute(_ctx(config, event=event, services=services))
        finally:
            handle.close()

        self.assertEqual(set(result.output.keys()), {field.key for field in spec.outputs})

    async def test_scan_files_declared_output_and_item_output_keys_match_returned_keys(self):
        registry = _registry()
        spec = registry.get("action.scan_files")

        with tempfile.TemporaryDirectory() as scan_dir:
            os.makedirs(os.path.join(scan_dir, "krea2"))
            with open(os.path.join(scan_dir, "krea2", "style.safetensors"), "w") as handle:
                handle.write("x")

            config = {"directory": scan_dir, "recursive": True, "resolve_models": False}
            result = await spec.execute(_ctx(config, services=AutomationServices()))

        self.assertEqual(set(result.output.keys()), {field.key for field in spec.outputs})
        self.assertEqual(len(result.items), 1)
        self.assertEqual(set(result.items[0].keys()), {field.key for field in spec.item_outputs})

    async def test_add_to_collection_declared_outputs_match_returned_keys_on_success_and_on_skip(self):
        from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository

        registry = _registry()
        spec = registry.get("action.add_to_collection")

        repo = MagicMock(spec=ModelCollectionRepository)
        repo.get_by_id.return_value = SimpleNamespace(id="c1", user_id="u1")
        repo.add_members.return_value = 1
        services = AutomationServices(model_collection_repository=repo)

        success_result = await spec.execute(_ctx({"collection": "c1", "model_id": "m1"}, services=services))
        self.assertEqual(set(success_result.output.keys()), {field.key for field in spec.outputs})

        skip_result = await spec.execute(_ctx({"collection": "c1", "model_id": ""}, services=services))
        self.assertEqual(set(skip_result.output.keys()), {field.key for field in spec.outputs})


class TestOutputDeclarationInvariants(unittest.TestCase):

    def test_every_condition_and_action_declares_outputs(self):
        for spec in _registry().all():
            if spec.kind in ("condition", "action"):
                with self.subTest(node_type=spec.key):
                    self.assertTrue(spec.outputs, f"{spec.key} declares no outputs")

    def test_every_trigger_declares_outputs_or_is_marked_dynamic(self):
        """A trigger either states its event payload, or admits the shape is runtime-defined."""
        for spec in _registry().by_kind("trigger"):
            with self.subTest(node_type=spec.key):
                self.assertTrue(
                    bool(spec.outputs) != bool(spec.dynamic_outputs),
                    f"{spec.key} must declare `outputs` XOR set `dynamic_outputs`",
                )

    def test_dynamic_triggers_are_exactly_manual_and_hook_event(self):
        dynamic = {spec.key for spec in _registry().by_kind("trigger") if spec.dynamic_outputs}
        self.assertEqual(dynamic, {"trigger.manual", "trigger.hook_event"})

    def test_filesystem_trigger_outputs_match_build_event_payload(self):
        """The one trigger whose payload comes from a pure function we can call directly."""
        spec = _registry().get("trigger.filesystem")

        with tempfile.TemporaryDirectory() as watch_dir:
            src_path = os.path.join(watch_dir, "krea2", "style.safetensors")
            os.makedirs(os.path.dirname(src_path))
            with open(src_path, "w") as handle:
                handle.write("x")

            payload = build_event_payload(watch_dir, src_path, "created")

        self.assertEqual(set(payload.keys()), {field.key for field in spec.outputs})

    def test_output_field_types_are_from_the_known_vocabulary(self):
        allowed = {"string", "number", "boolean", "array", "object", "any"}
        for spec in _registry().all():
            for field in list(spec.outputs) + list(spec.item_outputs):
                with self.subTest(node_type=spec.key, field=field.key):
                    self.assertIn(field.type, allowed)


class TestTemplatableMarkers(unittest.TestCase):
    """
    `templatable` means "this config value is run through `render_template`", so
    the frontend may insert `{{ ... }}` into it. Conditions resolve their `field`
    with `get_path` (a bare dot-path, no braces) and therefore carry `input_ref`
    instead. Mixing the two makes the variable picker insert broken syntax.
    """

    def _fields(self, key):
        return {f["name"]: f for f in _registry().get(key).config_schema}

    def test_action_templatable_fields_are_exactly_those_that_are_rendered(self):
        expected = {
            "action.index_model": {"path"},
            # `backends` is a list of ids (checkbox_group) and `timeout_s` a
            # number - neither is ever passed through render_template.
            "action.index_models": set(),
            "action.index_media_queue": set(),
            "action.add_tag": {"model_id", "tag_name"},
            "action.assign_model": {"model_id", "user"},
            "action.assign_user_to_group": {"user_id", "group"},
            "action.fetch_provider_metadata": {"model_id", "provider"},
            "action.send_notification": {"title", "message"},
            "action.wait_for_gpu": set(),
            "action.backend_action": set(),
            # `directory`/`custom_path`/`extensions`/`max_files` are read raw
            # (never through render_template) - mirrors trigger.filesystem.
            "action.scan_files": set(),
            "action.add_to_collection": {"model_id"},
        }
        for key, templatable in expected.items():
            with self.subTest(node_type=key):
                actual = {name for name, f in self._fields(key).items() if f.get("templatable")}
                self.assertEqual(actual, templatable)

    def test_conditions_use_input_ref_not_templatable(self):
        for key in ("condition.compare", "condition.path_match", "condition.switch"):
            with self.subTest(node_type=key):
                fields = self._fields(key)
                self.assertEqual(fields["field"].get("input_ref"), "path")
                self.assertNotIn("templatable", fields["field"])

        expression = self._fields("condition.jinja_expression")["expression"]
        self.assertEqual(expression.get("input_ref"), "expression")
        self.assertNotIn("templatable", expression)

    def test_no_trigger_config_field_is_templatable(self):
        for spec in _registry().by_kind("trigger"):
            for field in spec.config_schema:
                with self.subTest(node_type=spec.key, field=field["name"]):
                    self.assertNotIn("templatable", field)


if __name__ == "__main__":
    unittest.main()
