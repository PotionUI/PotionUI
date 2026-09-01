"""
Guards the seam between `AutomationServices` and the real objects wired into it.

`action.index_model` needs `src.features.models.indexer.ModelScanner` -
`index_single_model()` with its SHA256 dedup. The composition root used to
inject `src.features.models.directory.ModelIndexer` instead (a same-named,
whole-directory scanner with no single-file entry point), so `action.index_model`
raised `AttributeError: 'ModelIndexer' object has no attribute 'index_single_model'`
- but only at run time, inside a pipe, after a file landed in a watched
directory. `ModelIndexer` has since been deleted (it had no runtime reader),
so the mix-up itself can no longer happen; the wiring check below stays as a
regression guard on `ModelScanner` specifically.

Unit tests missed the original bug because a bare `MagicMock()` fabricates any
attribute you touch. Two defences live here:

1. `TestServiceMethodContracts` pins the methods each node's `execute()` calls,
   against the REAL classes. A rename or removal turns this red.
2. `TestAutomationWiring` reads the composition root's AST and asserts the name bound to
   `AutomationServices(model_indexer=...)` was imported from the right module.
   That's the only one of the two that catches "right method, wrong class".

The drift guard (`test_outputs_contract.py`) additionally uses `MagicMock(spec=...)`
so its fakes can no longer invent methods.
"""

import ast
import inspect
import unittest
from pathlib import Path

from src.features.models.indexer import ModelScanner as FileModelIndexer
from src.features.models.assignments import ModelAssignmentService
from src.features.models.provider_info import ProviderInfoFetcher
from src.features.models.repository import ModelRepository
from src.features.notifications import operations as notification_operations
from src.platform.runtime.gpu import GpuMonitor
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.features.backends.backend_config import BackendConfigStore, BaseBackendConfig
from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.base_backend import BaseBackend
from src.features.models.availability_repository import ModelAvailabilityRepository
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.tags.repository import TagRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.media_index.indexer import MediaIndexer

# Method -> the node type whose `execute()` calls it. Keep in step with
# `src/core/automation/nodes/actions.py`.
REQUIRED_METHODS = {
    FileModelIndexer: {"index_single_model": "action.index_model"},
    # `model_index_manager` on AutomationServices is a `ModelIndexCollaborators`
    # bundle, not a class instance - pin against the specific role objects
    # `action.assign_model`/`action.fetch_provider_metadata` reach through it
    # (`.assignments`/`.provider_info`) instead.
    ModelAssignmentService: {"assign_model_to_user": "action.assign_model"},
    ProviderInfoFetcher: {"run_provider_fetch": "action.fetch_provider_metadata"},
    TagRepository: {
        "get_tag_by_name": "action.add_tag",
        "create_tag": "action.add_tag",
        "add_tag_to_model": "action.add_tag",
    },
    # `notification_manager` on AutomationServices is a bound callable
    # (`functools.partial(operations.notify, collaborators)`, see
    # src.bootstrap.container), not a class instance - pin against the
    # operations module's `notify` function it's bound to instead.
    notification_operations: {"notify": "action.send_notification"},
    GpuMonitor: {"get_free_vram": "action.wait_for_gpu"},
    BackendConfigStore: {"get_backend": "action.backend_action"},
    BaseBackendConfig: {"quick_actions": "action.backend_action"},
    ModelLifecycle: {"invalidate": "action.backend_action"},
    BackendRegistry: {
        "get_backend": "action.index_models",
        "get_all_backends": "action.index_models",
    },
    BaseBackend: {"supports_model_listing": "action.index_models"},
    BackendModelIndexer: {"index_backend": "action.index_models"},
    # action.index_model reads availability back through the indexer's repo
    # (`backend_indexer.availability.backend_ids_by_model`).
    ModelAvailabilityRepository: {"backend_ids_by_model": "action.index_model"},
    UserGroupRepository: {
        "get_group_by_id": "action.assign_user_to_group",
        "add_user_to_group": "action.assign_user_to_group",
    },
    MediaIndexer: {"process_pending": "action.index_media_queue"},
    ModelRepository: {"get_by_file_path": "action.scan_files"},
    ModelCollectionRepository: {"get_by_id": "action.add_to_collection", "add_members": "action.add_to_collection"},
}

COMPOSITION_ROOT = Path(inspect.getsourcefile(__import__("src.bootstrap.container", fromlist=["x"])))


class TestServiceMethodContracts(unittest.TestCase):

    def test_every_method_the_actions_call_exists_on_the_real_class(self):
        for service_class, methods in REQUIRED_METHODS.items():
            for method, node_type in methods.items():
                with self.subTest(service=service_class.__name__, method=method):
                    self.assertTrue(
                        callable(getattr(service_class, method, None)),
                        f"{node_type} calls {service_class.__name__}.{method}(), which does not exist",
                    )


class TestAutomationWiring(unittest.TestCase):
    """Static check on how the composition root constructs `AutomationServices`."""

    @staticmethod
    def _automation_services_kwargs(tree: ast.AST) -> dict:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "AutomationServices"
            ):
                return {kw.arg: kw.value for kw in node.keywords}
        raise AssertionError("No AutomationServices(...) construction found in the composition root")

    @staticmethod
    def _import_source_of(tree: ast.AST, bound_name: str) -> str | None:
        """Which module did `bound_name` come from? `None` if it isn't an import
        (e.g. a local variable built earlier in `configure`)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == bound_name:
                        return node.module
        return None

    def setUp(self):
        self.tree = ast.parse(COMPOSITION_ROOT.read_text())
        self.kwargs = self._automation_services_kwargs(self.tree)

    def test_model_indexer_is_the_file_indexer_not_the_directory_scanner(self):
        value = self.kwargs.get("model_indexer")
        self.assertIsInstance(
            value, ast.Name, "AutomationServices(model_indexer=...) should be a plain name"
        )

        source_module = self._import_source_of(self.tree, value.id)
        origin = (
            f"imported from '{source_module}'"
            if source_module
            else f"a local variable (`{value.id}` is built inside build_container(), "
            "which is how the src.features.models.directory directory scanner got injected before)"
        )
        self.assertEqual(
            source_module,
            "src.features.models.indexer",
            f"AutomationServices(model_indexer=...) was given `{value.id}`, {origin}. "
            "action.index_model needs index_single_model(), which only exists on "
            "src.features.models.indexer.ModelScanner.",
        )

    def test_the_services_the_actions_need_are_all_wired(self):
        for name in ("model_index_manager", "model_indexer", "tag_repository",
                     "notification_manager", "gpu_monitor", "settings",
                     "backend_config_store", "model_lifecycle",
                     "backend_registry", "backend_model_indexer",
                     "user_group_repository", "media_indexer",
                     "generation_status_tracker", "model_repository",
                     "model_collection_repository"):
            with self.subTest(service=name):
                self.assertIn(name, self.kwargs, f"AutomationServices is missing {name}=")


if __name__ == "__main__":
    unittest.main()
