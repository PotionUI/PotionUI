"""Tests for MediaManager class."""

import pytest
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from PIL import Image
import io

from src.features.media.manager import MediaManager, UnsupportedSizeError
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import SettingsManager
from src.features.generation.file_repository import FileRepository
from src.features.generation.repository import GenerationRepository
from src.platform.filesystem.file_store import FileStore
from src.features.media.upload_repository import UploadRepository
from src.features.media.records import Upload
from src.features.media.dto import (
    MediaResult,
    UploadResult,
    UploadInfoResult,
    MediaListResult,
    DeleteResult,
    UploadListResult,
)


class TestMediaManager:
    """Tests for MediaManager."""

    @pytest.fixture
    def mock_file_resolver(self):
        """Create mock file resolver."""
        resolver = Mock(spec=FilePathResolver)
        resolver.get_uploads_directory.return_value = Path("/tmp/uploads")
        resolver.get_storage_directory.return_value = "/tmp/storage"
        return resolver

    @pytest.fixture
    def mock_image_processor(self):
        """Create mock image processor."""
        processor = Mock(spec=ImageProcessor)
        processor.resize_image.return_value = b"resized_image_data"
        processor.generate_thumbnail.return_value = b"thumbnail_data"
        return processor

    @pytest.fixture
    def mock_media_types(self):
        """Create mock media type resolver."""
        resolver = Mock(spec=MediaTypeResolver)
        resolver.get_media_type.return_value = "image/png"
        resolver.is_resizable.return_value = True
        resolver.is_valid_media_type.return_value = True
        return resolver

    @pytest.fixture
    def mock_file_repo(self):
        """Create mock file repository."""
        repo = Mock(spec=FileRepository)
        return repo

    @pytest.fixture
    def mock_generation_repo(self):
        """Create mock generation repository."""
        repo = Mock(spec=GenerationRepository)
        return repo

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings manager."""
        settings = Mock(spec=SettingsManager)
        settings.get_file_storage_directory.return_value = "/tmp/storage"
        return settings

    @pytest.fixture
    def mock_file_service(self):
        """Create mock file service."""
        service = Mock(spec=FileStore)
        return service

    @pytest.fixture
    def mock_plugin_registry(self):
        """Create mock plugin registry."""
        registry = Mock(spec=PluginRegistry)
        # Default hook execution returns no blocking
        mock_context = Mock()
        mock_context.data = {}
        registry.execute_hook.return_value = (mock_context, [])
        return registry

    @pytest.fixture
    def mock_upload_repo(self):
        """Create mock upload repository."""
        repo = Mock(spec=UploadRepository)
        return repo

    @pytest.fixture
    def manager(
        self,
        mock_file_resolver,
        mock_image_processor,
        mock_media_types,
        mock_file_repo,
        mock_generation_repo,
        mock_settings,
        mock_file_service,
        mock_plugin_registry,
        mock_upload_repo
    ):
        """Create MediaManager instance."""
        return MediaManager(
            file_resolver=mock_file_resolver,
            image_processor=mock_image_processor,
            media_type_resolver=mock_media_types,
            file_repository=mock_file_repo,
            generation_repository=mock_generation_repo,
            settings_manager=mock_settings,
            file_service=mock_file_service,
            plugin_registry=mock_plugin_registry,
            upload_repository=mock_upload_repo
        )

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_image_bytes(self):
        """Create sample image bytes."""
        img = Image.new('RGB', (100, 100), color='blue')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()

    # Test get_temp_media method
    def test_get_temp_media_success(self, manager, mock_file_resolver, temp_dir):
        """Test get_temp_media returns correct result."""
        # Create temp file
        temp_file = temp_dir / "test.png"
        temp_file.touch()
        mock_file_resolver.resolve_temp_file.return_value = temp_file

        result = manager.get_temp_media("test.png")

        assert isinstance(result, MediaResult)
        assert result.file_path == str(temp_file)
        assert result.media_type == "image/png"

    def test_get_temp_media_not_found(self, manager, mock_file_resolver, temp_dir):
        """Test get_temp_media raises when file not found."""
        nonexistent = temp_dir / "nonexistent.png"
        mock_file_resolver.resolve_temp_file.return_value = nonexistent

        with pytest.raises(ValueError, match="Temporary file not found"):
            manager.get_temp_media("nonexistent.png")

    # Test get_uploaded_media method
    def test_get_uploaded_media_success(self, manager, mock_settings):
        """Test get_uploaded_media returns correct result.

        The manager's default storage driver is local disk rooted at
        `mock_settings.get_file_storage_directory()` - the file has to exist
        at the same `uploads/<filename>` key the driver resolves, not at an
        arbitrary path a mocked `file_resolver` used to hand back.
        """
        uploads_dir = Path(mock_settings.get_file_storage_directory()) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        uploaded_file = uploads_dir / "test.png"
        uploaded_file.write_bytes(b"")

        result = manager.get_uploaded_media("test.png", "user123")

        assert isinstance(result, MediaResult)
        assert Path(result.file_path).resolve() == uploaded_file.resolve()

    def test_get_uploaded_media_not_found(self, manager, mock_file_resolver, temp_dir):
        """Test get_uploaded_media raises when file not found."""
        nonexistent = temp_dir / "nonexistent.png"
        mock_file_resolver.resolve_upload_file.return_value = nonexistent

        with pytest.raises(ValueError, match="Uploaded file not found"):
            manager.get_uploaded_media("nonexistent.png", "user123")

    # Test get_upload_info method (fetch-when-missing addendum)
    def test_get_upload_info_success(self, manager, mock_settings, mock_media_types):
        """Metadata for an already-uploaded file is resolved through the same
        containment-checked storage key the serving route uses, then probed."""
        uploads_dir = Path(mock_settings.get_file_storage_directory()) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        uploaded_file = uploads_dir / "test.mp4"
        uploaded_file.write_bytes(b"fake_video_data")
        mock_media_types.is_image.return_value = False
        mock_media_types.is_video.return_value = True

        with patch('src.features.media.manager.media_probe') as mock_probe:
            mock_probe.get_video_dimensions.return_value = (1920, 1080)
            mock_probe.get_video_duration_fps.return_value = (5.2, 24.0)

            result = manager.get_upload_info("test.mp4", "user123")

        assert isinstance(result, UploadInfoResult)
        assert result.filename == "test.mp4"
        assert result.size == len(b"fake_video_data")
        assert result.width == 1920
        assert result.height == 1080
        assert result.duration_seconds == 5.2
        assert result.fps == 24.0

    def test_get_upload_info_not_found(self, manager, mock_file_resolver, temp_dir):
        """Test get_upload_info raises when the file doesn't exist on disk."""
        nonexistent = temp_dir / "nonexistent.png"
        mock_file_resolver.resolve_upload_file.return_value = nonexistent

        with pytest.raises(ValueError, match="Uploaded file not found"):
            manager.get_upload_info("nonexistent.png", "user123")

    # Test upload_media method
    @pytest.mark.asyncio
    async def test_upload_media_success(self, manager, mock_file_resolver, temp_dir):
        """Test upload_media creates file and returns result."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)

        result = await manager.upload_media(
            file_data=b"test_data",
            filename="test.png",
            content_type="image/png",
            user_id="user123"
        )

        assert isinstance(result, UploadResult)
        assert result.size == len(b"test_data")
        assert result.url.startswith("/api/media/uploads/")

    @pytest.mark.asyncio
    async def test_upload_media_image_probes_dimensions(
        self, manager, mock_file_resolver, mock_media_types, mock_image_processor, temp_dir, sample_image_bytes
    ):
        """Uploading an image probes width/height via ImageProcessor."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = True
        mock_media_types.is_video.return_value = False
        mock_image_processor.get_image_dimensions.return_value = (100, 100)

        result = await manager.upload_media(
            file_data=sample_image_bytes,
            filename="test.png",
            content_type="image/png",
            user_id="user123"
        )

        assert result.width == 100
        assert result.height == 100
        assert result.duration_seconds is None
        assert result.fps is None

    @pytest.mark.asyncio
    async def test_upload_media_video_probes_metadata(
        self, manager, mock_file_resolver, mock_media_types, temp_dir
    ):
        """Uploading a video probes width/height/duration/fps via the shared
        ffprobe-backed media_probe helper."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = False
        mock_media_types.is_video.return_value = True

        with patch('src.features.media.manager.media_probe') as mock_probe:
            mock_probe.get_video_dimensions.return_value = (1920, 1080)
            mock_probe.get_video_duration_fps.return_value = (5.2, 24.0)

            result = await manager.upload_media(
                file_data=b"fake_video_data",
                filename="test.mp4",
                content_type="video/mp4",
                user_id="user123"
            )

        assert result.width == 1920
        assert result.height == 1080
        assert result.duration_seconds == 5.2
        assert result.fps == 24.0

    @pytest.mark.asyncio
    async def test_upload_media_probe_failure_is_non_fatal(
        self, manager, mock_file_resolver, mock_media_types, temp_dir
    ):
        """A probing exception must not fail the upload - metadata just stays None."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = True
        mock_media_types.is_video.return_value = False

        result = await manager.upload_media(
            file_data=b"not_a_real_image",
            filename="test.png",
            content_type="image/png",
            user_id="user123"
        )

        assert isinstance(result, UploadResult)
        assert result.width is None
        assert result.height is None

    @pytest.mark.asyncio
    async def test_upload_media_audio_probes_duration(
        self, manager, mock_file_resolver, mock_media_types, temp_dir, minimal_wav_bytes, wav_duration_seconds
    ):
        """Uploading audio probes duration via the shared soundfile-backed
        media_probe helper - width/height/fps stay None, unlike video."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = False
        mock_media_types.is_video.return_value = False
        mock_media_types.is_audio.return_value = True

        result = await manager.upload_media(
            file_data=minimal_wav_bytes,
            filename="test.wav",
            content_type="audio/wav",
            user_id="user123"
        )

        assert result.width is None
        assert result.height is None
        assert result.duration_seconds == pytest.approx(wav_duration_seconds)
        assert result.fps is None

    @pytest.mark.asyncio
    async def test_upload_media_invalid_type(self, manager, mock_media_types):
        """Test upload_media raises for invalid content type."""
        mock_media_types.is_valid_media_type.return_value = False

        with pytest.raises(ValueError, match="Only image, video, and audio files are allowed"):
            await manager.upload_media(
                file_data=b"test_data",
                filename="test.txt",
                content_type="text/plain",
                user_id="user123"
            )

    @pytest.mark.asyncio
    async def test_upload_media_blocked_by_hook(self, manager, mock_plugin_registry, mock_media_types):
        """Test upload_media raises when blocked by hook."""
        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Upload not allowed"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])
        mock_media_types.is_valid_media_type.return_value = True

        with pytest.raises(ValueError, match="Upload not allowed"):
            await manager.upload_media(
                file_data=b"test_data",
                filename="test.png",
                content_type="image/png",
                user_id="user123"
            )

    # Test upload ownership recording
    @pytest.mark.asyncio
    async def test_upload_media_records_ownership(
        self, manager, mock_file_resolver, mock_upload_repo, temp_dir
    ):
        """A successful upload is also recorded to the uploads table so it
        can show up in the user's "Load from uploads" library."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)

        await manager.upload_media(
            file_data=b"test_data",
            filename="cat.png",
            content_type="image/png",
            user_id="user123"
        )

        mock_upload_repo.create.assert_called_once()
        created_upload = mock_upload_repo.create.call_args[0][0]
        assert isinstance(created_upload, Upload)
        assert created_upload.user_id == "user123"
        assert created_upload.original_filename == "cat.png"
        assert created_upload.media_type == "image"
        assert created_upload.mime_type == "image/png"
        assert created_upload.file_size == len(b"test_data")
        # The stored filename is the unique on-disk name, not the original.
        assert created_upload.filename != "cat.png"
        assert created_upload.filename.endswith(".png")
        assert created_upload.purpose == "user_upload"

    @pytest.mark.asyncio
    async def test_upload_media_derived_artifact_purpose_is_recorded(
        self, manager, mock_file_resolver, mock_upload_repo, temp_dir
    ):
        """A mask upload passes purpose='derived_artifact' through to the
        recorded row, so the Library listing can exclude it."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)

        await manager.upload_media(
            file_data=b"test_data",
            filename="mask-123.png",
            content_type="image/png",
            user_id="user123",
            purpose="derived_artifact"
        )

        created_upload = mock_upload_repo.create.call_args[0][0]
        assert created_upload.purpose == "derived_artifact"

    @pytest.mark.asyncio
    async def test_upload_media_rejects_unknown_purpose(self, manager, mock_upload_repo):
        """An unrecognized purpose is rejected before anything is saved."""
        with pytest.raises(ValueError, match="purpose must be one of"):
            await manager.upload_media(
                file_data=b"test_data",
                filename="test.png",
                content_type="image/png",
                user_id="user123",
                purpose="not_a_real_purpose"
            )

        mock_upload_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_media_anonymous_skips_ownership(
        self, manager, mock_file_resolver, mock_upload_repo, temp_dir
    ):
        """No `user_id` means no owner to record - the upload still succeeds."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)

        result = await manager.upload_media(
            file_data=b"test_data",
            filename="cat.png",
            content_type="image/png",
            user_id=None
        )

        assert isinstance(result, UploadResult)
        mock_upload_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_media_ownership_failure_is_non_fatal(
        self, manager, mock_file_resolver, mock_upload_repo, temp_dir
    ):
        """A DB error recording ownership must not fail the upload - the
        file is already safely saved and servable either way."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_upload_repo.create.side_effect = Exception("db exploded")

        result = await manager.upload_media(
            file_data=b"test_data",
            filename="cat.png",
            content_type="image/png",
            user_id="user123"
        )

        assert isinstance(result, UploadResult)

    # Test upload thumbnail generation - the Library's counterpart to the
    # generation output handlers' `generate_thumbnails`/`generate_video_thumbnails`.
    @pytest.mark.asyncio
    async def test_upload_media_image_generates_thumbnails(
        self, manager, mock_file_resolver, mock_media_types, mock_upload_repo, temp_dir, sample_image_bytes
    ):
        """An image upload gets the same three thumbnail sizes a generation
        output does, generated synchronously and persisted on the row."""
        from src.platform.filesystem.storage_driver import LocalFileStorageDriver

        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = True
        mock_media_types.is_video.return_value = False
        manager.storage_driver = LocalFileStorageDriver(str(temp_dir))

        await manager.upload_media(
            file_data=sample_image_bytes,
            filename="cat.png",
            content_type="image/png",
            user_id="user123"
        )

        created_upload = mock_upload_repo.create.call_args[0][0]
        assert created_upload.thumbnail_small is not None
        assert created_upload.thumbnail_medium is not None
        assert created_upload.thumbnail_large is not None
        for path in (created_upload.thumbnail_small, created_upload.thumbnail_medium, created_upload.thumbnail_large):
            assert (uploads_dir / path).exists()

    @pytest.mark.asyncio
    async def test_upload_media_image_thumbnail_failure_is_non_fatal(
        self, manager, mock_file_resolver, mock_media_types, mock_upload_repo, temp_dir
    ):
        """Bytes that don't decode as an image must not fail the upload -
        the row is just created without thumbnails, like a failed probe."""
        from src.platform.filesystem.storage_driver import LocalFileStorageDriver

        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = True
        mock_media_types.is_video.return_value = False
        manager.storage_driver = LocalFileStorageDriver(str(temp_dir))

        result = await manager.upload_media(
            file_data=b"not_a_real_image",
            filename="fake.png",
            content_type="image/png",
            user_id="user123"
        )

        assert isinstance(result, UploadResult)
        created_upload = mock_upload_repo.create.call_args[0][0]
        assert created_upload.thumbnail_small is None

    @pytest.mark.asyncio
    async def test_upload_media_video_schedules_thumbnails_asynchronously(
        self, manager, mock_file_resolver, mock_media_types, mock_upload_repo, temp_dir
    ):
        """A video upload must not block on ffmpeg - thumbnail generation is
        scheduled off the request the same way generation video output does."""
        uploads_dir = temp_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        mock_file_resolver.get_uploads_directory.return_value = uploads_dir
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir)
        mock_media_types.is_image.return_value = False
        mock_media_types.is_video.return_value = True
        mock_upload_repo.create.return_value = Upload(
            id="up_video_1", user_id="user123", filename="clip.mp4", media_type="video"
        )

        with patch.object(manager, "_schedule_upload_video_thumbnails") as mock_schedule, \
             patch('src.features.media.manager.media_probe') as mock_probe:
            mock_probe.get_video_dimensions.return_value = (640, 480)
            mock_probe.get_video_duration_fps.return_value = (2.0, 24.0)

            await manager.upload_media(
                file_data=b"fake_video_data",
                filename="clip.mp4",
                content_type="video/mp4",
                user_id="user123"
            )

        mock_schedule.assert_called_once()
        args = mock_schedule.call_args[0]
        assert args[2] == "up_video_1"  # upload_id

    # Test upload thumbnail serving
    def test_get_uploaded_media_serves_thumbnail_when_present(
        self, manager, mock_settings, mock_upload_repo
    ):
        """`?size=` on the uploads route resolves through the upload's own
        thumbnail columns, the same shape the generation media route uses."""
        uploads_dir = Path(mock_settings.get_file_storage_directory()) / "uploads"
        thumbnails_dir = uploads_dir / "thumbnails"
        thumbnails_dir.mkdir(parents=True, exist_ok=True)
        (thumbnails_dir / "abc_small.webp").write_bytes(b"thumb-bytes")

        mock_upload_repo.get_by_filename_unscoped.return_value = Upload(
            id="up_1",
            user_id="user123",
            filename="test.png",
            media_type="image",
            thumbnail_small="thumbnails/abc_small.webp",
        )

        result = manager.get_uploaded_media("test.png", size="small")

        assert Path(result.file_path).resolve() == (thumbnails_dir / "abc_small.webp").resolve()

    def test_get_uploaded_media_missing_thumbnail_size_raises(
        self, manager, mock_upload_repo
    ):
        mock_upload_repo.get_by_filename_unscoped.return_value = Upload(
            id="up_1", user_id="user123", filename="test.png", media_type="image"
        )

        with pytest.raises(ValueError, match="Thumbnail size 'small' not available"):
            manager.get_uploaded_media("test.png", size="small")

    # Test upload library list/delete
    def test_list_uploads_success(self, manager, mock_upload_repo):
        """list_uploads maps repository rows into UploadFileInfo with a servable URL."""
        stored = Upload(
            id="up_1",
            user_id="user123",
            filename="abc.png",
            original_filename="cat.png",
            media_type="image",
            mime_type="image/png",
            width=800,
            height=600,
            file_size=2048,
        )
        mock_upload_repo.list_for_user.return_value = [stored]
        mock_upload_repo.count_for_user.return_value = 1

        result = manager.list_uploads("user123")

        assert isinstance(result, UploadListResult)
        assert result.total == 1
        assert len(result.uploads) == 1
        item = result.uploads[0]
        assert item.id == "up_1"
        assert item.filename == "abc.png"
        assert item.original_filename == "cat.png"
        assert item.url == "/api/media/uploads/abc.png"
        assert item.width == 800
        assert item.size == 2048
        mock_upload_repo.list_for_user.assert_called_once_with(
            "user123", media_type=None, limit=20, offset=0
        )

    def test_list_uploads_filters_by_media_type(self, manager, mock_upload_repo):
        """The media_type filter is passed straight through to the repository."""
        mock_upload_repo.list_for_user.return_value = []
        mock_upload_repo.count_for_user.return_value = 0

        manager.list_uploads("user123", media_type="video", limit=10, offset=5)

        mock_upload_repo.list_for_user.assert_called_once_with(
            "user123", media_type="video", limit=10, offset=5
        )

    def test_list_uploads_clamps_limit(self, manager, mock_upload_repo):
        """A caller can't turn the paginated list into an unbounded scan."""
        mock_upload_repo.list_for_user.return_value = []
        mock_upload_repo.count_for_user.return_value = 0

        manager.list_uploads("user123", limit=10_000)

        called_kwargs = mock_upload_repo.list_for_user.call_args.kwargs
        assert called_kwargs["limit"] <= 100

    def test_delete_upload_success(self, manager, mock_upload_repo, mock_settings):
        """Deleting an owned upload removes both the DB row and the file on disk."""
        uploads_dir = Path(mock_settings.get_file_storage_directory()) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        existing_file = uploads_dir / "abc.png"
        existing_file.touch()
        mock_upload_repo.get_by_filename.return_value = Upload(
            id="up_1", user_id="user123", filename="abc.png", media_type="image"
        )

        manager.delete_upload("abc.png", "user123")

        assert not existing_file.exists()
        mock_upload_repo.delete.assert_called_once_with("abc.png", "user123")

    def test_delete_upload_not_found_raises(self, manager, mock_upload_repo):
        """No row for this filename+user - a uniform 404, never a 403,
        whether the filename doesn't exist or just isn't this user's."""
        mock_upload_repo.get_by_filename.return_value = None

        with pytest.raises(ValueError, match="Upload not found"):
            manager.delete_upload("someone-elses.png", "user123")

        mock_upload_repo.delete.assert_not_called()

    def test_delete_upload_missing_file_still_removes_row(
        self, manager, mock_upload_repo, mock_file_resolver, temp_dir
    ):
        """The DB row is the source of truth for the library - a file already
        missing from disk shouldn't block clearing the stale row."""
        missing_file = temp_dir / "already-gone.png"
        mock_upload_repo.get_by_filename.return_value = Upload(
            id="up_1", user_id="user123", filename="already-gone.png", media_type="image"
        )
        mock_file_resolver.resolve_upload_file.return_value = missing_file

        manager.delete_upload("already-gone.png", "user123")

        mock_upload_repo.delete.assert_called_once_with("already-gone.png", "user123")

    # Test list_generation_media method
    def test_list_generation_media_success(self, manager, mock_generation_repo):
        """Test list_generation_media returns correct list."""
        mock_generation = Mock()
        mock_generation_repo.get_by_id.return_value = mock_generation

        mock_file = Mock()
        mock_file.id = "file123"
        mock_file.file_path = "/path/to/image.png"
        mock_file.file_type = "IMAGE"
        mock_file.mime_type = "image/png"
        mock_file.file_size = 1024
        mock_file.thumbnail_small = "thumb_small.jpg"
        mock_file.thumbnail_medium = "thumb_medium.jpg"
        mock_file.thumbnail_large = "thumb_large.jpg"
        mock_file.width = 1920
        mock_file.height = 1080
        mock_file.duration_seconds = 5.2
        mock_file.fps = 24.0
        mock_generation_repo.get_files.return_value = [mock_file]

        result = manager.list_generation_media("gen123", "user123")

        assert isinstance(result, MediaListResult)
        assert result.generation_id == "gen123"
        assert result.media_count == 1
        assert len(result.media) == 1
        assert result.media[0].id == "file123"
        assert result.media[0].width == 1920
        assert result.media[0].height == 1080
        assert result.media[0].duration_seconds == 5.2
        assert result.media[0].fps == 24.0

    def test_list_generation_media_not_found(self, manager, mock_generation_repo):
        """Test list_generation_media raises when generation not found."""
        mock_generation_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Generation not found"):
            manager.list_generation_media("nonexistent", "user123")

    # Test delete_generation_media method
    def test_delete_generation_media_success(self, manager, mock_generation_repo, mock_settings, mock_file_service):
        """Test delete_generation_media deletes files - keys enumerated from
        the `files` rows (main path + thumbnails), never a directory scan."""
        mock_generation = Mock()
        mock_generation_repo.get_by_id.return_value = mock_generation

        mock_file = Mock()
        mock_file.file_path = "generations/2024-01-01/gen123/0.png"
        mock_file.thumbnail_small = "thumbnails/0_small.webp"
        mock_file.thumbnail_medium = None
        mock_file.thumbnail_large = None
        mock_generation_repo.get_files.return_value = [mock_file]
        mock_file_service.delete_generation_outputs.return_value = (5, 0)

        result = manager.delete_generation_media("gen123", "user123")

        assert isinstance(result, DeleteResult)
        assert result.generation_id == "gen123"
        assert result.deleted_files == 5
        assert result.failed_files == 0
        mock_file_service.delete_generation_outputs.assert_called_once_with([
            "generations/2024-01-01/gen123/0.png",
            "generations/2024-01-01/gen123/thumbnails/0_small.webp",
        ])
        mock_generation_repo.delete.assert_called_once_with("gen123")

    def test_delete_generation_media_not_found(self, manager, mock_generation_repo):
        """Test delete_generation_media raises when generation not found."""
        mock_generation_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Generation not found"):
            manager.delete_generation_media("nonexistent", "user123")

    def test_delete_generation_media_blocked(self, manager, mock_generation_repo, mock_plugin_registry):
        """Test delete_generation_media raises when blocked by hook."""
        mock_generation = Mock()
        mock_generation_repo.get_by_id.return_value = mock_generation

        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Delete not allowed"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ValueError, match="Delete not allowed"):
            manager.delete_generation_media("gen123", "user123")

    # Test get_file_by_id method
    def test_get_file_by_id_success(self, manager, mock_file_repo, mock_generation_repo, temp_dir):
        """Test get_file_by_id returns correct result."""
        # Create test file at the exact key the storage_driver resolves
        from src.platform.filesystem.storage_driver import LocalFileStorageDriver
        manager.storage_driver = LocalFileStorageDriver(str(temp_dir))
        test_file = temp_dir / "test.png"
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(test_file)

        mock_file = Mock()
        mock_file.file_path = "test.png"
        mock_file.user_id = "user123"
        mock_file_repo.get_by_id.return_value = mock_file
        mock_file_repo.get_generation_file_by_file_id.return_value = None

        result = manager.get_file_by_id("file123")

        assert isinstance(result, MediaResult)
        assert result.content is not None

    def test_get_file_by_id_not_found(self, manager, mock_file_repo):
        """Test get_file_by_id raises when file not found."""
        mock_file_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="File not found"):
            manager.get_file_by_id("nonexistent")

    def test_get_file_by_id_with_resize(
        self, manager, mock_file_repo, mock_image_processor, mock_media_types, temp_dir
    ):
        """Test get_file_by_id resizes image when requested."""
        # Create test file at the exact key the storage_driver resolves
        from src.platform.filesystem.storage_driver import LocalFileStorageDriver
        manager.storage_driver = LocalFileStorageDriver(str(temp_dir))
        test_file = temp_dir / "test.png"
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(test_file)

        mock_file = Mock()
        mock_file.file_path = "test.png"
        mock_file.user_id = "user123"
        mock_file_repo.get_by_id.return_value = mock_file
        mock_file_repo.get_generation_file_by_file_id.return_value = None

        mock_media_types.is_resizable.return_value = True
        mock_image_processor.resize_image.return_value = b"resized_data"

        result = manager.get_file_by_id("file123", width=50)

        mock_image_processor.resize_image.assert_called_once()
        assert result.content == b"resized_data"

    # Test get_preset_file method
    def test_get_preset_file_success(self, manager, mock_file_resolver, temp_dir):
        """Test get_preset_file returns correct result."""
        # Create test file
        preset_file = temp_dir / "preset_image.png"
        preset_file.touch()
        mock_file_resolver.resolve_preset_file.return_value = preset_file

        result = manager.get_preset_file("preset123", "images/test.png")

        assert isinstance(result, MediaResult)
        assert result.file_path == str(preset_file)
        assert result.use_streaming is True

    def test_get_preset_file_not_found(self, manager, mock_file_resolver, temp_dir):
        """Test get_preset_file raises when file not found."""
        nonexistent = temp_dir / "nonexistent.png"
        mock_file_resolver.resolve_preset_file.return_value = nonexistent

        with pytest.raises(ValueError, match="[Ff]ile not found"):
            manager.get_preset_file("preset123", "public/nonexistent.png")

    def test_get_preset_file_thumbnail(
        self, manager, mock_file_resolver, mock_image_processor, temp_dir
    ):
        """`thumbnail` stays a 150px alias; the render is cached and streamed."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (100, 100), color='blue').save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir / "storage")
        mock_image_processor.generate_thumbnail.return_value = b"thumbnail_data"

        result = manager.get_preset_file("preset123", "public/test.png", size="thumbnail")

        mock_image_processor.generate_thumbnail.assert_called_once()
        assert mock_image_processor.generate_thumbnail.call_args.kwargs["width"] == 150
        assert result.use_streaming is True
        assert Path(result.file_path).read_bytes() == b"thumbnail_data"

    @pytest.mark.parametrize("size,width", [("small", 480), ("medium", 768), ("large", 1024)])
    def test_get_preset_file_named_sizes(
        self, manager, mock_file_resolver, mock_image_processor, temp_dir, size, width
    ):
        """small/medium/large used to fall through and serve the full-size original."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (2000, 1000), color='blue').save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir / "storage")
        mock_image_processor.generate_thumbnail.return_value = b"data"

        result = manager.get_preset_file("preset123", "public/test.png", size=size)

        assert mock_image_processor.generate_thumbnail.call_args.kwargs["width"] == width
        assert size in result.headers["ETag"]

    def test_get_preset_file_unknown_size_raises(self, manager, mock_file_resolver, temp_dir):
        """An unrecognised size is a client error, not a silent full-size serve."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (10, 10)).save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file

        with pytest.raises(UnsupportedSizeError):
            manager.get_preset_file("preset123", "public/test.png", size="huge")

    def test_get_preset_file_thumbnail_cache_reused(
        self, manager, mock_file_resolver, mock_image_processor, temp_dir
    ):
        """A second request for the same source+size renders nothing."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (800, 600)).save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir / "storage")
        mock_image_processor.generate_thumbnail.return_value = b"data"

        first = manager.get_preset_file("preset123", "public/test.png", size="small")
        second = manager.get_preset_file("preset123", "public/test.png", size="small")

        assert first.file_path == second.file_path
        assert mock_image_processor.generate_thumbnail.call_count == 1

    def test_get_preset_file_thumbnail_invalidated_on_edit(
        self, manager, mock_file_resolver, mock_image_processor, temp_dir
    ):
        """Editing the source must change the cache entry AND the ETag."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (800, 600), color='red').save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir / "storage")
        mock_image_processor.generate_thumbnail.return_value = b"data"

        first = manager.get_preset_file("preset123", "public/test.png", size="small")

        os.utime(preset_file, ns=(preset_file.stat().st_atime_ns + 10**9,
                                  preset_file.stat().st_mtime_ns + 10**9))
        second = manager.get_preset_file("preset123", "public/test.png", size="small")

        assert first.file_path != second.file_path
        assert first.headers["ETag"] != second.headers["ETag"]
        assert not Path(first.file_path).exists(), "stale render should be pruned"

    def test_purge_preset_thumbnail_cache(
        self, manager, mock_file_resolver, mock_image_processor, temp_dir
    ):
        """Reloading a preset reclaims its rendered thumbnails."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (800, 600)).save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file
        mock_file_resolver.get_storage_directory.return_value = str(temp_dir / "storage")
        mock_image_processor.generate_thumbnail.return_value = b"data"

        result = manager.get_preset_file("preset123", "public/test.png", size="small")
        assert Path(result.file_path).exists()

        manager.purge_preset_thumbnail_cache("preset123")
        assert not Path(result.file_path).exists()

    def test_get_preset_file_etag_tracks_mtime(self, manager, mock_file_resolver, temp_dir):
        """The full-size ETag was mtime-blind, so edits were never picked up."""
        preset_file = temp_dir / "preset_image.png"
        Image.new('RGB', (10, 10)).save(preset_file)
        mock_file_resolver.resolve_preset_file.return_value = preset_file

        before = manager.get_preset_file("preset123", "public/test.png").headers["ETag"]
        os.utime(preset_file, ns=(preset_file.stat().st_atime_ns + 10**9,
                                  preset_file.stat().st_mtime_ns + 10**9))
        after = manager.get_preset_file("preset123", "public/test.png").headers["ETag"]

        assert before != after


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
