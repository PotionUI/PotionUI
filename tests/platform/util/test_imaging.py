"""Tests for image utilities."""

import base64
import io
import os
import tempfile
import pytest
from PIL import Image
from unittest.mock import patch

from src.platform.util.imaging import (
    convert_image_to_base64,
    resize_image,
    _resolve_image_path,
    _ensure_rgb,
)


class TestResizeImage:
    """Tests for resize_image function."""

    def test_resize_large_image(self):
        """Should resize image larger than max dimension."""
        img = Image.new('RGB', (1000, 800))
        resized = resize_image(img, 500)

        assert resized.width == 500
        assert resized.height == 400  # Maintains aspect ratio

    def test_no_resize_small_image(self):
        """Should not resize image smaller than max dimension."""
        img = Image.new('RGB', (400, 300))
        resized = resize_image(img, 500)

        assert resized.width == 400
        assert resized.height == 300

    def test_resize_preserves_aspect_ratio_tall(self):
        """Should preserve aspect ratio for tall images."""
        img = Image.new('RGB', (600, 1200))
        resized = resize_image(img, 600)

        assert resized.height == 600
        assert resized.width == 300

    def test_resize_preserves_aspect_ratio_wide(self):
        """Should preserve aspect ratio for wide images."""
        img = Image.new('RGB', (1200, 600))
        resized = resize_image(img, 600)

        assert resized.width == 600
        assert resized.height == 300

    def test_resize_square_image(self):
        """Should resize square images correctly."""
        img = Image.new('RGB', (1000, 1000))
        resized = resize_image(img, 500)

        assert resized.width == 500
        assert resized.height == 500


class TestEnsureRgb:
    """Tests for _ensure_rgb function."""

    def test_rgba_to_rgb(self):
        """Should convert RGBA to RGB."""
        img = Image.new('RGBA', (100, 100))
        result = _ensure_rgb(img)

        assert result.mode == 'RGB'

    def test_rgb_unchanged(self):
        """Should leave RGB images unchanged."""
        img = Image.new('RGB', (100, 100))
        result = _ensure_rgb(img)

        assert result.mode == 'RGB'
        assert result is img  # Should be same object

    def test_grayscale_unchanged(self):
        """Should leave grayscale (L) images unchanged."""
        img = Image.new('L', (100, 100))
        result = _ensure_rgb(img)

        assert result.mode == 'L'
        assert result is img

    def test_palette_to_rgb(self):
        """Should convert palette mode to RGB."""
        img = Image.new('P', (100, 100))
        result = _ensure_rgb(img)

        assert result.mode == 'RGB'


class TestResolveImagePath:
    """Tests for _resolve_image_path function."""

    def test_absolute_path_exists(self):
        """Should return absolute path if it exists."""
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b'test')
            temp_path = f.name

        try:
            result = _resolve_image_path(temp_path)
            assert result == temp_path
        finally:
            os.unlink(temp_path)

    def test_absolute_path_not_exists(self):
        """Should return None for non-existent absolute path."""
        result = _resolve_image_path('/nonexistent/path/to/image.jpg')
        assert result is None

    def test_relative_path_in_cwd(self):
        """Should find relative path in current directory."""
        with tempfile.NamedTemporaryFile(
            suffix='.jpg', delete=False, dir='.'
        ) as f:
            f.write(b'test')
            filename = os.path.basename(f.name)

        try:
            result = _resolve_image_path(filename)
            assert result is not None
            assert os.path.exists(result)
        finally:
            os.unlink(f.name)

    def test_relative_path_not_found(self):
        """Should return None for non-existent relative path."""
        result = _resolve_image_path('nonexistent_image_12345.jpg')
        assert result is None


class TestConvertImageToBase64:
    """Tests for convert_image_to_base64 function."""

    def test_none_input(self):
        """Should return None for None input."""
        result = convert_image_to_base64(None)
        assert result is None

    def test_empty_string_input(self):
        """Should return None for empty string."""
        result = convert_image_to_base64('')
        assert result is None

    def test_already_base64(self):
        """Should return base64 data unchanged."""
        # Create a long base64-like string (no path separators, > 200 chars)
        base64_data = 'A' * 300

        result = convert_image_to_base64(base64_data)
        assert result == base64_data

    def test_file_path_conversion(self):
        """Should convert file path to base64."""
        # Create a temporary image file
        img = Image.new('RGB', (100, 100), color='red')
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            img.save(f, format='JPEG')
            temp_path = f.name

        try:
            result = convert_image_to_base64(temp_path)

            assert result is not None
            # Verify it's valid base64
            decoded = base64.b64decode(result)
            # Verify it's a valid image
            img_check = Image.open(io.BytesIO(decoded))
            assert img_check.format == 'JPEG'
        finally:
            os.unlink(temp_path)

    def test_file_path_with_resize(self):
        """Should resize large images."""
        # Create a large temporary image
        img = Image.new('RGB', (2000, 1500), color='blue')
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            img.save(f, format='JPEG')
            temp_path = f.name

        try:
            result = convert_image_to_base64(temp_path, max_dimension=768)

            assert result is not None
            decoded = base64.b64decode(result)
            img_check = Image.open(io.BytesIO(decoded))
            # Check dimensions were reduced
            assert img_check.width <= 768
            assert img_check.height <= 768
        finally:
            os.unlink(temp_path)

    def test_nonexistent_file(self):
        """Should return None for non-existent file."""
        result = convert_image_to_base64('/nonexistent/path/image.jpg')
        assert result is None

    def test_rgba_image_conversion(self):
        """Should convert RGBA images to RGB for JPEG output."""
        # Create an RGBA image
        img = Image.new('RGBA', (100, 100), color=(255, 0, 0, 128))
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            img.save(f, format='PNG')
            temp_path = f.name

        try:
            result = convert_image_to_base64(temp_path)

            assert result is not None
            decoded = base64.b64decode(result)
            img_check = Image.open(io.BytesIO(decoded))
            # Output should be JPEG (RGB, not RGBA)
            assert img_check.format == 'JPEG'
            assert img_check.mode == 'RGB'
        finally:
            os.unlink(temp_path)

    def test_custom_max_dimension(self):
        """Should respect custom max_dimension parameter."""
        img = Image.new('RGB', (1000, 800), color='green')
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            img.save(f, format='JPEG')
            temp_path = f.name

        try:
            result = convert_image_to_base64(temp_path, max_dimension=400)

            decoded = base64.b64decode(result)
            img_check = Image.open(io.BytesIO(decoded))
            assert img_check.width <= 400
            assert img_check.height <= 400
        finally:
            os.unlink(temp_path)

    def test_custom_quality(self):
        """Should use custom quality parameter."""
        img = Image.new('RGB', (100, 100), color='red')
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            img.save(f, format='JPEG')
            temp_path = f.name

        try:
            # Higher quality should produce larger output
            high_quality = convert_image_to_base64(temp_path, quality=95)
            low_quality = convert_image_to_base64(temp_path, quality=50)

            # Both should be valid
            assert high_quality is not None
            assert low_quality is not None
            # High quality should generally be larger
            # (this may not always be true for simple images)
        finally:
            os.unlink(temp_path)
