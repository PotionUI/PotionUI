import asyncio
import json
from unittest.mock import MagicMock, patch

from src.features.kits import operations
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.builtin.manage_kits_tool import ManageKitsTool
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.catalog import build_catalog
from tests.features.kits.support import KitsTestBase


def run(coro):
    return asyncio.run(coro)


class TestManageKitsTool(KitsTestBase):

    def setUp(self):
        super().setUp()
        self.tool = ManageKitsTool()

    def context(self, user_id=None, **extra):
        return ToolContext(user_id=user_id or self.user_id, kit_repository=self.repo, **extra)

    def preview(self, user_id=None, **kwargs):
        return run(self.tool.execute(self.context(user_id), **kwargs))

    def apply(self, user_id=None, **kwargs):
        return run(self.tool.execute_confirmed(self.context(user_id), **kwargs))

    def data(self, result):
        self.assertTrue(result.success, result.error)
        return json.loads(result.data)

    def make(self, name="Mara", user_id=None):
        return operations.create_kit(self.repo, user_id or self.user_id, name)

    def test_identity_and_flags(self):
        self.assertEqual(self.tool.name, "manage_kits")
        self.assertTrue(self.tool.mcp)
        self.assertTrue(self.tool.writes)
        self.assertEqual(self.tool.parameters["required"], ["action"])
        self.assertEqual(
            set(self.tool.parameters["properties"]["action"]["enum"]),
            {"list", "get", "create", "update", "duplicate", "add_item", "update_item", "reorder_items", "remove_item", "delete"},
        )

    def test_reads_need_no_approval_and_writes_do(self):
        for action in ("list", "get"):
            self.assertFalse(self.tool.approval_required({"action": action}))
        for action in ("create", "update", "duplicate", "add_item", "update_item", "reorder_items", "remove_item", "delete"):
            self.assertTrue(self.tool.approval_required({"action": action}))

    def test_unavailable_without_repository(self):
        result = run(self.tool.execute(ToolContext(user_id="u"), action="list"))
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Kits not available")

    def test_list_returns_handle_name_and_item_count(self):
        kit = self.make("Old harbour")
        operations.add_text_item(self.repo, self.user_id, kit.id, "grey stone quay")

        payload = self.data(self.preview(action="list"))

        self.assertEqual(payload["total"], 1)
        row = payload["kits"][0]
        self.assertEqual((row["handle"], row["name"], row["item_count"]), ("old_harbour", "Old harbour", 1))
        self.assertEqual(row["citation"], "@kits.old_harbour")

    def test_get_gives_items_with_the_exact_citation(self):
        kit = self.make("Mara")
        item = operations.add_text_item(self.repo, self.user_id, kit.id, "x" * 300, "bio")

        payload = self.data(self.preview(action="get", kit="mara"))["kit"]

        row = payload["items"][0]
        self.assertEqual(row["id"], item.id)
        self.assertEqual(row["citation"], "@kits.mara.bio")
        self.assertEqual(row["kind"], "text")
        self.assertTrue(row["text"].endswith("...") and len(row["text"]) < 300)

    def test_a_kit_is_found_by_id_handle_or_name(self):
        kit = self.make("Old harbour")
        for reference in (kit.id, "old_harbour", "@kits.old_harbour", "old harbour"):
            self.assertEqual(self.data(self.preview(action="get", kit=reference))["kit"]["id"], kit.id)

    def test_unknown_kit_is_an_error(self):
        result = self.preview(action="get", kit="nope")
        self.assertFalse(result.success)
        self.assertIn("nope", result.error)

    def test_write_preview_changes_nothing(self):
        result = self.preview(action="create", name="Mara")
        self.assertTrue(result.success)
        self.assertIsNotNone(result.preview)
        self.assertEqual(self.repo.list_kits(self.user_id)[1], 0)

    def test_create_update_duplicate(self):
        created = self.data(self.apply(action="create", name="Mara", label="people"))["kit"]
        self.assertEqual((created["handle"], created["label"]), ("mara", "people"))

        updated = self.data(self.apply(action="update", kit=created["id"], name="Mara Vale", handle="vale"))["kit"]
        self.assertEqual((updated["name"], updated["handle"]), ("Mara Vale", "vale"))

        copy = self.data(self.apply(action="duplicate", kit="vale"))["kit"]
        self.assertNotEqual(copy["id"], created["id"])
        self.assertEqual(self.repo.list_kits(self.user_id)[1], 2)

    def test_update_without_changes_is_refused(self):
        self.make("Mara")
        self.assertFalse(self.preview(action="update", kit="mara").success)

    def test_add_text_update_reorder_remove(self):
        self.make("Mara")
        self.apply(action="add_item", kit="mara", text="first", item_name="one")
        view = self.data(self.apply(action="add_item", kit="mara", text="second", item_name="two"))["kit"]
        one, two = view["items"][0]["id"], view["items"][1]["id"]

        edited = self.data(self.apply(action="update_item", kit="mara", item="one", item_name="uno", text="changed"))["kit"]
        self.assertEqual(edited["items"][0]["name"], "uno")
        self.assertEqual(edited["items"][0]["text"], "changed")

        reordered = self.data(self.apply(action="reorder_items", kit="mara", item_ids=[two, one]))["kit"]
        self.assertEqual([row["id"] for row in reordered["items"]], [two, one])

        removed = self.data(self.apply(action="remove_item", kit="mara", item="two"))["kit"]
        self.assertEqual([row["id"] for row in removed["items"]], [one])

    def test_add_library_items(self):
        self.make("Mara")
        upload = self.upload("a.png")

        payload = self.data(self.apply(action="add_item", kit="mara", upload_ids=[upload.id], item_name="face"))["kit"]

        self.assertEqual(payload["items"][0]["name"], "face")
        self.assertEqual(payload["items"][0]["kind"], "image")

    def test_add_history_items_copies_through_the_library(self):
        self.make("Mara")
        upload = self.upload("h.png")
        context = self.context(library_collaborators=object())
        with patch(
            "src.features.llm.tools.builtin.manage_kits_tool.library_operations.copy_generation_file",
            return_value=MagicMock(id=upload.id),
        ) as copy, patch.object(self.repo, "owned_generation_files", return_value={"file-1": "image"}):
            result = run(self.tool.execute_confirmed(
                context, action="add_item", kit="mara", generation_file_ids=["file-1"],
            ))

        payload = self.data(result)
        copy.assert_called_once()
        self.assertEqual(len(payload["kit"]["items"]), 1)

    def test_add_item_needs_exactly_one_source(self):
        self.make("Mara")
        self.assertFalse(self.preview(action="add_item", kit="mara").success)
        self.assertFalse(self.preview(action="add_item", kit="mara", text="a", upload_ids=["u"]).success)

    def test_delete_is_gated_and_warns(self):
        kit = self.make("Mara")

        result = self.preview(action="delete", kit="mara")

        self.assertTrue(self.tool.approval_required({"action": "delete"}))
        self.assertIn("cannot be undone", result.preview.note)
        self.assertIsNotNone(self.repo.get(kit.id, self.user_id))

        self.assertTrue(self.data(self.apply(action="delete", kit="mara"))["success"])
        self.assertIsNone(self.repo.get(kit.id, self.user_id))

    def test_another_users_kit_is_invisible_and_untouchable(self):
        theirs = self.make("Secret", user_id=self.other_user_id)

        self.assertEqual(self.data(self.preview(action="list"))["total"], 0)
        for reference in (theirs.id, "secret"):
            self.assertFalse(self.preview(action="get", kit=reference).success)
            self.assertFalse(self.apply(action="delete", kit=reference).success)
            self.assertFalse(self.apply(action="update", kit=reference, name="Mine").success)
            self.assertFalse(self.apply(action="add_item", kit=reference, text="x").success)
        self.assertEqual(self.repo.get(theirs.id, self.other_user_id).name, "Secret")

    def test_another_users_upload_cannot_be_added(self):
        self.make("Mara")
        foreign = self.upload("f.png", user_id=self.other_user_id)
        self.assertFalse(self.apply(action="add_item", kit="mara", upload_ids=[foreign.id]).success)

    def test_the_tool_is_in_the_mcp_catalogue_as_a_write(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        entry = next(entry for entry in build_catalog(registry) if entry.name == "manage_kits")
        self.assertTrue(entry.writes)
