"""Generation output writes/reads must go entirely through the injected
`storage_driver` - never a raw `Path`/`os` shortcut onto `base_storage_dir`
that would miss a non-local backend (S3).

Everything here drives the real save path (real DB, real handler) against a
driver whose `local_path()` returns `None` - the one behavioural difference
`S3FileStorageDriver` callers must handle - rooted OUTSIDE the FileStore's own
local `base_storage_dir`, so a fallback to a raw filesystem path would miss it
entirely.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler
from src.features.generation.handlers.mesh_handler import MeshGenerationOutputHandler
from src.features.generation.handlers.video_handler import VideoGenerationOutputHandler
from src.features.generation.repository import generation_repo
from src.pipelines.outputs import (
    AudioGenerationOutput,
    ImageGenerationOutput,
    MeshGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.settings.settings import SettingsManager
from src.platform.util.ids import generate_ulid


class _NoLocalPathDriver(LocalFileStorageDriver):
    """Same storage as `LocalFileStorageDriver`, but reports no local file -
    the one behavioural difference `S3FileStorageDriver` callers must handle."""

    def local_path(self, key):
        return None


@pytest.fixture
def repos_on_test_db(mock_db):
    with patch('src.features.generation.file_repository.db', mock_db), \
         patch('src.features.generation.repository.db', mock_db):
        yield mock_db


@pytest.fixture
def generation_id(repos_on_test_db):
    mock_db = repos_on_test_db
    user_id = generate_ulid()
    gen_id = generate_ulid()

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, 'driver_bypass_tester', 'driverbypass@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/test/driver-bypass', '1.0.0', '{}', user_id, 'running', 0.0),
        )

    return gen_id, user_id


@pytest.fixture
def local_root(tmp_path):
    """The FileStore's own local root - writes must NEVER land here when a
    storage_driver is injected; only the driver's separate bucket may hold
    the bytes."""
    root = tmp_path / "local_root"
    root.mkdir()
    return root


@pytest.fixture
def bucket_driver(tmp_path):
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    return _NoLocalPathDriver(str(bucket))


@pytest.fixture
def settings_manager(local_root):
    settings = Mock(spec=SettingsManager)
    settings.get_file_storage_directory.return_value = str(local_root)
    return settings


class TestImageHandlerDriverBypassClosed:
    def test_final_image_and_thumbnails_land_only_in_the_driver(
        self, generation_id, settings_manager, bucket_driver, local_root
    ):
        gen_id, user_id = generation_id
        handler = ImageGenerationOutputHandler(gen_id, user_id, settings_manager, bucket_driver)

        image = Image.new('RGB', (64, 64), color='red')
        output = ImageGenerationOutput(image=image, temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved_path = metadata['saved_path']
        assert bucket_driver.exists(saved_path)
        assert not any(local_root.rglob('*.png')), "image leaked onto the FileStore's own local root"

        files = generation_repo.get_files(gen_id, is_final=True)
        assert len(files) == 1
        record = files[0]
        assert record.thumbnail_small is not None
        thumb_key = f"{Path(saved_path).parent.as_posix()}/{record.thumbnail_small}"
        assert bucket_driver.exists(thumb_key)
        assert not any(local_root.rglob('*.webp')), "thumbnail leaked onto the FileStore's own local root"


class TestVideoHandlerDriverBypassClosed:
    def test_final_video_lands_only_in_the_driver(
        self, generation_id, settings_manager, bucket_driver, local_root, tmp_path
    ):
        gen_id, user_id = generation_id
        handler = VideoGenerationOutputHandler(gen_id, user_id, settings_manager, bucket_driver)

        source = tmp_path / "source.mp4"
        video_bytes = b"fake video bytes" * 100
        source.write_bytes(video_bytes)

        output = VideoGenerationOutput(video_path=str(source), temporary=False)
        with patch.object(handler, '_get_video_dimensions', return_value=(None, None)), \
             patch('src.features.generation.media_probe.get_video_duration_fps', return_value=(None, None)):
            metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved_path = metadata['saved_path']
        assert bucket_driver.get_bytes(saved_path) == video_bytes
        assert not any(local_root.rglob('*.mp4')), "video leaked onto the FileStore's own local root"


class TestAudioHandlerDriverBypassClosed:
    def test_final_audio_lands_only_in_the_driver(
        self, generation_id, settings_manager, bucket_driver, local_root, minimal_wav_file, minimal_wav_bytes
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings_manager, bucket_driver)

        output = AudioGenerationOutput(audio_path=str(minimal_wav_file), temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved_path = metadata['saved_path']
        assert bucket_driver.get_bytes(saved_path) == minimal_wav_bytes
        assert not any(local_root.rglob('*.wav')), "audio leaked onto the FileStore's own local root"

        files = generation_repo.get_files(gen_id, is_final=True)
        assert len(files) == 1
        # The duration probe had to materialize a local copy from the driver
        # (not `output.audio_path`) to run at all - a non-None value proves
        # that path was taken successfully.
        assert files[0].duration_seconds is not None


class TestMeshHandlerDriverBypassClosed:
    def test_final_mesh_lands_only_in_the_driver(
        self, generation_id, settings_manager, bucket_driver, local_root, minimal_glb_file, minimal_glb_bytes
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings_manager, bucket_driver)

        output = MeshGenerationOutput(mesh_path=str(minimal_glb_file), temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved_path = metadata['saved_path']
        assert bucket_driver.get_bytes(saved_path) == minimal_glb_bytes
        assert not any(local_root.rglob('*.glb')), "mesh leaked onto the FileStore's own local root"
