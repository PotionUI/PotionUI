import unittest

from src.platform.media_tools import probe
from src.platform.media_tools.ops import register_core_tools
from tests.features.media_tools.support import MediaToolsTestBase, needs_ffmpeg
from tests.platform.media_tools.media_factory import video_frame_count

GRAIN = {"tool": "film_grain", "params": {"amount": 50, "seed": 3}}


class FilmGrainToolTestBase(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        register_core_tools(self.registry)
        self.service.publisher.thumbnail_runner = lambda task: None


class TestFilmGrainCatalog(FilmGrainToolTestBase):

    def entry(self, **query):
        tools = self.client.get("/api/media-tools", params=query).json()["data"]["tools"]
        return next((tool for tool in tools if tool["id"] == "film_grain"), None)

    def test_the_listing_carries_the_tool_with_its_bounded_params(self):
        entry = self.entry(kind="video", scope="library")

        self.assertEqual(entry["label"], "Film grain")
        self.assertEqual(entry["accepts"], ["video"])
        self.assertEqual(entry["group"], "effects")
        self.assertEqual(entry["cardinality"], "one_to_one")
        self.assertTrue(entry["runnable"])
        by_name = {param["name"]: param for param in entry["params"]}
        self.assertEqual(list(by_name), ["amount", "size", "color", "seed"])
        self.assertEqual((by_name["amount"]["min"], by_name["amount"]["max"], by_name["amount"]["default"]), (0, 100, 30))
        self.assertEqual((by_name["size"]["min"], by_name["size"]["max"], by_name["size"]["default"]), (1, 3, 1.5))
        self.assertIs(by_name["color"]["default"], True)
        self.assertTrue(all(param["description"] for param in entry["params"]))

    def test_images_and_audio_are_not_offered_the_tool(self):
        self.assertIsNone(self.entry(kind="image"))
        self.assertIsNone(self.entry(kind="audio"))
        self.assertIsNotNone(self.entry(scope="history"))


@needs_ffmpeg
class TestFilmGrainRuns(FilmGrainToolTestBase):

    def test_save_as_new_adds_a_grained_copy_and_leaves_the_original(self):
        clip = self.video_item(seconds=1.0)
        original = (self.uploads_dir / clip.filename).read_bytes()

        run = self.finish([GRAIN], [{"item_id": clip.id}])

        self.assertEqual(run.status, "completed", run.error)
        self.assertFalse(run.replaced)
        produced = run.items[0]
        self.assertNotEqual(produced.id, clip.id)
        self.assertEqual(produced.original_filename, "original-clip-grain.mp4")
        self.assertEqual((produced.width, produced.height), (64, 48))
        written = probe(self.uploads_dir / produced.filename)
        self.assertEqual(video_frame_count(written.path), 10)
        self.assertTrue(written.has_audio)
        self.assertEqual((self.uploads_dir / clip.filename).read_bytes(), original)
        self.assertEqual(len(self.upload_rows()), 2)

    def test_replace_original_swaps_the_file_under_the_same_row(self):
        clip = self.video_item(seconds=1.0)
        old_file = self.uploads_dir / clip.filename

        run = self.finish([GRAIN], [{"item_id": clip.id}], mode="replace")

        self.assertEqual(run.status, "completed", run.error)
        self.assertTrue(run.replaced)
        produced = run.items[0]
        self.assertEqual(produced.id, clip.id)
        self.assertFalse(old_file.exists())
        written = probe(self.uploads_dir / produced.filename)
        self.assertEqual(video_frame_count(written.path), 10)
        self.assertTrue(written.has_audio)
        row = self.upload_repo.get_by_id(clip.id, self.user_id)
        self.assertEqual((row.filename, row.width, row.height), (produced.filename, 64, 48))
        self.assertEqual(len(self.upload_rows()), 1)

    def test_an_out_of_range_amount_is_refused_before_anything_runs(self):
        clip = self.video_item(seconds=1.0)

        response = self.start([{"tool": "film_grain", "params": {"amount": 500}}], [{"item_id": clip.id}])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "invalid_params")
        self.assertEqual(self.error_of(response)["field"], "amount")

    def test_an_image_is_refused(self):
        item = self.image_item()

        response = self.start([GRAIN], [{"item_id": item.id}])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "unsupported_media")


if __name__ == "__main__":
    unittest.main()
