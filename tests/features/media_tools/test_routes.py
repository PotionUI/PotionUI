import io
import unittest

from PIL import Image

from src.features.collections.repository import CollectionRepository
from src.platform.media_tools import ToolSpec
from tests.features.media_tools.support import MediaToolsTestBase, needs_ffmpeg


def shrink(size=8):
    return {"tool": "shrink", "params": {"size": size}}


class TestCatalog(MediaToolsTestBase):

    def tool(self, tools, tool_id):
        return next(entry for entry in tools if entry["id"] == tool_id)

    def test_catalog_returns_the_contract_fields_and_never_the_implementation(self):
        response = self.client.get("/api/media-tools")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        entry = self.tool(body["data"]["tools"], "shrink")
        self.assertEqual(
            sorted(entry),
            sorted([
                "id", "label", "icon", "accepts", "produces", "cardinality", "params", "requires",
                "selection", "scopes", "editor", "preview", "component", "source", "description",
                "last_params", "runnable", "group",
            ]),
        )
        self.assertEqual(entry["accepts"], ["image"])
        self.assertEqual(entry["source"], "core")
        self.assertTrue(entry["preview"])
        self.assertIsNone(entry["last_params"])
        self.assertNotIn("impl", entry)

    def test_runnable_is_true_for_python_tools_and_false_for_component_only_tools(self):
        tools = self.client.get("/api/media-tools").json()["data"]["tools"]

        self.assertTrue(self.tool(tools, "shrink")["runnable"])
        self.assertTrue(self.tool(tools, "acme.mirror")["runnable"])
        self.assertFalse(self.tool(tools, "acme.panel")["runnable"])
        self.assertEqual(
            {tool["id"]: tool["runnable"] for tool in tools if tool["component"]},
            {"acme.panel": False},
        )

    def test_kind_filter_keeps_only_tools_that_accept_the_kind(self):
        audio = self.client.get("/api/media-tools", params={"kind": "audio"}).json()["data"]["tools"]
        video = self.client.get("/api/media-tools", params={"kind": "video"}).json()["data"]["tools"]

        self.assertEqual([entry["id"] for entry in audio], ["splittwo"])
        self.assertEqual(sorted(entry["id"] for entry in video), ["acme.panel", "rot", "toimage"])

    def test_scope_filter_drops_tools_not_offered_there(self):
        history = self.client.get("/api/media-tools", params={"scope": "history"}).json()["data"]["tools"]
        library = self.client.get("/api/media-tools", params={"scope": "library"}).json()["data"]["tools"]

        self.assertIn("acme.panel", [entry["id"] for entry in history])
        self.assertNotIn("acme.panel", [entry["id"] for entry in library])
        self.assertIn("shrink", [entry["id"] for entry in library])

    def test_a_component_only_plugin_tool_is_listed_with_its_component_string(self):
        tools = self.client.get("/api/media-tools").json()["data"]["tools"]

        panel = self.tool(tools, "acme.panel")
        self.assertEqual(panel["component"], "plugin:acme:panel")
        self.assertEqual(panel["source"], "acme")
        self.assertEqual(panel["selection"], {"min": 1, "max": 4})

    def test_unregistering_a_plugin_removes_its_tools_from_the_catalog(self):
        self.registry.unregister_source("acme")

        ids = [entry["id"] for entry in self.client.get("/api/media-tools").json()["data"]["tools"]]

        self.assertNotIn("acme.panel", ids)
        self.assertNotIn("acme.mirror", ids)
        self.assertIn("shrink", ids)

    def test_last_params_come_back_per_user(self):
        item = self.image_item()
        self.finish([shrink(12)], [{"item_id": item.id}])

        mine = self.tool(self.client.get("/api/media-tools").json()["data"]["tools"], "shrink")
        self.as_user(self.other_user_id)
        theirs = self.tool(self.client.get("/api/media-tools").json()["data"]["tools"], "shrink")

        self.assertEqual(mine["last_params"], {"size": 12})
        self.assertIsNone(theirs["last_params"])

    def test_an_unknown_kind_lists_nothing(self):
        tools = self.client.get("/api/media-tools", params={"kind": "hologram"}).json()["data"]["tools"]

        self.assertEqual(tools, [])


class TestRunHappyPaths(MediaToolsTestBase):

    def test_a_single_step_run_answers_202_and_saves_a_new_library_item(self):
        item = self.image_item(size=(40, 30))
        before = (self.uploads_dir / item.filename).read_bytes()

        response = self.start([shrink(8)], [{"item_id": item.id}])

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["success"])
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed")
        self.assertFalse(run.replaced)
        self.assertEqual(run.progress, 1.0)
        produced = run.items[0]
        self.assertNotEqual(produced.id, item.id)
        self.assertEqual((produced.width, produced.height), (8, 8))
        self.assertEqual(produced.media_type, "image")
        self.assertEqual(produced.original_filename, "original-pic-shrink.png")
        self.assertEqual(produced.url, f"/api/media/uploads/{produced.filename}")
        self.assertEqual((self.uploads_dir / item.filename).read_bytes(), before)
        with Image.open(self.uploads_dir / produced.filename) as written:
            self.assertEqual(written.size, (8, 8))
        self.assertEqual(self.upload_repo.get_by_id(produced.id, self.user_id).file_size, produced.size)
        self.assertEqual(len(self.upload_rows()), 2)

    def test_get_run_returns_the_envelope_with_the_contract_fields(self):
        item = self.image_item()
        run_id = self.start_ok([shrink()], [{"item_id": item.id}])
        self.wait_run(run_id)

        response = self.client.get(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(
            sorted(data),
            sorted(["run_id", "kind", "status", "progress", "label", "items", "replaced", "preview", "error", "summary"]),
        )
        self.assertEqual(data["status"], "completed")
        self.assertIsNone(data["error"])
        self.assertEqual(
            sorted(data["items"][0]),
            sorted([
                "id", "filename", "original_filename", "media_type", "mime_type", "url", "width",
                "height", "duration_seconds", "fps", "size", "created_at",
                "thumbnail_small", "thumbnail_medium", "thumbnail_large", "source_generation_id", "provenance",
            ]),
        )

    def test_a_chain_runs_every_step_and_names_the_result_after_the_last_one(self):
        item = self.image_item(size=(40, 30))

        run = self.finish([shrink(16), {"tool": "shrink2", "params": {"size": 4}}], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed")
        self.assertEqual((run.items[0].width, run.items[0].height), (4, 4))
        self.assertEqual(run.items[0].original_filename, "original-pic-again.png")
        self.assertEqual(len(self.upload_rows()), 2)

    def test_defaults_fill_in_omitted_params(self):
        item = self.image_item()

        run = self.finish([{"tool": "shrink"}], [{"item_id": item.id}])

        self.assertEqual((run.items[0].width, run.items[0].height), (8, 8))

    def test_one_run_over_several_items_makes_one_result_each(self):
        first = self.image_item("a.png")
        second = self.image_item("b.png")

        run = self.finish([shrink(6)], [{"item_id": first.id}, {"item_id": second.id}])

        self.assertEqual(
            [entry.original_filename for entry in run.items],
            ["original-a-shrink.png", "original-b-shrink.png"],
        )
        self.assertEqual(len(self.upload_rows()), 4)

    def test_a_many_to_one_tool_joins_the_selection_into_one_item(self):
        first = self.image_item("a.png", size=(10, 10))
        second = self.image_item("b.png", size=(20, 10))

        run = self.finish(
            [{"tool": "stack"}], [{"item_id": first.id}, {"item_id": second.id}]
        )

        self.assertEqual(len(run.items), 1)
        self.assertEqual((run.items[0].width, run.items[0].height), (30, 10))
        self.assertEqual(run.items[0].original_filename, "original-a-joined.png")

    @needs_ffmpeg
    def test_a_one_to_many_tool_saves_every_part_as_a_new_item(self):
        sound = self.audio_item("sound.wav", seconds=1.0)

        run = self.finish([{"tool": "splittwo"}], [{"item_id": sound.id}])

        self.assertEqual(run.status, "completed")
        self.assertEqual(
            [entry.original_filename for entry in run.items],
            ["original-sound — part 1 of 2.wav", "original-sound — part 2 of 2.wav"],
        )
        self.assertEqual(len(self.upload_rows()), 3)

    def test_a_generation_file_becomes_a_new_library_item_and_stays_untouched(self):
        record, on_disk = self.generation_file()
        before = on_disk.read_bytes()

        run = self.finish([shrink(5)], [{"generation_file_id": record.id}])

        self.assertEqual(run.status, "completed")
        self.assertFalse(run.replaced)
        self.assertEqual((run.items[0].width, run.items[0].height), (5, 5))
        self.assertEqual(run.items[0].original_filename, "0-shrink.png")
        self.assertEqual(on_disk.read_bytes(), before)
        self.assertEqual(len(self.upload_rows()), 1)
        self.assertEqual(self.file_repo.get_by_id(record.id, user_id=self.user_id).id, record.id)

    def test_the_real_rotate_tool_runs_end_to_end_through_the_api(self):
        from src.platform.media_tools.ops import register_core_tools

        register_core_tools(self.registry)
        item = self.image_item(size=(40, 30))

        run = self.finish([{"tool": "rotate", "params": {"degrees": 90}}], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed", run.error)
        self.assertEqual((run.items[0].width, run.items[0].height), (30, 40))
        self.assertEqual(run.items[0].original_filename, "original-pic-rotated.png")


class TestLastParams(MediaToolsTestBase):

    def test_a_single_step_run_remembers_its_validated_params(self):
        item = self.image_item()

        self.finish([shrink(12)], [{"item_id": item.id}])

        self.assertEqual(self.service.last_params(self.make_user(self.user_id), "shrink"), {"size": 12})
        self.assertIsNone(self.service.last_params(self.make_user(self.other_user_id), "shrink"))

    def test_a_chain_does_not_overwrite_what_a_single_run_remembered(self):
        item = self.image_item()
        self.finish([shrink(12)], [{"item_id": item.id}])

        self.finish([shrink(20), {"tool": "shrink2", "params": {"size": 3}}], [{"item_id": item.id}])

        self.assertEqual(self.params_repo.get(self.user_id, "shrink"), {"size": 12})
        self.assertIsNone(self.params_repo.get(self.user_id, "shrink2"))

    def test_a_failed_run_remembers_nothing(self):
        item = self.image_item()

        run = self.finish([{"tool": "explode"}], [{"item_id": item.id}])

        self.assertEqual(run.status, "failed")
        self.assertIsNone(self.params_repo.get(self.user_id, "explode"))

    def test_defaults_are_what_gets_remembered_when_params_were_omitted(self):
        item = self.image_item()

        self.finish([{"tool": "shrink"}], [{"item_id": item.id}])

        self.assertEqual(self.params_repo.get(self.user_id, "shrink"), {"size": 8})


class TestReplace(MediaToolsTestBase):

    def test_replace_keeps_the_row_id_and_swaps_the_file(self):
        item = self.image_item(size=(40, 30))
        old_file = self.uploads_dir / item.filename

        run = self.finish([shrink(8)], [{"item_id": item.id}], mode="replace")

        self.assertEqual(run.status, "completed")
        self.assertTrue(run.replaced)
        produced = run.items[0]
        self.assertEqual(produced.id, item.id)
        self.assertNotEqual(produced.filename, item.filename)
        self.assertFalse(old_file.exists())
        self.assertTrue((self.uploads_dir / produced.filename).exists())
        row = self.upload_repo.get_by_id(item.id, self.user_id)
        self.assertEqual((row.filename, row.width, row.height), (produced.filename, 8, 8))
        self.assertEqual(row.original_filename, item.original_filename)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_replace_keeps_tags_and_collection_membership(self):
        item = self.image_item()
        tag = self.tag_repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        self.tag_repo.set_upload_tags(item.id, [tag.id])
        collections = CollectionRepository()
        album = collections.create("album", self.user_id, "library")
        collections.add_upload_members(album.id, [item.id], self.user_id, "library")

        self.finish([shrink(8)], [{"item_id": item.id}], mode="replace")

        self.assertEqual([entry.name for entry in self.tag_repo.get_upload_tags(item.id)], ["cats"])
        self.assertEqual([entry.id for entry in collections.get_for_upload(item.id)], [album.id])

    def test_replace_with_a_chain_of_kind_preserving_tools_is_allowed(self):
        item = self.image_item()

        run = self.finish(
            [shrink(16), {"tool": "shrink2", "params": {"size": 4}}], [{"item_id": item.id}], mode="replace"
        )

        self.assertEqual(run.status, "completed")
        self.assertEqual((run.items[0].width, run.items[0].height), (4, 4))

    def test_a_failing_replace_leaves_the_original_row_and_file_alone(self):
        item = self.image_item()
        before = (self.uploads_dir / item.filename).read_bytes()

        run = self.finish([{"tool": "explode"}], [{"item_id": item.id}], mode="replace")

        self.assertEqual(run.status, "failed")
        row = self.upload_repo.get_by_id(item.id, self.user_id)
        self.assertEqual(row.filename, item.filename)
        self.assertEqual((self.uploads_dir / item.filename).read_bytes(), before)
        self.assertEqual(self.stored_files(), [item.filename])


class TestQuota(MediaToolsTestBase):

    def test_a_refused_precheck_answers_403_and_starts_nothing(self):
        item = self.image_item()
        self.guard.refuse_check = True

        response = self.start([shrink()], [{"item_id": item.id}])

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.error_of(response)["error"], "limit_exceeded")
        self.assertEqual(self.jobs.counts(), {"queued": 0, "running": 0, "finished": 0})

    def test_a_refused_admission_removes_the_stored_file_and_leaves_no_row(self):
        item = self.image_item()
        self.guard.refuse_admit = True

        run = self.finish([shrink()], [{"item_id": item.id}])

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "limit_exceeded")
        self.assertEqual(self.stored_files(), [item.filename])
        self.assertEqual(len(self.upload_rows()), 1)
        self.assertEqual(self.scratch_entries(), [])

    def test_a_refused_admission_cleans_up_every_part_of_a_split(self):
        self.guard.refuse_admit = True
        first = self.image_item("a.png")
        second = self.image_item("b.png")

        run = self.finish([shrink()], [{"item_id": first.id}, {"item_id": second.id}])

        self.assertEqual(run.status, "failed")
        self.assertEqual(self.stored_files(), ["a.png", "b.png"])
        self.assertEqual(len(self.upload_rows()), 2)

    def test_a_refused_replace_admission_keeps_the_original_file_and_row(self):
        item = self.image_item()
        self.guard.refuse_admit = True

        run = self.finish([shrink()], [{"item_id": item.id}], mode="replace")

        self.assertEqual(run.status, "failed")
        self.assertEqual(self.stored_files(), [item.filename])
        self.assertEqual(self.upload_repo.get_by_id(item.id, self.user_id).filename, item.filename)

    def test_replace_skips_the_precheck_and_new_runs_admit_with_the_item_sizes(self):
        item = self.image_item()

        self.finish([shrink()], [{"item_id": item.id}], mode="replace")
        self.assertEqual(self.guard.checks, [])
        self.finish([shrink()], [{"item_id": item.id}])

        self.assertEqual(len(self.guard.checks), 1)
        self.assertEqual(self.guard.checks[0].point, "upload")
        self.assertEqual(self.guard.checks[0].user_id, self.user_id)
        admitted = self.guard.admits[-1]
        self.assertEqual(len(admitted.item_bytes), 1)
        self.assertEqual(admitted.incoming_bytes, sum(admitted.item_bytes))


class TestRunErrors(MediaToolsTestBase):

    def refused(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        detail = self.error_of(response)
        self.assertEqual(detail["error"], code)
        self.assertFalse(detail["success"])
        return detail

    def test_unknown_tool(self):
        item = self.image_item()

        detail = self.refused(self.start([{"tool": "nope"}], [{"item_id": item.id}]), 400, "unknown_tool")

        self.assertEqual(detail["step_index"], 0)

    def test_a_component_only_tool_has_no_run_api(self):
        item = self.image_item()

        self.refused(self.start([{"tool": "acme.panel"}], [{"item_id": item.id}]), 400, "unknown_tool")

    def test_unsupported_media_for_the_input_kind(self):
        clip = self.fake_video()

        detail = self.refused(self.start([shrink()], [{"item_id": clip.id}]), 400, "unsupported_media")

        self.assertEqual(detail["step_index"], 0)

    def test_unsupported_media_for_a_kind_media_tools_do_not_edit(self):
        mesh = self.mesh_item()

        self.refused(self.start([shrink()], [{"item_id": mesh.id}]), 400, "unsupported_media")

    def fake_video(self):
        (self.uploads_dir / "fake.mp4").write_bytes(b"not really a video")
        return self.record(self.uploads_dir / "fake.mp4", "video")

    def test_unsupported_media_tracks_the_kind_through_the_chain(self):
        clip = self.fake_video()

        refused = self.start([{"tool": "toimage"}, {"tool": "splittwo"}], [{"item_id": clip.id}])
        accepted = self.start([{"tool": "toimage"}, shrink()], [{"item_id": clip.id}])

        detail = self.refused(refused, 400, "unsupported_media")
        self.assertEqual(detail["step_index"], 1)
        self.assertEqual(accepted.status_code, 202)
        self.wait_run(accepted.json()["data"]["run_id"])

    def test_invalid_params_name_the_field_and_the_step(self):
        item = self.image_item()

        detail = self.refused(
            self.start([shrink(), {"tool": "shrink2", "params": {"size": 0}}], [{"item_id": item.id}]),
            400, "invalid_params",
        )

        self.assertEqual(detail["field"], "size")
        self.assertEqual(detail["step_index"], 1)

    def test_invalid_params_for_an_unknown_parameter(self):
        item = self.image_item()

        detail = self.refused(
            self.start([{"tool": "shrink", "params": {"bogus": 1}}], [{"item_id": item.id}]), 400, "invalid_params"
        )

        self.assertEqual(detail["field"], "bogus")
        self.assertEqual(detail["step_index"], 0)

    def test_invalid_params_for_a_wrong_type(self):
        item = self.image_item()

        detail = self.refused(
            self.start([{"tool": "shrink", "params": {"size": "wide"}}], [{"item_id": item.id}]),
            400, "invalid_params",
        )

        self.assertEqual(detail["field"], "size")

    def test_zero_steps_and_too_many_steps_are_invalid_params_on_steps(self):
        item = self.image_item()

        empty = self.refused(self.start([], [{"item_id": item.id}]), 400, "invalid_params")
        many = self.refused(self.start([shrink()] * 65, [{"item_id": item.id}]), 400, "invalid_params")

        self.assertEqual((empty["field"], many["field"]), ("steps", "steps"))

    def test_invalid_selection_for_no_inputs_too_many_inputs_and_malformed_inputs(self):
        item = self.image_item()

        self.refused(self.start([shrink()], []), 400, "invalid_selection")
        self.refused(self.start([shrink()], [{"item_id": item.id}] * 51), 400, "invalid_selection")
        self.refused(self.start([shrink()], [{}]), 400, "invalid_selection")
        self.refused(
            self.start([shrink()], [{"item_id": item.id, "generation_file_id": "x"}]), 400, "invalid_selection"
        )

    def test_invalid_selection_for_a_many_to_one_tool_outside_its_bounds(self):
        first = self.image_item("a.png")
        others = [self.image_item(f"b{index}.png") for index in range(3)]

        too_few = self.refused(self.start([{"tool": "stack"}], [{"item_id": first.id}]), 400, "invalid_selection")
        too_many = self.refused(
            self.start([{"tool": "stack"}], [{"item_id": entry.id} for entry in [first, *others]]),
            400, "invalid_selection",
        )

        self.assertEqual((too_few["step_index"], too_many["step_index"]), (0, 0))

    def test_invalid_selection_applies_a_plugin_tools_own_bounds_to_one_to_one_steps(self):
        items = [self.image_item(f"c{index}.png") for index in range(4)]
        self.registry.register(ToolSpec(
            id="acme.pair", label="Pair", accepts=("image",), source="acme",
            selection={"min": 1, "max": 2}, impl=self.registry.get("shrink").impl,
        ))

        self.refused(
            self.start([{"tool": "acme.pair"}], [{"item_id": entry.id} for entry in items]),
            400, "invalid_selection",
        )

    def test_replace_not_allowed_for_a_generation_file(self):
        record, _ = self.generation_file()

        self.refused(
            self.start([shrink()], [{"generation_file_id": record.id}], mode="replace"),
            400, "replace_not_allowed",
        )

    def test_replace_not_allowed_for_several_items(self):
        first = self.image_item("a.png")
        second = self.image_item("b.png")

        self.refused(
            self.start([shrink()], [{"item_id": first.id}, {"item_id": second.id}], mode="replace"),
            400, "replace_not_allowed",
        )

    def test_replace_not_allowed_when_the_chain_changes_the_kind(self):
        clip = self.fake_video()

        self.refused(
            self.start([{"tool": "toimage"}], [{"item_id": clip.id}], mode="replace"),
            400, "replace_not_allowed",
        )

    def test_replace_not_allowed_for_a_many_to_one_or_one_to_many_tool(self):
        sound = self.record(self.write_audio_row(), "audio")

        self.refused(
            self.start([{"tool": "splittwo"}], [{"item_id": sound.id}], mode="replace"),
            400, "replace_not_allowed",
        )

    def write_audio_row(self):
        path = self.uploads_dir / "fake.wav"
        path.write_bytes(b"RIFF")
        return path

    def test_an_unknown_mode_is_rejected_by_request_validation(self):
        item = self.image_item()

        response = self.start([shrink()], [{"item_id": item.id}], mode="bogus")

        self.assertEqual(response.status_code, 422)

    def test_a_missing_item_and_someone_elses_item_are_both_plain_404s(self):
        theirs = self.image_item("theirs.png", user_id=self.other_user_id)

        missing = self.refused(self.start([shrink()], [{"item_id": "nope"}]), 404, "not_found")
        foreign = self.refused(self.start([shrink()], [{"item_id": theirs.id}]), 404, "not_found")

        self.assertEqual(missing["message"], foreign["message"])

    def test_a_row_whose_file_is_gone_is_a_404(self):
        item = self.image_item()
        (self.uploads_dir / item.filename).unlink()

        self.refused(self.start([shrink()], [{"item_id": item.id}]), 404, "not_found")

    def test_a_generation_file_that_belongs_to_someone_else_is_a_404(self):
        record, _ = self.generation_file(user_id=self.other_user_id)

        self.refused(self.start([shrink()], [{"generation_file_id": record.id}]), 404, "not_found")

    def test_a_generation_file_of_an_unsupported_type_is_unsupported_media(self):
        record, _ = self.generation_file(file_type="MESH", suffix=".glb")

        self.refused(self.start([shrink()], [{"generation_file_id": record.id}]), 400, "unsupported_media")

    def test_a_generation_file_whose_bytes_are_missing_is_a_404(self):
        record, on_disk = self.generation_file()
        on_disk.unlink()

        self.refused(self.start([shrink()], [{"generation_file_id": record.id}]), 404, "not_found")

    def test_a_refused_request_creates_no_run_and_no_scratch(self):
        item = self.image_item()

        self.start([{"tool": "nope"}], [{"item_id": item.id}])
        self.start([shrink(0)], [{"item_id": item.id}])

        self.assertEqual(self.jobs.counts(), {"queued": 0, "running": 0, "finished": 0})
        self.assertEqual(self.scratch_entries(), [])

    def test_unknown_run_ids_are_404_for_get_and_delete(self):
        self.refused(self.client.get("/api/media-tools/runs/nope"), 404, "not_found")
        self.refused(self.client.delete("/api/media-tools/runs/nope"), 404, "not_found")

    def test_a_tool_that_blows_up_fails_the_run_without_leaking_internals(self):
        item = self.image_item()

        run = self.finish([{"tool": "explode"}], [{"item_id": item.id}])

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "tool_failed")
        self.assertNotIn("secret", run.error.message)
        self.assertNotIn("/srv", run.error.message)
        self.assertEqual(run.items, [])
        self.assertEqual(len(self.upload_rows()), 1)
        self.assertEqual(self.scratch_entries(), [])

    def test_requires_is_enforced_at_run_time_and_reported_on_the_run(self):
        item = self.image_item(mode="RGB")

        run = self.finish([{"tool": "needsalpha"}], [{"item_id": item.id}])

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "unsupported_media")
        self.assertEqual(len(self.upload_rows()), 1)

    def test_requires_passes_for_an_image_with_transparency(self):
        item = self.image_item("alpha.png", mode="RGBA", color=(0, 0, 0, 0))

        run = self.finish([{"tool": "needsalpha"}], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed", run.error)

    def test_the_error_body_never_contains_an_absolute_path(self):
        item = self.image_item()

        run = self.finish([{"tool": "explode"}], [{"item_id": item.id}])

        self.assertNotIn(str(self.storage_dir), str(run.error))
        self.assertNotIn(str(self.scratch_root), str(run.error))


class TestOwnerIsolation(MediaToolsTestBase):

    def test_another_user_cannot_read_or_cancel_a_run(self):
        item = self.image_item()
        run_id = self.start_ok([{"tool": "block"}], [{"item_id": item.id}])
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        self.as_user(self.other_user_id)
        read = self.client.get(f"/api/media-tools/runs/{run_id}")
        cancel = self.client.delete(f"/api/media-tools/runs/{run_id}")
        self.as_user(self.user_id)
        still_running = self.client.get(f"/api/media-tools/runs/{run_id}")

        self.assertEqual((read.status_code, cancel.status_code), (404, 404))
        self.assertEqual(still_running.json()["data"]["status"], "running")
        self.gate.release.set()
        self.assertEqual(self.wait_run(run_id).status, "completed")

    def test_another_user_cannot_run_a_tool_on_my_item(self):
        item = self.image_item()
        self.as_user(self.other_user_id)

        response = self.start([shrink()], [{"item_id": item.id}])

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.upload_rows(self.other_user_id)), 0)

    def test_runs_of_two_users_do_not_mix_their_results(self):
        mine = self.image_item("mine.png")
        theirs = self.image_item("theirs.png", user_id=self.other_user_id)
        first = self.start_ok([shrink()], [{"item_id": mine.id}])
        self.as_user(self.other_user_id)
        second = self.start_ok([shrink()], [{"item_id": theirs.id}])

        their_run = self.wait_run(second, self.other_user_id)
        self.as_user(self.user_id)
        my_run = self.wait_run(first)

        self.assertEqual(len(self.upload_rows(self.user_id)), 2)
        self.assertEqual(len(self.upload_rows(self.other_user_id)), 2)
        self.assertNotEqual(my_run.items[0].id, their_run.items[0].id)


class TestCancelRunRoute(MediaToolsTestBase):

    def test_cancelling_a_running_run_stops_it_and_leaves_nothing_behind(self):
        item = self.image_item()
        run_id = self.start_ok([{"tool": "block"}], [{"item_id": item.id}])
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        response = self.client.delete(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "cancelled")
        self.assertEqual(self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]["status"], "cancelled")
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(self.stored_files(), [item.filename])
        self.assertEqual(len(self.upload_rows()), 1)

    @needs_ffmpeg
    def test_cancelling_kills_the_ffmpeg_process_and_removes_its_files(self):
        item = self.image_item()
        marker = f"killme-{self.registry.get('longencode').impl.token}"
        run_id = self.start_ok([{"tool": "longencode"}], [{"item_id": item.id}])
        pids = self.wait_for(lambda: self.encode_processes(marker))

        response = self.client.delete(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(response.json()["data"]["status"], "cancelled")
        self.wait_for(lambda: not any(self.process_alive(pid) for pid in pids))
        self.assertEqual(self.encode_processes(marker), [])
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(self.stored_files(), [item.filename])
        self.assertEqual(len(self.upload_rows()), 1)

    def process_alive(self, pid):
        import psutil

        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def test_cancelling_a_finished_run_is_a_no_op_that_returns_its_state(self):
        item = self.image_item()
        run_id = self.start_ok([shrink()], [{"item_id": item.id}])
        self.wait_run(run_id)

        response = self.client.delete(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "completed")
        self.assertEqual(len(self.upload_rows()), 2)

    def test_cancelling_a_queued_run_means_it_never_starts(self):
        items = [self.image_item(f"q{index}.png") for index in range(3)]
        ids = [self.start_ok([{"tool": "block"}], [{"item_id": entry.id}]) for entry in items]
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        self.assertEqual(self.jobs.counts()["queued"], 1)

        response = self.client.delete(f"/api/media-tools/runs/{ids[2]}")
        self.gate.release.set()
        self.wait_run(ids[0])
        self.wait_run(ids[1])

        self.assertEqual(response.json()["data"]["status"], "cancelled")
        self.assertEqual(self.client.get(f"/api/media-tools/runs/{ids[2]}").json()["data"]["status"], "cancelled")
        self.assertEqual(len(self.gate.seen), 2)
        self.assertEqual(len(self.upload_rows()), 5)


class TestConcurrency(MediaToolsTestBase):

    def test_at_most_two_runs_execute_at_once_and_the_rest_wait_their_turn(self):
        items = [self.image_item(f"c{index}.png") for index in range(3)]
        ids = [self.start_ok([{"tool": "block"}], [{"item_id": entry.id}]) for entry in items]
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        statuses = [self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]["status"] for run_id in ids]

        self.assertEqual(sorted(statuses), ["queued", "running", "running"])
        self.assertEqual(len(self.gate.seen), 2)
        self.gate.release.set()
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        finals = [self.wait_run(run_id).status for run_id in ids]
        self.assertEqual(finals, ["completed"] * 3)
        self.assertEqual(len(self.gate.seen), 3)

    def test_queued_runs_start_in_submission_order(self):
        items = [self.image_item(f"o{index}.png") for index in range(3)]
        ids = [self.start_ok([{"tool": "block"}], [{"item_id": entry.id}]) for entry in items]
        self.assertTrue(self.gate.entered.acquire(timeout=10))
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        queued = [
            run_id for run_id in ids
            if self.client.get(f"/api/media-tools/runs/{run_id}").json()["data"]["status"] == "queued"
        ]

        self.assertEqual(queued, [ids[2]])


class TestRetention(MediaToolsTestBase):

    def test_a_finished_run_is_kept_for_ten_minutes_then_forgotten(self):
        item = self.image_item()
        run_id = self.start_ok([shrink()], [{"item_id": item.id}])
        self.wait_run(run_id)

        self.clock.advance(599)
        kept = self.client.get(f"/api/media-tools/runs/{run_id}")
        self.clock.advance(2)
        gone = self.client.get(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(kept.status_code, 200)
        self.assertEqual(gone.status_code, 404)

    def test_a_running_run_is_never_forgotten(self):
        item = self.image_item()
        run_id = self.start_ok([{"tool": "block"}], [{"item_id": item.id}])
        self.assertTrue(self.gate.entered.acquire(timeout=10))

        self.clock.advance(3600)
        response = self.client.get(f"/api/media-tools/runs/{run_id}")

        self.assertEqual(response.json()["data"]["status"], "running")
        self.gate.release.set()
        self.wait_run(run_id)


class TestPreview(MediaToolsTestBase):

    def preview(self, tool, params, source, **extra):
        return self.client.post(
            "/api/media-tools/preview", json={"tool": tool, "params": params, "input": source, **extra}
        )

    def decode(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/webp")
        return Image.open(io.BytesIO(response.content))

    def test_a_preview_returns_webp_bytes_of_the_tool_result(self):
        item = self.image_item(size=(40, 30))

        image = self.decode(self.preview("shrink", {"size": 8}, {"item_id": item.id}))

        self.assertEqual(image.format, "WEBP")
        self.assertEqual(image.size, (8, 8))

    def test_the_long_side_of_a_preview_never_exceeds_512(self):
        item = self.image_item(size=(40, 30))

        image = self.decode(self.preview("grow", {"size": 900}, {"item_id": item.id}))

        self.assertEqual(max(image.size), 512)

    def test_a_large_source_is_downscaled_before_the_tool_sees_it(self):
        item = self.image_item("big.png", size=(3000, 2000))

        self.decode(self.preview("grow", {"size": 100}, {"item_id": item.id}))

        self.assertEqual(max(self.registry.get("grow").impl.seen[-1]), 1024)

    def test_a_preview_of_a_generation_file_works(self):
        record, _ = self.generation_file(size=(32, 24))

        image = self.decode(self.preview("shrink", {"size": 6}, {"generation_file_id": record.id}))

        self.assertEqual(image.size, (6, 6))

    @needs_ffmpeg
    def test_a_video_preview_uses_the_frame_at_the_requested_time(self):
        clip = self.video_item(seconds=1.0)

        image = self.decode(self.preview("rot", {}, {"item_id": clip.id}, time_seconds=0.5))

        self.assertEqual(image.size, (48, 64))

    def test_a_preview_writes_nothing_and_leaves_no_scratch(self):
        item = self.image_item()
        before = self.stored_files()

        self.decode(self.preview("shrink", {"size": 8}, {"item_id": item.id}))

        self.assertEqual(self.stored_files(), before)
        self.assertEqual(len(self.upload_rows()), 1)
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(self.guard.admits, [])
        self.assertEqual(self.jobs.counts(), {"queued": 0, "running": 0, "finished": 0})

    def test_a_tool_without_preview_is_a_400(self):
        item = self.image_item()

        response = self.preview("shrink2", {"size": 8}, {"item_id": item.id})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "unsupported_media")

    def test_a_tool_that_does_not_take_the_kind_is_a_400(self):
        sound = self.record(self.write_wav(), "audio")

        response = self.preview("rot", {}, {"item_id": sound.id})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "unsupported_media")

    def write_wav(self):
        path = self.uploads_dir / "fake.wav"
        path.write_bytes(b"RIFF")
        return path

    def test_a_video_for_an_image_only_tool_is_a_400(self):
        path = self.uploads_dir / "fake.mp4"
        path.write_bytes(b"not a video")
        clip = self.record(path, "video")

        response = self.preview("shrink", {"size": 8}, {"item_id": clip.id})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "unsupported_media")

    def test_invalid_params_name_the_field(self):
        item = self.image_item()

        response = self.preview("shrink", {"size": 0}, {"item_id": item.id})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "invalid_params")
        self.assertEqual(self.error_of(response)["field"], "size")

    def test_unknown_tool_and_unknown_or_foreign_item(self):
        theirs = self.image_item("theirs.png", user_id=self.other_user_id)

        unknown = self.preview("nope", {}, {"item_id": "x"})
        missing = self.preview("shrink", {}, {"item_id": "nope"})
        foreign = self.preview("shrink", {}, {"item_id": theirs.id})
        malformed = self.preview("shrink", {}, {})

        self.assertEqual((unknown.status_code, self.error_of(unknown)["error"]), (400, "unknown_tool"))
        self.assertEqual((missing.status_code, self.error_of(missing)["error"]), (404, "not_found"))
        self.assertEqual((foreign.status_code, self.error_of(foreign)["error"]), (404, "not_found"))
        self.assertEqual((malformed.status_code, self.error_of(malformed)["error"]), (400, "invalid_selection"))


if __name__ == "__main__":
    unittest.main()
