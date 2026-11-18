from types import SimpleNamespace

from src.platform.http.errors import DomainError
from src.platform.media_tools import probe
from tests.features.media_tools.editor_support import EditorTestBase
from tests.features.media_tools.support import needs_ffmpeg

MUTE = {"tool": "mute", "params": {}}


@needs_ffmpeg
class TestRenderClip(EditorTestBase):
    def user(self):
        return SimpleNamespace(id=self.user_id)

    def test_a_video_without_steps_is_handed_over_as_it_is(self):
        clip = self.video_item(seconds=1.0)

        with self.service.render_clip(self.user(), {"item_id": clip.id}, []) as path:
            found = probe(path)

        self.assertEqual((found.width, found.height), (64, 48))
        self.assertTrue(found.has_audio)

    def test_the_steps_are_applied_before_the_clip_is_handed_over(self):
        clip = self.video_item(seconds=1.0)

        with self.service.render_clip(self.user(), {"item_id": clip.id}, [MUTE]) as path:
            found = probe(path)

        self.assertFalse(found.has_audio)

    def test_a_picture_is_refused_as_a_clip(self):
        picture = self.image_item()

        with self.assertRaises(DomainError) as refused:
            with self.service.render_clip(self.user(), {"item_id": picture.id}, []):
                pass

        self.assertEqual(refused.exception.message, "A picture is not a video")

    def test_a_video_is_refused_as_a_picture(self):
        clip = self.video_item(seconds=1.0)

        with self.assertRaises(DomainError) as refused:
            with self.service.render_picture(self.user(), {"item_id": clip.id}, []):
                pass

        self.assertEqual(refused.exception.message, "A video file is not a picture")
