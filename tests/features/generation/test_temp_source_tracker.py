"""Tests for TempSourceTracker: per-generation registry of temp video
source files, unlinked once their owning generation reaches a terminal state.
"""

import os
import tempfile

import pytest

from src.features.generation.temp_source_tracker import TempSourceTracker


@pytest.fixture
def tracker():
    return TempSourceTracker()


@pytest.fixture
def temp_file():
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


class TestRegisterAndCleanup:
    def test_cleanup_unlinks_registered_temp_file(self, tracker, temp_file):
        tracker.register("gen-1", temp_file)

        removed = tracker.cleanup("gen-1")

        assert removed == 1
        assert not os.path.exists(temp_file)

    def test_cleanup_forgets_generation_after_running(self, tracker, temp_file):
        tracker.register("gen-1", temp_file)
        tracker.cleanup("gen-1")

        # Second call finds nothing left to remove -- not an error.
        assert tracker.cleanup("gen-1") == 0

    def test_cleanup_dedups_repeated_registration_of_same_path(self, tracker, temp_file):
        """The same source path is read by more than one handler invocation
        (temporary preview, then final save) -- registering it twice must not
        double-count or double-unlink."""
        tracker.register("gen-1", temp_file)
        tracker.register("gen-1", temp_file)

        assert tracker.cleanup("gen-1") == 1

    def test_cleanup_unknown_generation_is_a_noop(self, tracker):
        assert tracker.cleanup("never-registered") == 0

    def test_cleanup_missing_file_is_not_an_error(self, tracker, temp_file):
        """A file already removed (e.g. by a manual cleanup, or a race) must
        not raise -- idempotent unlink."""
        tracker.register("gen-1", temp_file)
        os.remove(temp_file)

        removed = tracker.cleanup("gen-1")

        assert removed == 0

    def test_cleanup_only_removes_paths_for_the_given_generation(self, tracker, temp_file):
        fd, other_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        try:
            tracker.register("gen-1", temp_file)
            tracker.register("gen-2", other_path)

            tracker.cleanup("gen-1")

            assert not os.path.exists(temp_file)
            assert os.path.exists(other_path)
        finally:
            if os.path.exists(other_path):
                os.remove(other_path)


class TestStorageDirGuard:
    """Only files under the system temp dir may ever be unlinked -- never a
    path inside storage/, even if such a path were mistakenly registered.

    ``tmp_path``/``tempfile.mkstemp`` both resolve under the real system temp
    dir, so a "storage-like" path here is instead rooted next to this test
    file (guaranteed outside ``tempfile.gettempdir()``) rather than under any
    pytest-provided temp fixture.
    """

    @pytest.fixture
    def storage_like_dir(self):
        base = os.path.join(os.path.dirname(__file__), "_be140_fake_storage")
        os.makedirs(base, exist_ok=True)
        yield base
        import shutil
        shutil.rmtree(base, ignore_errors=True)

    def test_register_ignores_path_outside_temp_dir(self, tracker, storage_like_dir):
        storage_like_path = os.path.join(storage_like_dir, "generations", "gen-1", "1.mp4")
        os.makedirs(os.path.dirname(storage_like_path), exist_ok=True)
        with open(storage_like_path, "wb") as f:
            f.write(b"data")

        tracker.register("gen-1", storage_like_path)
        removed = tracker.cleanup("gen-1")

        assert removed == 0
        assert os.path.exists(storage_like_path)

    def test_cleanup_defensively_refuses_non_temp_path_even_if_registered_directly(self, tracker, storage_like_dir):
        """Belt-and-suspenders: even if a non-temp path ended up in the
        internal set (e.g. a future bug bypassing register()), cleanup()
        itself re-checks before unlinking."""
        storage_like_path = os.path.join(storage_like_dir, "1.mp4")
        with open(storage_like_path, "wb") as f:
            f.write(b"data")

        with tracker._lock:
            tracker._paths["gen-1"] = {storage_like_path}

        removed = tracker.cleanup("gen-1")

        assert removed == 0
        assert os.path.exists(storage_like_path)


class TestRegisterGuards:
    def test_register_ignores_none_generation_id(self, tracker, temp_file):
        tracker.register(None, temp_file)
        assert tracker.cleanup("") == 0

    def test_register_ignores_none_path(self, tracker):
        tracker.register("gen-1", None)
        assert tracker.cleanup("gen-1") == 0

    def test_register_ignores_empty_generation_id(self, tracker, temp_file):
        tracker.register("", temp_file)
        assert tracker.cleanup("") == 0
