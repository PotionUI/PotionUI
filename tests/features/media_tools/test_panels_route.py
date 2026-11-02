from PIL import Image

from src.features.media_tools import service as service_module
from tests.features.media_tools.test_slice_service import BG, SliceBase

URL = "/api/media-tools/analyse/panels"


class TestDetectPanelsRoute(SliceBase):
    def grid_sheet(self, name="grid.png", size=(600, 400)):
        image = Image.new("RGB", size, BG)
        for left in (20, 220, 420):
            for top in (20, 210):
                image.paste((30, 60, 120), (left, top, left + 160, top + 170))
        path = self.uploads_dir / name
        image.save(path)
        return self.record(path, "image", None, width=size[0], height=size[1])

    def detect(self, item, **extra):
        return self.client.post(URL, json={"input": {"item_id": item.id}, **extra})

    def test_returns_panels_gutters_and_background_as_fractions(self):
        response = self.detect(self.grid_sheet())

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual((data["width"], data["height"]), (600, 400))
        self.assertEqual(data["background"], "#f0e6d2")
        self.assertEqual(len(data["panels"]), 6)
        self.assertAlmostEqual(data["panels"][0]["x"], 20 / 600, places=3)
        self.assertAlmostEqual(data["panels"][5]["y"], 210 / 400, places=3)
        self.assertEqual(len([band for band in data["gutters"]["x"] if not band["edge"]]), 2)
        self.assertNotIn("grid_cells", data)

    def test_the_panels_feed_straight_into_a_slice_run(self):
        sheet = self.grid_sheet()
        panels = self.detect(sheet).json()["data"]["panels"]

        run = self.run_ok([{"tool": "slice", "params": {"rects": panels, "trim": True}, "hidden": False}], {"item_id": sheet.id})

        self.assertEqual(len(run.items), 6)
        self.assertEqual({(item.width, item.height) for item in run.items}, {(160, 170)})

    def test_a_grid_returns_snapped_cells(self):
        response = self.detect(self.grid_sheet(), grid=[3, 2])

        cells = response.json()["data"]["grid_cells"]
        self.assertEqual(len(cells), 6)
        self.assertAlmostEqual(cells[0]["x"] + cells[0]["w"], 180 / 600, places=2)

    def test_the_tolerance_is_honoured(self):
        image = Image.new("RGB", (300, 200), BG)
        image.paste((BG[0] - 20, BG[1] - 20, BG[2] - 20), (30, 30, 270, 170))
        path = self.uploads_dir / "faint.png"
        image.save(path)
        item = self.record(path, "image", None, width=300, height=200)

        self.assertEqual(self.detect(item, tolerance=12).json()["data"]["panels"][0]["w"] > 0.5, True)
        self.assertEqual(self.detect(item, tolerance=30).json()["data"]["panels"], [])

    def test_a_generated_file_can_be_analysed(self):
        record, _ = self.generation_file(size=(64, 48))

        response = self.client.post(URL, json={"input": {"generation_file_id": record.id}})

        self.assertEqual(response.status_code, 200, response.text)

    def test_bad_grids_and_tolerances_are_refused(self):
        sheet = self.grid_sheet()

        self.assertEqual(self.detect(sheet, grid=[0, 2]).status_code, 400)
        self.assertEqual(self.detect(sheet, grid=[2, 13]).status_code, 400)
        self.assertEqual(self.detect(sheet, grid=[2]).status_code, 422)
        self.assertEqual(self.detect(sheet, tolerance=65).status_code, 422)
        self.assertEqual(self.detect(sheet, tolerance=-1).status_code, 422)

    def test_a_video_has_no_panels(self):
        response = self.detect(self.video_item())

        self.refused(response, 400, "unsupported_media")

    def test_an_oversized_picture_is_refused_without_a_path(self):
        sheet = self.grid_sheet()
        original = service_module.MAX_SOURCE_PIXELS
        service_module.MAX_SOURCE_PIXELS = 100
        self.addCleanup(setattr, service_module, "MAX_SOURCE_PIXELS", original)

        response = self.detect(sheet)

        self.refused(response, 400, "invalid_params")

    def test_an_unreadable_file_is_refused_without_a_path(self):
        path = self.uploads_dir / "broken.png"
        path.write_bytes(b"not a picture")
        item = self.record(path, "image", None)

        response = self.detect(item)

        self.refused(response, 400, "unsupported_media")

    def test_another_users_picture_is_a_404(self):
        theirs = self.grid_sheet("theirs.png")
        self.as_user(self.other_user_id)

        response = self.detect(theirs)

        self.refused(response, 404, "not_found")

    def test_a_missing_picture_is_a_404(self):
        response = self.client.post(URL, json={"input": {"item_id": "missing"}})

        self.refused(response, 404, "not_found")

    def test_it_needs_a_signed_in_user(self):
        response = self.anonymous_client().post(URL, json={"input": {"item_id": "x"}})

        self.assertEqual(response.status_code, 401)
