"""Tests for the resolve_model_remote_download core op."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import asyncio

from src.features.models.records import Model, ModelInfo as ModelProviderLink
from src.features.providers import ProviderCapability, ProviderMetadata, RemoteDownloadRef
from src.features.providers.remote_download import (
    ModelNotLinkedError,
    ProviderCapabilityMissingError,
    ProviderResolutionFailedError,
    resolve_model_remote_download,
)


def _model(providers):
    return Model(id="model-1", filename="model.safetensors", providers=providers)


def _link(provider="civitai", provider_model_id="12345", provider_version_id="67890"):
    return ModelProviderLink(
        model_id="model-1",
        provider=provider,
        provider_model_id=provider_model_id,
        provider_version_id=provider_version_id,
    )


class TestResolveModelRemoteDownload(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_provider_link_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[])
        registry = MagicMock()

        with self.assertRaises(ModelNotLinkedError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))

    def test_missing_model_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        registry = MagicMock()

        with self.assertRaises(ModelNotLinkedError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))

    def test_provider_not_installed_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()])
        registry = MagicMock()
        registry.get_provider.return_value = None

        with self.assertRaises(ModelNotLinkedError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))

    def test_provider_lacking_capability_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()])
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.DOWNLOAD_URL],
        )
        registry = MagicMock()
        registry.get_provider.return_value = provider

        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))

    def test_provider_resolution_failure_wrapped(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()])
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
        )
        provider.resolve_remote_download = AsyncMock(side_effect=RuntimeError("boom"))
        registry = MagicMock()
        registry.get_provider.return_value = provider

        with self.assertRaises(ProviderResolutionFailedError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))

    def test_success_returns_ref(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()])
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
        )
        ref = RemoteDownloadRef(url="https://cdn.example.com/signed", headers={})
        provider.resolve_remote_download = AsyncMock(return_value=ref)
        registry = MagicMock()
        registry.get_provider.return_value = provider

        result = self._run(resolve_model_remote_download(repo, registry, "model-1"))

        self.assertIs(result, ref)
        provider.resolve_remote_download.assert_awaited_once_with("12345", "67890")


if __name__ == "__main__":
    unittest.main()
