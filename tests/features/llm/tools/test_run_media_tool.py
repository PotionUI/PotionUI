import asyncio
import json

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.library_items_tool import ListLibraryItemsTool
from src.features.llm.tools.builtin.run_media_tool_tool import RunMediaToolTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry
from tests.features.media_tools.support import MediaToolsTestBase

class RunMediaToolCase(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        self.tool = RunMediaToolTool(self.registry)
        self.tools = ToolRegistry()
        self.tools.register(self.tool)
        self.executor = ToolExecutor(self.tools)

    def context(self, user_id=None):
        return ToolContext(user_id=user_id or self.user_id, media_tool_service=self.service)

    def call(self, user_id=None, **arguments):
        return asyncio.run(self.executor._execute_tool("run_media_tool", self.context(user_id), arguments))

    def confirm(self, user_id=None, **arguments):
        return asyncio.run(self.executor.execute_tool_confirmed("run_media_tool", self.context(user_id), arguments))

    def payload(self, result):
        self.assertTrue(result.success, result.error)
        return json.loads(result.data)

    def schema(self, names=None):
        schemas = self.tools.get_schemas(names or ["run_media_tool"])
        return next(item for item in schemas if item["function"]["name"] == "run_media_tool")["function"]

    def tool_enum(self, schema):
        return set(schema["parameters"]["properties"]["steps"]["items"]["properties"]["tool"]["enum"])

    def row(self, item_id):
        return self.upload_repo.get_by_id(item_id, self.user_id)


class TestSchemaFromTheCatalog(RunMediaToolCase):

    def test_the_tool_enum_lists_every_runnable_tool_and_skips_component_only_ones(self):
        schema = self.schema()

        runnable = {spec.id for spec in self.registry.list() if spec.impl is not None}
        self.assertEqual(self.tool_enum(schema), runnable)
        self.assertTrue({"shrink", "acme.mirror"} <= runnable)
        self.assertIsNone(self.registry.get("acme.panel").impl)
        self.assertNotIn("acme.panel", self.tool_enum(schema))
        self.assertNotIn("acme.panel", schema["description"])

    def test_the_description_states_what_each_tool_accepts_and_takes_in_compact_form(self):
        description = self.schema()["description"]

        self.assertIn("- shrink (image): size int[1..64] =8", description)
        self.assertIn("- acme.mirror (image): size int[1..64] =8", description)
        self.assertIn("- rot (image|video): no params", description)
        self.assertIn("- toimage (video -> image): no params", description)
        self.assertIn("needs transparency", description)
        self.assertIn("splits one item into several", description)
        self.assertIn("joins 2-3 items into one", description)

    def test_the_request_shape_requires_steps_and_items_and_defaults_to_new(self):
        parameters = self.schema()["parameters"]

        self.assertEqual(parameters["required"], ["steps", "items"])
        self.assertEqual(parameters["properties"]["mode"]["enum"], ["new", "replace"])
        self.assertEqual(parameters["properties"]["mode"]["default"], "new")
        self.assertEqual(parameters["properties"]["steps"]["items"]["required"], ["tool"])

    def test_a_plugin_tool_leaves_the_schema_when_its_plugin_is_disabled_and_returns_when_enabled(self):
        mirror = self.registry.get("acme.mirror")
        self.assertIn("acme.mirror", self.tool_enum(self.schema()))

        self.registry.unregister_source("acme")
        self.assertNotIn("acme.mirror", self.tool_enum(self.schema()))
        self.assertNotIn("acme.mirror", self.schema()["description"])

        self.registry.register(mirror)
        self.assertIn("acme.mirror", self.tool_enum(self.schema()))

    def test_references_to_sibling_tools_appear_only_when_those_tools_are_offered(self):
        alone = self.schema()["description"]
        self.assertNotIn("list_library_items", alone)
        self.assertNotIn("search_gallery", alone)

        self.tools.register(ListLibraryItemsTool())
        self.tools.register(SearchGalleryTool())
        both = self.schema(["run_media_tool", "list_library_items", "search_gallery"])["description"]
        self.assertIn("from list_library_items", both)
        self.assertIn("file_id from search_gallery", both)

    def test_only_replace_needs_approval(self):
        self.assertTrue(self.tool.requires_approval)
        self.assertFalse(self.tool.approval_required({}))
        self.assertFalse(self.tool.approval_required({"mode": "new"}))
        self.assertTrue(self.tool.approval_required({"mode": "replace"}))
        self.assertTrue(self.tool.approval_required({"mode": " Replace "}))


class TestNewMode(RunMediaToolCase):

    def test_a_new_mode_run_executes_without_approval_and_reports_terse_items(self):
        item = self.image_item()

        result, pending = self.call(
            steps=[{"tool": "shrink", "params": {"size": 12}}], items=[{"item_id": item.id}]
        )

        self.assertFalse(pending)
        data = self.payload(result)
        self.assertEqual(data["status"], "completed")
        self.assertFalse(data["replaced"])
        [entry] = data["items"]
        self.assertEqual(
            set(entry), {"id", "filename", "url", "kind", "size", "width", "height"}
        )
        self.assertEqual((entry["width"], entry["height"], entry["kind"]), (12, 12, "image"))
        self.assertTrue(entry["url"].startswith("/api/media/uploads/"))
        self.assertEqual(entry["filename"], "original-pic-shrink.png")
        self.assertNotEqual(entry["filename"], entry["url"].rsplit("/", 1)[1])
        self.assertGreater(entry["size"], 0)
        self.assertNotEqual(entry["id"], item.id)
        self.assertEqual(self.row(item.id).filename, item.filename)
        self.assertIsNotNone(self.row(entry["id"]))

    def test_the_default_mode_is_new_and_params_may_be_omitted(self):
        item = self.image_item()

        result, pending = self.call(steps=[{"tool": "shrink"}], items=[{"item_id": item.id}])

        self.assertFalse(pending)
        self.assertEqual(self.payload(result)["items"][0]["width"], 8)

    def test_chained_steps_run_in_order_on_every_item(self):
        first = self.image_item("a.png", size=(40, 30))
        second = self.image_item("b.png", size=(50, 20))

        result, _pending = self.call(
            steps=[{"tool": "rot"}, {"tool": "shrink", "params": {"size": 9}}],
            items=[{"item_id": first.id}, {"item_id": second.id}],
        )

        items = self.payload(result)["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual({(entry["width"], entry["height"]) for entry in items}, {(9, 9)})

    def test_a_generated_file_is_a_valid_input_and_always_produces_a_new_item(self):
        record, _path = self.generation_file()

        result, pending = self.call(
            steps=[{"tool": "shrink", "params": {"size": 6}}], items=[{"generation_file_id": record.id}]
        )

        self.assertFalse(pending)
        [entry] = self.payload(result)["items"]
        self.assertEqual((entry["width"], entry["height"]), (6, 6))
        self.assertIsNotNone(self.row(entry["id"]))

    def test_arguments_the_model_wrote_as_json_text_are_accepted(self):
        item = self.image_item()

        result, _pending = self.call(
            steps=json.dumps([{"tool": "shrink", "params": json.dumps({"size": 5})}]),
            items=json.dumps({"item_id": item.id}),
        )

        self.assertEqual(self.payload(result)["items"][0]["width"], 5)

    def test_a_plugin_tool_runs_like_a_core_tool(self):
        item = self.image_item()

        result, _pending = self.call(
            steps=[{"tool": "acme.mirror", "params": {"size": 4}}], items=[{"item_id": item.id}]
        )

        self.assertEqual(self.payload(result)["items"][0]["width"], 4)

    def test_a_confirm_call_on_a_new_mode_request_is_refused(self):
        item = self.image_item()

        result = self.confirm(steps=[{"tool": "shrink"}], items=[{"item_id": item.id}])

        self.assertFalse(result.success)
        self.assertIn("does not require approval", result.error)
        self.assertEqual(len(self.upload_rows()), 1)


class TestReplaceMode(RunMediaToolCase):

    def test_replace_is_proposed_for_approval_and_changes_nothing_yet(self):
        item = self.image_item()

        result, pending = self.call(
            steps=[{"tool": "shrink", "params": {"size": 10}}], items=[{"item_id": item.id}], mode="replace"
        )

        self.assertTrue(pending)
        self.assertTrue(result.success)
        self.assertEqual(result.preview.action, "Replace original")
        self.assertEqual(result.preview.target, item.original_filename)
        self.assertEqual(result.preview.items, ["Shrink (size=10)"])
        self.assertIn("cannot be restored", result.preview.note)
        proposal = json.loads(result.data)["proposal"]
        self.assertEqual(proposal["mode"], "replace")
        self.assertEqual(proposal["items"], [{"item_id": item.id}])
        self.assertEqual(self.row(item.id).filename, item.filename)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_a_default_settings_step_is_described_as_such(self):
        item = self.image_item()

        result, _pending = self.call(steps=[{"tool": "rot"}], items=[{"item_id": item.id}], mode="replace")

        self.assertEqual(result.preview.items, ["Rot (default settings)"])

    def test_the_approved_replace_overwrites_the_original_and_keeps_its_id(self):
        item = self.image_item()
        arguments = {"steps": [{"tool": "shrink", "params": {"size": 10}}], "items": [{"item_id": item.id}], "mode": "replace"}

        result = self.confirm(**arguments)

        data = self.payload(result)
        self.assertTrue(data["replaced"])
        [entry] = data["items"]
        self.assertEqual(entry["id"], item.id)
        self.assertEqual((entry["width"], entry["height"]), (10, 10))
        row = self.row(item.id)
        self.assertNotEqual(row.filename, item.filename)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_replacing_a_generated_file_is_refused_before_anyone_is_asked(self):
        record, _path = self.generation_file()

        result, pending = self.call(
            steps=[{"tool": "shrink"}], items=[{"generation_file_id": record.id}], mode="replace"
        )

        self.assertFalse(pending)
        self.assertFalse(result.success)
        self.assertIn("replace_not_allowed", result.error)
        self.assertIn("Use mode 'new' instead", result.error)

    def test_replacing_several_items_is_refused_before_anyone_is_asked(self):
        first, second = self.image_item("a.png"), self.image_item("b.png")

        result, pending = self.call(
            steps=[{"tool": "shrink"}], items=[{"item_id": first.id}, {"item_id": second.id}], mode="replace"
        )

        self.assertFalse(pending)
        self.assertIn("replace_not_allowed", result.error)

    def test_a_refused_proposal_for_an_unknown_tool_never_reaches_approval(self):
        item = self.image_item()

        result, pending = self.call(steps=[{"tool": "nope"}], items=[{"item_id": item.id}], mode="replace")

        self.assertFalse(pending)
        self.assertIn("unknown_tool (step 1)", result.error)


class TestErrors(RunMediaToolCase):

    def failure(self, **arguments):
        result, pending = self.call(**arguments)
        self.assertFalse(result.success)
        self.assertFalse(pending)
        return result.error

    def test_an_unknown_tool_names_the_valid_ones(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "shrink"}, {"tool": "nope"}], items=[{"item_id": item.id}])

        self.assertIn("unknown_tool (step 2): Unknown tool: nope.", error)
        self.assertIn("Tools: ", error)
        self.assertIn("acme.mirror", error)
        self.assertNotIn("acme.panel", error)

    def test_invalid_params_carry_the_step_and_the_field(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "shrink", "params": {"size": 9999}}], items=[{"item_id": item.id}])

        self.assertTrue(error.startswith("invalid_params (step 1, 'size'): "), error)
        self.assertIn("at most 64", error)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_an_unknown_param_is_named(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "shrink", "params": {"colour": 1}}], items=[{"item_id": item.id}])

        self.assertIn("Unknown parameter: colour", error)

    def test_a_tool_that_does_not_accept_the_kind_is_refused(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "toimage"}], items=[{"item_id": item.id}])

        self.assertIn("unsupported_media (step 1)", error)
        self.assertIn("cannot be applied to image", error)

    def test_the_wrong_number_of_items_for_a_joining_tool_is_refused(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "stack"}], items=[{"item_id": item.id}])

        self.assertIn("invalid_selection (step 1)", error)

    def test_a_missing_item_points_at_where_ids_come_from(self):
        error = self.failure(steps=[{"tool": "shrink"}], items=[{"item_id": "does-not-exist"}])

        self.assertIn("not_found: Media not found.", error)
        self.assertIn("generation_file_id", error)

    def test_another_users_item_is_not_found_not_forbidden(self):
        foreign = self.image_item(user_id=self.other_user_id)

        error = self.failure(steps=[{"tool": "shrink"}], items=[{"item_id": foreign.id}])

        self.assertIn("not_found", error)
        self.assertEqual(len(self.upload_rows(self.other_user_id)), 1)
        self.assertEqual(self.upload_rows(), [])

    def test_another_users_generated_file_is_not_found(self):
        record, _path = self.generation_file(user_id=self.other_user_id)

        error = self.failure(steps=[{"tool": "shrink"}], items=[{"generation_file_id": record.id}])

        self.assertIn("not_found", error)

    def test_an_item_with_both_ids_or_none_is_an_invalid_selection(self):
        item = self.image_item()

        both = self.failure(
            steps=[{"tool": "shrink"}], items=[{"item_id": item.id, "generation_file_id": "x"}]
        )
        none = self.failure(steps=[{"tool": "shrink"}], items=[{}])

        self.assertIn("invalid_selection", both)
        self.assertIn("invalid_selection", none)

    def test_a_failed_run_reports_the_code_without_leaking_the_internal_message(self):
        item = self.image_item()

        error = self.failure(steps=[{"tool": "explode"}], items=[{"item_id": item.id}])

        self.assertTrue(error.startswith("tool_failed: "), error)
        self.assertNotIn("/srv/private", error)

    def test_a_quota_refusal_comes_back_as_a_message_the_model_can_relay(self):
        item = self.image_item()
        self.guard.refuse_check = True

        error = self.failure(steps=[{"tool": "shrink"}], items=[{"item_id": item.id}])

        self.assertIn("Upload limit reached", error)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_malformed_arguments_get_a_teaching_error(self):
        item = self.image_item()
        refs = [{"item_id": item.id}]

        missing_steps = self.failure(items=refs)
        no_items = self.failure(steps=[{"tool": "shrink"}])
        text_steps = self.failure(steps="trim it", items=refs)
        no_tool = self.failure(steps=[{"params": {}}], items=refs)
        bad_params = self.failure(steps=[{"tool": "shrink", "params": [1]}], items=refs)
        bad_mode = self.failure(steps=[{"tool": "shrink"}], items=refs, mode="overwrite")

        self.assertIn("'steps' must be a non-empty array of objects", missing_steps)
        self.assertIn('"item_id"', missing_steps)
        self.assertIn("'items' must be a non-empty array of objects", no_items)
        self.assertIn("'steps' must be a non-empty array", text_steps)
        self.assertIn("Step 1 has no 'tool'", no_tool)
        self.assertIn("Step 1 'params' must be an object", bad_params)
        self.assertIn("'mode' 'overwrite' is not valid", bad_mode)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_an_invalid_mode_is_not_treated_as_a_replace(self):
        self.assertFalse(self.tool.approval_required({"mode": "overwrite"}))

    def test_without_the_media_tool_service_the_tool_says_so(self):
        item = self.image_item()
        context = ToolContext(user_id=self.user_id)

        result = asyncio.run(self.tool.execute(context, steps=[{"tool": "shrink"}], items=[{"item_id": item.id}]))

        self.assertFalse(result.success)
        self.assertIn("not available", result.error)


class TestLongResults(RunMediaToolCase):

    def test_a_fifty_part_result_lists_twenty_items_and_says_it_was_cut(self):
        from src.features.media_tools.dto import MediaToolItem, RunResult

        parts = [
            MediaToolItem(id=f"p{index}", filename=f"part-{index}.wav", media_type="audio", url=f"/api/media/uploads/{index}.wav", size=10)
            for index in range(50)
        ]

        class Splitter:
            async def run_sync(self, *args, **kwargs):
                return RunResult(items=parts, run_id="r")

        context = ToolContext(user_id=self.user_id, media_tool_service=Splitter())
        result = asyncio.run(self.tool.execute(context, steps=[{"tool": "splittwo"}], items=[{"item_id": "x"}]))

        data = json.loads(result.data)
        self.assertEqual((data["count"], len(data["items"]), data["truncated"]), (50, 20, True))
        self.assertEqual(data["items"][-1]["id"], "p19")
        self.assertLess(len(result.data), 8000)

    def test_a_run_that_saved_elsewhere_reports_its_own_summary(self):
        from src.features.media_tools.dto import RunResult, RunSummaryDTO

        class SavesElsewhere:
            async def run_sync(self, *args, **kwargs):
                return RunResult(items=[], run_id="r", summary=RunSummaryDTO(message="Saved hero to the model folder"))

        context = ToolContext(user_id=self.user_id, media_tool_service=SavesElsewhere())
        result = asyncio.run(self.tool.execute(context, steps=[{"tool": "splittwo"}], items=[{"item_id": "x"}]))

        data = json.loads(result.data)
        self.assertEqual((data["count"], data["message"]), (0, "Saved hero to the model folder"))

    def test_a_short_result_reports_its_count_and_is_not_marked_truncated(self):
        item = self.image_item()

        data = self.payload(self.call(steps=[{"tool": "shrink"}], items=[{"item_id": item.id}])[0])

        self.assertEqual(data["count"], 1)
        self.assertNotIn("truncated", data)
