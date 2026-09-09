"""Upload dedup by content hash (migration 022).

Exercised against the real migrated schema and a real `LocalFileStorageDriver`
rooted at a scratch directory - a mocked `UploadRepository`/storage driver
would just echo back whatever the test told it to, and the whole point here
is the hash lookup + on-disk existence check `MediaStore.upload_media`
performs before deciding to reuse a file.
"""

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.media.store import MediaStore
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.upload_repository import UploadRepository
from src.features.generation.file_repository import FileRepository
from src.features.generation.repository import GenerationRepository
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid


@pytest.fixture
def repos_on_test_db(mock_db):
    """`mock_db` already patches `db` globally for the duration of the test -
    this just names that dependency for fixtures below that need real rows,
    not the raw fixture itself."""
    return mock_db


def _create_user(db, username: str) -> str:
    user_id = generate_ulid()
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, username, f"{username}@example.com", "hashed"),
        )
    return user_id


@pytest.fixture
def user_id(repos_on_test_db):
    return _create_user(repos_on_test_db, "dedup_tester")


@pytest.fixture
def other_user_id(repos_on_test_db):
    return _create_user(repos_on_test_db, "dedup_tester_2")


@pytest.fixture
def storage_dir(tmp_path):
    return tmp_path / "storage"


@pytest.fixture
def manager(repos_on_test_db, storage_dir):
    """A MediaStore wired to a real UploadRepository and a real local storage
    driver, with everything else mocked out - none of it participates in the
    dedup decision itself."""
    file_resolver = Mock(spec=FilePathResolver)
    image_processor = Mock(spec=ImageProcessor)

    media_types = Mock(spec=MediaTypeResolver)
    media_types.is_valid_media_type.return_value = True
    media_types.is_image.return_value = False
    media_types.is_video.return_value = False
    media_types.is_audio.return_value = False

    file_repo = Mock(spec=FileRepository)
    generation_repo = Mock(spec=GenerationRepository)
    settings = Mock(spec=Settings)
    file_service = Mock(spec=FileStore)

    plugin_registry = Mock(spec=PluginRegistry)
    mock_context = Mock()
    mock_context.data = {}
    plugin_registry.execute_hook.return_value = (mock_context, [])

    return MediaStore(
        file_resolver=file_resolver,
        image_processor=image_processor,
        media_type_resolver=media_types,
        file_repository=file_repo,
        generation_repository=generation_repo,
        settings=settings,
        file_service=file_service,
        plugin_registry=plugin_registry,
        upload_repository=UploadRepository(),
        storage_driver=LocalFileStorageDriver(str(storage_dir)),
    )


def _uploaded_files(storage_dir: Path) -> list:
    uploads_dir = storage_dir / "uploads"
    if not uploads_dir.exists():
        return []
    return [p for p in uploads_dir.iterdir() if p.is_file()]


class TestUploadDedup:

    @pytest.mark.asyncio
    async def test_duplicate_upload_reuses_existing_file(self, manager, user_id, storage_dir):
        data = b"identical bytes across both uploads"

        first = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=user_id)
        second = await manager.upload_media(data, "b.bin", "application/octet-stream", user_id=user_id)

        assert second.filename == first.filename
        assert len(_uploaded_files(storage_dir)) == 1
        assert manager.upload_repo.count_for_user(user_id) == 1

    @pytest.mark.asyncio
    async def test_different_user_gets_a_separate_file(
        self, manager, user_id, other_user_id, storage_dir
    ):
        data = b"same bytes, different owners"

        mine = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=user_id)
        theirs = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=other_user_id)

        assert mine.filename != theirs.filename
        assert len(_uploaded_files(storage_dir)) == 2

    @pytest.mark.asyncio
    async def test_a_changed_byte_creates_a_new_file(self, manager, user_id, storage_dir):
        first = await manager.upload_media(b"hello world", "a.bin", "application/octet-stream", user_id=user_id)
        second = await manager.upload_media(b"hello worlD", "a.bin", "application/octet-stream", user_id=user_id)

        assert second.filename != first.filename
        assert len(_uploaded_files(storage_dir)) == 2
        assert manager.upload_repo.count_for_user(user_id) == 2

    @pytest.mark.asyncio
    async def test_a_row_whose_file_was_deleted_from_disk_is_not_reused(
        self, manager, user_id, storage_dir
    ):
        data = b"bytes whose file goes missing"

        first = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=user_id)
        (storage_dir / "uploads" / first.filename).unlink()

        second = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=user_id)

        assert second.filename != first.filename
        # The stale row is untouched (dedup never deletes rows) - the fresh
        # upload adds a second one, and exactly one file sits on disk.
        assert manager.upload_repo.count_for_user(user_id) == 2
        assert len(_uploaded_files(storage_dir)) == 1

    @pytest.mark.asyncio
    async def test_a_different_purpose_is_not_reused(self, manager, user_id, storage_dir):
        data = b"bytes shared by two purposes"

        library_upload = await manager.upload_media(
            data, "a.bin", "application/octet-stream", user_id=user_id, purpose="user_upload"
        )
        derived = await manager.upload_media(
            data, "a.bin", "application/octet-stream", user_id=user_id, purpose="derived_artifact"
        )

        assert derived.filename != library_upload.filename
        assert len(_uploaded_files(storage_dir)) == 2

    @pytest.mark.asyncio
    async def test_content_hash_is_the_sha256_of_the_bytes(self, manager, user_id):
        data = b"hash me"

        result = await manager.upload_media(data, "a.bin", "application/octet-stream", user_id=user_id)

        stored = manager.upload_repo.get_by_filename(result.filename, user_id)
        assert stored.content_hash == hashlib.sha256(data).hexdigest()
