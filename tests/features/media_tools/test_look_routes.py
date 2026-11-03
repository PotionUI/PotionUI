import io

import numpy as np
from PIL import Image

from src.platform.media_tools.effects.looks import Look
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import EffectTool
from tests.features.media_tools.support import needs_ffmpeg
from tests.features.media_tools.test_effect_routes import EffectRoutesTestBase

TABLE_URL = "/api/media-tools/looks/table"
WARM = Look(id="warm", name="Warm", group="Film", intensity=100, steps=({"op": "white_balance", "temperature": 60},), revision="w1")


class OwnedLooks:
    def __init__(self, owner):
        self.owner = owner
        self.mine = Look(
            id="mine:abc", name="Mine", group="Mine", intensity=80,
            steps=({"op": "tone", "contrast": 40}, {"op": "vignette", "amount": 50}), revision="m1",
        )
        self.calls = []

    def resolve(self, ref, user_id):
        self.calls.append((ref, user_id))
        if ref == "warm":
            return WARM
        return self.mine if ref == "mine:abc" and user_id == self.owner else None

    def list(self, user_id):
        return [WARM, self.mine] if user_id == self.owner else [WARM]


class TestLookRoutes(EffectRoutesTestBase):

    def setUp(self):
        super().setUp()
        self.looks = OwnedLooks(self.user_id)
        runtime = EffectRuntime()
        runtime.use_looks(self.looks)
        for spec in self.registry.list():
            if isinstance(spec.impl, EffectTool):
                spec.impl.runtime = runtime

    def look_param(self):
        tool = self.catalog(kind="image")["look"]
        return tool, next(param for param in tool["params"] if param["name"] == "look")

    def test_the_catalog_lists_shared_looks_and_only_your_own_with_their_preview_values(self):
        tool, param = self.look_param()

        self.assertEqual([option["value"] for option in param["options"]], ["", "warm", "mine:abc"])
        mine = param["options"][2]
        self.assertEqual((mine["label"], mine["group"], mine["revision"]), ("Mine", "Mine", "m1"))
        self.assertEqual((mine["env"]["has"], mine["env"]["intensity"], mine["env"]["spatial"]), (1, 80.0, 1))
        self.assertEqual((mine["env"]["s0_kind"], mine["env"]["s0_a"], mine["env"]["live"]), (1, 50.0, 1))
        self.assertEqual(param["options"][0]["env"]["has"], 0)
        self.assertEqual(tool["effect"]["live"], "look.live == 1")
        self.assertEqual(tool["accepts"], ["video", "image"])

        self.as_user(self.other_user_id)
        _, param = self.look_param()
        self.assertEqual([option["value"] for option in param["options"]], ["", "warm"])

    def test_the_table_is_the_full_strength_look_and_your_own_looks_stay_yours(self):
        response = self.client.get(TABLE_URL, params={"ref": "mine:abc"})

        self.assertEqual(response.status_code, 200, response.text)
        table = np.frombuffer(response.content, dtype="<u2").reshape(-1, 3)
        self.assertEqual(table.shape[0], 33 ** 3)
        self.assertGreater(int(table[-1].min()), 60000)
        self.assertLess(int(table[0].max()), 4000)
        self.assertEqual(self.looks.calls[-1], ("mine:abc", self.user_id))

        self.as_user(self.other_user_id)
        for ref in ("mine:abc", "nope", "", "bad ref!"):
            self.assertEqual(self.client.get(TABLE_URL, params={"ref": ref}).status_code, 404, ref)
        self.assertEqual(self.client.get(TABLE_URL, params={"ref": "warm"}).status_code, 200)

    def test_saving_an_image_with_your_own_look_grades_it_and_another_user_cannot(self):
        item = self.image_item(size=(48, 32), color=(180, 120, 90))
        step = {"tool": "look", "params": {"look": "mine:abc", "strength": 1}}

        run = self.finish([step], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed", run.error)
        row = self.upload_repo.get_by_id(run.items[0].id, self.user_id)
        with Image.open(self.uploads_dir / row.filename) as out:
            pixels = np.asarray(out.convert("RGB"), dtype=np.int16)
        self.assertNotEqual(pixels[16, 24].tolist(), [180, 120, 90])
        self.assertLess(int(pixels[0, 0].sum()), int(pixels[16, 24].sum()))
        self.assertIn(("mine:abc", self.user_id), self.looks.calls)

        self.as_user(self.other_user_id)
        theirs = self.image_item(name="theirs.png", user_id=self.other_user_id)
        failed = self.wait_run(self.start_ok([step], [{"item_id": theirs.id}]), user_id=self.other_user_id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("not available", failed.error.message)

    def test_the_frame_preview_reads_your_own_look(self):
        item = self.image_item(size=(48, 32), color=(180, 120, 90))
        step = {"tool": "look", "params": {"look": "mine:abc", "strength": 1}}

        response = self.client.post("/api/media-tools/preview/frame", json={"input": {"item_id": item.id}, "steps": [step]})

        self.assertEqual(response.status_code, 200, response.text)
        with Image.open(io.BytesIO(response.content)) as out:
            self.assertNotEqual(out.convert("RGB").getpixel((24, 16)), (180, 120, 90))

    @needs_ffmpeg
    def test_the_clip_preview_reads_your_own_look_and_another_user_cannot(self):
        step = {"tool": "look", "params": {"look": "mine:abc", "strength": 1}}
        body = {"steps": [step], "start_seconds": 0, "duration_seconds": 0.5, "height": 24}
        clip = self.video_item(seconds=1.0)

        response = self.client.post("/api/media-tools/preview/clip", json={"input": {"item_id": clip.id}, **body})

        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed", run.error)
        self.assertIn(("mine:abc", self.user_id), self.looks.calls)

        self.as_user(self.other_user_id)
        theirs = self.video_item(name="theirs.mp4", user_id=self.other_user_id, seconds=1.0)
        refused = self.client.post("/api/media-tools/preview/clip", json={"input": {"item_id": theirs.id}, **body})
        self.assertEqual(refused.status_code, 202, refused.text)
        failed = self.wait_run(refused.json()["data"]["run_id"], user_id=self.other_user_id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("not available", failed.error.message)

    def test_a_look_has_nothing_to_measure(self):
        clip = self.video_item(seconds=1.0)
        step = {"tool": "look", "params": {"look": "mine:abc"}}

        response = self.client.post(
            "/api/media-tools/effects/analysis", json={"input": {"item_id": clip.id}, "steps": [step], "step_index": 0}
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("nothing to measure", response.text)
