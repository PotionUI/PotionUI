"""Tests for FilePathResolver class."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, MagicMock

from src.features.media.file_resolver import FilePathResolver
from src.platform.settings.settings import Settings


class TestFilePathResolver:
    """Tests for FilePathResolver."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings manager."""
        settings = Mock(spec=Settings)
        settings.get_file_storage_directory.return_value = "/tmp/storage"
        return settings

    @pytest.fixture
    def mock_preset_loader(self):
        """Create mock preset loader."""
        loader = Mock()
        loader.preset_files_path = Path("/tmp/presets")
        loader.presets = []
        return loader

    @pytest.fixture
    def resolver(self, mock_settings, mock_preset_loader):
        """Create FilePathResolver instance."""
        return FilePathResolver(mock_settings, mock_preset_loader)

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    # Test validate_path_security method
    def test_validate_path_security_valid(self, resolver, temp_dir):
        """Test validate_path_security returns True for valid paths."""
        valid_path = temp_dir / "subdir" / "file.txt"
        assert resolver.validate_path_security(valid_path, temp_dir) is True

    def test_validate_path_security_traversal(self, resolver, temp_dir):
        """Test validate_path_security returns False for path traversal."""
        traversal_path = temp_dir / ".." / ".." / "etc" / "passwd"
        assert resolver.validate_path_security(traversal_path, temp_dir) is False

    def test_validate_path_security_outside_base(self, resolver, temp_dir):
        """Test validate_path_security returns False for paths outside base."""
        outside_path = Path("/etc/passwd")
        assert resolver.validate_path_security(outside_path, temp_dir) is False

    # Test resolve_temp_file method
    def test_resolve_temp_file_valid(self, resolver, mock_settings, temp_dir):
        """Test resolve_temp_file returns correct path."""
        mock_settings.get_file_storage_directory.return_value = str(temp_dir)

        # Create tmp directory
        tmp_dir = temp_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)

        result = resolver.resolve_temp_file("test.png")
        assert result.name == "test.png"

    def test_resolve_temp_file_traversal_raises(self, resolver, mock_settings, temp_dir):
        """Test resolve_temp_file raises ValueError for path traversal."""
        mock_settings.get_file_storage_directory.return_value = str(temp_dir)

        # Create tmp directory
        tmp_dir = temp_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)

        with pytest.raises(ValueError, match="Access denied"):
            resolver.resolve_temp_file("../../../etc/passwd")

    # Test resolve_upload_file method
    def test_resolve_upload_file_valid(self, resolver, mock_settings, temp_dir):
        """Test resolve_upload_file returns correct path."""
        mock_settings.get_file_storage_directory.return_value = str(temp_dir)

        # Create uploads directory
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)

        result = resolver.resolve_upload_file("test.png")
        assert result.name == "test.png"

    def test_resolve_upload_file_traversal_raises(self, resolver, mock_settings, temp_dir):
        """Test resolve_upload_file raises ValueError for path traversal."""
        mock_settings.get_file_storage_directory.return_value = str(temp_dir)

        # Create uploads directory
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)

        with pytest.raises(ValueError, match="Access denied"):
            resolver.resolve_upload_file("../../../etc/passwd")

    # Test resolve_preset_file method
    def test_resolve_preset_file_no_loader(self, mock_settings):
        """Test resolve_preset_file raises when no preset loader."""
        resolver = FilePathResolver(mock_settings, preset_loader=None)

        with pytest.raises(ValueError, match="Preset loader not configured"):
            resolver.resolve_preset_file("test_preset", "file.png")

    def test_resolve_preset_file_not_found(self, resolver, mock_preset_loader):
        """Test resolve_preset_file raises when preset not found."""
        mock_preset_loader.presets = []

        with pytest.raises(ValueError, match="Preset not found"):
            resolver.resolve_preset_file("nonexistent_preset", "file.png")

    @staticmethod
    def _make_preset(mock_preset_loader, temp_dir, name='standard'):
        """Create a preset dir with a populated public/ and a private preset.yml."""
        mock_preset = Mock()
        mock_preset.id = "test_preset"

        preset_dir = temp_dir / 'test_author' / 'test_model' / 'v1' / name
        (preset_dir / 'public' / 'nested').mkdir(parents=True, exist_ok=True)
        (preset_dir / 'files' / 'form').mkdir(parents=True, exist_ok=True)

        (preset_dir / 'public' / 'cover.png').touch()
        (preset_dir / 'public' / 'clip.mp4').touch()
        (preset_dir / 'public' / 'sample.wav').touch()
        (preset_dir / 'public' / 'logo.svg').touch()
        (preset_dir / 'public' / 'nested' / 'deep.png').touch()
        (preset_dir / 'files' / 'form' / 'opts.yml').touch()
        (preset_dir / 'preset.yml').touch()

        mock_preset.path = str(preset_dir)
        mock_preset_loader.presets = [mock_preset]
        return preset_dir

    def test_resolve_preset_file_valid(self, resolver, mock_preset_loader, temp_dir):
        """A public/ image resolves, including a nested one."""
        self._make_preset(mock_preset_loader, temp_dir)

        result = resolver.resolve_preset_file("test_preset", "public/cover.png")
        assert result.name == "cover.png"
        assert result.exists()

        nested = resolver.resolve_preset_file("test_preset", "public/nested/deep.png")
        assert nested.exists()

    def test_resolve_preset_file_allows_video(self, resolver, mock_preset_loader, temp_dir):
        """Gallery entries may be clips - `category: video` presets exist."""
        self._make_preset(mock_preset_loader, temp_dir)
        assert resolver.resolve_preset_file("test_preset", "public/clip.mp4").exists()

    def test_resolve_preset_file_allows_audio(self, resolver, mock_preset_loader, temp_dir):
        """A preset may ship an audio sample under public/ (e.g. a text-to-audio
        model's demo track) - PRESET_SERVABLE_EXTENSIONS must include audio."""
        self._make_preset(mock_preset_loader, temp_dir)
        assert resolver.resolve_preset_file("test_preset", "public/sample.wav").exists()

    def test_resolve_preset_file_rejects_manifest(self, resolver, mock_preset_loader, temp_dir):
        """preset.yml sits at the preset root and must never be served."""
        self._make_preset(mock_preset_loader, temp_dir)

        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_preset_file("test_preset", "preset.yml")

    def test_resolve_preset_file_rejects_non_public_root(self, resolver, mock_preset_loader, temp_dir):
        """files/ holds option YAML read server-side; it is not servable."""
        self._make_preset(mock_preset_loader, temp_dir)

        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_preset_file("test_preset", "files/form/opts.yml")

    def test_resolve_preset_file_rejects_svg(self, resolver, mock_preset_loader, temp_dir):
        """Inline SVG can carry script, and preset content may come from a marketplace."""
        self._make_preset(mock_preset_loader, temp_dir)

        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_preset_file("test_preset", "public/logo.svg")

    def test_resolve_preset_file_traversal_raises(self, resolver, mock_preset_loader, temp_dir):
        """Test resolve_preset_file raises ValueError for path traversal."""
        self._make_preset(mock_preset_loader, temp_dir)

        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_preset_file("test_preset", "../../../etc/passwd")

    def test_resolve_preset_file_escape_via_public_raises(self, resolver, mock_preset_loader, temp_dir):
        """A path that clears the root+extension checks must still not escape the dir."""
        self._make_preset(mock_preset_loader, temp_dir)
        outside = temp_dir / 'outside.png'
        outside.touch()

        with pytest.raises(ValueError, match="Access denied"):
            resolver.resolve_preset_file("test_preset", "public/../../../../outside.png")

    def test_resolve_preset_file_sibling_prefix_dir_raises(self, resolver, mock_preset_loader, temp_dir):
        """Regression: a sibling whose name merely EXTENDS the preset dir name.

        The old guard compared with `str.startswith`, so '.../standard-evil' passed
        for base '.../standard'.
        """
        preset_dir = self._make_preset(mock_preset_loader, temp_dir)
        evil = preset_dir.parent / f"{preset_dir.name}-evil"
        evil.mkdir(parents=True, exist_ok=True)
        (evil / 'stolen.png').touch()

        with pytest.raises(ValueError, match="Access denied"):
            resolver.resolve_preset_file(
                "test_preset", f"public/../../{evil.name}/stolen.png"
            )

    def test_validate_path_security_rejects_sibling_prefix(self, resolver, temp_dir):
        """The guard itself, isolated from the root/extension allowlists."""
        base = temp_dir / 'standard'
        sibling = temp_dir / 'standard-evil'
        base.mkdir(parents=True, exist_ok=True)
        sibling.mkdir(parents=True, exist_ok=True)

        assert resolver.validate_path_security(base / 'public' / 'a.png', base) is True
        assert resolver.validate_path_security(sibling / 'a.png', base) is False

    # Test get_thumbnail_path method
    def test_get_thumbnail_path_small(self, resolver):
        """Test get_thumbnail_path returns correct path for small thumbnail."""
        mock_file = Mock()
        mock_file.thumbnail_small = "thumbnails/small.jpg"
        mock_file.file_type = "IMAGE"

        result = resolver.get_thumbnail_path(mock_file, "small")
        assert result == Path("thumbnails/small.jpg")

    def test_get_thumbnail_path_medium(self, resolver):
        """Test get_thumbnail_path returns correct path for medium thumbnail."""
        mock_file = Mock()
        mock_file.thumbnail_medium = "thumbnails/medium.jpg"
        mock_file.file_type = "IMAGE"

        result = resolver.get_thumbnail_path(mock_file, "medium")
        assert result == Path("thumbnails/medium.jpg")

    def test_get_thumbnail_path_large(self, resolver):
        """Test get_thumbnail_path returns correct path for large thumbnail."""
        mock_file = Mock()
        mock_file.thumbnail_large = "thumbnails/large.jpg"
        mock_file.file_type = "IMAGE"

        result = resolver.get_thumbnail_path(mock_file, "large")
        assert result == Path("thumbnails/large.jpg")

    def test_get_thumbnail_path_invalid_size(self, resolver):
        """Test get_thumbnail_path returns None for invalid size."""
        mock_file = Mock()
        result = resolver.get_thumbnail_path(mock_file, "invalid")
        assert result is None

    def test_get_thumbnail_path_missing_thumbnail(self, resolver):
        """Test get_thumbnail_path returns None when thumbnail not available."""
        mock_file = Mock()
        mock_file.thumbnail_small = None

        result = resolver.get_thumbnail_path(mock_file, "small")
        assert result is None

    def test_get_thumbnail_path_animated_video(self, resolver):
        """Test get_thumbnail_path returns animated path for videos."""
        mock_file = Mock()
        mock_file.thumbnail_small = "thumbnails/small.jpg"
        mock_file.file_type = "VIDEO"

        result = resolver.get_thumbnail_path(mock_file, "small", animated=True)
        assert result == Path("thumbnails/small_animated.webp")

    # Test get_uploads_directory method
    def test_get_uploads_directory(self, resolver, mock_settings, temp_dir):
        """Test get_uploads_directory creates and returns correct path."""
        mock_settings.get_file_storage_directory.return_value = str(temp_dir)

        result = resolver.get_uploads_directory()
        assert result == temp_dir / "uploads"
        assert result.exists()

    # Test get_storage_directory method
    def test_get_storage_directory(self, resolver, mock_settings):
        """Test get_storage_directory returns correct path."""
        mock_settings.get_file_storage_directory.return_value = "/tmp/storage"

        result = resolver.get_storage_directory()
        assert result == "/tmp/storage"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
