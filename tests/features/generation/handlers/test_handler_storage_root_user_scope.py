"""Output handlers must write under the SAME storage root the readers use.

`file_storage_directory` is a per-user setting. Containment
(`GenerationOrchestrator`, via `bind_form`) and serving
(`src.features.media.file_resolver`) both resolve it WITH the owning user, so a
handler that resolves it without one writes into the global root while every
reader looks in the user's - the file exists, its recorded path looks
plausible, and nothing can find it.

Everything here drives the real save path over a real FileStore and a real
migrated schema; the only double is the settings manager, whose whole point is
to answer differently for the global and the per-user root.
"""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler
from src.features.generation.handlers.mesh_handler import MeshGenerationOutputHandler
from src.features.generation.handlers.video_handler import VideoGenerationOutputHandler
from src.features.generation.repository import generation_repo
from src.pipelines.outputs import ImageGenerationOutput, VideoGenerationOutput, AudioGenerationOutput
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid


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
            (user_id, 'root_scope_tester', 'roots@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/test/roots', '1.0.0', '{}', user_id, 'running', 0.0),
        )

    return gen_id, user_id


@pytest.fixture
def split_roots(tmp_path):
    """A global storage root and a per-user override that is NOT under it."""
    global_root = tmp_path / "global_storage"
    user_root = tmp_path / "user_storage"
    for root in (global_root, user_root):
        for sub in ("generations", "tmp", "models"):
            (root / sub).mkdir(parents=True)
    return global_root, user_root


@pytest.fixture
def split_settings(split_roots, generation_id):
    """`get_file_storage_directory()` answers differently with and without a user."""
    global_root, user_root = split_roots
    _, user_id = generation_id

    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.side_effect = (
        lambda uid=None: str(user_root) if uid == user_id else str(global_root)
    )
    return settings


def _written_files(root: Path) -> list:
    return sorted(p for p in root.rglob("*") if p.is_file())


class TestImageHandlerStorageRoot:
    def test_image_lands_under_the_owning_user_s_root(
        self, generation_id, split_settings, split_roots
    ):
        global_root, user_root = split_roots
        gen_id, user_id = generation_id
        handler = ImageGenerationOutputHandler(gen_id, user_id, split_settings)

        metadata = handler.handle(
            ImageGenerationOutput(image=Image.new('RGB', (8, 8), 'red'), temporary=False)
        )

        assert metadata['processed'] is True
        saved_path = metadata['saved_path']

        # Assert on the FULLY RESOLVED path, not merely on "a file was written":
        # the recorded path is storage-root-relative, so it reads as plausible
        # against either root.
        resolved = (user_root / saved_path).resolve()
        assert resolved.exists(), (
            f"image not under the user's root; global root holds {_written_files(global_root)}"
        )
        assert not _written_files(global_root), "image leaked into the global root"

    def test_recorded_file_size_proves_reader_and_writer_agree(
        self, generation_id, split_settings
    ):
        """`_save_file_record` sizes the file against the per-user root: a
        writer/reader split shows up as a `None` size on an existing file."""
        gen_id, user_id = generation_id
        handler = ImageGenerationOutputHandler(gen_id, user_id, split_settings)

        handler.handle(ImageGenerationOutput(image=Image.new('RGB', (8, 8), 'blue'), temporary=False))

        record = generation_repo.get_files(gen_id, is_final=True)[0]
        assert record.file_size is not None and record.file_size > 0


class TestVideoHandlerStorageRoot:
    def test_video_lands_under_the_owning_user_s_root(
        self, generation_id, split_settings, split_roots, tmp_path
    ):
        global_root, user_root = split_roots
        gen_id, user_id = generation_id
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"not-a-real-mp4-but-a-real-file")

        handler = VideoGenerationOutputHandler(gen_id, user_id, split_settings)
        saved_path = handler._save_video_file(
            VideoGenerationOutput(video_path=str(source), temporary=False)
        )

        assert saved_path
        assert (user_root / saved_path).resolve().exists(), (
            f"video not under the user's root; global root holds {_written_files(global_root)}"
        )
        assert not _written_files(global_root), "video leaked into the global root"

    def test_file_record_sizes_the_video_under_the_user_s_root(
        self, generation_id, split_settings, split_roots, tmp_path
    ):
        _, user_root = split_roots
        gen_id, user_id = generation_id
        source = tmp_path / "clip.mp4"
        payload = b"not-a-real-mp4-but-a-real-file"
        source.write_bytes(payload)

        handler = VideoGenerationOutputHandler(gen_id, user_id, split_settings)
        output = VideoGenerationOutput(video_path=str(source), temporary=False)
        saved_path = handler._save_video_file(output)

        with patch.object(handler, '_schedule_async_thumbnail_generation'):
            record = handler._create_file_record(output, saved_path)

        assert record is not None
        assert record.file_size == len(payload)


class TestAudioHandlerStorageRoot:
    def test_audio_lands_under_the_owning_user_s_root(
        self, generation_id, split_settings, split_roots, minimal_wav_file, minimal_wav_bytes
    ):
        global_root, user_root = split_roots
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, split_settings)

        metadata = handler.handle(
            AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False)
        )

        saved_path = metadata['saved_path']
        assert (user_root / saved_path).resolve().exists(), (
            f"audio not under the user's root; global root holds {_written_files(global_root)}"
        )
        assert not _written_files(global_root), "audio leaked into the global root"

        record = generation_repo.get_files(gen_id, is_final=True)[0]
        assert record.file_size == len(minimal_wav_bytes)


class TestMeshHandlerStorageRoot:
    def test_resolved_storage_root_is_the_owning_user_s(self, generation_id, split_settings, split_roots):
        _, user_root = split_roots
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, split_settings)

        assert Path(handler._resolve_storage_dir()).resolve() == user_root.resolve()
