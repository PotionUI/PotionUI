import dataclasses
import json
from unittest.mock import patch

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.library import operations
from tests.features.edits.support import STATE
from tests.features.library.test_operations import LibraryTestBase
from tests.fixtures.query_counter import CountingDb


class TestMadeInEditor(LibraryTestBase):

    def setUp(self):
        super().setUp()
        self.edits = EditRepository()
        self.manager = EditManager(self.edits, self.upload_repo)
        self.collaborators = dataclasses.replace(self.collaborators, edit_repository=self.edits)

    def edit_for(self, user_id=None):
        return self.manager.create(user_id or self.user_id, "image", None, "library", "up_source", STATE)

    def linked(self, filename, edit_id, user_id=None, continuable=True, revision=2):
        item = self._upload(filename=filename, user_id=user_id)
        link = {"id": edit_id, "tool": "image", "revision": revision, "continuable": continuable}
        self.set_provenance(item.id, json.dumps({"edit": link}))
        return item

    def set_provenance(self, upload_id, raw):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE uploads SET provenance = ? WHERE id = ?", (raw, upload_id))

    def test_get_reports_the_link_for_a_live_edit(self):
        edit = self.edit_for()
        item = self.linked("a.png", edit.id)

        got = operations.get_item(self.collaborators, item.id, self.user_id)

        self.assertEqual(got.made_in_editor.model_dump(), {
            "edit_id": edit.id, "tool": "image", "revision": 2, "available": True, "continuable": True,
        })
        self.assertIn("edit", got.provenance)

    def test_list_reports_the_link(self):
        edit = self.edit_for()
        self.linked("a.png", edit.id, continuable=False)
        self._upload(filename="plain.png")

        result = operations.list_items(self.collaborators, self.user_id)

        by_name = {i.filename: i for i in result.items}
        self.assertFalse(by_name["a.png"].made_in_editor.continuable)
        self.assertTrue(by_name["a.png"].made_in_editor.available)
        self.assertIsNone(by_name["plain.png"].made_in_editor)

    def test_a_deleted_edit_is_not_available(self):
        edit = self.edit_for()
        item = self.linked("a.png", edit.id)
        self.manager.delete(self.user_id, edit.id)

        got = operations.get_item(self.collaborators, item.id, self.user_id)

        self.assertFalse(got.made_in_editor.available)
        self.assertEqual(got.made_in_editor.edit_id, edit.id)

    def test_another_users_edit_is_not_available(self):
        foreign = self.edit_for(self.other_user_id)
        item = self.linked("a.png", foreign.id)

        got = operations.get_item(self.collaborators, item.id, self.user_id)

        self.assertFalse(got.made_in_editor.available)

    def test_malformed_links_read_as_null(self):
        cases = {
            "string.png": '{"edit": "nope"}',
            "list.png": '{"edit": [1]}',
            "noid.png": '{"edit": {"tool": "image"}}',
            "badid.png": '{"edit": {"id": 5}}',
            "empty.png": '{"edit": {"id": ""}}',
            "garbage.png": "not json",
        }
        for filename, raw in cases.items():
            item = self._upload(filename=filename)
            self.set_provenance(item.id, raw)

        result = operations.list_items(self.collaborators, self.user_id)

        self.assertEqual(len(result.items), len(cases))
        for entry in result.items:
            self.assertIsNone(entry.made_in_editor, entry.filename)

    def test_a_page_of_linked_items_makes_one_edits_query(self):
        edit = self.edit_for()
        for index in range(6):
            self.linked(f"l{index}.png", edit.id)
        counting = CountingDb(self.db)
        patcher = patch("src.platform.database.database.db", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

        counting.statements.clear()
        result = operations.list_items(self.collaborators, self.user_id)

        self.assertEqual(len(result.items), 6)
        edit_queries = [sql for sql in counting.statements if "FROM edits" in sql]
        self.assertEqual(len(edit_queries), 1, counting.statements)

    def test_without_an_edits_repository_the_link_is_unavailable(self):
        edit = self.edit_for()
        item = self.linked("a.png", edit.id)
        bare = dataclasses.replace(self.collaborators, edit_repository=None)

        got = operations.get_item(bare, item.id, self.user_id)

        self.assertFalse(got.made_in_editor.available)
