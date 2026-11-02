import threading

from PIL import Image

from src.features.collections.repository import CollectionRepository
from src.features.library.mappers import upload_to_item
from src.platform.media_tools.ops import slice as slice_op
from tests.features.media_tools.test_editor_stack import EditorStackTestBase, step

BG = (240, 230, 210)


def cell(x, y, w, h, **extra):
    return {"x": x, "y": y, "w": w, "h": h, **extra}


def halves(**extra):
    return [cell(0, 0, 0.5, 1, **extra), cell(0.5, 0, 0.5, 1)]


def slice_step(rects, **params):
    return step("slice", rects=rects, **params)


class SliceBase(EditorStackTestBase):
    def setUp(self):
        super().setUp()
        self.collections = CollectionRepository()
        self.service.collection_repo = self.collections

    def sheet_item(self, name="mira.png", user_id=None, size=(200, 100)):
        image = Image.new("RGB", size, BG)
        image.paste((200, 30, 30), (20, 10, 80, 70))
        image.paste((30, 30, 200), (120, 20, 180, 90))
        path = self.uploads_dir / name
        image.save(path)
        return self.record(path, "image", user_id, width=size[0], height=size[1])

    def post_run(self, steps, source, output=None):
        body = {"steps": steps, "inputs": [source], "output": output or {"mode": "new"}}
        return self.client.post("/api/media-tools/runs", json=body)

    def run_ok(self, steps, source, output=None):
        response = self.post_run(steps, source, output)
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed", run.error)
        return run

    def library_collection(self, user_id=None, name="Mira refs"):
        return self.collections.create(name, user_id or self.user_id, "library")


class TestSliceRun(SliceBase):
    def test_one_run_makes_one_library_item_per_cell(self):
        sheet = self.sheet_item()

        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        self.assertEqual(len(run.items), 2)
        self.assertEqual({(item.width, item.height, item.media_type) for item in run.items}, {(100, 100, "image")})
        self.assertFalse(run.replaced)
        self.assertEqual(run.progress, 1.0)
        rows = [self.upload_repo.get_by_id(item.id, self.user_id) for item in run.items]
        self.assertTrue(all(row is not None for row in rows))
        self.assertIsNotNone(self.upload_repo.get_by_id(sheet.id, self.user_id))

    def test_default_names_number_the_cells(self):
        sheet = self.sheet_item()

        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        self.assertEqual(
            [item.original_filename for item in run.items], ["original-mira-cell-01.png", "original-mira-cell-02.png"]
        )

    def test_typed_names_are_used_and_made_unique(self):
        sheet = self.sheet_item()
        rects = [
            cell(0, 0, 0.25, 1, name="Mira front"),
            cell(0.25, 0, 0.25, 1, name="Mira front"),
            cell(0.5, 0, 0.25, 1, name="mira front.png"),
            cell(0.75, 0, 0.25, 1, name="../escape"),
        ]

        run = self.run_ok([slice_step(rects)], {"item_id": sheet.id})

        self.assertEqual(
            [item.original_filename for item in run.items],
            ["Mira front.png", "Mira front-2.png", "mira front-3.png", "..escape.png"],
        )

    def test_cells_are_cut_after_the_steps_above_them(self):
        sheet = self.sheet_item()

        run = self.run_ok([step("shrink", size=40), slice_step(halves())], {"item_id": sheet.id})

        self.assertEqual({(item.width, item.height) for item in run.items}, {(20, 40)})

    def test_trim_padding_and_aspect_reach_the_output(self):
        sheet = self.sheet_item()

        run = self.run_ok(
            [slice_step([cell(0, 0, 0.5, 1)], trim=True, padding=5, aspect="square")], {"item_id": sheet.id}
        )

        self.assertEqual((run.items[0].width, run.items[0].height), (70, 70))

    def test_a_generated_file_can_be_sliced(self):
        record, _ = self.generation_file(size=(64, 48))

        run = self.run_ok([slice_step(halves())], {"generation_file_id": record.id})

        self.assertEqual(len(run.items), 2)

    def test_the_cells_are_not_remembered_as_the_last_parameters(self):
        sheet = self.sheet_item()

        self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        self.assertIsNone(self.service.last_params(self.user_id, "slice"))


class TestProvenance(SliceBase):
    def test_each_item_records_its_source_and_rect(self):
        sheet = self.sheet_item()

        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        for index, item in enumerate(run.items):
            mark = self.upload_repo.get_by_id(item.id, self.user_id).provenance["slice"]
            self.assertEqual(mark["source_item_id"], sheet.id)
            self.assertEqual((mark["index"], mark["count"]), (index, 2))
            self.assertEqual(mark["source_name"], "original-mira.png")
            self.assertEqual(mark["rect"], {"x": index * 0.5, "y": 0.0, "w": 0.5, "h": 1.0})
            self.assertEqual(item.provenance["slice"]["index"], index)

    def test_a_generated_source_is_recorded_by_file_id(self):
        record, _ = self.generation_file(size=(64, 48))

        run = self.run_ok([slice_step(halves())], {"generation_file_id": record.id})

        mark = self.upload_repo.get_by_id(run.items[0].id, self.user_id).provenance["slice"]
        self.assertEqual(mark["generation_file_id"], record.id)
        self.assertNotIn("source_item_id", mark)

    def test_the_library_item_exposes_it(self):
        sheet = self.sheet_item()
        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        item = upload_to_item(self.upload_repo.get_by_id(run.items[1].id, self.user_id), [])

        self.assertEqual(item.provenance["slice"]["index"], 1)
        self.assertIsNone(upload_to_item(self.upload_repo.get_by_id(sheet.id, self.user_id), []).provenance)

    def test_other_tools_leave_no_provenance(self):
        sheet = self.sheet_item()

        run = self.run_ok([step("shrink", size=8)], {"item_id": sheet.id})

        self.assertIsNone(self.upload_repo.get_by_id(run.items[0].id, self.user_id).provenance)


class TestCollection(SliceBase):
    def test_every_output_joins_the_collection(self):
        sheet = self.sheet_item()
        collection = self.library_collection()

        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id}, {"mode": "new", "collection_id": collection.id})

        for item in run.items:
            self.assertEqual([c.id for c in self.collections.get_for_upload(item.id)], [collection.id])
        self.assertEqual(self.collections.get_by_id(collection.id, "library", self.user_id).item_count, 2)

    def test_without_a_collection_nothing_is_filed(self):
        sheet = self.sheet_item()

        run = self.run_ok([slice_step(halves())], {"item_id": sheet.id})

        self.assertEqual(self.collections.get_for_upload(run.items[0].id), [])

    def test_someone_elses_collection_is_a_404_and_starts_nothing(self):
        sheet = self.sheet_item()
        theirs = self.library_collection(self.other_user_id, "Theirs")

        response = self.post_run([slice_step(halves())], {"item_id": sheet.id}, {"mode": "new", "collection_id": theirs.id})

        self.refused(response, 404, "not_found")
        self.assertEqual(len(self.upload_rows()), 1)

    def test_a_collection_from_another_scope_is_a_404(self):
        sheet = self.sheet_item()
        history = self.collections.create("Hist", self.user_id, "history")

        response = self.post_run([slice_step(halves())], {"item_id": sheet.id}, {"mode": "new", "collection_id": history.id})

        self.refused(response, 404, "not_found")

    def test_an_unknown_collection_is_a_404(self):
        sheet = self.sheet_item()

        response = self.post_run([slice_step(halves())], {"item_id": sheet.id}, {"mode": "new", "collection_id": "nope"})

        self.refused(response, 404, "not_found")

    def test_a_collection_needs_a_run_that_makes_new_items(self):
        sheet = self.sheet_item()
        collection = self.library_collection()

        response = self.post_run(
            [step("shrink", size=8)], {"item_id": sheet.id}, {"mode": "replace", "collection_id": collection.id}
        )

        self.refused(response, 400, "invalid_params")


class TestRefusals(SliceBase):
    def test_replace_and_update_are_refused_with_a_clear_message(self):
        sheet = self.sheet_item()

        for output in ({"mode": "replace"}, {"mode": "update", "target_item_id": sheet.id}):
            response = self.post_run([slice_step(halves())], {"item_id": sheet.id}, output)
            detail = self.refused(response, 400, "replace_not_allowed")
            self.assertEqual(detail["message"], "Slicing makes new items")
        self.assertEqual(len(self.upload_rows()), 1)

    def test_slice_must_be_the_last_step(self):
        sheet = self.sheet_item()

        response = self.post_run([slice_step(halves()), step("shrink", size=8)], {"item_id": sheet.id})

        detail = self.refused(response, 400, "invalid_params")
        self.assertEqual(detail["field"], "steps")
        self.assertEqual(detail["step_index"], 0)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_a_hidden_step_below_slice_does_not_count(self):
        sheet = self.sheet_item()

        run = self.run_ok([slice_step(halves()), step("shrink", hidden=True, size=8)], {"item_id": sheet.id})

        self.assertEqual(len(run.items), 2)

    def test_slice_must_be_last_in_a_preview_stack_too(self):
        sheet = self.sheet_item()

        response = self.client.post(
            "/api/media-tools/preview/frame",
            json={"input": {"item_id": sheet.id}, "steps": [slice_step(halves()), step("shrink", size=8)]},
        )

        self.refused(response, 400, "invalid_params")

    def test_a_stack_ending_in_slice_previews_the_picture_before_the_cut(self):
        sheet = self.sheet_item()

        response = self.client.post(
            "/api/media-tools/preview/frame",
            json={"input": {"item_id": sheet.id}, "steps": [slice_step(halves())], "format": "png"},
        )

        self.assertEqual(response.status_code, 200, response.text)

    def test_too_many_cells_are_refused_with_the_step(self):
        sheet = self.sheet_item()
        rects = [cell(0, 0, 0.1, 0.1)] * (slice_op.MAX_SLICE_CELLS + 1)

        response = self.post_run([slice_step(rects)], {"item_id": sheet.id})

        detail = self.refused(response, 400, "invalid_params")
        self.assertEqual((detail["field"], detail["step_index"]), ("rects", 0))

    def test_a_cell_outside_the_picture_is_refused(self):
        sheet = self.sheet_item()

        response = self.post_run([slice_step([cell(0.6, 0, 0.6, 1)])], {"item_id": sheet.id})

        self.refused(response, 400, "invalid_params")

    def test_a_video_is_refused(self):
        clip = self.video_item()

        response = self.post_run([slice_step(halves())], {"item_id": clip.id})

        self.refused(response, 400, "unsupported_media")

    def test_a_tiny_cell_fails_the_run_without_leaking_paths(self):
        sheet = self.sheet_item()

        run = self.run_ok_or_failed([slice_step([cell(0, 0, 0.01, 1)])], {"item_id": sheet.id})

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "invalid_params")
        self.assertNotIn(str(self.temp_dir), run.error.message)
        self.assertEqual(len(self.upload_rows()), 1)

    def run_ok_or_failed(self, steps, source):
        response = self.post_run(steps, source)
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait_run(response.json()["data"]["run_id"])

    def test_a_refused_quota_stores_no_cell(self):
        sheet = self.sheet_item()
        self.guard.refuse_admit = True
        before = self.stored_files()

        run = self.run_ok_or_failed([slice_step(halves())], {"item_id": sheet.id})

        self.assertEqual(run.status, "failed")
        self.assertEqual(self.stored_files(), before)
        self.assertEqual(len(self.upload_rows()), 1)


class TestCancel(SliceBase):
    def test_cancelling_between_cells_publishes_nothing(self):
        sheet = self.sheet_item()
        entered, release = threading.Event(), threading.Event()
        real = slice_op.save_image

        def gated(image, dest):
            result = real(image, dest)
            entered.set()
            release.wait(10)
            return result

        original = slice_op.save_image
        slice_op.save_image = gated
        self.addCleanup(setattr, slice_op, "save_image", original)
        before = self.stored_files()

        run_id = self.post_run([slice_step(halves())], {"item_id": sheet.id}).json()["data"]["run_id"]
        self.assertTrue(entered.wait(10))
        self.client.delete(f"/api/media-tools/runs/{run_id}")
        release.set()
        run = self.wait_run(run_id)

        self.assertEqual(run.status, "cancelled")
        self.assertEqual(self.stored_files(), before)
        self.assertEqual(len(self.upload_rows()), 1)
        self.assertEqual(self.scratch_entries(), [])


class TestAuthorization(SliceBase):
    def test_another_users_sheet_is_a_404(self):
        theirs = self.sheet_item("theirs.png", user_id=self.other_user_id)

        response = self.post_run([slice_step(halves())], {"item_id": theirs.id})

        self.refused(response, 404, "not_found")
        self.assertEqual(self.upload_rows(), [])

    def test_the_run_needs_a_signed_in_user(self):
        response = self.anonymous_client().post(
            "/api/media-tools/runs",
            json={"steps": [slice_step(halves())], "inputs": [{"item_id": "x"}], "output": {"mode": "new"}},
        )

        self.assertEqual(response.status_code, 401)
