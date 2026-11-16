from unittest.mock import patch

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.media.media_types import MediaTypeResolver
from src.features.media_tools.service import MediaToolService
from src.platform.media_tools.ops import register_core_tools
from tests.features.edits.support import STATE
from tests.features.media_tools.support import MediaToolsTestBase

SHRINK = [{"tool": "shrink", "params": {"size": 20}}]


class EditorLinkBase(MediaToolsTestBase):

    def setUp(self):
        self.edits = EditRepository()
        super().setUp()
        register_core_tools(self.registry)
        self.service.publisher.thumbnail_runner = lambda task: None
        self.manager = EditManager(self.edits, self.upload_repo)

    def make_service(self, driver):
        return MediaToolService(
            registry=self.registry,
            params_repository=self.params_repo,
            upload_repository=self.upload_repo,
            file_repository=self.file_repo,
            media_type_resolver=MediaTypeResolver(),
            storage_driver=driver,
            limit_guard=self.guard,
            jobs=self.jobs,
            scratch_root=self.scratch_root,
            edit_repository=self.edits,
        )

    def edit_for(self, user_id=None):
        owner = user_id or self.user_id
        source = self.image_item(f"src-{owner}.png", user_id=owner)
        return self.manager.create(owner, "image", None, "library", source.id, STATE)

    def run_with(self, item, edit, mode="new", target=None, revision=1):
        output = {"mode": mode}
        if target is not None:
            output["target_item_id"] = target.id
        if edit is not None:
            output["edit"] = {"id": edit if isinstance(edit, str) else edit.id, "revision": revision}
        response = self.client.post(
            "/api/media-tools/runs",
            json={"steps": SHRINK, "inputs": [{"item_id": item.id}], "output": output},
        )
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed", run.error)
        return run

    def stored(self, item_id):
        return self.upload_repo.get_by_id(item_id, self.user_id).provenance


class TestRecording(EditorLinkBase):

    def test_new_records_a_continuable_link_on_every_output(self):
        edit = self.edit_for()
        item = self.image_item()

        run = self.run_with(item, edit)

        self.assertEqual(self.stored(run.items[0].id), {
            "edit": {"id": edit.id, "tool": "image", "revision": 1, "continuable": True}
        })

    def test_update_records_a_continuable_link_on_the_target(self):
        edit = self.edit_for()
        item = self.image_item()
        target = self.image_item("target.png")

        self.run_with(item, edit, mode="update", target=target)

        self.assertEqual(self.stored(target.id)["edit"]["continuable"], True)
        self.assertEqual(self.stored(target.id)["edit"]["id"], edit.id)
        self.assertIsNone(self.stored(item.id))

    def test_replace_records_a_link_that_is_not_continuable(self):
        edit = self.edit_for()
        item = self.image_item()

        self.run_with(item, edit, mode="replace")

        self.assertEqual(self.stored(item.id), {
            "edit": {"id": edit.id, "tool": "image", "revision": 1, "continuable": False}
        })

    def test_the_tool_comes_from_the_edit_row(self):
        edit = self.edit_for()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE edits SET tool = 'video' WHERE id = ?", (edit.id,))
        run = self.run_with(self.image_item(), edit)

        self.assertEqual(self.stored(run.items[0].id)["edit"]["tool"], "video")

    def test_the_revision_is_the_smaller_of_client_and_server(self):
        edit = self.edit_for()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE edits SET revision = 4 WHERE id = ?", (edit.id,))

        ahead = self.run_with(self.image_item("a.png"), edit, revision=9)
        behind = self.run_with(self.image_item("b.png"), edit, revision=2)

        self.assertEqual(self.stored(ahead.items[0].id)["edit"]["revision"], 4)
        self.assertEqual(self.stored(behind.items[0].id)["edit"]["revision"], 2)

    def test_a_replace_without_a_link_removes_the_old_one_and_keeps_other_keys(self):
        edit = self.edit_for()
        item = self.image_item()
        self.run_with(item, edit, mode="replace")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE uploads SET provenance = ? WHERE id = ?",
                ('{"slice": {"index": 1}, "edit": {"id": "%s"}}' % edit.id, item.id),
            )

        self.run_with(item, None, mode="replace")

        self.assertEqual(self.stored(item.id), {"slice": {"index": 1}})

    def test_removing_the_only_key_stores_null(self):
        edit = self.edit_for()
        item = self.image_item()
        self.run_with(item, edit, mode="replace")

        self.run_with(item, None, mode="replace")

        self.assertIsNone(self.stored(item.id))
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT provenance FROM uploads WHERE id = ?", (item.id,))
            self.assertIsNone(cursor.fetchone()["provenance"])

    def test_a_replace_with_a_link_keeps_other_keys(self):
        edit = self.edit_for()
        item = self.image_item()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE uploads SET provenance = ? WHERE id = ?", ('{"slice": {"index": 1}}', item.id))

        self.run_with(item, edit, mode="replace")

        stored = self.stored(item.id)
        self.assertEqual(stored["slice"], {"index": 1})
        self.assertEqual(stored["edit"]["id"], edit.id)


class TestUnverifiedLinks(EditorLinkBase):

    def shapes(self, run):
        return sorted(run.items[0].model_dump().keys()) if hasattr(run.items[0], "model_dump") else None

    def test_another_users_edit_is_not_recorded_and_the_save_succeeds(self):
        foreign = self.edit_for(self.other_user_id)
        mine = self.edit_for()
        baseline = self.run_with(self.image_item("a.png"), mine)

        run = self.run_with(self.image_item("b.png"), foreign)

        self.assertIsNone(self.stored(run.items[0].id))
        self.assertEqual(self.shapes(run), self.shapes(baseline))
        self.assertFalse(run.replaced)

    def test_a_missing_edit_is_not_recorded(self):
        run = self.run_with(self.image_item(), "no-such-edit")

        self.assertIsNone(self.stored(run.items[0].id))

    def test_a_deleted_edit_is_not_recorded(self):
        edit = self.edit_for()
        self.manager.delete(self.user_id, edit.id)

        run = self.run_with(self.image_item(), edit)

        self.assertIsNone(self.stored(run.items[0].id))

    def test_a_foreign_id_on_replace_clears_a_stale_link(self):
        mine = self.edit_for()
        foreign = self.edit_for(self.other_user_id)
        item = self.image_item()
        self.run_with(item, mine, mode="replace")

        self.run_with(item, foreign, mode="replace")

        self.assertIsNone(self.stored(item.id))

    def test_no_edit_field_behaves_as_before(self):
        run = self.run_with(self.image_item(), None)

        self.assertIsNone(self.stored(run.items[0].id))


class TestFailedPublish(EditorLinkBase):

    def test_a_failed_replace_leaves_no_link(self):
        edit = self.edit_for()
        item = self.image_item()
        response = None
        with patch("src.features.media.upload_repository.UploadRepository.replace_file", side_effect=RuntimeError("boom")):
            response = self.client.post(
                "/api/media-tools/runs",
                json={
                    "steps": SHRINK,
                    "inputs": [{"item_id": item.id}],
                    "output": {"mode": "replace", "edit": {"id": edit.id, "revision": 1}},
                },
            )
            run = self.wait_run(response.json()["data"]["run_id"])

        self.assertEqual(run.status, "failed")
        self.assertIsNone(self.stored(item.id))

    def test_a_failed_new_save_leaves_no_row(self):
        edit = self.edit_for()
        item = self.image_item()
        before = len(self.upload_rows())
        with patch("src.features.media.upload_repository.UploadRepository.create", side_effect=RuntimeError("boom")):
            response = self.client.post(
                "/api/media-tools/runs",
                json={
                    "steps": SHRINK,
                    "inputs": [{"item_id": item.id}],
                    "output": {"mode": "new", "edit": {"id": edit.id, "revision": 1}},
                },
            )
            run = self.wait_run(response.json()["data"]["run_id"])

        self.assertEqual(run.status, "failed")
        self.assertEqual(len(self.upload_rows()), before)
