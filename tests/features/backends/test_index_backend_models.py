from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER
from src.features.backends.routes import BackendController
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from tests.features.models.test_indexing_scheduling import (
    CountingScanner,
    OpenPlugins,
    ThreadSpawner,
)


@pytest.mark.asyncio
async def test_reindexing_the_native_local_backend_actually_runs_the_scan():
    scanner = CountingScanner()
    spawner = ThreadSpawner()
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=OpenPlugins(),
        scanner=scanner,
        native_availability_projector=reconciler,
        spawn=spawner,
    )
    backend = MagicMock()
    backend.name = "Local"
    backend.engine = NATIVE_ENGINE
    backend.config.driver = NATIVE_LOCAL_DRIVER
    registry = MagicMock()
    registry.get_backend.return_value = backend
    controller = BackendController(MagicMock(), registry, model_indexing_coordinator=coordinator)

    response = await controller.index_backend_models("native")

    assert response.success is True
    spawner.join()
    status = coordinator.status()
    assert status["state"] == "done"
    assert status["trigger"] == "admin_reindex"
    assert scanner.calls == 1
