"""Tests for `media_input._check_media_constraints`, the shared
`accepted_types`/`max_resolution`/duration-limit enforcement every
Image/Video/Audio/Media field's `input()` runs through
`process_media_input`.

Exercised directly against `process_media_input` (not through a concrete
field class) so these prove the shared logic once, independent of which
field type's `validate_legacy`/`process_legacy` pair is plugged in - the
per-class wiring is covered in each field's own test file
(test_image.py/test_video.py/test_audio.py/test_media.py).
"""
import unittest

from src.features.fields.media_input import process_media_input


def _noop_validate(item, rules):
    return []


def _noop_process(item):
    return item


def _input(field_name, value, config):
    return process_media_input(
        field_name, value, config,
        validate_legacy=_noop_validate,
        process_legacy=_noop_process,
    )


class TestAcceptedTypes(unittest.TestCase):

    def test_unconfigured_accepted_types_imposes_no_restriction(self):
        item = {"path": "a.bin", "type": "video"}
        self.assertEqual(_input("refs", item, {}), item)

    def test_disallowed_category_is_rejected(self):
        item = {"path": "a.mp3", "type": "audio"}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"accepted_types": ["image", "video"]})
        self.assertIn("type 'audio' is not accepted", str(cm.exception))
        self.assertIn("refs", str(cm.exception))

    def test_allowed_category_passes(self):
        item = {"path": "a.png", "type": "image"}
        self.assertEqual(_input("refs", item, {"accepted_types": ["image", "video"]}), item)

    def test_missing_type_fails_open(self):
        """An item with no discoverable category (bare path string, or a
        dict with no `type` key) can't be checked - skip rather than
        guess."""
        self.assertEqual(_input("refs", "uploads/a.bin", {"accepted_types": ["image"]}), "uploads/a.bin")
        item = {"path": "a.bin"}
        self.assertEqual(_input("refs", item, {"accepted_types": ["image"]}), item)

    def test_multi_field_reports_the_offending_item_number(self):
        items = [
            {"path": "a.png", "type": "image"},
            {"path": "b.mp3", "type": "audio"},
        ]
        with self.assertRaises(ValueError) as cm:
            _input("refs", items, {"multi": True, "accepted_types": ["image"]})
        self.assertIn("item 2", str(cm.exception))


class TestMaxResolution(unittest.TestCase):

    def test_within_bounds_passes(self):
        item = {"path": "a.png", "type": "image", "metadata": {"width": 1024, "height": 1024}}
        self.assertEqual(_input("refs", item, {"max_resolution": 2048}), item)

    def test_width_over_the_cap_is_rejected(self):
        item = {"path": "a.png", "type": "image", "metadata": {"width": 4096, "height": 1024}}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_resolution": 2048})
        self.assertIn("width 4096px exceeds the maximum resolution of 2048px", str(cm.exception))

    def test_height_over_the_cap_is_rejected(self):
        item = {"path": "a.png", "type": "image", "metadata": {"width": 1024, "height": 4096}}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_resolution": 2048})
        self.assertIn("height 4096px exceeds the maximum resolution of 2048px", str(cm.exception))

    def test_applies_to_video_too(self):
        item = {"path": "a.mp4", "type": "video", "metadata": {"width": 4096, "height": 2160}}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_resolution": 2048})
        self.assertIn("width", str(cm.exception))

    def test_does_not_apply_to_audio(self):
        item = {"path": "a.mp3", "type": "audio", "metadata": {"duration_seconds": 5}}
        self.assertEqual(_input("refs", item, {"max_resolution": 2048}), item)

    def test_missing_dimensions_fail_open(self):
        item = {"path": "a.png", "type": "image", "metadata": {}}
        self.assertEqual(_input("refs", item, {"max_resolution": 2048}), item)

    def test_legacy_shape_reads_top_level_dimensions(self):
        item = {"data": "x", "type": "image/png", "width": 4096, "height": 4096}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_resolution": 2048})
        self.assertIn("exceeds the maximum resolution", str(cm.exception))


class TestPerItemDuration(unittest.TestCase):

    def test_video_under_the_cap_passes(self):
        item = {"path": "a.mp4", "type": "video", "metadata": {"duration_seconds": 4}}
        self.assertEqual(_input("refs", item, {"max_video_duration_seconds": 5}), item)

    def test_video_over_the_cap_is_rejected(self):
        item = {"path": "a.mp4", "type": "video", "metadata": {"duration_seconds": 8.4}}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_video_duration_seconds": 5})
        self.assertIn("video duration 8.4s exceeds the per-video maximum of 5s", str(cm.exception))

    def test_audio_over_the_cap_is_rejected(self):
        item = {"path": "a.mp3", "type": "audio", "metadata": {"duration_seconds": 45}}
        with self.assertRaises(ValueError) as cm:
            _input("refs", item, {"max_audio_duration_seconds": 30})
        self.assertIn("audio duration 45s exceeds the per-audio maximum of 30s", str(cm.exception))

    def test_missing_duration_fails_open(self):
        item = {"path": "a.mp4", "type": "video", "metadata": {}}
        self.assertEqual(_input("refs", item, {"max_video_duration_seconds": 5}), item)


class TestTotalDurationAcrossAMixedField(unittest.TestCase):
    """The card's headline case: one field holding images, videos and audio
    at once, with independent totals per category."""

    def _items(self):
        return [
            {"path": "a.png", "type": "image", "metadata": {"width": 512, "height": 512}},
            {"path": "a.mp4", "type": "video", "metadata": {"duration_seconds": 5}},
            {"path": "b.mp4", "type": "video", "metadata": {"duration_seconds": 6}},
            {"path": "a.mp3", "type": "audio", "metadata": {"duration_seconds": 20}},
        ]

    def test_video_total_is_computed_over_video_items_only(self):
        with self.assertRaises(ValueError) as cm:
            _input("refs", self._items(), {"multi": True, "max_total_video_duration_seconds": 10})
        self.assertIn("video items total 11s", str(cm.exception))
        self.assertNotIn("audio items total", str(cm.exception))

    def test_audio_total_is_computed_over_audio_items_only(self):
        with self.assertRaises(ValueError) as cm:
            _input("refs", self._items(), {"multi": True, "max_total_audio_duration_seconds": 10})
        self.assertIn("audio items total 20s", str(cm.exception))
        self.assertNotIn("video items total", str(cm.exception))

    def test_both_totals_within_bounds_passes(self):
        result = _input(
            "refs", self._items(),
            {"multi": True, "max_total_video_duration_seconds": 20, "max_total_audio_duration_seconds": 20},
        )
        self.assertEqual(len(result), 4)

    def test_image_items_do_not_affect_either_total(self):
        items = [{"path": f"{i}.png", "type": "image", "metadata": {"width": 100, "height": 100}} for i in range(5)]
        result = _input(
            "refs", items,
            {"multi": True, "max_total_video_duration_seconds": 0.001, "max_total_audio_duration_seconds": 0.001},
        )
        self.assertEqual(len(result), 5)

    def test_one_unknown_video_duration_makes_the_total_fail_open(self):
        items = [
            {"path": "a.mp4", "type": "video", "metadata": {"duration_seconds": 100}},
            {"path": "b.mp4", "type": "video", "metadata": {}},
        ]
        result = _input("refs", items, {"multi": True, "max_total_video_duration_seconds": 1})
        self.assertEqual(len(result), 2)

    def test_multiple_violations_are_all_reported_together(self):
        items = [
            {"path": "a.mp3", "type": "audio", "metadata": {"duration_seconds": 45}},
            {"path": "b.mp4", "type": "video", "metadata": {"duration_seconds": 8}},
        ]
        with self.assertRaises(ValueError) as cm:
            _input(
                "refs", items,
                {"multi": True, "accepted_types": ["video"], "max_audio_duration_seconds": 30},
            )
        message = str(cm.exception)
        self.assertIn("not accepted", message)
        self.assertIn("audio duration 45s", message)


if __name__ == '__main__':
    unittest.main()
