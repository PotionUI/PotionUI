from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import Mock

import pytest
from pydantic import Field

from src.features.backends import native_backend
from src.features.backends.backend_config import BackendConfigStore, BaseBackendConfig
from src.features.backends.in_process_backend import InProcessBackend
from src.features.backends.native_backend import NativeBackend
from src.features.backends.records import Backend
from src.features.backends.routes import BackendController
from src.platform.plugins import runtime_registries
from src.platform.runtime.gpu import DeviceIdentity
from src.plugin_api import backends as api

INSTANCE_KEYS = {"execution_device", "max_concurrent_runs", "vram_total_gb", "vram_free_gb"}


class FarmConfig(BaseBackendConfig):
    engine: str = "farm"
    api_key: Optional[str] = Field(default=None, json_schema_extra={"secret": True})


class FarmBackend(InProcessBackend):
    execution_device = "remote"

    @property
    def max_concurrent_runs(self) -> int:
        return 4

    async def health_check(self) -> Dict[str, Any]:
        return {"status": "healthy"}

    async def get_system_info(self) -> Dict[str, Any]:
        return {}


class InMemoryBackendRepository:
    def __init__(self, rows):
        self.rows = list(rows)

    def get_all(self):
        return list(self.rows)

    def create(self, row):
        self.rows.append(row)


def _row(backend_id, engine, driver, *, enabled=True, is_default=False, **config):
    return Backend(
        id=backend_id, name=backend_id.title(), engine=engine, driver=driver,
        enabled=enabled, is_default=is_default, config=config,
    )


class World:
    def __init__(self, native_device="cuda:0", monitored_uuid="GPU-a"):
        repository = InMemoryBackendRepository([
            _row("native", "native", "native.local", is_default=True,
                 device=native_device, dtype="bfloat16", gpu_max_vram=20, priority=5),
            _row("farm", "farm", "farm", is_default=True, api_key="k-123", priority=3),
            _row("farm-idle", "farm", "farm", priority=1),
            _row("farm-off", "farm", "farm", enabled=False, priority=9),
        ])
        self.store = BackendConfigStore(backend_repository=repository, registered_config_types={"farm": FarmConfig})
        self.instances = {
            "native": NativeBackend(backend_config=self.store.get_backend("native"), generation_engine=Mock()),
            "farm": FarmBackend(self.store.get_backend("farm")),
        }
        self.registry = SimpleNamespace(backend_config_store=self.store, get_backend=self.instances.get)
        self.gpu_monitor = SimpleNamespace(
            available=True,
            device_identity=DeviceIdentity(uuid=monitored_uuid),
            get_free_vram=lambda: 6144,
            get_total_vram=lambda: 24576,
        )
        self.container = SimpleNamespace(backend_registry=self.registry, gpu_monitor=self.gpu_monitor)


@pytest.fixture(autouse=True)
def cuda_identity(monkeypatch):
    monkeypatch.setattr(
        native_backend, "_cuda_device_identity",
        lambda index: DeviceIdentity(uuid="GPU-a") if index == 0 else None,
    )


@pytest.fixture
def install(monkeypatch):
    def _install(world: World) -> World:
        monkeypatch.setattr(runtime_registries, "_container", world.container)
        return world
    return _install


def _by_id(entries):
    return {entry["id"]: entry for entry in entries}


@pytest.mark.asyncio
async def test_each_entry_is_what_the_enabled_backends_route_returns_plus_its_instance_facts(install):
    world = install(World())

    route = await BackendController(Mock(), world.registry).get_enabled_backends()
    listed = api.list_backends()

    assert [{k: v for k, v in entry.items() if k not in INSTANCE_KEYS} for entry in listed] == route.data
    assert all(INSTANCE_KEYS <= set(entry) for entry in listed)


def test_lists_enabled_backends_only_highest_priority_first(install):
    install(World())

    assert [entry["id"] for entry in api.list_backends()] == ["native", "farm", "farm-idle"]


def test_a_local_native_backend_reports_its_gpu_and_the_monitored_vram(install):
    install(World())

    native = _by_id(api.list_backends())["native"]

    assert native["engine"] == "native"
    assert native["driver"] == "native.local"
    assert native["enabled"] is True
    assert native["is_default"] is True
    assert native["gpu_max_vram"] == 20
    assert native["execution_device"] == "this_host_gpu"
    assert native["max_concurrent_runs"] == 1
    assert native["vram_total_gb"] == 24.0
    assert native["vram_free_gb"] == 6.0


def test_a_remote_backend_reports_its_capacity_and_no_vram_and_keeps_its_secret_redacted(install):
    install(World())

    farm = _by_id(api.list_backends())["farm"]

    assert farm["execution_device"] == "remote"
    assert farm["max_concurrent_runs"] == 4
    assert farm["vram_total_gb"] is None
    assert farm["vram_free_gb"] is None
    assert "api_key" not in farm
    assert farm["has_api_key"] is True


def test_an_enabled_backend_that_is_not_running_reports_nothing_about_its_instance(install):
    install(World())

    idle = _by_id(api.list_backends())["farm-idle"]

    assert idle["execution_device"] == "unestablished"
    assert idle["max_concurrent_runs"] is None
    assert idle["vram_total_gb"] is None
    assert idle["vram_free_gb"] is None


def test_vram_is_unknown_when_the_monitored_gpu_is_not_the_native_backends_own(install):
    install(World(monitored_uuid="GPU-b"))

    native = _by_id(api.list_backends())["native"]

    assert native["execution_device"] == "this_host_gpu"
    assert native["vram_total_gb"] is None
    assert native["vram_free_gb"] is None


def test_a_native_backend_on_cpu_reports_no_gpu_and_no_vram(install):
    install(World(native_device="cpu"))

    native = _by_id(api.list_backends())["native"]

    assert native["execution_device"] == "no_gpu"
    assert native["vram_total_gb"] is None
    assert native["vram_free_gb"] is None
