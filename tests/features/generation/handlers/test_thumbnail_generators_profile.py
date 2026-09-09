"""Both thumbnail generators render exactly the profile they are handed -
which sizes, at what quality, and (for video) at what frame rate and length."""

import io
import random
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from src.features.generation.handlers.image_handler import generate_thumbnails
from src.features.generation.handlers.video_handler import generate_video_thumbnails
from src.features.generation.thumbnail_profile import PROFILES, ThumbnailProfile


class _RecordingDriver:
    """A storage driver that keeps every written key in memory."""

    def __init__(self):
        self.written = {}

    def put_bytes(self, key, data):
        self.written[key] = data
        return len(data)

    def local_path(self, key):
        return None

    def put_file(self, key, source_path):
        self.written[key] = source_path.read_bytes()
        return len(self.written[key])


def _ok(*_args, **_kwargs):
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    return result


class TestImageThumbnailProfile(unittest.TestCase):

    def setUp(self):
        # Noise, not a flat fill: a flat image compresses to the same few
        # bytes at any quality and could not tell the settings apart.
        random.seed(7)
        self.image = Image.frombytes(
            "RGB", (2048, 2048), bytes(random.getrandbits(8) for _ in range(2048 * 2048 * 3))
        )
        self.driver = _RecordingDriver()

    def test_balanced_renders_only_the_medium_size(self):
        paths = generate_thumbnails(self.image, self.driver, "generations/x", 0, PROFILES["balanced"])

        self.assertEqual(set(paths), {"medium"})
        self.assertEqual(set(self.driver.written), {"generations/x/thumbnails/0_medium.webp"})

    def test_full_renders_all_three_sizes(self):
        paths = generate_thumbnails(self.image, self.driver, "generations/x", 0, PROFILES["full"])

        self.assertEqual(set(paths), {"small", "medium", "large"})
        self.assertEqual(
            set(self.driver.written),
            {
                "generations/x/thumbnails/0_small.webp",
                "generations/x/thumbnails/0_medium.webp",
                "generations/x/thumbnails/0_large.webp",
            },
        )

    def test_each_size_is_bounded_by_its_configured_width(self):
        generate_thumbnails(self.image, self.driver, "generations/x", 0, PROFILES["full"])

        widths = {
            key.rsplit("_", 1)[1]: Image.open(io.BytesIO(data)).size[0]
            for key, data in self.driver.written.items()
        }
        self.assertEqual(widths["small.webp"], 480)
        self.assertEqual(widths["medium.webp"], 768)
        self.assertEqual(widths["large.webp"], 1024)

    def test_the_profile_quality_reaches_pil(self):
        profile = ThumbnailProfile(("medium",), 12, 3, 50, 33)

        with patch.object(Image.Image, "save", autospec=True) as save:
            generate_thumbnails(self.image, self.driver, "generations/x", 0, profile)

        self.assertEqual(save.call_args.kwargs["quality"], 33)
        self.assertEqual(save.call_args.kwargs["format"], "WebP")

    def test_a_lower_quality_writes_fewer_bytes(self):
        cheap = _RecordingDriver()
        rich = _RecordingDriver()
        generate_thumbnails(self.image, cheap, "b", 0, ThumbnailProfile(("medium",), 12, 3, 50, 20))
        generate_thumbnails(self.image, rich, "b", 0, ThumbnailProfile(("medium",), 12, 3, 50, 95))

        self.assertLess(
            len(cheap.written["b/thumbnails/0_medium.webp"]),
            len(rich.written["b/thumbnails/0_medium.webp"]),
        )


class TestVideoThumbnailProfile(unittest.TestCase):

    def setUp(self):
        self.driver = _RecordingDriver()

    def _argv(self, profile):
        with patch("src.features.generation.handlers.video_handler.subprocess.run", side_effect=_ok) as run:
            generate_video_thumbnails("/tmp/clip.mp4", self.driver, "generations/x", 1, profile)
        return [call.args[0] for call in run.call_args_list]

    @staticmethod
    def _animated(argvs):
        return [argv for argv in argvs if "libwebp" in argv]

    @staticmethod
    def _static(argvs):
        return [argv for argv in argvs if "-vframes" in argv]

    def test_balanced_encodes_one_size_at_twelve_fps_for_three_seconds(self):
        argvs = self._argv(PROFILES["balanced"])

        animated = self._animated(argvs)
        self.assertEqual(len(animated), 1)
        argv = animated[0]
        self.assertIn("fps=12,scale=768:-1", argv)
        self.assertEqual(argv[argv.index("-t") + 1], "3")
        self.assertEqual(argv[argv.index("-quality") + 1], "50")

        self.assertEqual(len(self._static(argvs)), 1)
        self.assertIn("scale=768:-1", self._static(argvs)[0])

    def test_full_encodes_three_sizes_at_twenty_four_fps(self):
        argvs = self._argv(PROFILES["full"])

        animated = self._animated(argvs)
        self.assertEqual(len(animated), 3)
        scales = sorted(argv[argv.index("-vf") + 1] for argv in animated)
        self.assertEqual(
            scales,
            ["fps=24,scale=1024:-1", "fps=24,scale=480:-1", "fps=24,scale=768:-1"],
        )
        self.assertEqual(len(self._static(argvs)), 3)

    def test_compact_encodes_a_shorter_lower_quality_clip(self):
        argvs = self._argv(PROFILES["compact"])

        animated = self._animated(argvs)
        self.assertEqual(len(animated), 1)
        argv = animated[0]
        self.assertIn("fps=8,scale=480:-1", argv)
        self.assertEqual(argv[argv.index("-t") + 1], "2")
        self.assertEqual(argv[argv.index("-quality") + 1], "40")

    def test_only_the_profile_sizes_are_returned(self):
        with patch("src.features.generation.handlers.video_handler.subprocess.run", side_effect=_ok):
            paths = generate_video_thumbnails(
                "/tmp/clip.mp4", self.driver, "generations/x", 1, PROFILES["balanced"]
            )

        self.assertEqual(set(paths), {"medium"})
        self.assertEqual(paths["medium"], "thumbnails/1_medium.jpg")

    def test_no_animated_pass_runs_when_every_static_frame_fails(self):
        def failing(*_args, **_kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "boom"
            return result

        with patch("src.features.generation.handlers.video_handler.subprocess.run", side_effect=failing) as run:
            paths = generate_video_thumbnails(
                "/tmp/clip.mp4", self.driver, "generations/x", 1, PROFILES["full"]
            )

        self.assertEqual(paths, {})
        self.assertFalse(self._animated([call.args[0] for call in run.call_args_list]))


if __name__ == "__main__":
    unittest.main()
