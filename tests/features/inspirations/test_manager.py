"""Tests for InspirationManager.

Runs against a real migrated scratch database and a real temporary storage
tree - real rows, real bytes on disk, the real `FileStore`/`FilePathResolver`/
`LocalFileStorageDriver` triple. The claim that matters most (a published
inspiration survives deletion of the generation it came from) is a claim
about that machinery; a mocked repository would only assert its own
configuration.
"""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.fields.builtin import register_builtin_fields
from src.features.generation.file_repository import FileRepository
from src.features.generation.parameter_repository import GenerationParameterRepository
from src.features.generation.records import File
from src.features.generation.repository import GenerationRepository
from src.features.inspirations.manager import InspirationManager
from src.features.inspirations.repository import InspirationRepository
from src.features.media.file_resolver import FilePathResolver
from src.features.media.upload_repository import UploadRepository
from src.features.notifications.manager import NotificationManager
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from src.platform.filesystem import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.util.ids import generate_ulid

import src.features.generation.file_repository as file_repository_module
import src.features.generation.parameter_repository as parameter_repository_module
import src.features.generation.repository as generation_repository_module
import src.features.inspirations.repository as inspiration_repository_module
import src.features.media.upload_repository as upload_repository_module


class _StorageDirSettings:
    """Minimal settings stand-in: the storage root is all FilePathResolver reads."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = str(storage_dir)

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._storage_dir


class _FakePresetNameResolver:
    """Duck-typed stand-in for `PresetNameResolver` - a real one scans the
    filesystem for preset.yml files, which this test has none of."""

    def __init__(self, names):
        self._names = names

    def resolve(self, preset_id, default=None):
        if not preset_id:
            return default
        return self._names.get(preset_id, default if default is not None else preset_id)


class _FakePresetTemplateLoader:
    """Duck-typed stand-in for `PresetTemplateLoader` - returns pre-built
    `PresetTemplate`s from a dict instead of scanning the filesystem."""

    def __init__(self, presets_by_id):
        self._presets_by_id = presets_by_id

    def load_preset_by_id(self, preset_id):
        return self._presets_by_id.get(preset_id)


def _preset_template(preset_id="preset-1", category="image", extra_fields=None):
    """A minimal loadable preset: one `txt2img` mode, one default form,
    declaring `prompt` (shareable) and `seed` (shareable) plus whatever
    `extra_fields` the caller adds (e.g. a non-shareable `image` field)."""
    fields = [
        FieldTemplate(type="string", name="prompt"),
        FieldTemplate(type="seed", name="seed"),
    ]
    fields.extend(extra_fields or [])
    form = FormTemplate(name="default", fields=fields, default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    return PresetTemplate(
        id=preset_id,
        name="My Cool Preset",
        version="1.0",
        path="/fake/preset/path",
        modes={"txt2img": mode},
        category=category,
    )


class InspirationManagerTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        for module in (
            file_repository_module,
            parameter_repository_module,
            generation_repository_module,
            inspiration_repository_module,
            upload_repository_module,
        ):
            module.db = self.db

        self.storage_dir = Path(self.temp_dir) / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.generation_repo = GenerationRepository()
        self.file_repo = FileRepository()
        self.parameter_repo = GenerationParameterRepository()
        self.upload_repo = UploadRepository()
        self.file_store = FileStore(base_storage_dir=str(self.storage_dir))
        self.file_resolver = FilePathResolver(_StorageDirSettings(self.storage_dir))
        self.storage_driver = LocalFileStorageDriver(str(self.storage_dir))
        self.inspiration_repository = InspirationRepository()
        self.notification_manager = Mock(spec=NotificationManager)
        self.preset_name_resolver = _FakePresetNameResolver({"preset-1": "My Cool Preset"})
        self.preset_template_loader = _FakePresetTemplateLoader({"preset-1": _preset_template()})
        self.field_type_registry = FieldTypeRegistry()
        register_builtin_fields(self.field_type_registry)

        self.manager = InspirationManager(
            inspiration_repository=self.inspiration_repository,
            generation_repository=self.generation_repo,
            generation_parameter_repository=self.parameter_repo,
            preset_name_resolver=self.preset_name_resolver,
            preset_template_loader=self.preset_template_loader,
            field_type_registry=self.field_type_registry,
            file_store=self.file_store,
            file_resolver=self.file_resolver,
            storage_driver=self.storage_driver,
            upload_repository=self.upload_repo,
            notification_manager=self.notification_manager,
        )

        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def tearDown(self):
        import shutil
        for child in Path(self.temp_dir).iterdir():
            if child == self.temp_db_path:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        super().tearDown()

    # --- fixtures ---

    def _generated_file(
        self,
        user_id=None,
        content=b"generated-pixels",
        suffix=".png",
        preset_id="preset-1",
        seed=12345,
        form_data=None,
        mode=None,
    ):
        """A generation, one final IMAGE file, its bytes on disk, and a couple
        of generation_parameters rows (mirrors what a real run leaves).

        `form_data`/`mode` default to the same `{"prompt": "a cat", "seed":
        12345}` / `txt2img` every other test in this module relies on.
        """
        owner = user_id or self.user_id
        generation_id = generate_ulid()
        form_data_json = json.dumps(form_data if form_data is not None else {"prompt": "a cat", "seed": 12345})
        with self.db.get_cursor() as cursor:
            if mode is not None:
                cursor.execute(
                    "INSERT INTO generations (id, preset_id, form_data, user_id, status, mode) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (generation_id, preset_id, form_data_json, owner, "completed", mode),
                )
            else:
                cursor.execute(
                    "INSERT INTO generations (id, preset_id, form_data, user_id, status) VALUES (?, ?, ?, ?, ?)",
                    (generation_id, preset_id, form_data_json, owner, "completed"),
                )

        generation_dir = (
            Path(self.file_store.base_storage_dir)
            / "generations"
            / datetime.now().strftime("%Y-%m-%d")
            / generation_id
        )
        generation_dir.mkdir(parents=True, exist_ok=True)
        on_disk = generation_dir / f"0{suffix}"
        on_disk.write_bytes(content)

        file_record = self.file_repo.create(File(
            file_path=self.file_store.get_relative_path(str(on_disk)),
            file_type="IMAGE",
            user_id=owner,
            mime_type="image/png",
            file_size=len(content),
            is_final=True,
            width=1024,
            height=768,
        ))
        self.file_repo.associate_with_generation(generation_id, file_record.id)

        self.parameter_repo.create_batch(generation_id, "seed", [seed])
        self.parameter_repo.create_batch(generation_id, "steps", [30])

        return generation_id, file_record, on_disk


class TestPublish(InspirationManagerTestBase):

    def test_publish_copies_files_and_embeds_snapshot(self):
        generation_id, file_record, source = self._generated_file(content=b"generated-pixels")

        insp = self.manager.publish(
            self.user_id, generation_id, [source.name], "My Shot", "a description"
        )

        self.assertEqual(insp.title, "My Shot")
        self.assertEqual(insp.description, "a description")
        self.assertEqual(insp.source_generation_id, generation_id)
        self.assertEqual(insp.preset_id, "preset-1")
        self.assertEqual(insp.preset_name, "My Cool Preset")

        self.assertEqual(len(insp.media), 1)
        self.assertEqual(insp.media[0]["filename"], source.name)
        self.assertEqual(insp.media[0]["type"], "image")
        self.assertEqual(insp.media[0]["width"], 1024)

        copied = self.storage_driver.local_path(f"inspirations/{insp.id}/{source.name}")
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), b"generated-pixels")
        self.assertNotEqual(copied.resolve(), source.resolve())

        self.assertEqual(insp.params_snapshot["form_data"]["seed"], 12345)
        self.assertEqual(insp.params_snapshot["form_data"]["prompt"], "a cat")
        self.assertEqual(insp.params_snapshot["omitted_fields"], [])
        self.assertEqual(insp.params_snapshot["mode"], "txt2img")
        self.assertEqual(insp.technique, "txt2img")
        preview_names = [p["name"] for p in insp.params_snapshot["preview"]]
        self.assertIn("preset", preview_names)
        self.assertIn("seed", preview_names)

    def test_publish_rejects_a_generation_owned_by_someone_else(self):
        """The ownership check: a foreign generation_id must not publish."""
        generation_id, file_record, source = self._generated_file(user_id=self.other_user_id)

        with self.assertRaises(ValueError):
            self.manager.publish(self.user_id, generation_id, [source.name], "Stolen")

    def test_publish_rejects_a_filename_not_in_the_generation_output(self):
        generation_id, file_record, source = self._generated_file()

        with self.assertRaises(ValueError):
            self.manager.publish(self.user_id, generation_id, ["not-a-real-file.png"], "Title")

    def test_publish_requires_a_title(self):
        generation_id, file_record, source = self._generated_file()

        with self.assertRaises(ValueError):
            self.manager.publish(self.user_id, generation_id, [source.name], "   ")

    def test_publish_requires_at_least_one_filename(self):
        generation_id, file_record, source = self._generated_file()

        with self.assertRaises(ValueError):
            self.manager.publish(self.user_id, generation_id, [], "Title")


class TestAllowlistSnapshot(InspirationManagerTestBase):
    """The form_data allowlist filter: never leak by default."""

    def test_non_shareable_field_is_omitted_and_named(self):
        """`image` is a media field type - never shareable - so it must be
        dropped from the snapshot's form_data and recorded by name only."""
        self.preset_template_loader = _FakePresetTemplateLoader({
            "preset-1": _preset_template(extra_fields=[FieldTemplate(type="image", name="init_image")]),
        })
        self.manager.preset_template_loader = self.preset_template_loader

        generation_id, file_record, source = self._generated_file(
            form_data={"prompt": "a cat", "seed": 12345, "init_image": "uploads/user-1/secret.png"},
        )

        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.assertEqual(insp.params_snapshot["form_data"], {"prompt": "a cat", "seed": 12345})
        self.assertEqual(insp.params_snapshot["omitted_fields"], ["init_image"])
        self.assertNotIn("secret.png", str(insp.params_snapshot))

    def test_unknown_field_not_declared_by_the_preset_is_omitted(self):
        """A form_data key the preset's form never declared (stale/legacy
        submission) has no field type to classify - allowlist stance says
        omit, never guess it's safe."""
        generation_id, file_record, source = self._generated_file(
            form_data={"prompt": "a cat", "seed": 12345, "mystery_field": "anything"},
        )

        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.assertEqual(insp.params_snapshot["form_data"], {"prompt": "a cat", "seed": 12345})
        self.assertEqual(insp.params_snapshot["omitted_fields"], ["mystery_field"])

    def test_unresolvable_preset_omits_every_field(self):
        """No form definition to classify against at all - the fallback is
        empty form_data and every submitted key named as omitted, never
        'can't classify -> include'."""
        generation_id, file_record, source = self._generated_file(
            preset_id="does-not-exist",
            form_data={"prompt": "a cat", "seed": 12345},
        )

        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.assertEqual(insp.params_snapshot["form_data"], {})
        self.assertEqual(sorted(insp.params_snapshot["omitted_fields"]), ["prompt", "seed"])

    def test_image_input_on_an_image_preset_derives_img2img(self):
        self.preset_template_loader = _FakePresetTemplateLoader({
            "preset-1": _preset_template(
                category="image", extra_fields=[FieldTemplate(type="image", name="init_image")]
            ),
        })
        self.manager.preset_template_loader = self.preset_template_loader

        generation_id, file_record, source = self._generated_file(
            form_data={"prompt": "a cat", "seed": 12345, "init_image": "uploads/user-1/x.png"},
        )

        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.assertEqual(insp.technique, "img2img")

    def test_upscale_mode_derives_upscale_regardless_of_category(self):
        self.preset_template_loader = _FakePresetTemplateLoader({
            "preset-1": _preset_template(category="image"),
        })
        self.manager.preset_template_loader = self.preset_template_loader

        generation_id, file_record, source = self._generated_file(mode="upscale")

        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.assertEqual(insp.technique, "upscale")


class TestParamsSnapshotSurvivesGenerationDeletion(InspirationManagerTestBase):

    def test_snapshot_and_params_outlive_the_source_generation(self):
        """The whole point of publishing a snapshot: deleting the source
        generation must not take the inspiration's params down with it."""
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Snapshot Test")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (generation_id,))

        reloaded = self.inspiration_repository.get_by_id(insp.id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.params_snapshot["form_data"]["seed"], 12345)
        self.assertEqual(reloaded.preset_name, "My Cool Preset")

        copied = self.storage_driver.local_path(f"inspirations/{insp.id}/{source.name}")
        self.assertTrue(copied.exists())


class TestDelete(InspirationManagerTestBase):

    def test_delete_removes_row_and_copied_files(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Doomed")
        copied = self.storage_driver.local_path(f"inspirations/{insp.id}/{source.name}")
        self.assertTrue(copied.exists())

        self.manager.delete(insp.id, self.user_id)

        self.assertFalse(copied.exists())
        self.assertIsNone(self.inspiration_repository.get_by_id(insp.id))

    def test_delete_by_a_non_owner_is_rejected(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        with self.assertRaises(ValueError):
            self.manager.delete(insp.id, self.other_user_id)

        self.assertIsNotNone(self.inspiration_repository.get_by_id(insp.id))

    def test_delete_by_an_admin_is_allowed(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.manager.delete(insp.id, self.other_user_id, is_admin=True)

        self.assertIsNone(self.inspiration_repository.get_by_id(insp.id))


class TestComments(InspirationManagerTestBase):

    def test_comment_from_another_user_notifies_the_author(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.manager.add_comment(insp.id, self.other_user_id, "nice work")

        self.notification_manager.notify.assert_called_once()
        kwargs = self.notification_manager.notify.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user_id)
        self.assertEqual(kwargs["type"], "inspiration.comment")

    def test_comment_from_the_author_does_not_notify(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        self.manager.add_comment(insp.id, self.user_id, "self note")

        self.notification_manager.notify.assert_not_called()

    def test_delete_comment_by_author_or_admin_only(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")
        comment = self.manager.add_comment(insp.id, self.other_user_id, "hi")

        with self.assertRaises(ValueError):
            self.manager.delete_comment(comment.id, self.user_id)  # not the comment author, not admin

        self.manager.delete_comment(comment.id, self.user_id, is_admin=True)
        self.assertIsNone(self.inspiration_repository.get_comment(comment.id))

    def test_empty_comment_body_is_rejected(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        with self.assertRaises(ValueError):
            self.manager.add_comment(insp.id, self.other_user_id, "   ")


class TestSaveToLibrary(InspirationManagerTestBase):

    def test_save_to_library_copies_file_and_marks_saved(self):
        generation_id, file_record, source = self._generated_file(content=b"generated-pixels")
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")

        save_count = self.manager.save_to_library(insp.id, self.other_user_id)

        self.assertEqual(save_count, 1)
        uploads = self.upload_repo.list_for_user(self.other_user_id)
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].original_filename, source.name)
        self.assertEqual(uploads[0].media_type, "image")
        copied = self.file_resolver.get_uploads_directory(self.other_user_id) / uploads[0].filename
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), b"generated-pixels")
        self.assertTrue(self.inspiration_repository.get_by_id(insp.id, viewer_id=self.other_user_id).saved_by_me)

    def test_unsave_keeps_library_copies(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")
        self.manager.save_to_library(insp.id, self.other_user_id)

        save_count = self.manager.unsave(insp.id, self.other_user_id)

        self.assertEqual(save_count, 0)
        self.assertFalse(self.inspiration_repository.get_by_id(insp.id, viewer_id=self.other_user_id).saved_by_me)


class TestCollections(InspirationManagerTestBase):

    def test_create_add_and_remove_item(self):
        generation_id, file_record, source = self._generated_file()
        insp = self.manager.publish(self.user_id, generation_id, [source.name], "Mine")
        collection = self.manager.create_collection(self.user_id, "Favorites")

        self.manager.add_item(collection.id, self.user_id, insp.id)
        self.manager.add_item(collection.id, self.user_id, insp.id)  # idempotent

        refreshed = self.inspiration_repository.get_collection(collection.id, self.user_id)
        self.assertEqual(refreshed.item_count, 1)

        self.manager.remove_item(collection.id, self.user_id, insp.id)
        refreshed = self.inspiration_repository.get_collection(collection.id, self.user_id)
        self.assertEqual(refreshed.item_count, 0)

    def test_move_collection_rejects_a_cycle(self):
        parent = self.manager.create_collection(self.user_id, "Parent")
        child = self.manager.create_collection(self.user_id, "Child", parent.id)

        with self.assertRaises(ValueError):
            self.manager.update_collection(parent.id, self.user_id, parent_id=child.id, parent_id_set=True)

    def test_collection_ownership_is_scoped(self):
        theirs = self.manager.create_collection(self.other_user_id, "Theirs")

        with self.assertRaises(ValueError):
            self.manager.delete_collection(theirs.id, self.user_id)


if __name__ == "__main__":
    unittest.main()
