import tempfile
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.backend import CloudBackend
from src.features.cloud.capabilities import CloudCapabilities
from src.features.cloud.catalog import CloudCatalog
from src.features.models.access_policy import ModelAccessPolicy
from src.features.cloud.registration import register_cloud_provider
from src.features.cloud.scope_repository import ModelPresetScopeRepository
from src.features.cloud.scopes import CloudModelScopes
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.testing.fake import FakeBehaviour, FakeCloudConfig, FakeCloudProvider, fake_specs
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.models.repository import model_repo
from src.platform.plugins.hooks import HookContext


@pytest.fixture(autouse=True)
def private_system_temp(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("systmp")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(root))
    return root


class ScriptedPluginRegistry:
    def __init__(self, *provider_classes):
        self.context = HookContext(
            hook_name="backend.register", plugin_id="test", data={"backend_types": {}, "config_types": {}}
        )
        for provider_class in provider_classes:
            register_cloud_provider(self.context, provider_class)

    def execute_hook(self, hook_name, initial_data=None):
        return self.context, True

    def get_enabled_plugins(self):
        return ()


class CloudEnv:
    def __init__(self, registry, catalog, repository, capabilities):
        self.capabilities = capabilities
        self.registry = registry
        self.catalog = catalog
        self.repository = repository

    def backend(self, backend_id="cloud-1") -> CloudBackend:
        backend = self.registry.get_backend(backend_id)
        assert isinstance(backend, CloudBackend)
        return backend

    def behaviour(self, backend_id="cloud-1") -> FakeBehaviour:
        return self.backend(backend_id).provider.behaviour

    def slug_of(self, provider_model_id: str, backend_id="cloud-1") -> str:
        for slug, known in self.repository.provider_ids(backend_id).items():
            if known == provider_model_id:
                return slug
        raise AssertionError(provider_model_id)

    def model_row(self, slug: str):
        return model_repo.get_by_identity("cloud", slug)

    async def add_backend(self, backend_id: str, name: str = "Fake cloud") -> CloudBackend:
        await self.registry.add_backend(FakeCloudConfig(id=backend_id, name=name))
        return self.backend(backend_id)


@pytest.fixture
def cloud_env(mock_db):
    registry = BackendRegistry(
        generation_engine_factory=lambda: None,
        plugin_registry=ScriptedPluginRegistry(FakeCloudProvider),
    )
    repository = CloudCatalogRepository()
    catalog = CloudCatalog(
        backend_registry=registry,
        repository=repository,
        model_repository=model_repo,
        backend_indexer=BackendModelIndexer(),
    )
    capabilities = CloudCapabilities(
        backend_registry=registry,
        repository=repository,
        model_repository=model_repo,
        model_access_policy=ModelAccessPolicy(model_repo),
    )
    presets = SimpleNamespace(presets=[], _ensure_loaded=lambda: None)
    scopes = CloudModelScopes(ModelPresetScopeRepository(), presets, model_repo)
    env = CloudEnv(registry, catalog, repository, capabilities)
    env.presets = presets
    env.scopes = scopes
    return env


@pytest.fixture
async def fake_backend(cloud_env):
    await cloud_env.add_backend("cloud-1")
    return cloud_env


@pytest.fixture
async def refreshed(fake_backend):
    await fake_backend.catalog.refresh("cloud-1")
    return fake_backend


@pytest.fixture
def container(cloud_env):
    return SimpleNamespace(
        cloud_catalog=cloud_env.catalog, cloud_capabilities=cloud_env.capabilities, cloud_model_scopes=cloud_env.scopes,
    )


def returning(specs):
    async def discover():
        return list(specs)

    return discover


@pytest.fixture
async def dotted_slug(refreshed):
    dotted = replace(fake_specs()[0], provider_model_id="fake/image-1.5", label="Fake Image 1.5")
    refreshed.backend().provider.discover = returning([dotted])
    await refreshed.catalog.refresh("cloud-1")
    (slug,) = [s for s, known in refreshed.repository.provider_ids("cloud-1").items() if known == "fake/image-1.5"]
    return slug
