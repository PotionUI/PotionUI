"""Tests for ImageProcessor class."""

import pytest
import tempfile
import shutil
from pathlib import Path
from PIL import Image
import io

from src.features.media.image_processor import ImageProcessor


class TestImageProcessor:
    """Tests for ImageProcessor."""

    @pytest.fixture
    def processor(self):
        """Create ImageProcessor instance."""
        return ImageProcessor()

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_image_path(self, temp_dir):
        """Create a sample test image file."""
        img = Image.new('RGB', (200, 100), color='blue')
        img_path = temp_dir / "test.png"
        img.save(img_path)
        return img_path

    @pytest.fixture
    def sample_jpeg_path(self, temp_dir):
        """Create a sample JPEG test image file."""
        img = Image.new('RGB', (200, 100), color='red')
        img_path = temp_dir / "test.jpg"
        img.save(img_path)
        return img_path

    # Test resize_image method
    def test_resize_image_width_only(self, processor, sample_image_path):
        """Test resize_image with width only maintains aspect ratio."""
        result = processor.resize_image(sample_image_path, width=100)

        # Load result and check dimensions
        img = Image.open(io.BytesIO(result))
        assert img.width == 100
        assert img.height == 50  # Original is 200x100, half width = half height

    def test_resize_image_height_only(self, processor, sample_image_path):
        """Test resize_image with height only maintains aspect ratio."""
        result = processor.resize_image(sample_image_path, height=50)

        # Load result and check dimensions
        img = Image.open(io.BytesIO(result))
        assert img.height == 50
        assert img.width == 100  # Original is 200x100, half height = half width

    def test_resize_image_both_dimensions(self, processor, sample_image_path):
        """Test resize_image with both dimensions."""
        result = processor.resize_image(sample_image_path, width=50, height=25)

        # Load result and check dimensions
        img = Image.open(io.BytesIO(result))
        assert img.width == 50
        assert img.height == 25

    def test_resize_image_no_dimensions(self, processor, sample_image_path):
        """Test resize_image without dimensions returns original size."""
        result = processor.resize_image(sample_image_path)

        # Load result and check dimensions
        img = Image.open(io.BytesIO(result))
        assert img.width == 200
        assert img.height == 100

    def test_resize_image_nonexistent_file(self, processor):
        """Test resize_image raises ValueError for nonexistent file."""
        with pytest.raises(ValueError, match="File not found"):
            processor.resize_image(Path("/nonexistent/file.png"), width=100)

    def test_resize_image_jpeg_format(self, processor, sample_jpeg_path):
        """Test resize_image preserves JPEG format."""
        result = processor.resize_image(sample_jpeg_path, width=100)

        # Should be able to open as image
        img = Image.open(io.BytesIO(result))
        assert img is not None
        assert img.width == 100

    # Test generate_thumbnail method
    def test_generate_thumbnail_default_width(self, processor, sample_image_path):
        """Test generate_thumbnail uses default width."""
        result = processor.generate_thumbnail(sample_image_path)

        img = Image.open(io.BytesIO(result))
        assert img.width == 150  # Default thumbnail width

    def test_generate_thumbnail_custom_width(self, processor, sample_image_path):
        """Test generate_thumbnail with custom width."""
        result = processor.generate_thumbnail(sample_image_path, width=100)

        img = Image.open(io.BytesIO(result))
        assert img.width == 100

    # Test get_image_dimensions method
    def test_get_image_dimensions(self, processor, sample_image_path):
        """Test get_image_dimensions returns correct dimensions."""
        width, height = processor.get_image_dimensions(sample_image_path)
        assert width == 200
        assert height == 100

    def test_get_image_dimensions_nonexistent(self, processor):
        """Test get_image_dimensions raises ValueError for nonexistent file."""
        with pytest.raises(ValueError, match="File not found"):
            processor.get_image_dimensions(Path("/nonexistent/file.png"))

    # Test _calculate_dimensions method
    def test_calculate_dimensions_width_only(self, processor):
        """Test _calculate_dimensions with width only."""
        result = processor._calculate_dimensions(200, 100, 100, None)
        assert result == (100, 50)

    def test_calculate_dimensions_height_only(self, processor):
        """Test _calculate_dimensions with height only."""
        result = processor._calculate_dimensions(200, 100, None, 50)
        assert result == (100, 50)

    def test_calculate_dimensions_both(self, processor):
        """Test _calculate_dimensions with both dimensions."""
        result = processor._calculate_dimensions(200, 100, 50, 25)
        assert result == (50, 25)

    def test_calculate_dimensions_none(self, processor):
        """Test _calculate_dimensions with no target dimensions."""
        result = processor._calculate_dimensions(200, 100, None, None)
        assert result == (200, 100)

    # Test _get_save_format method
    def test_get_save_format_jpeg(self, processor):
        """Test _get_save_format for JPEG."""
        assert processor._get_save_format('.jpg') == 'JPEG'
        assert processor._get_save_format('.jpeg') == 'JPEG'
        assert processor._get_save_format('.JPG') == 'JPEG'

    def test_get_save_format_png(self, processor):
        """Test _get_save_format for PNG."""
        assert processor._get_save_format('.png') == 'PNG'
        assert processor._get_save_format('.PNG') == 'PNG'

    def test_get_save_format_webp(self, processor):
        """Test _get_save_format for WebP."""
        assert processor._get_save_format('.webp') == 'WEBP'

    def test_get_save_format_bmp(self, processor):
        """Test _get_save_format for BMP."""
        assert processor._get_save_format('.bmp') == 'BMP'

    def test_get_save_format_gif(self, processor):
        """Test _get_save_format for GIF."""
        assert processor._get_save_format('.gif') == 'GIF'

    def test_get_save_format_unknown(self, processor):
        """Test _get_save_format defaults to JPEG for unknown."""
        assert processor._get_save_format('.xyz') == 'JPEG'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
