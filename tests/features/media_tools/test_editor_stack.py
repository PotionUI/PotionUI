import io
from unittest.mock import patch

from PIL import Image

from src.features.media import validators
from src.platform.media_tools import probe
from tests.features.media_tools.editor_support import EditorTestBase
from tests.features.media_tools.support import needs_ffmpeg


def step(tool, hidden=False, **params):
    return {"tool": tool, "params": params, "hidden": hidden}


def save_body(steps, source, mode="new", target=None):
    output = {"mode": mode}
    if target:
        output["target_item_id"] = target
    return {"steps": steps, "inputs": [source], "output": output}


class EditorStackTestBase(EditorTestBase):
    def save(self, steps, source, mode="new", target=None):
        return self.client.post("/api/media-tools/runs", json=save_body(steps, source, mode, target))

    def save_ok(self, steps, source, mode="new", target=None):
        response = self.save(steps, source, mode, target)
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed", run.error)
        return run

    def refused(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        detail = self.error_of(response)
        self.assertEqual(detail["error"], code)
        self.assert_no_paths(response)
        return detail

    def size_of(self, item_id):
        row = self.upload_repo.get_by_id(item_id, self.user_id)
        with Image.open(self.uploads_dir / row.filename) as image:
            return image.size


class TestStackOrderAndHiddenSteps(EditorStackTestBase):
    def test_hidden_steps_are_skipped(self):
        item = self.image_item(size=(40, 30))
        run = self.save_ok([step("padpx", hidden=True, width=20, height=10), step("rot")], {"item_id": item.id})
        self.assertEqual(self.size_of(run.items[0].id), (30, 40))

    def test_steps_run_in_the_order_given(self):
        item = self.image_item(size=(40, 30))
        first = self.save_ok([step("padpx", width=20, height=10), step("rot")], {"item_id": item.id})
        second = self.save_ok([step("rot"), step("padpx", width=20, height=10)], {"item_id": item.id})
        self.assertEqual(self.size_of(first.items[0].id), (10, 20))
        self.assertEqual(self.size_of(second.items[0].id), (20, 10))

    def test_a_stack_with_only_hidden_steps_is_refused(self):
        item = self.image_item()
        detail = self.refused(self.save([step("rot", hidden=True)], {"item_id": item.id}), 400, "invalid_params")
        self.assertEqual(detail["field"], "steps")

    def test_a_hidden_step_is_not_validated(self):
        item = self.image_item(size=(40, 30))
        run = self.save_ok([step("shrink", hidden=True, size=9999), step("shrink", size=8)], {"item_id": item.id})
        self.assertEqual(self.size_of(run.items[0].id), (8, 8))

    def test_sixty_four_visible_steps_run_and_hidden_ones_do_not_count(self):
        item = self.image_item(size=(40, 30))
        run = self.save_ok([step("rot")] * 64 + [step("rot", hidden=True)] * 5, {"item_id": item.id})
        self.assertEqual(self.size_of(run.items[0].id), (40, 30))

    def test_sixty_five_visible_steps_are_refused(self):
        item = self.image_item()
        detail = self.refused(self.save([step("rot")] * 65, {"item_id": item.id}), 400, "invalid_params")
        self.assertEqual(detail["field"], "steps")


class TestSaveModes(EditorStackTestBase):
    def test_save_as_new_then_save_updates_that_copy(self):
        item = self.image_item(size=(40, 30))
        first = self.save_ok([step("padpx", width=20, height=10)], {"item_id": item.id})
        copy = first.items[0]
        rows_before = len(self.upload_rows())

        second = self.save_ok(
            [step("padpx", width=12, height=6)], {"item_id": item.id}, mode="update", target=copy.id
        )

        self.assertEqual(second.items[0].id, copy.id)
        self.assertTrue(second.replaced)
        self.assertEqual(self.size_of(copy.id), (12, 6))
        self.assertEqual(self.size_of(item.id), (40, 30))
        self.assertEqual(len(self.upload_rows()), rows_before)

    def test_save_from_a_history_source_updates_its_library_copy(self):
        record, _ = self.generation_file(size=(32, 24))
        first = self.save_ok([step("rot")], {"generation_file_id": record.id})
        self.save_ok([step("padpx", width=9, height=7)], {"generation_file_id": record.id}, "update", first.items[0].id)
        self.assertEqual(self.size_of(first.items[0].id), (9, 7))

    def test_save_needs_a_copy_to_update(self):
        item = self.image_item()
        detail = self.refused(self.save([step("rot")], {"item_id": item.id}, mode="update"), 400, "invalid_params")
        self.assertEqual(detail["field"], "target_item_id")

    def test_save_cannot_update_someone_elses_item(self):
        item = self.image_item()
        theirs = self.image_item(name="theirs.png", user_id=self.other_user_id)
        self.refused(self.save([step("rot")], {"item_id": item.id}, "update", theirs.id), 404, "not_found")
        self.assertEqual(self.size_of(item.id), (40, 30))

    def test_save_cannot_update_a_helper_file(self):
        item = self.image_item()
        path = self.uploads_dir / "helper.png"
        path.write_bytes(self.png_bytes((4, 4), (1, 2, 3, 255)))
        helper = self.derived_item(path, "image")
        self.refused(self.save([step("rot")], {"item_id": item.id}, "update", helper.id), 400, "update_not_allowed")

    def test_save_cannot_update_with_a_split(self):
        clip = self.video_item(seconds=2.0)
        copy = self.image_item(name="copy.png")
        self.refused(
            self.save([step("split", mode="at", at=1.0)], {"item_id": clip.id}, "update", copy.id),
            400,
            "update_not_allowed",
        )

    def test_replace_original_is_refused_for_a_history_source(self):
        record, _ = self.generation_file()
        self.refused(self.save([step("rot")], {"generation_file_id": record.id}, "replace"), 400, "replace_not_allowed")

    def test_replace_original_is_refused_for_a_dropped_file(self):
        path = self.uploads_dir / "dropped.png"
        path.write_bytes(self.png_bytes((8, 8), (9, 9, 9, 255)))
        dropped = self.derived_item(path, "image")
        self.refused(self.save([step("rot")], {"item_id": dropped.id}, "replace"), 400, "replace_not_allowed")
        run = self.save_ok([step("rot")], {"item_id": dropped.id})
        self.assertEqual(len(run.items), 1)


@needs_ffmpeg
class TestVideoStack(EditorStackTestBase):
    def test_split_saves_two_library_items_whose_lengths_add_up(self):
        clip = self.video_item(seconds=2.0)
        run = self.save_ok([step("split", mode="at", at=0.8)], {"item_id": clip.id})
        self.assertEqual(len(run.items), 2)
        self.assertEqual(
            [item.original_filename for item in run.items],
            ["original-clip — part 1 of 2.mp4", "original-clip — part 2 of 2.mp4"],
        )
        total = sum(item.duration_seconds for item in run.items)
        self.assertAlmostEqual(total, 2.0, delta=0.15)

    def test_a_split_followed_by_an_effect_still_names_the_parts(self):
        clip = self.video_item(seconds=2.0)
        run = self.save_ok([step("split", mode="at", at=1.0), step("mute")], {"item_id": clip.id})
        self.assertEqual(len(run.items), 2)
        self.assertTrue(all("part" in item.original_filename for item in run.items))

    def test_progress_rises_through_the_steps_and_ends_complete(self):
        clip = self.video_item(seconds=1.0)
        seen = []
        original = self.jobs._report

        def record(run, fraction, label):
            seen.append((round(fraction, 4), label))
            original(run, fraction, label)

        self.jobs._report = record
        run = self.save_ok(
            [step("trim", start=0.1, end=0.9), step("film_grain", amount=20), step("volume", percent=50)],
            {"item_id": clip.id},
        )
        fractions = [fraction for fraction, _ in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertIn("Film grain", {label for _, label in seen})
        self.assertIn("Saving", {label for _, label in seen})
        self.assertEqual(run.progress, 1.0)

    def test_replace_audio_takes_its_track_from_the_library(self):
        clip = self.video_item(seconds=1.0)
        tone = self.tone_item(seconds=0.4)
        run = self.save_ok([step("replace_audio", audio={"item_id": tone.id})], {"item_id": clip.id})
        result = probe(self.stored_path(run.items[0]))
        self.assertTrue(result.has_audio)
        self.assertAlmostEqual(result.duration, 1.0, delta=0.1)

    def test_replace_audio_takes_an_uploaded_helper_track(self):
        clip = self.video_item(seconds=1.0)
        tone = self.tone_item(name="helper.wav", seconds=0.4, derived=True)
        run = self.save_ok([step("replace_audio", audio=tone.id)], {"item_id": clip.id})
        self.assertTrue(probe(self.stored_path(run.items[0])).has_audio)

    def test_a_replacement_track_of_someone_else_is_not_found_and_nothing_starts(self):
        clip = self.video_item(seconds=1.0)
        theirs = self.tone_item(name="theirs.wav", user_id=self.other_user_id)
        rows = len(self.upload_rows())
        response = self.save([step("mute"), step("replace_audio", audio={"item_id": theirs.id})], {"item_id": clip.id})
        self.refused(response, 404, "not_found")
        self.assertEqual(self.jobs.counts()["queued"] + self.jobs.counts()["running"], 0)
        self.assertEqual(len(self.upload_rows()), rows)

    def test_a_replacement_track_of_the_wrong_kind_names_the_step(self):
        clip = self.video_item(seconds=1.0)
        picture = self.image_item()
        detail = self.refused(
            self.save([step("mute"), step("replace_audio", audio=picture.id)], {"item_id": clip.id}),
            400,
            "unsupported_media",
        )
        self.assertEqual((detail["step_index"], detail["field"]), (1, "audio"))


class TestPaintLayer(EditorStackTestBase):
    def test_an_uploaded_layer_is_painted_over_the_picture(self):
        item = self.image_item(size=(20, 10), color=(10, 20, 30))
        layer = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
        layer.paste((250, 0, 0, 255), (0, 0, 10, 10))
        buffer = io.BytesIO()
        layer.save(buffer, format="PNG")
        uploaded = self.client.post(
            "/api/media-tools/artifacts", files={"file": ("layer.png", buffer.getvalue(), "image/png")}
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        layer_id = uploaded.json()["data"]["id"]

        run = self.save_ok([step("paint_layer", layer={"item_id": layer_id})], {"item_id": item.id})

        with Image.open(self.uploads_dir / self.upload_repo.get_by_id(run.items[0].id, self.user_id).filename) as out:
            pixels = out.convert("RGB")
            self.assertEqual(pixels.getpixel((2, 5)), (250, 0, 0))
            self.assertEqual(pixels.getpixel((15, 5)), (10, 20, 30))


class TestRunsReattach(EditorStackTestBase):
    def test_a_running_save_is_listed_and_readable_by_id_then_finishes(self):
        item = self.image_item()
        response = self.save([step("rot"), step("block")], {"item_id": item.id})
        run_id = response.json()["data"]["run_id"]
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        listed = self.client.get("/api/media-tools/runs").json()["data"]["runs"]
        self.assertEqual([(run["run_id"], run["kind"], run["status"]) for run in listed], [(run_id, "run", "running")])
        self.assertEqual(self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]["status"], "running")

        self.gate.release.set()
        self.wait_run(run_id)
        done = self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(len(done["items"]), 1)

    def test_cancelling_a_save_keeps_the_library_unchanged(self):
        item = self.image_item()
        rows = len(self.upload_rows())
        run_id = self.save([step("rot"), step("block")], {"item_id": item.id}).json()["data"]["run_id"]
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        cancelled = self.client.delete(f"/api/media-tools/runs/{run_id}").json()["data"]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(len(self.upload_rows()), rows)

    def test_another_user_cannot_see_or_cancel_the_run(self):
        item = self.image_item()
        run_id = self.save([step("block")], {"item_id": item.id}).json()["data"]["run_id"]
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        self.as_user(self.other_user_id)
        self.assertEqual(self.client.get("/api/media-tools/runs").json()["data"]["runs"], [])
        self.refused(self.client.get(f"/api/media-tools/runs/{run_id}"), 404, "not_found")
        self.refused(self.client.delete(f"/api/media-tools/runs/{run_id}"), 404, "not_found")
        self.as_user(self.user_id)
        self.assertEqual(self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]["status"], "running")


class TestArtifacts(EditorStackTestBase):
    def test_an_upload_is_a_helper_file_with_an_id_and_no_path(self):
        response = self.client.post(
            "/api/media-tools/artifacts", files={"file": ("mask.png", self.png_bytes((4, 4), (0, 0, 0, 255)), "image/png")}
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(
            sorted(data),
            sorted(["id", "media_type", "mime_type", "url", "width", "height", "duration_seconds", "fps", "size"]),
        )
        self.assertNotIn("path", data)
        self.assert_no_paths(response)
        row = self.upload_repo.get_by_id(data["id"], self.user_id)
        self.assertEqual((row.purpose, row.media_type), ("derived_artifact", "image"))
        self.assertTrue(data["url"].startswith("/api/media/uploads/"))

    def upload(self, name, data, content_type="image/png"):
        return self.client.post("/api/media-tools/artifacts", files={"file": (name, data, content_type)})

    def test_a_hostile_name_is_stored_flat_under_the_uploads(self):
        for name in ("../../evil.png", "..\\..\\evil.png", "/etc/passwd.png", "noext"):
            response = self.upload(name, self.png_bytes((4, 4), (len(name), 0, 0, 255)))
            self.assertEqual(response.status_code, 200, response.text)
            row = self.upload_repo.get_by_id(response.json()["data"]["id"], self.user_id)
            for text in (row.filename, row.original_filename):
                self.assertNotIn("/", text)
                self.assertNotIn("\\", text)
            self.assertNotIn("..", row.filename)
            self.assertTrue((self.uploads_dir / row.filename).is_file())

    def test_an_oversize_file_is_refused_before_it_is_read(self):
        with patch.object(validators, "DERIVED_ARTIFACT_MAX_BYTES", 64), patch.object(
            self.service, "upload_artifact", wraps=self.service.upload_artifact
        ) as stored:
            response = self.upload("big.png", b"x" * 65)
        self.refused(response, 400, "upload_failed")
        stored.assert_not_called()
        self.assertEqual(self.upload_rows(), [])

    def test_a_declared_type_that_is_not_media_is_refused(self):
        self.refused(self.upload("layer.png", self.png_bytes((4, 4), (1, 1, 1, 255)), "application/json"), 400, "upload_failed")

    def test_an_upload_over_the_quota_answers_403_and_stores_nothing(self):
        self.guard.refuse_admit = True
        response = self.upload("layer.png", self.png_bytes((4, 4), (1, 1, 1, 255)))
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.upload_rows(), [])
        self.assertEqual(self.stored_files(), [])

    def test_a_file_that_is_not_media_is_refused(self):
        response = self.client.post(
            "/api/media-tools/artifacts", files={"file": ("notes.txt", b"hello", "text/plain")}
        )
        self.refused(response, 400, "upload_failed")

    def test_another_user_cannot_use_my_upload(self):
        uploaded = self.client.post(
            "/api/media-tools/artifacts", files={"file": ("layer.png", self.png_bytes((4, 4), (1, 1, 1, 255)), "image/png")}
        ).json()["data"]
        self.as_user(self.other_user_id)
        theirs = self.image_item(name="theirs.png", user_id=self.other_user_id)
        response = self.save([step("paint_layer", layer=uploaded["id"])], {"item_id": theirs.id})
        self.refused(response, 404, "not_found")


class TestAuthentication(EditorStackTestBase):
    def test_every_editor_endpoint_needs_a_signed_in_user(self):
        client = self.anonymous_client()
        calls = [
            client.get("/api/media-tools/runs"),
            client.post("/api/media-tools/preview/frame", json={"input": {"item_id": "x"}}),
            client.post("/api/media-tools/preview/clip", json={"input": {"item_id": "x"}}),
            client.get(f"/api/media-tools/previews/{'a' * 32}"),
            client.post("/api/media-tools/artifacts", files={"file": ("a.png", b"x", "image/png")}),
        ]
        self.assertEqual([response.status_code for response in calls], [401] * len(calls))

    def test_a_refused_quota_answers_403_and_starts_nothing(self):
        item = self.image_item()
        self.guard.refuse_check = True
        response = self.save([step("rot")], {"item_id": item.id})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.jobs.counts()["queued"] + self.jobs.counts()["running"], 0)
