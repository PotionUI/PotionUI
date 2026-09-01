"""Generation media serve/get/delete must go entirely through the injected
`storage_driver` - never a raw `Path`/`os` shortcut onto `base_storage_dir`
that would miss a non-local backend (S3). Mirrors test_audio_serving.py's
real-object wiring, but with a driver whose `local_path()` returns `None`
rooted OUTSIDE the FileStore's own local root.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.generation.file_repository import FileRepository
from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.repository import GenerationRepository
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.store import MediaStore
from src.features.media.media_types import MediaTypeResolver
from src.features.media.upload_repository import UploadRepository
from src.pipelines.outputs import AudioGenerationOutput
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

WAV_MEDIA_TYPE = 'audio/wav'


class _NoLocalPathDriver(LocalFileStorageDriver):
    """Same storage as `LocalFileStorageDriver`, but reports no local file -
    the one behavioural difference `S3FileStorageDriver` callers must handle."""

    def local_path(self, key):
        return None


class _NoopHookContext:
    data: dict = {}


class _NoopPluginRegistry:
    def execute_hook(self, hook, initial_data=None):
        context = _NoopHookContext()
        context.data = dict(initial_data or {})
        return context, []


@pytest.fixture
def repos_on_test_db(mock_db):
    with patch('src.platform.database.database.db', mock_db):
        yield mock_db


@pytest.fixture
def local_root(tmp_path):
    """The FileStore's own local root - writes/reads must NEVER touch this
    when a storage_driver is injected."""
    root = tmp_path / "local_root"
    root.mkdir()
    return root


@pytest.fixture
def bucket_driver(tmp_path):
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    return _NoLocalPathDriver(str(bucket))


@pytest.fixture
def settings(local_root):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(local_root)
    return settings


@pytest.fixture
def media_store(settings, local_root, bucket_driver):
    return MediaStore(
        file_resolver=FilePathResolver(settings),
        image_processor=ImageProcessor(),
        media_type_resolver=MediaTypeResolver(),
        file_repository=FileRepository(),
        generation_repository=GenerationRepository(),
        settings=settings,
        file_service=FileStore(str(local_root), storage_driver=bucket_driver),
        plugin_registry=_NoopPluginRegistry(),
        upload_repository=UploadRepository(),
        storage_driver=bucket_driver,
    )


@pytest.fixture
def saved_audio(repos_on_test_db, settings, bucket_driver, minimal_wav_file):
    """A real .wav written through the real save path against the bucket
    driver, with its generation row. Returns (generation_id, user_id, filename, bytes)."""
    mock_db = repos_on_test_db
    user_id = generate_ulid()
    gen_id = generate_ulid()

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, 'driver_bypass_serve_tester', 'driver-bypass-serve@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/audio/driver-bypass', '1.0.0', '{}', user_id, 'completed', 1.0),
        )

    handler = AudioGenerationOutputHandler(gen_id, user_id, settings, bucket_driver)
    metadata = handler.handle(AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False))
    assert metadata['processed'] is True

    return gen_id, user_id, Path(metadata['saved_path']).name, minimal_wav_file.read_bytes()


class TestGenerationMediaDriverBypassClosed:
    def test_get_generation_media_serves_from_the_driver(
        self, media_store, saved_audio, local_root
    ):
        gen_id, _user_id, filename, expected_bytes = saved_audio

        result = media_store.get_generation_media(gen_id, filename)

        assert result.use_streaming is False
        assert result.content == expected_bytes
        assert not any(local_root.rglob('*.wav')), "audio leaked onto the FileStore's own local root"

    def test_get_file_by_id_serves_from_the_driver(
        self, media_store, saved_audio
    ):
        from src.features.generation.file_repository import file_repo

        gen_id, _user_id, filename, expected_bytes = saved_audio
        files = file_repo.get_generation_files(gen_id)
        assert len(files) == 1

        result = media_store.get_file_by_id(files[0].id)

        assert result.content == expected_bytes

    def test_delete_generation_media_deletes_through_the_driver(
        self, media_store, saved_audio, bucket_driver
    ):
        gen_id, user_id, _filename, _expected_bytes = saved_audio
        from src.features.generation.repository import generation_repo
        saved_path = generation_repo.get_files(gen_id)[0].file_path
        assert bucket_driver.exists(saved_path)

        result = media_store.delete_generation_media(gen_id, user_id=user_id)

        assert result.deleted_files == 1
        assert result.failed_files == 0
        assert not bucket_driver.exists(saved_path)
