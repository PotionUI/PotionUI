"""
Tests for FileStore.

This module tests all functionality of the FileStore class including
file operations, directory management, and cleanup operations.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from src.platform.filesystem.file_store import FileStore


class TestFileService:
    """Test cases for FileStore."""
    
    def setup_method(self):
        """Set up test environment with temporary directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileStore(base_storage_dir=self.temp_dir)
        self.test_generation_id = "test_generation_123"
    
    def teardown_method(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init(self):
        """Test FileStore initialization."""
        assert self.file_service.base_storage_dir == Path(self.temp_dir)
        assert self.file_service.base_storage_dir.exists()
        assert self.file_service.generations_dir.exists()
        assert self.file_service.tmp_dir.exists()
        assert self.file_service.models_dir.exists()
    
    def test_init_creates_directory(self):
        """Test that initialization creates base directory if it doesn't exist."""
        non_existent_dir = Path(self.temp_dir) / "new_dir"
        service = FileStore(base_storage_dir=str(non_existent_dir))
        assert non_existent_dir.exists()
        assert (non_existent_dir / "generations").exists()
        assert (non_existent_dir / "tmp").exists()
        assert (non_existent_dir / "models").exists()
    
    def test_save_file_success(self):
        """Test successful file saving."""
        test_data = b"test image data"

        with patch('src.platform.util.ids.generate_ulid', return_value="test_ulid"):
            file_path, metadata = self.file_service.save_file(
                self.test_generation_id,
                test_data,
                extension="jpg",
                storage_type='generations'
            )

        assert file_path is not None
        assert metadata is not None
        assert Path(file_path).exists()
        assert metadata['file_type'] == 'IMAGE'
        assert metadata['mime_type'] == 'image/jpeg'
        assert metadata['file_size'] == len(test_data)

        # Verify file contents
        with open(file_path, 'rb') as f:
            assert f.read() == test_data
    
    def test_save_file_with_prefix(self):
        """Test file saving with prefix."""
        test_data = b"test data"
        prefix = "0"  # For generation files, prefix is the numeric index

        with patch('src.platform.util.ids.generate_ulid', return_value="test_ulid"):
            file_path, metadata = self.file_service.save_file(
                self.test_generation_id,
                test_data,
                prefix=prefix,
                storage_type='generations'
            )

        assert file_path is not None
        assert metadata is not None
        filename = Path(file_path).name
        # For generation files, the filename is just "{prefix}.{extension}"
        assert filename == f"{prefix}.png"  # default extension
    
    def test_save_file_failure(self):
        """Test file saving failure handling."""
        test_data = b"test data"

        # Mock Path to raise exception
        with patch('pathlib.Path.mkdir', side_effect=OSError("Permission denied")):
            file_path, metadata = self.file_service.save_file(
                self.test_generation_id,
                test_data
            )

        assert file_path is None
        assert metadata is None

    def test_save_file_no_part_files_left_behind(self):
        """A normal save leaves no leftover .part temp files in the directory."""
        test_data = b"test image data"

        file_path, metadata = self.file_service.save_file(
            self.test_generation_id,
            test_data,
            extension="jpg",
            storage_type='generations'
        )

        assert file_path is not None
        assert metadata is not None
        gen_dir = Path(file_path).parent
        part_files = list(gen_dir.glob("*.part"))
        assert part_files == []
        # The full file is there and complete
        assert Path(file_path).read_bytes() == test_data

    def test_save_file_mid_write_failure_leaves_no_partial_file(self):
        """If the write fails partway through, no truncated file must reach the final path."""
        test_data = b"test image data" * 100

        original_open = open

        def failing_open(path, mode='r', *args, **kwargs):
            f = original_open(path, mode, *args, **kwargs)
            if mode == 'wb' and str(path).endswith('.part'):
                original_write = f.write

                def raising_write(data):
                    original_write(data)
                    raise OSError("Simulated disk full")

                f.write = raising_write
            return f

        with patch('src.platform.filesystem.storage_driver.open', side_effect=failing_open):
            file_path, metadata = self.file_service.save_file(
                self.test_generation_id,
                test_data,
                extension="jpg",
                storage_type='generations'
            )

        assert file_path is None
        assert metadata is None

        gen_dir = self.file_service.generations_dir / datetime.now().strftime('%Y-%m-%d') / self.test_generation_id
        # No file at the final path
        final_files = [f for f in gen_dir.glob("*.jpg")]
        assert final_files == []
        # No leftover temp file either
        part_files = list(gen_dir.glob("*.part"))
        assert part_files == []

    def test_save_file_fsync_failure_leaves_no_partial_file(self):
        """If fsync fails after the bytes are written, the temp file must still be cleaned up
        and no file must appear at the final path."""
        test_data = b"test data"

        with patch('src.platform.filesystem.storage_driver.os.fsync', side_effect=OSError("I/O error")):
            file_path, metadata = self.file_service.save_file(
                self.test_generation_id,
                test_data,
                extension="png",
                storage_type='generations'
            )

        assert file_path is None
        assert metadata is None

        gen_dir = self.file_service.generations_dir / datetime.now().strftime('%Y-%m-%d') / self.test_generation_id
        assert list(gen_dir.glob("*.png")) == []
        assert list(gen_dir.glob("*.part")) == []

    def test_save_file_from_path_success(self):
        """Test successful streamed save from a source path."""
        test_data = b"test video data" * 1000
        source = Path(self.temp_dir) / "source.mp4"
        source.write_bytes(test_data)

        file_path, metadata = self.file_service.save_file_from_path(
            self.test_generation_id,
            str(source),
            extension="mp4",
            storage_type='generations'
        )

        assert file_path is not None
        assert metadata is not None
        assert Path(file_path).exists()
        assert metadata['file_type'] == 'VIDEO'
        assert metadata['mime_type'] == 'video/mp4'
        assert metadata['file_size'] == len(test_data)
        assert Path(file_path).read_bytes() == test_data

    def test_save_file_from_path_types_a_glb_as_mesh(self):
        """`.glb` must not fall through to the 'IMAGE' default."""
        source = Path(self.temp_dir) / "source.glb"
        source.write_bytes(b"glTF fake bytes")

        _file_path, metadata = self.file_service.save_file_from_path(
            self.test_generation_id,
            str(source),
            extension="glb",
            storage_type='generations'
        )

        assert metadata['file_type'] == 'MESH'

    def test_determine_file_type_glb(self):
        assert self.file_service.determine_file_type('glb') == 'MESH'
        assert self.file_service.determine_file_type('.GLB') == 'MESH'

    def test_determine_file_type_unregistered_mesh_extension_falls_through(self):
        """No format is registered for `.ply` - it must not be classified MESH."""
        assert self.file_service.determine_file_type('ply') != 'MESH'

    def test_determine_file_type_driven_by_mesh_format_registry(self):
        """Classification tracks the registry, not a hardcoded literal.

        Registering a second self-contained format must make `determine_file_type`
        recognize it without any change to `FileStore` itself.
        """
        from src.platform.filesystem.mesh_formats import MeshFormat, mesh_format_registry

        assert self.file_service.determine_file_type('ply') != 'MESH'

        mesh_format_registry.register(
            MeshFormat(extension='.ply', mime_type='application/x-ply', probe=lambda path: (None, None))
        )
        try:
            assert self.file_service.determine_file_type('ply') == 'MESH'
            assert self.file_service.determine_file_type('.PLY') == 'MESH'
        finally:
            del mesh_format_registry._by_extension['.ply']

    def test_determine_file_type_wav(self):
        """A `.wav` must not fall through to the 'IMAGE' default (the bug
        `audio_formats` fixes: FileType previously had no 'AUDIO' member and
        this branch didn't exist at all)."""
        assert self.file_service.determine_file_type('wav') == 'AUDIO'
        assert self.file_service.determine_file_type('.WAV') == 'AUDIO'

    @pytest.mark.parametrize("extension", ['mp3', 'ogg', 'flac', 'm4a', 'aac'])
    def test_determine_file_type_other_audio_extensions(self, extension):
        assert self.file_service.determine_file_type(extension) == 'AUDIO'

    def test_determine_file_type_unregistered_audio_extension_falls_through(self):
        """No format is registered for `.opus` - it must not be classified AUDIO."""
        assert self.file_service.determine_file_type('opus') != 'AUDIO'

    def test_save_file_from_path_types_a_wav_as_audio(self):
        """`.wav` must not fall through to the 'IMAGE' default.

        Only `file_type` is pinned here, not `mime_type`: `FileStore.
        get_mime_type` goes through the stdlib `mimetypes` module (a
        different source of truth than `audio_formats`/`MediaTypeResolver`,
        which is what the serve path actually uses - see media_types.py and
        `test_media_types.py`), and the stdlib maps `.wav` to `audio/x-wav`
        on this platform rather than `audio/wav`.
        """
        source = Path(self.temp_dir) / "source.wav"
        source.write_bytes(b"fake wav bytes")

        _file_path, metadata = self.file_service.save_file_from_path(
            self.test_generation_id,
            str(source),
            extension="wav",
            storage_type='generations'
        )

        assert metadata['file_type'] == 'AUDIO'

    def test_save_file_from_path_does_not_consume_source(self):
        """The source file must survive the copy - callers may read it again."""
        test_data = b"source bytes"
        source = Path(self.temp_dir) / "source.mp4"
        source.write_bytes(test_data)

        self.file_service.save_file_from_path(
            self.test_generation_id, str(source), extension="mp4"
        )

        assert source.exists()
        assert source.read_bytes() == test_data

    def test_save_file_from_path_missing_source_fails_cleanly(self):
        """A source path that doesn't exist fails without raising, matching
        `save_file`'s (None, None) failure contract."""
        file_path, metadata = self.file_service.save_file_from_path(
            self.test_generation_id, "/nonexistent/source.mp4", extension="mp4"
        )

        assert file_path is None
        assert metadata is None

    def test_save_file_from_path_no_part_files_left_behind(self):
        """A normal streamed save leaves no leftover .part temp files."""
        test_data = b"video bytes" * 500
        source = Path(self.temp_dir) / "source.mp4"
        source.write_bytes(test_data)

        file_path, metadata = self.file_service.save_file_from_path(
            self.test_generation_id, str(source), extension="mp4"
        )

        assert file_path is not None
        gen_dir = Path(file_path).parent
        assert list(gen_dir.glob("*.part")) == []
        assert Path(file_path).read_bytes() == test_data

    def test_save_file_from_path_mid_copy_failure_leaves_no_partial_file(self):
        """If the streamed copy fails partway through, no truncated file must
        reach the final path, and the .part temp file is cleaned up."""
        test_data = b"video bytes" * 500
        source = Path(self.temp_dir) / "source.mp4"
        source.write_bytes(test_data)

        original_open = open

        def failing_open(path, mode='r', *args, **kwargs):
            f = original_open(path, mode, *args, **kwargs)
            if mode == 'wb' and str(path).endswith('.part'):
                original_write = f.write

                def raising_write(data):
                    original_write(data)
                    raise OSError("Simulated disk full")

                f.write = raising_write
            return f

        with patch('src.platform.filesystem.storage_driver.open', side_effect=failing_open):
            file_path, metadata = self.file_service.save_file_from_path(
                self.test_generation_id, str(source), extension="mp4"
            )

        assert file_path is None
        assert metadata is None

        gen_dir = self.file_service.generations_dir / datetime.now().strftime('%Y-%m-%d') / self.test_generation_id
        assert list(gen_dir.glob("*.mp4")) == []
        assert list(gen_dir.glob("*.part")) == []

    def test_delete_generation_outputs_success(self):
        """Deletion takes an explicit key list - the caller's DB-known
        file_path plus thumbnail paths - and removes each through the
        storage driver, not by scanning the generation directory."""
        gen_dir = self.file_service.generations_dir / datetime.now().strftime('%Y-%m-%d') / self.test_generation_id
        gen_dir.mkdir(parents=True, exist_ok=True)
        test_files = ["file1.png", "file2.jpg", "file3.txt"]
        relative_paths = []

        for filename in test_files:
            file_path = gen_dir / filename
            file_path.write_bytes(b"test data")
            relative_paths.append(self.file_service.get_relative_path(str(file_path)))

        deleted, failed = self.file_service.delete_generation_outputs(relative_paths)

        assert deleted == 3
        assert failed == 0

        # Verify files are deleted
        for filename in test_files:
            assert not (gen_dir / filename).exists()

    def test_delete_generation_outputs_missing_key_is_not_a_failure(self):
        """A key with nothing behind it (e.g. an ungenerated thumbnail size)
        is silently skipped, not counted as a failure."""
        deleted, failed = self.file_service.delete_generation_outputs(
            [f"generations/2024-01-15/{self.test_generation_id}/missing.png"]
        )

        assert deleted == 0
        assert failed == 0

    def test_delete_generation_outputs_with_failures(self):
        """An unexpected error deleting one key is tallied as a failure and
        does not stop the rest of the batch."""
        gen_dir = self.file_service.generations_dir / datetime.now().strftime('%Y-%m-%d') / self.test_generation_id
        gen_dir.mkdir(parents=True, exist_ok=True)

        regular_file = gen_dir / "regular.txt"
        regular_file.write_bytes(b"test")
        failing_file = gen_dir / "failing.txt"
        failing_file.write_bytes(b"test")

        relative_paths = [
            self.file_service.get_relative_path(str(regular_file)),
            self.file_service.get_relative_path(str(failing_file)),
        ]

        original_unlink = Path.unlink
        def mock_unlink(self):
            if self.name == "failing.txt":
                raise OSError("Permission denied")
            original_unlink(self)

        with patch.object(Path, 'unlink', mock_unlink):
            deleted, failed = self.file_service.delete_generation_outputs(relative_paths)

        assert deleted == 1
        assert failed == 1
    

class TestFileServiceIntegration:
    """Integration tests for FileStore with realistic workflows."""

    def setup_method(self):
        """Set up integration test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileStore(base_storage_dir=self.temp_dir)
    
    def teardown_method(self):
        """Clean up integration test environment."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_full_generation_workflow(self):
        """Test complete workflow: create directory, save files, list, delete."""
        generation_id = "integration_test_gen"
        test_files_data = {
            "main_image.png": b"main image data",
            "thumbnail.jpg": b"thumbnail data",
            "metadata.json": b'{"prompt": "test"}'
        }
        
        # Save multiple files
        saved_paths = []
        saved_keys = []
        for filename, data in test_files_data.items():
            prefix, ext = filename.split('.')
            with patch('src.platform.util.ids.generate_ulid', return_value=f"ulid_{prefix}"):
                path, metadata = self.file_service.save_file(
                    generation_id, data, extension=ext, prefix=prefix
                )
                saved_paths.append(path)
                saved_keys.append(metadata['file_path'])

        # Verify all files were saved
        assert all(path is not None for path in saved_paths)
        assert all(self.file_service.generation_exists(key) for key in saved_keys)

        # Delete all files (caller supplies the keys, as media_manager does from its `files` rows)
        deleted, failed = self.file_service.delete_generation_outputs(saved_keys)
        assert deleted == 3
        assert failed == 0

        # Verify cleanup
        assert not any(self.file_service.generation_exists(key) for key in saved_keys)

    def test_multiple_generations_isolation(self):
        """Test that multiple generations are properly isolated."""
        gen1_id = "generation_1"
        gen2_id = "generation_2"

        # Save files for both generations
        with patch('src.platform.util.ids.generate_ulid', side_effect=["ulid1", "ulid2"]):
            path1, metadata1 = self.file_service.save_file(gen1_id, b"data1")
            path2, metadata2 = self.file_service.save_file(gen2_id, b"data2")

        key1 = metadata1['file_path']
        key2 = metadata2['file_path']

        # Verify isolation
        assert key1 != key2
        assert self.file_service.generation_exists(key1)
        assert self.file_service.generation_exists(key2)

        # Delete one generation should not affect the other
        deleted, failed = self.file_service.delete_generation_outputs([key1])
        assert deleted == 1

        assert not self.file_service.generation_exists(key1)
        assert self.file_service.generation_exists(key2)


from src.platform.filesystem.storage_driver import LocalFileStorageDriver


class _NoLocalPathDriver(LocalFileStorageDriver):
    """Same storage as `LocalFileStorageDriver`, but reports no local file -
    the one behavioural difference `S3FileStorageDriver` callers must handle."""

    def local_path(self, key):
        return None


class TestFileStoreGenerationsDriverBypassClosed:
    """`storage_type='generations'` writes/reads/deletes must go entirely
    through `self.storage_driver` - never a raw `Path`/`os` shortcut onto
    `base_storage_dir` that would miss a non-local backend."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        # The driver's own storage lives OUTSIDE base_storage_dir, so a
        # fallback to a raw filesystem path built from base_storage_dir
        # would miss it entirely.
        self.driver_root = Path(self.temp_dir) / "bucket"
        self.driver_root.mkdir(parents=True, exist_ok=True)
        self.storage_driver = _NoLocalPathDriver(str(self.driver_root))

        self.local_root = Path(self.temp_dir) / "local"
        self.file_service = FileStore(
            base_storage_dir=str(self.local_root), storage_driver=self.storage_driver
        )
        self.generation_id = "gen_bypass_test"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_file_writes_only_through_driver(self):
        data = b"bucket image bytes"
        full_path, metadata = self.file_service.save_file(
            self.generation_id, data, extension="png", prefix="0"
        )

        assert metadata is not None
        key = metadata['file_path']
        assert self.storage_driver.get_bytes(key) == data
        # Nothing was written under the FileStore's own local root.
        assert not any(self.local_root.rglob("*.png"))

    def test_save_file_from_path_writes_only_through_driver(self):
        source = Path(self.temp_dir) / "source.mp4"
        data = b"bucket video bytes" * 100
        source.write_bytes(data)

        full_path, metadata = self.file_service.save_file_from_path(
            self.generation_id, str(source), extension="mp4", prefix="0"
        )

        assert metadata is not None
        key = metadata['file_path']
        assert self.storage_driver.get_bytes(key) == data
        assert not any(self.local_root.rglob("*.mp4"))

    def test_generation_exists_goes_through_driver(self):
        data = b"bucket bytes"
        _, metadata = self.file_service.save_file(self.generation_id, data, prefix="0")
        key = metadata['file_path']

        assert self.file_service.generation_exists(key) is True
        assert self.file_service.generation_exists("generations/nope/nope/nope.png") is False

    def test_local_copy_of_materializes_a_real_path(self):
        data = b"bucket bytes for probing"
        _, metadata = self.file_service.save_file(self.generation_id, data, extension="png", prefix="0")
        key = metadata['file_path']

        with self.file_service.local_copy_of(key, suffix=".png") as local_path:
            assert local_path.exists()
            assert local_path.read_bytes() == data
        # Materialized copy is cleaned up on exit.
        assert not local_path.exists()

    def test_delete_generation_outputs_goes_through_driver(self):
        _, metadata = self.file_service.save_file(self.generation_id, b"data", prefix="0")
        key = metadata['file_path']
        assert self.storage_driver.exists(key)

        deleted, failed = self.file_service.delete_generation_outputs([key])

        assert deleted == 1
        assert failed == 0
        assert not self.storage_driver.exists(key)