"""Tests for the bounded-memory contract of GenerationHistoryArchive's zip
export builders (export_zip / export_bundle): the archive is built into a
SpooledTemporaryFile that rolls to disk above a fixed threshold instead of
retaining the whole zip in RAM, and the spooled file is closed (not leaked)
if building it fails partway through.
"""

import tempfile
import tracemalloc
import zipfile

import pytest
from unittest.mock import Mock

from src.features.generation import history_archive as history_archive_module
from src.features.generation.history_archive import GenerationHistoryArchive
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.exceptions import GenerationNotFoundException
from src.platform.filesystem.file_store import FileStore


def _make_file(relative_path, file_type='IMAGE', mime_type='image/png'):
    f = Mock()
    f.file_path = relative_path
    f.file_type = file_type
    f.mime_type = mime_type
    return f


class _Harness:
    """Same double shape as test_export.py: a real FileStore over a tmp_path
    so export reads bytes back through `file_service.local_copy_of`."""

    def __init__(self, tmp_path):
        self.mock_repo = Mock()
        self.mock_plugins = Mock()
        self.query = GenerationHistoryQuery(generation_repo=self.mock_repo)
        self.file_service = FileStore(str(tmp_path))
        self.archive = GenerationHistoryArchive(
            self.mock_repo, self.file_service, self.mock_plugins, self.query, Mock()
        )
        self.tmp_path = tmp_path

    def write_source_file(self, generation_id, idx, content: bytes):
        path = self.tmp_path / f"generations/2025-01-01/{generation_id}/{idx}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return f"generations/2025-01-01/{generation_id}/{idx}.bin"


class TestExportZipSpoolThreshold:
    def test_stays_in_memory_below_threshold(self, tmp_path, monkeypatch):
        harness = _Harness(tmp_path)
        monkeypatch.setattr(history_archive_module, "_EXPORT_SPOOL_MAX_MEMORY_BYTES", 10 * 1024 * 1024)

        relative = harness.write_source_file("gen1", 0, b"small content")
        harness.mock_repo.get_by_id.return_value = Mock()
        harness.mock_repo.get_files.return_value = [_make_file(relative, file_type='VIDEO', mime_type='video/mp4')]

        zip_file, _ = harness.archive.export_zip(["gen1"], "user-1")
        try:
            assert zip_file._rolled is False
        finally:
            zip_file.close()

    def test_rolls_over_to_disk_above_threshold(self, tmp_path, monkeypatch):
        harness = _Harness(tmp_path)
        # A tiny threshold forces rollover even for this small fixture, without
        # needing a multi-megabyte source file to prove the mechanism works.
        monkeypatch.setattr(history_archive_module, "_EXPORT_SPOOL_MAX_MEMORY_BYTES", 16)

        relative = harness.write_source_file("gen1", 0, b"x" * 500)
        harness.mock_repo.get_by_id.return_value = Mock()
        harness.mock_repo.get_files.return_value = [_make_file(relative, file_type='VIDEO', mime_type='video/mp4')]

        zip_file, _ = harness.archive.export_zip(["gen1"], "user-1")
        try:
            assert zip_file._rolled is True
        finally:
            zip_file.close()

    def test_peak_memory_bounded_as_export_size_grows(self, tmp_path, monkeypatch):
        """Peak retained memory for the export should track the spool
        threshold, not the total exported size."""
        harness = _Harness(tmp_path)
        spool_threshold = 512 * 1024
        monkeypatch.setattr(history_archive_module, "_EXPORT_SPOOL_MAX_MEMORY_BYTES", spool_threshold)

        # Incompressible content so ZIP_DEFLATED can't shrink it away - a
        # deflate bomb of zeros would defeat the measurement.
        file_size = 4 * 1024 * 1024
        file_count = 4
        total_size = file_size * file_count
        content = bytes((i % 256 for i in range(file_size)))
        for idx in range(file_count):
            harness.write_source_file("gen1", idx, content)

        harness.mock_repo.get_by_id.return_value = Mock()
        harness.mock_repo.get_files.return_value = [
            _make_file(f"generations/2025-01-01/gen1/{idx}.bin", file_type='VIDEO', mime_type='video/mp4')
            for idx in range(file_count)
        ]

        tracemalloc.start()
        try:
            zip_file, _ = harness.archive.export_zip(["gen1"], "user-1")
            try:
                _current, peak = tracemalloc.get_traced_memory()
            finally:
                zip_file.close()
        finally:
            tracemalloc.stop()

        # Peak Python-heap usage stays close to the spool threshold, nowhere
        # near the full archive size - the old io.BytesIO implementation would
        # retain the whole thing (>= total_size).
        assert total_size >= 16 * 1024 * 1024
        assert peak < total_size / 2


class TestExportZipCleanupOnFailure:
    def test_spooled_file_is_closed_when_a_later_generation_is_not_found(self, tmp_path, monkeypatch):
        harness = _Harness(tmp_path)
        relative = harness.write_source_file("gen1", 0, b"content")

        # gen1 is owned, gen2 is not - export_zip must raise partway through
        # building the archive, after some bytes have already been written.
        def get_by_id(generation_id, *args, **kwargs):
            return Mock() if generation_id == "gen1" else None

        harness.mock_repo.get_by_id.side_effect = get_by_id
        harness.mock_repo.get_files.return_value = [_make_file(relative, file_type='VIDEO', mime_type='video/mp4')]

        created = []
        real_spooled = tempfile.SpooledTemporaryFile

        def spy_spooled(*args, **kwargs):
            instance = real_spooled(*args, **kwargs)
            created.append(instance)
            return instance

        monkeypatch.setattr(history_archive_module.tempfile, "SpooledTemporaryFile", spy_spooled)

        with pytest.raises(GenerationNotFoundException):
            harness.archive.export_zip(["gen1", "gen2"], "user-1")

        assert len(created) == 1
        assert created[0].closed is True


class TestExportBundleSpoolThreshold:
    def test_rolls_over_to_disk_above_threshold(self, tmp_path, monkeypatch):
        from tests.fixtures.persistence_base import PersistenceTestBase

        class _Harness(PersistenceTestBase):
            def runTest(self):
                pass

        db_harness = _Harness()
        db_harness.setUp()
        try:
            monkeypatch.setattr(history_archive_module, "_EXPORT_SPOOL_MAX_MEMORY_BYTES", 16)

            from src.features.generation.repository import GenerationRepository
            from src.features.generation.records import Generation
            from src.platform.util.ids import generate_ulid

            user_id = db_harness.create_test_user()
            generation_repo = GenerationRepository()
            gen_id = generate_ulid()
            generation_repo.create(Generation(
                id=gen_id,
                preset_id=None,
                form_data={"prompt": "a cat", "seed": 1},
                user_id=user_id,
                mode="txt2img",
                form_name="default",
            ))

            mock_file_service = Mock()
            mock_file_service.generation_exists.return_value = False
            query = GenerationHistoryQuery(generation_repo=generation_repo)
            archive = GenerationHistoryArchive(
                generation_repo, mock_file_service, Mock(), query, Mock()
            )

            zip_file, _ = archive.export_bundle(gen_id, user_id)
            try:
                assert zip_file._rolled is True
                zf = zipfile.ZipFile(zip_file)
                assert "generation.json" in zf.namelist()
            finally:
                zip_file.close()
        finally:
            db_harness.tearDown()
