"""Table-driven tests for `derive_technique` - pure, no database."""

import unittest

from src.features.inspirations.technique import derive_technique


class TestDeriveTechnique(unittest.TestCase):

    CASES = [
        # (mode, category, has_image_input, has_video_input) -> expected
        ("txt2img", "image", False, False, "txt2img"),
        ("txt2img", "image", True, False, "img2img"),
        ("txt2img", "image", False, True, "img2img"),
        ("txt2vid", "video", False, False, "txt2vid"),
        ("txt2vid", "video", True, False, "img2vid"),
        ("txt2vid", "video", False, True, "vid2vid"),
        ("txt2vid", "video", True, True, "vid2vid"),
        ("upscale", "image", False, False, "upscale"),
        ("video_upscale", "video", True, False, "upscale"),
        ("Upscale", "image", False, False, "upscale"),  # case-insensitive
        (None, "image", False, False, "txt2img"),
        ("song", "audio", False, False, "txt2img"),
        ("song", "audio", False, True, "txt2vid"),
        ("interpolate", None, False, False, "txt2img"),
        ("interpolate", None, False, True, "txt2vid"),
        ("edit", "utility", True, False, "txt2img"),
    ]

    def test_table(self):
        for mode, category, has_image, has_video, expected in self.CASES:
            with self.subTest(mode=mode, category=category, image=has_image, video=has_video):
                self.assertEqual(
                    derive_technique(mode, category, has_image, has_video),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
