"""The thumbnail profile: what the named profiles are, how settings become
one, and how a stored row's fingerprint is computed."""

import unittest
from unittest.mock import Mock

from src.features.generation.thumbnail_profile import (
    DEFAULT_PROFILE,
    PROFILES,
    SIZE_WIDTHS,
    ThumbnailProfile,
    estimate_bytes,
    fallback_sizes,
    load_thumbnail_profile,
    match_profile,
    profile_hash,
    validate_setting,
)


def _settings(**values):
    """A Settings stand-in whose `get_setting` answers from `values` and falls
    back to the caller's default for anything else."""
    settings = Mock()
    settings.get_setting.side_effect = lambda key, default=None, user_id=None: values.get(key, default)
    return settings


class TestNamedProfiles(unittest.TestCase):

    def test_full_reproduces_the_pre_profile_behaviour(self):
        full = PROFILES["full"]
        self.assertEqual(full.sizes, ("small", "medium", "large"))
        self.assertEqual(full.video_seconds, 3)
        self.assertEqual(full.video_quality, 50)
        self.assertEqual(full.image_quality, 85)

    def test_balanced_is_the_default(self):
        self.assertIs(DEFAULT_PROFILE, PROFILES["balanced"])
        self.assertEqual(DEFAULT_PROFILE.sizes, ("medium",))

    def test_compact_is_the_cheapest_of_the_three(self):
        compact = PROFILES["compact"]
        self.assertEqual(compact.sizes, ("small",))
        self.assertLess(compact.video_fps, PROFILES["balanced"].video_fps)
        self.assertLess(compact.video_seconds, PROFILES["balanced"].video_seconds)

    def test_widths_are_ordered_small_first_regardless_of_declaration_order(self):
        profile = ThumbnailProfile(("large", "small"), 12, 3, 50, 85)
        self.assertEqual(profile.widths(), (("small", 480), ("large", 1024)))

    def test_size_widths_are_the_single_source(self):
        self.assertEqual(SIZE_WIDTHS, {"small": 480, "medium": 768, "large": 1024})


class TestProfileHash(unittest.TestCase):

    def test_hash_is_stable_across_calls(self):
        self.assertEqual(profile_hash(PROFILES["balanced"]), profile_hash(PROFILES["balanced"]))

    def test_hash_ignores_the_order_sizes_are_declared_in(self):
        one = ThumbnailProfile(("small", "large"), 12, 3, 50, 85)
        other = ThumbnailProfile(("large", "small"), 12, 3, 50, 85)
        self.assertEqual(profile_hash(one), profile_hash(other))

    def test_every_value_changes_the_hash(self):
        base = PROFILES["balanced"]
        variants = [
            ThumbnailProfile(("small",), 12, 3, 50, 85),
            ThumbnailProfile(("medium",), 24, 3, 50, 85),
            ThumbnailProfile(("medium",), 12, 2, 50, 85),
            ThumbnailProfile(("medium",), 12, 3, 40, 85),
            ThumbnailProfile(("medium",), 12, 3, 50, 75),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(profile_hash(base), profile_hash(variant))


class TestMatchProfile(unittest.TestCase):

    def test_named_profiles_match_themselves(self):
        for name, profile in PROFILES.items():
            with self.subTest(name=name):
                self.assertEqual(match_profile(profile), name)

    def test_anything_else_is_custom(self):
        self.assertEqual(match_profile(ThumbnailProfile(("medium",), 15, 3, 50, 85)), "custom")


class TestLoadThumbnailProfile(unittest.TestCase):

    def test_empty_settings_give_the_balanced_default(self):
        self.assertEqual(load_thumbnail_profile(_settings()), DEFAULT_PROFILE)

    def test_reads_all_five_keys(self):
        profile = load_thumbnail_profile(_settings(
            thumbnail_sizes=["small", "large"],
            thumbnail_video_fps=24,
            thumbnail_video_seconds=2,
            thumbnail_video_quality=40,
            thumbnail_image_quality=75,
        ))
        self.assertEqual(profile, ThumbnailProfile(("small", "large"), 24, 2, 40, 75))

    def test_sizes_stored_as_a_json_string_still_load(self):
        profile = load_thumbnail_profile(_settings(thumbnail_sizes='["small"]'))
        self.assertEqual(profile.sizes, ("small",))

    def test_unknown_size_names_are_dropped(self):
        profile = load_thumbnail_profile(_settings(thumbnail_sizes=["huge", "medium"]))
        self.assertEqual(profile.sizes, ("medium",))

    def test_an_empty_size_list_falls_back_to_the_default(self):
        self.assertEqual(load_thumbnail_profile(_settings(thumbnail_sizes=[])).sizes, DEFAULT_PROFILE.sizes)

    def test_out_of_range_values_fall_back_per_field(self):
        profile = load_thumbnail_profile(_settings(
            thumbnail_sizes=["large"],
            thumbnail_video_fps=999,
            thumbnail_video_seconds=0,
            thumbnail_video_quality="nonsense",
            thumbnail_image_quality=90,
        ))
        # Only the bad fields revert; the good ones survive.
        self.assertEqual(profile.sizes, ("large",))
        self.assertEqual(profile.video_fps, DEFAULT_PROFILE.video_fps)
        self.assertEqual(profile.video_seconds, DEFAULT_PROFILE.video_seconds)
        self.assertEqual(profile.video_quality, DEFAULT_PROFILE.video_quality)
        self.assertEqual(profile.image_quality, 90)

    def test_a_settings_read_that_raises_gives_the_default(self):
        settings = Mock()
        settings.get_setting.side_effect = RuntimeError("database gone")
        self.assertEqual(load_thumbnail_profile(settings), DEFAULT_PROFILE)


class TestEstimateBytes(unittest.TestCase):

    def test_named_profiles_are_ordered_full_balanced_compact(self):
        full = estimate_bytes(PROFILES["full"], 100, 100)
        balanced = estimate_bytes(PROFILES["balanced"], 100, 100)
        compact = estimate_bytes(PROFILES["compact"], 100, 100)
        self.assertGreater(full, balanced)
        self.assertGreater(balanced, compact)

    def test_more_files_estimate_more_bytes(self):
        self.assertGreater(
            estimate_bytes(PROFILES["balanced"], 200, 200),
            estimate_bytes(PROFILES["balanced"], 100, 100),
        )

    def test_a_video_costs_more_than_an_image_at_the_same_size(self):
        profile = PROFILES["balanced"]
        self.assertGreater(estimate_bytes(profile, 0, 10), estimate_bytes(profile, 10, 0))

    def test_frame_rate_and_length_both_drive_the_animated_cost(self):
        slow = ThumbnailProfile(("medium",), 8, 3, 50, 85)
        fast = ThumbnailProfile(("medium",), 24, 3, 50, 85)
        longer = ThumbnailProfile(("medium",), 8, 6, 50, 85)
        self.assertGreater(estimate_bytes(fast, 0, 10), estimate_bytes(slow, 0, 10))
        self.assertGreater(estimate_bytes(longer, 0, 10), estimate_bytes(slow, 0, 10))

    def test_nothing_stored_estimates_nothing(self):
        self.assertEqual(estimate_bytes(PROFILES["full"], 0, 0), 0)


class TestFallbackSizes(unittest.TestCase):

    def test_each_size_tries_itself_first(self):
        for size in ("small", "medium", "large"):
            with self.subTest(size=size):
                self.assertEqual(fallback_sizes(size)[0], size)

    def test_small_prefers_medium_over_large(self):
        self.assertEqual(fallback_sizes("small"), ("small", "medium", "large"))

    def test_medium_prefers_large_over_small(self):
        self.assertEqual(fallback_sizes("medium"), ("medium", "large", "small"))

    def test_large_prefers_medium_over_small(self):
        self.assertEqual(fallback_sizes("large"), ("large", "medium", "small"))

    def test_an_unknown_size_has_no_chain(self):
        self.assertEqual(fallback_sizes("enormous"), ())


class TestValidateSetting(unittest.TestCase):

    def test_a_key_this_module_does_not_own_is_never_rejected(self):
        self.assertIsNone(validate_setting("models_dir", "anything at all"))

    def test_valid_values_pass(self):
        self.assertIsNone(validate_setting("thumbnail_sizes", ["small", "large"]))
        self.assertIsNone(validate_setting("thumbnail_video_fps", 60))
        self.assertIsNone(validate_setting("thumbnail_video_seconds", 1))
        self.assertIsNone(validate_setting("thumbnail_video_quality", 100))
        self.assertIsNone(validate_setting("thumbnail_image_quality", 1))

    def test_an_empty_size_list_is_rejected(self):
        self.assertIsNotNone(validate_setting("thumbnail_sizes", []))

    def test_an_unknown_size_is_rejected(self):
        self.assertIn("enormous", validate_setting("thumbnail_sizes", ["enormous"]))

    def test_out_of_range_numbers_are_rejected(self):
        self.assertIsNotNone(validate_setting("thumbnail_video_fps", 0))
        self.assertIsNotNone(validate_setting("thumbnail_video_fps", 61))
        self.assertIsNotNone(validate_setting("thumbnail_video_seconds", 11))
        self.assertIsNotNone(validate_setting("thumbnail_video_quality", 0))
        self.assertIsNotNone(validate_setting("thumbnail_image_quality", 101))

    def test_non_numeric_values_are_rejected(self):
        self.assertIsNotNone(validate_setting("thumbnail_video_fps", "fast"))
        self.assertIsNotNone(validate_setting("thumbnail_image_quality", True))


if __name__ == "__main__":
    unittest.main()
