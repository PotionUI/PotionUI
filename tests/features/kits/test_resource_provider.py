import asyncio

from src.features.kits import operations
from src.features.kits.resource_provider import KitsResourceProvider
from src.platform.resources import ResourceContext
from src.platform.resources.registry import ResourceRegistry
from tests.features.kits.support import KitsTestBase


class TestKitsProvider(KitsTestBase):

    def setUp(self):
        super().setUp()
        self.registry = ResourceRegistry()
        self.registry.register(KitsResourceProvider(self.repo), source="builtin")
        self.ctx = ResourceContext(user_id=self.user_id)
        self.kit = operations.create_kit(self.repo, self.user_id, "Mara", label="Actor")
        self.upload_a = self.upload("a.png")
        self.face = operations.add_media_item(self.repo, self.user_id, self.kit.id, self.upload_a.id, name="face")
        self.line = operations.add_text_item(self.repo, self.user_id, self.kit.id, "She whispers. " * 3, "line")

    def suggest(self, query, ctx=None):
        return asyncio.run(self.registry.suggest(query, None, ctx or self.ctx))

    def resolve(self, uri, ctx=None):
        return asyncio.run(self.registry.resolve(uri, ctx or self.ctx))

    def test_kits_appears_as_a_namespace(self):
        namespaces = {s.uri: s for s in self.suggest("")}

        self.assertEqual(namespaces["kits"].label, "Kits")
        self.assertTrue(namespaces["kits"].has_children)

    def test_the_dotted_path_lists_kits_with_a_stable_ref_and_the_readable_path(self):
        (entry,) = self.suggest("kits.")

        self.assertEqual((entry.uri, entry.label, entry.kind, entry.badge), ("kits.mara", "Mara", "kit", "Actor"))
        self.assertTrue(entry.has_children and entry.attachable)
        self.assertEqual(entry.ref, f"kit:{self.kit.id}")
        self.assertEqual(entry.description, "1 image, 1 text")

    def test_the_kit_path_lists_its_items_in_kit_order_with_kind_icons(self):
        items = self.suggest("kits.mara.")

        self.assertEqual([(i.uri, i.kind, i.icon) for i in items], [
            ("kits.mara.face", "kit_image", "image"), ("kits.mara.line", "kit_text", "type"),
        ])
        self.assertEqual(items[0].ref, f"kit:{self.kit.id}/{self.face.id}")
        self.assertFalse(items[0].has_children)

    def test_a_partial_filters_kits_and_items(self):
        self.assertEqual([s.uri for s in self.suggest("kits.zzz")], [])
        self.assertEqual([s.uri for s in self.suggest("kits.mara.fa")], ["kits.mara.face"])

    def test_a_deleted_library_file_shows_on_the_item_suggestion(self):
        self.delete_upload(self.upload_a)

        self.assertEqual(self.suggest("kits.mara.")[0].description, "File deleted")

    def test_suggestions_only_list_the_callers_kits(self):
        other = ResourceContext(user_id=self.other_user_id)

        self.assertEqual(self.suggest("kits.", other), [])
        self.assertEqual(self.suggest("kits.mara.", other), [])

    def test_resolving_a_kit_describes_every_item_for_the_model(self):
        resolved = self.resolve("kits.mara")

        self.assertEqual((resolved.kind, resolved.title, resolved.metadata["ref"]), ("kit", "Mara", f"kit:{self.kit.id}"))
        self.assertIn("- face: image, source.png", resolved.content)
        self.assertIn("- line: She whispers.", resolved.content)

    def test_resolving_an_item_carries_the_ids(self):
        resolved = self.resolve("kits.mara.face")

        self.assertEqual(resolved.kind, "kit_image")
        self.assertEqual(
            (resolved.metadata["kit_id"], resolved.metadata["item_id"], resolved.metadata["missing"]),
            (self.kit.id, self.face.id, False),
        )

    def test_stable_ids_resolve_after_a_kit_and_item_rename(self):
        operations.update_kit(self.repo, self.user_id, self.kit.id, name="Mara Voss", handle="voss")
        operations.update_item(self.repo, self.user_id, self.kit.id, self.face.id, name="portrait")

        by_id = self.resolve(f"kits.{self.kit.id}.{self.face.id}")
        by_old_path = self.resolve("kits.mara")

        self.assertEqual((by_id.kind, by_id.uri, by_id.metadata["name"]), ("kit_image", "kits.voss.portrait", "portrait"))
        self.assertEqual(by_old_path.uri, "kits.voss")

    def test_a_deleted_kit_or_item_resolves_to_missing_never_to_an_error(self):
        operations.remove_item(self.repo, self.user_id, self.kit.id, self.face.id)
        operations.delete_kit(self.repo, self.user_id, operations.create_kit(self.repo, self.user_id, "Gone").id)

        item = self.resolve(f"kits.{self.kit.id}.{self.face.id}")
        kit = self.resolve("kits.kt_00000000")

        self.assertEqual((item.kind, item.metadata), ("missing", {"missing": True}))
        self.assertEqual(kit.kind, "missing")

    def test_a_missing_library_file_is_flagged_on_the_resolved_item(self):
        self.delete_upload(self.upload_a)

        resolved = self.resolve("kits.mara.face")

        self.assertTrue(resolved.metadata["missing"])
        self.assertIn("deleted", resolved.content)

    def test_another_users_kit_resolves_to_missing(self):
        self.assertEqual(self.resolve("kits.mara", ResourceContext(user_id=self.other_user_id)).kind, "missing")
