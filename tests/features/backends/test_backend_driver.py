"""Tests for the backend `driver` discriminator (migration 119).

`engine` stays the preset-facing protocol (`native`, `comfyui`, ...) and every
selection/default/priority rule (`BackendRegistry.select_backend_for_generation`,
`BackendConfigStore.get_default_backend`) stays keyed on it. `driver` is the
narrower, registry-internal discriminator that decides which registered
implementation class actually executes a row: an engine that only ever
registers once (the common case, e.g. `comfyui`) has exactly one driver -
itself - by the "engine-only registration" compatibility contract; `native` is
the one engine with more than one (`native.local`, auto-provisioned and
singleton; `native.remote`, user-creatable, not yet implemented).

See docs/backends.md and migration 119.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

from fastapi import HTTPException

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.features.backends.records import Backend
from src.features.backends.repository import BackendRepository
from src.features.backends.backend_config import (
    BackendConfigStore,
    BaseBackendConfig,
    NativeBackendConfig,
    NATIVE_ENGINE,
    NATIVE_LOCAL_DRIVER,
)
from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.routes import BackendController


class FakeEngineConfig(BaseBackendConfig):
    """Stands in for an engine-only plugin registration (e.g. comfyui)."""
    engine: str = "fake-engine"


class TestEngineOnlyRegistrationDefaultsDriverToEngine(unittest.TestCase):
    """The compatibility contract: a config that doesn't set `driver` gets it
    from `engine`, so a plugin that registers by engine name alone (never
    changing its manifest) keeps working."""

    def test_driver_defaults_to_engine_name(self):
        cfg = FakeEngineConfig(id="x", name="X", engine="comfyui")

        self.assertEqual(cfg.driver, "comfyui")

    def test_explicit_driver_is_preserved(self):
        cfg = FakeEngineConfig(id="x", name="X", engine="native", driver="native.remote")

        self.assertEqual(cfg.driver, "native.remote")

    def test_native_backend_config_defaults_to_native_local(self):
        cfg = NativeBackendConfig(id="native", name="Local Generation")

        self.assertEqual(cfg.engine, NATIVE_ENGINE)
        self.assertEqual(cfg.driver, NATIVE_LOCAL_DRIVER)


class TestRegistryInstantiatesByDriver(unittest.TestCase):
    """BackendRegistry._create_backend_instance resolves the implementation
    class by config.driver, not config.engine."""

    def _registry(self, registered_backend_types):
        registry = BackendRegistry.__new__(BackendRegistry)
        registry._registered_backend_types = registered_backend_types
        registry.generation_engine_factory = lambda: Mock()
        return registry

    def test_native_engine_resolves_via_its_native_local_driver(self):
        from src.features.backends.native_backend import NativeBackend

        registry = self._registry({NATIVE_LOCAL_DRIVER: NativeBackend})
        cfg = NativeBackendConfig(id="native", name="Local Generation")

        backend = registry._create_backend_instance(cfg)

        self.assertIsInstance(backend, NativeBackend)

    def test_engine_only_registration_resolves_via_the_engine_name_as_driver(self):
        class PluginBackend:
            def __init__(self, backend_config):
                self.config = backend_config

        registry = self._registry({"comfyui": PluginBackend})
        cfg = FakeEngineConfig(id="c1", name="Comfy", engine="comfyui")

        backend = registry._create_backend_instance(cfg)

        self.assertIsInstance(backend, PluginBackend)

    def test_unknown_driver_raises_a_clear_error_naming_the_driver(self):
        registry = self._registry({})
        cfg = FakeEngineConfig(id="x", name="X", engine="native", driver="native.remote")

        with self.assertRaises(ValueError) as ctx:
            registry._create_backend_instance(cfg)

        self.assertIn("native.remote", str(ctx.exception))


class TestSingletonScopedToDriverNotEngine(unittest.TestCase):
    """engine_singleton blocks creating a second backend for a singleton
    DRIVER's config class - not for every backend sharing an engine name."""

    def _manager(self, registered_config_types):
        repo = Mock()
        repo.get_all.return_value = []
        return BackendConfigStore(
            backend_repository=repo,
            registered_config_types=registered_config_types,
        )

    def test_add_backend_rejects_a_second_native_local(self):
        manager = self._manager({NATIVE_LOCAL_DRIVER: NativeBackendConfig})
        # Loading seeds the auto-provisioned native.local row (id="native").
        manager.get_backends()

        with self.assertRaises(ValueError):
            manager.add_backend(NativeBackendConfig(id="native-2", name="Second Local GPU"))

    def test_add_backend_allows_a_non_singleton_driver_of_the_same_engine(self):
        """A hypothetical native.remote driver (engine_singleton=False) is
        creatable freely, unlike native.local - the block is per-driver."""

        class NativeRemoteConfig(BaseBackendConfig):
            engine: str = NATIVE_ENGINE
            driver: str = "native.remote"
            engine_singleton: ClassVar[bool] = False

        manager = self._manager({
            NATIVE_LOCAL_DRIVER: NativeBackendConfig,
            "native.remote": NativeRemoteConfig,
        })
        manager.get_backends()  # seeds native.local

        manager.add_backend(NativeRemoteConfig(id="remote-1", name="Remote Worker"))

        ids = {b.id for b in manager.get_backends()}
        self.assertIn("native", ids)       # native.local, untouched
        self.assertIn("remote-1", ids)     # native.remote, newly created


class TestSecondNativeRowCoexistsAtTheRepositoryLayer(unittest.TestCase):
    """No implementation is registered for native.remote yet, so it can't be
    created through BackendConfigStore.add_backend (nothing to validate
    against) - but the ROW can exist at the repository layer, exactly like a
    row for a disabled plugin's engine does today. BackendConfigStore skips
    it gracefully (mirrors the disabled-plugin path) without disturbing the
    auto-provisioned native.local singleton.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
            patch("src.features.backends.repository.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM backends")

        self.repo = BackendRepository()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def test_native_remote_row_can_be_created_alongside_native_local(self):
        self.repo.create(Backend(
            id="native", name="Local Generation", engine=NATIVE_ENGINE,
            driver=NATIVE_LOCAL_DRIVER, enabled=True, is_default=True, config={},
        ))
        self.repo.create(Backend(
            id="remote-1", name="Remote Worker", engine=NATIVE_ENGINE,
            driver="native.remote", enabled=True, is_default=False, config={},
        ))

        rows = self.repo.get_all()

        self.assertEqual({r.id for r in rows}, {"native", "remote-1"})
        self.assertEqual(
            {r.id: r.driver for r in rows},
            {"native": NATIVE_LOCAL_DRIVER, "remote-1": "native.remote"},
        )

    def test_config_manager_skips_the_unimplemented_driver_without_crashing(self):
        self.repo.create(Backend(
            id="remote-1", name="Remote Worker", engine=NATIVE_ENGINE,
            driver="native.remote", enabled=True, is_default=False, config={},
        ))

        manager = BackendConfigStore(
            backend_repository=self.repo,
            registered_config_types={NATIVE_LOCAL_DRIVER: NativeBackendConfig},
        )

        backends = manager.get_backends()

        ids = {b.id for b in backends}
        self.assertIn("native", ids)        # auto-provisioned native.local
        self.assertNotIn("remote-1", ids)   # unimplemented driver, skipped
        # The row itself is untouched in the repository - not deleted.
        self.assertIsNotNone(self.repo.get_by_id("remote-1"))


class TestEngineDescriptorsAreDriverAware(unittest.TestCase):
    """get_engine_descriptors() reports one descriptor per DRIVER, not deduped
    by engine - native.local and native.remote both speak engine="native", so
    a dedup keyed on engine would silently drop whichever driver loses (the
    admin "Add Backend" flow could never reach native.remote's own fields -
    base_url/worker_token/timeouts - through this endpoint before this)."""

    def test_native_remote_is_not_swallowed_by_native_local(self):
        from src.features.backends.backend_config import NativeRemoteBackendConfig

        registry = BackendRegistry.__new__(BackendRegistry)
        registry._registered_config_types = {
            NATIVE_LOCAL_DRIVER: NativeBackendConfig,
            "native.remote": NativeRemoteBackendConfig,
        }

        descriptors = {d["driver"]: d for d in registry.get_engine_descriptors()}

        self.assertEqual(set(descriptors), {NATIVE_LOCAL_DRIVER, "native.remote"})

        local = descriptors[NATIVE_LOCAL_DRIVER]
        self.assertEqual(local["engine"], NATIVE_ENGINE)
        self.assertTrue(local["singleton"])
        self.assertFalse(local["creatable"])

        remote = descriptors["native.remote"]
        self.assertEqual(remote["engine"], NATIVE_ENGINE)
        self.assertFalse(remote["singleton"])
        self.assertTrue(remote["creatable"])
        field_names = {f["name"] for f in remote["fields"]}
        self.assertEqual(
            field_names,
            {"base_url", "worker_token", "connect_timeout_seconds", "request_timeout_seconds"},
        )
        worker_token_field = next(f for f in remote["fields"] if f["name"] == "worker_token")
        self.assertTrue(worker_token_field["secret"])

    def test_single_driver_engine_still_reports_exactly_one_descriptor(self):
        class ComfyLikeConfig(BaseBackendConfig):
            engine: str = "comfyui"

        registry = BackendRegistry.__new__(BackendRegistry)
        registry._registered_config_types = {"comfyui": ComfyLikeConfig}

        descriptors = registry.get_engine_descriptors()

        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0]["driver"], "comfyui")
        self.assertEqual(descriptors[0]["engine"], "comfyui")


class TestRequireLocalBackendCapabilityGating(unittest.TestCase):
    """The native-engine "Optimizations" panel gates on the `is_local`
    capability of the backend's registered driver, not on `engine == 'native'`
    - a backend can speak the native engine without running in this process."""

    def _controller(self, backend_config):
        bcm = Mock()
        bcm.get_backend = Mock(return_value=backend_config)
        registry = Mock()
        registry.backend_config_store = bcm
        return BackendController(Mock(), registry, Mock())

    def test_native_local_backend_passes(self):
        controller = self._controller(NativeBackendConfig(id="native", name="Local Generation"))

        result = controller._require_local_backend("native")

        self.assertEqual(result.driver, NATIVE_LOCAL_DRIVER)

    def test_comfyui_backend_is_rejected(self):
        controller = self._controller(FakeEngineConfig(id="c1", name="Comfy", engine="comfyui"))

        with self.assertRaises(HTTPException) as ctx:
            controller._require_local_backend("c1")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_native_engine_but_non_local_driver_is_rejected(self):
        """A future native.remote driver speaks engine=native but does not run
        locally - the old `engine != NATIVE_ENGINE` check would have wrongly
        let this through."""

        class NativeRemoteConfig(BaseBackendConfig):
            engine: str = NATIVE_ENGINE
            driver: str = "native.remote"
            is_local: ClassVar[bool] = False

        controller = self._controller(NativeRemoteConfig(id="nr1", name="Native Remote"))

        with self.assertRaises(HTTPException) as ctx:
            controller._require_local_backend("nr1")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_backend_is_404(self):
        controller = self._controller(None)

        with self.assertRaises(HTTPException) as ctx:
            controller._require_local_backend("nope")

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
