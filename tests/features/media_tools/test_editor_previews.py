import io

from PIL import Image

from tests.features.media_tools.editor_support import EditorTestBase
from tests.features.media_tools.support import needs_ffmpeg

GRAIN = {"tool": "film_grain", "params": {"amount": 40, "seed": 2}}


class PreviewTestBase(EditorTestBase):
    def frame(self, source, steps, **extra):
        return self.client.post(
            "/api/media-tools/preview/frame", json={"input": source, "steps": steps, **extra}
        )

    def clip(self, source, steps, **extra):
        return self.client.post(
            "/api/media-tools/preview/clip", json={"input": source, "steps": steps, **extra}
        )

    def picture(self, response):
        return Image.open(io.BytesIO(response.content))

    def refused(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        detail = self.error_of(response)
        self.assertEqual(detail["error"], code)
        self.assert_no_paths(response)
        return detail


@needs_ffmpeg
class TestFramePreview(PreviewTestBase):
    def test_a_video_frame_renders_through_the_stack_and_is_cached(self):
        clip = self.video_item(seconds=1.0)
        first = self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5)
        again = self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["content-type"], "image/webp")
        self.assertEqual((first.headers["x-preview-cache"], again.headers["x-preview-cache"]), ("miss", "hit"))
        self.assertEqual(first.content, again.content)
        self.assertEqual(self.picture(first).size, (64, 48))

    def test_the_effect_changes_the_frame(self):
        clip = self.video_item(seconds=1.0)
        plain = self.frame({"item_id": clip.id}, [], time_seconds=0.5, format="png")
        grained = self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5, format="png")
        self.assertEqual(plain.headers["content-type"], "image/png")
        self.assertNotEqual(self.picture(plain).tobytes(), self.picture(grained).tobytes())

    def test_new_params_or_another_time_miss_the_cache_and_a_hidden_step_does_not(self):
        clip = self.video_item(seconds=1.0)
        self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5)
        other = {"tool": "film_grain", "params": {"amount": 41, "seed": 2}}
        hidden = {"tool": "film_grain", "params": {"amount": 90}, "hidden": True}
        self.assertEqual(self.frame({"item_id": clip.id}, [other], time_seconds=0.5).headers["x-preview-cache"], "miss")
        self.assertEqual(self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.3).headers["x-preview-cache"], "miss")
        self.assertEqual(
            self.frame({"item_id": clip.id}, [GRAIN, hidden], time_seconds=0.5).headers["x-preview-cache"], "hit"
        )

    def test_the_cache_is_per_user(self):
        clip = self.video_item(seconds=1.0)
        self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5)
        self.as_user(self.other_user_id)
        self.refused(self.frame({"item_id": clip.id}, [GRAIN], time_seconds=0.5), 404, "not_found")

    def test_an_image_previews_through_its_stack_at_the_asked_size(self):
        item = self.image_item(size=(40, 30))
        response = self.frame({"item_id": item.id}, [{"tool": "rot", "params": {}}], max_side=20)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.picture(response).size, (15, 20))
        self.assertEqual((response.headers["x-frame-width"], response.headers["x-frame-height"]), ("30", "40"))

    def test_a_cached_frame_still_reports_the_full_output_size(self):
        item = self.image_item(size=(40, 30))
        self.frame({"item_id": item.id}, [], max_side=16)
        again = self.frame({"item_id": item.id}, [], max_side=16)
        self.assertEqual(again.headers["x-preview-cache"], "hit")
        self.assertEqual(self.picture(again).size, (16, 12))
        self.assertEqual((again.headers["x-frame-width"], again.headers["x-frame-height"]), ("40", "30"))

    def test_a_tool_without_a_frame_path_is_refused_with_its_step(self):
        clip = self.video_item(seconds=1.0)
        detail = self.refused(
            self.frame({"item_id": clip.id}, [{"tool": "mute", "params": {}}, {"tool": "frame", "params": {}}]),
            400,
            "unsupported_media",
        )
        self.assertEqual(detail["step_index"], 1)

    def test_bad_params_name_the_step_and_field(self):
        clip = self.video_item(seconds=1.0)
        detail = self.refused(
            self.frame({"item_id": clip.id}, [GRAIN, {"tool": "film_grain", "params": {"amount": 500}}]),
            400,
            "invalid_params",
        )
        self.assertEqual((detail["step_index"], detail["field"]), (1, "amount"))

    def test_more_than_sixty_four_visible_steps_are_refused(self):
        item = self.image_item(size=(40, 30))
        rot = {"tool": "rot", "params": {}}
        hidden = {**rot, "hidden": True}
        self.assertEqual(self.frame({"item_id": item.id}, [rot] * 64 + [hidden] * 3).status_code, 200)
        detail = self.refused(self.frame({"item_id": item.id}, [rot] * 65), 400, "invalid_params")
        self.assertEqual(detail["field"], "steps")
        video = self.video_item(seconds=1.0)
        detail = self.refused(self.clip({"item_id": video.id}, [rot] * 65), 400, "invalid_params")
        self.assertEqual(detail["field"], "steps")

    def test_a_frame_preview_with_no_free_slot_answers_busy(self):
        clip = self.video_item(seconds=1.0)
        self.service._preview_wait = 0.05
        held = [self.service._preview_slots.acquire(timeout=1) for _ in range(2)]
        try:
            self.refused(self.frame({"item_id": clip.id}, [GRAIN]), 429, "preview_busy")
        finally:
            for _ in held:
                self.service._preview_slots.release()

    def test_a_failed_frame_preview_gives_its_slot_back(self):
        clip = self.video_item(seconds=1.0)
        self.service._preview_wait = 0.05
        failing = [{"tool": "frame", "params": {}}]
        for _ in range(3):
            self.refused(self.frame({"item_id": clip.id}, failing), 400, "unsupported_media")
        self.assertEqual(self.frame({"item_id": clip.id}, [GRAIN]).status_code, 200)

    def test_audio_has_no_frame(self):
        tone = self.tone_item()
        self.refused(self.frame({"item_id": tone.id}, []), 400, "unsupported_media")

    def test_someone_elses_replacement_track_is_not_found(self):
        clip = self.video_item(seconds=1.0)
        theirs = self.tone_item(name="theirs.wav", user_id=self.other_user_id)
        response = self.frame({"item_id": clip.id}, [{"tool": "replace_audio", "params": {"audio": theirs.id}}])
        self.refused(response, 404, "not_found")


@needs_ffmpeg
class TestClipPreview(PreviewTestBase):
    def finished(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["data"]["run_id"]
        run = self.wait_run(run_id)
        self.assertEqual(run.status, "completed", run.error)
        listed = self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]
        self.assertEqual(listed["kind"], "preview")
        return listed["preview"]

    def test_a_short_clip_has_the_asked_length_and_height_and_plays_back(self):
        clip = self.video_item(seconds=2.0)
        preview = self.finished(
            self.clip({"item_id": clip.id}, [GRAIN], start_seconds=0.5, duration_seconds=1.0, height=24)
        )
        self.assertEqual((preview["width"], preview["height"]), (32, 24))
        self.assertAlmostEqual(preview["duration_seconds"], 1.0, delta=0.11)
        self.assertEqual(preview["mime_type"], "video/mp4")

        served = self.client.get(preview["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "video/mp4")
        self.assertGreater(len(served.content), 0)

    def test_the_same_stack_and_start_come_straight_from_the_cache(self):
        clip = self.video_item(seconds=2.0)
        body = dict(start_seconds=0.5, duration_seconds=1.0, height=24)
        first = self.finished(self.clip({"item_id": clip.id}, [GRAIN], **body))
        again = self.clip({"item_id": clip.id}, [GRAIN], **body)
        self.assertEqual(again.status_code, 202)
        self.assertEqual(again.json()["data"], {"run_id": None, "preview": first})
        moved = self.clip({"item_id": clip.id}, [GRAIN], **{**body, "start_seconds": 0.6})
        self.assertIsNotNone(moved.json()["data"]["run_id"])
        self.wait_run(moved.json()["data"]["run_id"])

    def test_a_trimmed_stack_previews_inside_the_trim(self):
        clip = self.video_item(seconds=2.0)
        steps = [{"tool": "trim", "params": {"start": 0.2, "end": 1.0}}]
        preview = self.finished(self.clip({"item_id": clip.id}, steps, start_seconds=0.0, duration_seconds=3.0))
        self.assertAlmostEqual(preview["duration_seconds"], 0.8, delta=0.11)

    def test_cancelling_stops_the_preview_and_stores_nothing(self):
        clip = self.video_item(seconds=2.0)
        response = self.clip({"item_id": clip.id}, [{"tool": "vblock", "params": {}}], duration_seconds=1.0)
        run_id = response.json()["data"]["run_id"]
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        cancelled = self.client.delete(f"/api/media-tools/runs/{run_id}").json()["data"]

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["preview"])
        self.gate.release.set()
        again = self.clip({"item_id": clip.id}, [{"tool": "vblock", "params": {}}], duration_seconds=1.0)
        self.assertIsNotNone(again.json()["data"]["run_id"])
        self.wait_run(again.json()["data"]["run_id"])

    def test_only_the_owner_can_fetch_the_clip(self):
        clip = self.video_item(seconds=2.0)
        preview = self.finished(self.clip({"item_id": clip.id}, [], duration_seconds=1.0, height=24))
        self.as_user(self.other_user_id)
        self.refused(self.client.get(preview["url"]), 404, "not_found")
        self.refused(self.client.get("/api/media-tools/previews/not-an-id"), 404, "not_found")

    def test_an_image_has_no_clip_preview(self):
        item = self.image_item()
        self.refused(self.clip({"item_id": item.id}, []), 400, "unsupported_media")

    def test_someone_elses_video_is_not_found(self):
        theirs = self.video_item(name="theirs.mp4", user_id=self.other_user_id)
        self.refused(self.clip({"item_id": theirs.id}, []), 404, "not_found")
