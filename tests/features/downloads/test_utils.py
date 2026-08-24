"""
Tests for download utility functions.
"""

import pytest
from src.features.downloads.utils import (
    extract_filename_from_content_disposition,
    extract_filename_from_url,
)


class TestExtractFilenameFromContentDisposition:
    """Tests for extract_filename_from_content_disposition function."""

    def test_extract_rfc5987_utf8(self):
        """Test extracting filename with RFC 5987 UTF-8 encoding."""
        header = "attachment; filename*=UTF-8''my%20model.safetensors"
        result = extract_filename_from_content_disposition(header)
        assert result == "my model.safetensors"

    def test_extract_rfc5987_utf8_lowercase(self):
        """Test extracting filename with lowercase UTF-8."""
        header = "attachment; filename*=utf-8''model%20name.ckpt"
        result = extract_filename_from_content_disposition(header)
        assert result == "model name.ckpt"

    def test_extract_quoted_filename(self):
        """Test extracting quoted filename."""
        header = 'attachment; filename="my_model.safetensors"'
        result = extract_filename_from_content_disposition(header)
        assert result == "my_model.safetensors"

    def test_extract_unquoted_filename(self):
        """Test extracting unquoted filename."""
        header = "attachment; filename=model.safetensors"
        result = extract_filename_from_content_disposition(header)
        assert result == "model.safetensors"

    def test_extract_with_multiple_params(self):
        """Test extracting from header with multiple parameters."""
        header = 'attachment; size=12345; filename="test.bin"; type=model'
        result = extract_filename_from_content_disposition(header)
        assert result == "test.bin"

    def test_empty_header_returns_none(self):
        """Test that empty header returns None."""
        assert extract_filename_from_content_disposition("") is None
        assert extract_filename_from_content_disposition(None) is None

    def test_no_filename_returns_none(self):
        """Test header without filename returns None."""
        header = "attachment; size=12345"
        result = extract_filename_from_content_disposition(header)
        assert result is None


class TestExtractFilenameFromUrl:
    """Tests for extract_filename_from_url function."""

    def test_extract_simple_filename(self):
        """Test extracting filename from simple URL."""
        url = "https://example.com/models/my_model.safetensors"
        result = extract_filename_from_url(url)
        assert result == "my_model.safetensors"

    def test_extract_encoded_filename(self):
        """Test extracting URL-encoded filename."""
        url = "https://example.com/models/my%20model.safetensors"
        result = extract_filename_from_url(url)
        assert result == "my model.safetensors"

    def test_extract_from_cdn_disposition_param(self):
        """Test extracting from response-content-disposition query param."""
        url = "https://cdn.example.com/file?response-content-disposition=attachment%3B%20filename%3D%22model.safetensors%22"
        result = extract_filename_from_url(url)
        assert result == "model.safetensors"

    def test_no_extension_returns_none(self):
        """Test URL without file extension returns None."""
        url = "https://example.com/api/download/12345"
        result = extract_filename_from_url(url)
        assert result is None

    def test_empty_path_returns_none(self):
        """Test URL with empty path returns None."""
        url = "https://example.com/"
        result = extract_filename_from_url(url)
        assert result is None

    def test_complex_url_with_query(self):
        """Test extracting from URL with query parameters."""
        url = "https://civitai.com/api/download/models/123456?type=Model&format=SafeTensor"
        result = extract_filename_from_url(url)
        # Path is "123456" which has no extension
        assert result is None
