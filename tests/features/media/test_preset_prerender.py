"""Tests for the background preset-media pre-render scheduler.

Runs against a real `MediaStore` + real `ImageProcessor` (no ffmpeg needed -
only image cover/gallery renders are exercised here) so a render genuinely
lands in the on-disk cache `MediaStore.get_preset_file` itself reads from -
not a mock configured to claim it did.
"""

from unittest.mock import Mock

import pytest
from PIL import Image

from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.preset_prerender import PresetMediaPrerenderQueue
from src.features.media.store import MediaStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver


class _FakePreset:
    """Duck-types the one PresetTemplate shape `_preset_media_paths` reads."""

    def __init__(self, preset_id, media):
        self.id = preset_id
        self.media = media


@pytest.fixture
def media_store(tmp_path):
    resolver = Mock(spec=FilePathResolver)
    resolver.get_storage_directory.return_value = str(tmp_path / "storage")
    store = MediaStore(
        file_resolver=resolver,
        image_processor=ImageProcessor(),
        media_type_resolver=MediaTypeResolver(),
        file_repository=Mock(),
        generation_repository=Mock(),
        settings=Mock(),
        file_service=Mock(),
        plugin_registry=Mock(),
        upload_repository=Mock(),
        storage_driver=LocalFileStorageDriver(str(tmp_path / "storage")),
    )
    return store, resolver


def _drain_synchronously(store, queue_obj):
    """Drain inline, then wait for the worker thread schedule_scan started -
    it may still be mid-render on the item it took before the inline drain."""
    queue_obj._drain()
    queue_obj._queue.join()
    worker = queue_obj._worker
    if worker is not None:
        worker.join(timeout=10)


class TestPresetMediaPrerenderQueue:
    def test_schedule_scan_enqueues_small_and_medium_for_cover_and_gallery(
        self, media_store, tmp_path
    ):
        store, resolver = media_store
        cover = tmp_path / "cover.png"
        gallery_item = tmp_path / "example.png"
        Image.new("RGB", (800, 600)).save(cover)
        Image.new("RGB", (800, 600)).save(gallery_item)

        def resolve(preset_id, file_path):
            return cover if file_path == "public/cover.png" else gallery_item

        resolver.resolve_preset_file.side_effect = resolve

        preset = _FakePreset(
            "preset1",
            {"cover": "public/cover.png", "gallery": [{"src": "public/example.png"}]},
        )
        prerender_queue = PresetMediaPrerenderQueue(store)

        scheduled = prerender_queue.schedule_scan([preset])

        # 2 files x (small, medium) = 4
        assert scheduled == 4
        _drain_synchronously(store, prerender_queue)

        cache_dir = tmp_path / "storage" / "preset_media" / "preset1"
        rendered = list(cache_dir.glob("*.png"))
        assert len(rendered) == 4

    def test_schedule_scan_is_idempotent_once_cached(self, media_store, tmp_path):
        """A second scan after the first has rendered finds nothing pending."""
        store, resolver = media_store
        cover = tmp_path / "cover.png"
        Image.new("RGB", (800, 600)).save(cover)
        resolver.resolve_preset_file.return_value = cover

        preset = _FakePreset("preset1", {"cover": "public/cover.png", "gallery": []})
        prerender_queue = PresetMediaPrerenderQueue(store)

        first = prerender_queue.schedule_scan([preset])
        _drain_synchronously(store, prerender_queue)
        second = prerender_queue.schedule_scan([preset])

        assert first == 2
        assert second == 0

    def test_schedule_scan_skips_presets_with_no_media(self, media_store):
        store, _resolver = media_store
        preset = _FakePreset("preset1", None)
        prerender_queue = PresetMediaPrerenderQueue(store)

        assert prerender_queue.schedule_scan([preset]) == 0

    def test_schedule_scan_ignores_a_broken_file_and_continues(self, media_store, tmp_path):
        """One preset's media failing to resolve must not stop the scan from
        reaching the next one."""
        store, resolver = media_store
        good_cover = tmp_path / "cover.png"
        Image.new("RGB", (400, 300)).save(good_cover)

        def resolve(preset_id, file_path):
            if preset_id == "broken":
                raise ValueError("Preset file not found")
            return good_cover

        resolver.resolve_preset_file.side_effect = resolve

        broken = _FakePreset("broken", {"cover": "public/missing.png", "gallery": []})
        good = _FakePreset("good", {"cover": "public/cover.png", "gallery": []})
        prerender_queue = PresetMediaPrerenderQueue(store)

        scheduled = prerender_queue.schedule_scan([broken, good])

        # `broken`'s cache-check treats an unresolvable path as "not current",
        # so both entries are still enqueued - the resolve failure only
        # surfaces once the worker actually tries to render it.
        assert scheduled == 4
        _drain_synchronously(store, prerender_queue)

        good_cache_dir = tmp_path / "storage" / "preset_media" / "good"
        assert len(list(good_cache_dir.glob("*.png"))) == 2
        broken_cache_dir = tmp_path / "storage" / "preset_media" / "broken"
        assert not broken_cache_dir.exists()
