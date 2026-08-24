"""SSRF guard for the model download job.

`run_download_and_index` fetches a provider-supplied URL server-side, so the URL
is an SSRF vector. These tests pin the guard: only public http(s) hosts are
allowed, and a rejected URL is never requested.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.features.models.jobs import ModelJobs, is_safe_download_url


class TestIsSafeDownloadUrl:
    """Literal IPs / localhost keep these checks free of real DNS or network."""

    def test_rejects_non_http_scheme(self):
        ok, reason = is_safe_download_url("file:///etc/passwd")
        assert ok is False
        assert "scheme" in reason

    def test_rejects_ftp_scheme(self):
        ok, _ = is_safe_download_url("ftp://example.com/model.safetensors")
        assert ok is False

    def test_rejects_localhost(self):
        ok, reason = is_safe_download_url("http://localhost:8000/model.safetensors")
        assert ok is False
        assert "non-public" in reason

    def test_rejects_loopback_ip(self):
        ok, _ = is_safe_download_url("http://127.0.0.1/model.safetensors")
        assert ok is False

    def test_rejects_private_range(self):
        ok, _ = is_safe_download_url("https://10.0.0.5/model.safetensors")
        assert ok is False

    def test_rejects_cloud_metadata_endpoint(self):
        ok, reason = is_safe_download_url("http://169.254.169.254/latest/meta-data/")
        assert ok is False
        assert "non-public" in reason

    def test_rejects_url_without_host(self):
        ok, _ = is_safe_download_url("https:///model.safetensors")
        assert ok is False

    def test_allows_public_https_host(self):
        # A public literal IP: no DNS lookup, no network, and not private.
        ok, reason = is_safe_download_url("https://1.1.1.1/model.safetensors")
        assert ok is True
        assert reason == ""


class TestRunDownloadHonoursTheGuard:
    def _job(self) -> ModelJobs:
        return ModelJobs(MagicMock(), MagicMock(), MagicMock(), MagicMock())

    def test_unsafe_url_is_never_requested(self):
        job = self._job()
        with patch("requests.get") as mock_get:
            asyncio.run(job.run_download_and_index(
                name="evil",
                link="http://169.254.169.254/latest/meta-data/",
                sha256="",
            ))
        mock_get.assert_not_called()

    def test_private_url_is_never_requested(self):
        job = self._job()
        with patch("requests.get") as mock_get:
            asyncio.run(job.run_download_and_index(
                name="evil",
                link="http://127.0.0.1/model.safetensors",
                sha256="",
            ))
        mock_get.assert_not_called()


def test_type_dir_map_mirrors_the_scanner_mapping_exactly():
    # A model type present in the scanner but absent here downloads into
    # 'checkpoints/', gets indexed under the wrong type, and every picker
    # filtering on the right type comes up empty - detection_segm did exactly
    # that. The two maps must be exact inverses so a new type cannot land on
    # only one side.
    from src.features.models.jobs import TYPE_DIR_MAP
    from src.features.models.indexer import ModelScanner

    inverted_scanner = {t: d for d, t in ModelScanner.MODEL_TYPE_MAPPING.items()}
    assert TYPE_DIR_MAP == inverted_scanner
