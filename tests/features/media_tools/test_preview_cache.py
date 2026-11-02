from pathlib import Path
from unittest.mock import patch

import pytest

from src.features.media_tools.previews import PreviewCache, is_preview_id


@pytest.fixture
def clips_dir(tmp_path):
    return tmp_path / "clips"


@pytest.fixture
def cache(clips_dir):
    def make():
        clips_dir.mkdir(exist_ok=True)
        return clips_dir

    return PreviewCache(make, frame_bytes=10, frame_entries=2, clip_entries=2)


def produced(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"clip")
    return path


def store(cache, tmp_path, key, user="u1"):
    return cache.store_clip(user, key, produced(tmp_path, f"{key}.mp4"), "video/mp4", duration_seconds=1.0)


def on_disk(clips_dir):
    return sorted(path.name for path in clips_dir.iterdir()) if clips_dir.is_dir() else []


class TestFrames:
    def test_the_oldest_frame_goes_once_the_entry_count_is_passed(self, cache):
        for key in ("a", "b", "c"):
            cache.store_frame("u1", key, b"x", "image/webp")
        assert cache.frame("u1", "a") is None
        assert cache.frame("u1", "b").data == b"x"
        assert cache.frame("u1", "c").cached

    def test_the_byte_budget_evicts_the_least_recently_used(self, clips_dir):
        cache = PreviewCache(lambda: clips_dir, frame_bytes=10, frame_entries=100)
        cache.store_frame("u1", "a", b"1234", "image/webp")
        cache.store_frame("u1", "b", b"5678", "image/webp")
        cache.frame("u1", "a")
        cache.store_frame("u1", "c", b"90x", "image/webp")
        assert cache.frame("u1", "b") is None
        assert [cache.frame("u1", key).data for key in ("a", "c")] == [b"1234", b"90x"]

    def test_a_frame_bigger_than_the_budget_is_not_kept(self, cache):
        cache.store_frame("u1", "a", b"x", "image/webp")
        cache.store_frame("u1", "big", b"x" * 11, "image/webp")
        assert cache.frame("u1", "big") is None
        assert cache.frame("u1", "a") is not None

    def test_frames_are_kept_per_user(self, cache):
        cache.store_frame("u1", "a", b"x", "image/webp")
        assert cache.frame("u2", "a") is None


class TestClips:
    def test_a_clip_is_moved_into_the_cache_and_served_to_its_owner_only(self, cache, tmp_path, clips_dir):
        dto = store(cache, tmp_path, "k1")
        assert is_preview_id(dto.id) and dto.url.endswith(dto.id)
        path, mime = cache.clip_file("u1", dto.id)
        assert path.parent == clips_dir and mime == "video/mp4"
        assert not (tmp_path / "k1.mp4").exists()
        assert cache.clip_file("u2", dto.id) is None
        assert cache.clip("u1", "k1") == dto and cache.clip("u2", "k1") is None

    def test_evicted_clips_leave_the_disk_and_their_id(self, cache, tmp_path, clips_dir):
        first = store(cache, tmp_path, "k1")
        second = store(cache, tmp_path, "k2")
        store(cache, tmp_path, "k3")
        assert cache.clip_file("u1", first.id) is None
        assert cache.clip_file("u1", second.id) is not None
        assert len(on_disk(clips_dir)) == 2

    def test_serving_a_clip_keeps_it_from_being_evicted_first(self, cache, tmp_path):
        first = store(cache, tmp_path, "k1")
        second = store(cache, tmp_path, "k2")
        cache.clip_file("u1", first.id)
        store(cache, tmp_path, "k3")
        assert cache.clip_file("u1", first.id) is not None
        assert cache.clip_file("u1", second.id) is None

    def test_rendering_the_same_key_again_replaces_the_old_file(self, cache, tmp_path, clips_dir):
        old = store(cache, tmp_path, "k1")
        new = store(cache, tmp_path, "k1")
        assert cache.clip_file("u1", old.id) is None
        assert on_disk(clips_dir) == [f"{new.id}.mp4"]

    def test_clear_empties_the_directory(self, cache, tmp_path, clips_dir):
        store(cache, tmp_path, "k1")
        cache.store_frame("u1", "a", b"x", "image/webp")
        cache.clear()
        assert on_disk(clips_dir) == []
        assert cache.frame("u1", "a") is None


class TestFilesStillOpen:
    def locked(self, held):
        original = Path.unlink

        def unlink(path, missing_ok=False):
            if path in held:
                raise PermissionError(32, "The process cannot access the file")
            return original(path, missing_ok=missing_ok)

        return patch.object(Path, "unlink", unlink)

    def test_an_eviction_of_an_open_file_is_queued_and_retried(self, cache, tmp_path, clips_dir):
        first = store(cache, tmp_path, "k1")
        held = {cache.clip_file("u1", first.id)[0]}
        store(cache, tmp_path, "k2")
        with self.locked(held):
            third = store(cache, tmp_path, "k3")
        assert third.id and cache.pending_deletes() == held
        assert cache.clip_file("u1", first.id) is None
        store(cache, tmp_path, "k4")
        assert cache.pending_deletes() == set()
        assert len(on_disk(clips_dir)) == 2

    def test_clear_carries_on_past_an_open_file(self, cache, tmp_path, clips_dir):
        first = store(cache, tmp_path, "k1")
        second = store(cache, tmp_path, "k2")
        held = {cache.clip_file("u1", first.id)[0]}
        cache.store_frame("u1", "a", b"x", "image/webp")
        with self.locked(held):
            cache.clear()
        assert cache.pending_deletes() == held
        assert on_disk(clips_dir) == [f"{first.id}.mp4"]
        assert cache.clip_file("u1", second.id) is None
        assert cache.frame("u1", "a") is None
