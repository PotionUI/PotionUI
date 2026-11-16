from src.features.library.mappers import upload_to_item
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.platform.media_tools.ops import register_core_tools
from tests.features.media_tools.support import MediaToolsTestBase, needs_ffmpeg
from tests.platform.media_tools.media_factory import make_video


class TestPublishedSound(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        register_core_tools(self.registry)
        self.service.publisher.thumbnail_runner = lambda task: None

    def clip(self, name, audio, has_audio):
        path = make_video(self.uploads_dir / name, seconds=1.0, size=(64, 48), audio=audio)
        return self.upload_repo.create(Upload(
            user_id=self.user_id,
            filename=path.name,
            original_filename=path.name,
            media_type="video",
            mime_type=MediaTypeResolver().get_media_type(path.suffix),
            file_size=path.stat().st_size,
            width=64,
            height=48,
            duration_seconds=1.0,
            has_audio=has_audio,
        ))

    def row(self, item_id):
        return self.upload_repo.get_by_id(item_id, self.user_id)

    @needs_ffmpeg
    def test_replacing_a_clip_with_its_muted_version_records_no_sound(self):
        item = self.clip("talk.mp4", audio=True, has_audio=True)

        run = self.finish([{"tool": "mute", "params": {}}], [{"item_id": item.id}], mode="replace")

        self.assertEqual(run.status, "completed", run.error)
        self.assertIs(self.row(item.id).has_audio, False)
        self.assertIs(upload_to_item(self.row(item.id), []).has_audio, False)
        self.assertIs(run.items[0].has_audio, False)
        polled = self.client.get(f"/api/media-tools/runs/{run.run_id}").json()["data"]
        self.assertIs(polled["items"][0]["has_audio"], False)

    @needs_ffmpeg
    def test_a_new_item_made_from_a_clip_records_its_own_sound(self):
        item = self.clip("talk.mp4", audio=True, has_audio=True)

        muted = self.finish([{"tool": "mute", "params": {}}], [{"item_id": item.id}])
        flipped = self.finish([{"tool": "flip", "params": {}}], [{"item_id": item.id}])

        self.assertIs(self.row(muted.items[0].id).has_audio, False)
        self.assertIs(self.row(flipped.items[0].id).has_audio, True)

    @needs_ffmpeg
    def test_a_replace_of_an_item_of_unknown_sound_records_what_the_result_has(self):
        item = self.clip("talk.mp4", audio=True, has_audio=None)

        self.finish([{"tool": "flip", "params": {}}], [{"item_id": item.id}], mode="replace")

        self.assertIs(self.row(item.id).has_audio, True)

    @needs_ffmpeg
    def test_a_frame_taken_from_a_clip_leaves_sound_unknown(self):
        item = self.clip("talk.mp4", audio=True, has_audio=True)

        run = self.finish([{"tool": "frame", "params": {}}], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed", run.error)
        self.assertIsNone(self.row(run.items[0].id).has_audio)

    def test_an_image_result_leaves_sound_unknown(self):
        item = self.image_item()

        run = self.finish([{"tool": "shrink", "params": {"size": 8}}], [{"item_id": item.id}])

        self.assertIsNone(self.row(run.items[0].id).has_audio)
