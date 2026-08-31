"""Tests for the resolve_model_remote_download core op."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncio

from src.features.models.records import Model, ModelInfo as ModelProviderLink
from src.features.providers import ProviderCapability, ProviderMetadata, RemoteDownloadRef
from src.features.providers.remote_download import (
    ModelNotLinkedError,
    ProviderCapabilityMissingError,
    ProviderResolutionFailedError,
    RemoteDownloadSizeMismatchError,
    providers_support_hash_lookup,
    resolve_model_remote_download,
    resolve_url_remote_download,
)


def _model(providers, **kwargs):
    return Model(id="model-1", filename="model.safetensors", providers=providers, **kwargs)


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

    def test_no_provider_link_and_no_hash_capable_provider_raises(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[])
        registry = MagicMock()
        registry.get_providers_with_capability.return_value = []

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

    def test_a_usable_link_is_preferred_over_the_hash_fallback(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()], sha256="a" * 64)
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
        )
        ref = RemoteDownloadRef(url="https://cdn.example.com/via-link")
        provider.resolve_remote_download = AsyncMock(return_value=ref)
        registry = MagicMock()
        registry.get_provider.return_value = provider

        result = self._run(resolve_model_remote_download(repo, registry, "model-1"))

        self.assertIs(result, ref)
        registry.get_providers_with_capability.assert_not_called()

    def test_a_linkless_model_falls_back_to_a_hash_capable_provider(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[], sha256="b" * 64)
        provider = MagicMock()
        ref = RemoteDownloadRef(url="https://cdn.example.com/by-hash")
        provider.resolve_remote_download_by_hash = AsyncMock(return_value=ref)
        registry = MagicMock()
        registry.get_providers_with_capability.return_value = [provider]

        result = self._run(resolve_model_remote_download(repo, registry, "model-1"))

        self.assertIs(result, ref)
        provider.resolve_remote_download_by_hash.assert_awaited_once_with("b" * 64)

    def test_the_hash_fallback_hashes_an_unhashed_file_on_demand_and_persists_it(self):
        content = b"model bytes" * 1000
        expected_sha256 = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "model.safetensors"
            file_path.write_bytes(content)

            repo = MagicMock()
            repo.get_by_id.return_value = _model(providers=[], file_path=str(file_path))
            provider = MagicMock()
            ref = RemoteDownloadRef(url="https://cdn.example.com/by-hash")
            provider.resolve_remote_download_by_hash = AsyncMock(return_value=ref)
            registry = MagicMock()
            registry.get_providers_with_capability.return_value = [provider]

            result = self._run(resolve_model_remote_download(repo, registry, "model-1"))

            self.assertIs(result, ref)
            provider.resolve_remote_download_by_hash.assert_awaited_once_with(expected_sha256)
            repo.update_digest.assert_called_once_with("model-1", sha256=expected_sha256, file_size=len(content))

    def test_a_resolved_size_that_contradicts_the_model_row_is_rejected(self):
        repo = MagicMock()
        repo.get_by_id.return_value = _model(providers=[_link()], file_size=999)
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
        )
        ref = RemoteDownloadRef(url="https://cdn.example.com/signed", size_hint=123)
        provider.resolve_remote_download = AsyncMock(return_value=ref)
        registry = MagicMock()
        registry.get_provider.return_value = provider

        with self.assertRaises(RemoteDownloadSizeMismatchError):
            self._run(resolve_model_remote_download(repo, registry, "model-1"))


def _url_provider(resolved_url, leftover_headers, claims_resolved=False):
    provider = MagicMock()
    provider.get_metadata.return_value = ProviderMetadata(
        id="civitai", name="CivitAI", description="", website="",
        capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
    )

    async def prepare_download(session, url, headers):
        headers.update(leftover_headers)
        return resolved_url

    provider.prepare_download = prepare_download
    provider.matches_download_url = MagicMock(return_value=claims_resolved)
    provider.resolve_remote_url = AsyncMock(side_effect=NotImplementedError())
    return provider


class TestResolveUrlRemoteDownload(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_claiming_provider_passes_the_url_through(self):
        ref = self._run(resolve_url_remote_download(None, MagicMock(), "https://example.com/file.safetensors"))
        self.assertEqual(ref.url, "https://example.com/file.safetensors")
        self.assertEqual(ref.headers, {})

    def test_provider_without_the_capability_refuses(self):
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="civitai", name="CivitAI", description="", website="",
            capabilities=[ProviderCapability.DOWNLOAD_URL],
        )
        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

    def test_a_leftover_user_agent_is_not_a_credential(self):
        provider = _url_provider(
            "https://cdn.example.com/signed",
            {"User-Agent": "Mozilla/5.0"},
        )
        ref = self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))
        self.assertEqual(ref.url, "https://cdn.example.com/signed")
        self.assertEqual(ref.headers, {"User-Agent": "Mozilla/5.0"})

    def test_a_leftover_authorization_header_refuses(self):
        provider = _url_provider(
            "https://cdn.example.com/signed",
            {"User-Agent": "Mozilla/5.0", "Authorization": "Bearer secret"},
        )
        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

    def test_an_unknown_leftover_header_refuses(self):
        provider = _url_provider(
            "https://cdn.example.com/signed",
            {"X-Auth-Token": "secret"},
        )
        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

    def test_a_provider_resolved_ref_is_preferred_even_when_the_url_still_matches_the_provider(self):
        provider = _url_provider(
            "https://civitai.com/api/download/models/1",
            {},
            claims_resolved=True,
        )
        resolved = RemoteDownloadRef(url="https://civitai.com/api/download/models/1")
        provider.resolve_remote_url = AsyncMock(return_value=resolved)

        ref = self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

        self.assertIs(ref, resolved)

    def test_a_provider_resolved_ref_carrying_a_credential_header_refuses(self):
        provider = _url_provider("https://civitai.com/api/download/models/1", {})
        provider.resolve_remote_url = AsyncMock(
            return_value=RemoteDownloadRef(
                url="https://civitai.com/api/download/models/1",
                headers={"Authorization": "Bearer secret"},
            )
        )

        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

    def test_a_provider_whose_resolve_remote_url_is_unimplemented_falls_back_to_prepare_download(self):
        provider = _url_provider(
            "https://cdn.example.com/signed",
            {"User-Agent": "Mozilla/5.0"},
        )
        provider.resolve_remote_url = AsyncMock(side_effect=NotImplementedError())

        ref = self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))

        self.assertEqual(ref.url, "https://cdn.example.com/signed")
        self.assertEqual(ref.headers, {"User-Agent": "Mozilla/5.0"})

    def test_a_url_still_on_the_providers_host_refuses(self):
        provider = _url_provider(
            "https://civitai.com/api/download/models/1?token=secret",
            {},
            claims_resolved=True,
        )
        with self.assertRaises(ProviderCapabilityMissingError):
            self._run(resolve_url_remote_download(provider, MagicMock(), "https://civitai.com/api/download/models/1"))


class TestProvidersSupportHashLookup(unittest.TestCase):
    def test_true_when_a_provider_advertises_the_capability(self):
        registry = MagicMock()
        registry.get_providers_with_capability.return_value = [MagicMock()]

        self.assertTrue(providers_support_hash_lookup(registry))

    def test_false_when_no_provider_advertises_the_capability(self):
        registry = MagicMock()
        registry.get_providers_with_capability.return_value = []

        self.assertFalse(providers_support_hash_lookup(registry))


if __name__ == "__main__":
    unittest.main()
