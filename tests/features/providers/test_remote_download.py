"""Tests for resolve_url_remote_download."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import asyncio

from src.features.providers import ProviderCapability, ProviderMetadata, RemoteDownloadRef
from src.features.providers.remote_download import (
    ProviderCapabilityMissingError,
    resolve_url_remote_download,
)


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


if __name__ == "__main__":
    unittest.main()
