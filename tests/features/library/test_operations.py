"""Tests for src.features.library.operations.

Everything here runs against a real migrated scratch database and a real
temporary storage tree - real `uploads` rows, real bytes on disk, the real
`FileStore`/`FilePathResolver` pair. The two claims that matter (a copy is
independent of the generation it came from, and a page costs a constant number
of queries) are only claims about that machinery; a mocked repository would
assert nothing but its own configuration.
"""

import dataclasses
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from tests.fixtures.persistence_base import PersistenceTestBase
from tests.fixtures.query_counter import CountingDb
from src.features.generation.file_repository import FileRepository
from src.features.generation.records import File
from src.features.library import operations
from src.features.library.collaborators import LibraryCollaborators
from src.features.library.repository import LibraryRepository
from src.features.media.file_resolver import FilePathResolver
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.repository import TagRepository
from src.platform.filesystem import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.util.ids import generate_ulid



class LibraryTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.storage_dir = Path(self.temp_dir) / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.upload_repo = UploadRepository()
        self.tag_repo = TagRepository()
        self.file_repo = FileRepository()
        self.file_store = FileStore(base_storage_dir=str(self.storage_dir))
        self.file_resolver = FilePathResolver(_StorageDirSettings(self.storage_dir))
        self.storage_driver = LocalFileStorageDriver(str(self.storage_dir))

        self.collaborators = LibraryCollaborators(
            repository=LibraryRepository(),
            upload_repository=self.upload_repo,
            tag_repository=self.tag_repo,
            file_repository=self.file_repo,
            file_resolver=self.file_resolver,
            file_store=self.file_store,
            storage_driver=self.storage_driver,
        )

        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def tearDown(self):
        # PersistenceTestBase removes its temp dir with os.rmdir, which fails if
        # anything this test wrote is still in it.
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

    def _upload(self, filename="a.png", user_id=None, media_type="image", original_filename="cat.png"):
        """An upload row with matching bytes on disk, as the upload flow leaves it."""
        owner = user_id or self.user_id
        uploads_dir = self.file_resolver.get_uploads_directory(owner)
        (uploads_dir / filename).write_bytes(b"upload-bytes")
        return self.upload_repo.create(Upload(
            user_id=owner,
            filename=filename,
            original_filename=original_filename,
            media_type=media_type,
            mime_type="image/png",
            file_size=len(b"upload-bytes"),
        ))

    def _generated_file(self, user_id=None, content=b"generated-pixels", suffix=".png"):
        """A generation, one file row, and the file's bytes under storage/generations."""
        owner = user_id or self.user_id
        generation_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, user_id, status) VALUES (?, ?, ?, ?, ?)",
                (generation_id, "test_preset", '{"prompt": "a cat"}', owner, "completed")
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
        return generation_id, file_record, on_disk

    def _tag(self, name, user_id=None, tag_type="UPLOAD"):
        return self.tag_repo.create_tag(name, type=tag_type, user_id=user_id or self.user_id)


class _NoLocalPathDriver(LocalFileStorageDriver):
    """Same storage as `LocalFileStorageDriver`, but reports no local file -
    the one behavioural difference `S3FileStorageDriver` callers must handle."""

    def local_path(self, key):
        return None


class _StorageDirSettings:
    """Minimal settings stand-in: the storage root is all FilePathResolver reads."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = str(storage_dir)

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._storage_dir


class TestLibraryListing(LibraryTestBase):

    def test_list_includes_tags_per_item(self):
        item = self._upload(filename="tagged.png")
        tag = self._tag("cats")
        operations.set_tags(self.collaborators, item.id, [tag.id], self.user_id)

        result = operations.list_items(self.collaborators, self.user_id)

        self.assertEqual(len(result.items), 1)
        self.assertEqual([t['name'] for t in result.items[0].tags], ["cats"])
        self.assertEqual(result.items[0].url, f"/api/media/uploads/{item.filename}")

    def test_list_never_leaks_another_users_items(self):
        self._upload(filename="mine.png")
        self._upload(filename="theirs.png", user_id=self.other_user_id)

        result = operations.list_items(self.collaborators, self.user_id)

        self.assertEqual([i.filename for i in result.items], ["mine.png"])
        self.assertEqual(result.total, 1)

    def test_list_rejects_unknown_media_type(self):
        with self.assertRaises(ValueError):
            operations.list_items(self.collaborators, self.user_id, media_type="mesh")

    def test_list_rejects_a_tag_the_user_does_not_own(self):
        theirs = self._tag("private", user_id=self.other_user_id)

        with self.assertRaises(ValueError):
            operations.list_items(self.collaborators, self.user_id, tag_ids=[theirs.id])

    def test_limit_is_clamped(self):
        self._upload(filename="a.png")

        result = operations.list_items(self.collaborators, self.user_id, limit=10_000)

        self.assertEqual(result.limit, 200)

    def test_list_query_count_is_constant_regardless_of_page_size(self):
        """The N+1 guard: a 30-row page must cost what a 3-row page costs.

        Fetching tags (or files, or anything else) per row is the regression
        this pins - it is invisible in behaviour and only shows up as a slow
        page under real data.
        """
        counting = CountingDb(self.db)
        counting_patcher = patch("src.platform.database.database.db", counting)
        counting_patcher.start()
        self.addCleanup(counting_patcher.stop)

        for i in range(3):
            item = self._upload(filename=f"small_{i}.png")
            self.tag_repo.set_upload_tags(item.id, [self._tag(f"tag_small_{i}").id])

        counting.statements.clear()
        operations.list_items(self.collaborators, self.user_id, limit=100)
        small_page = list(counting.statements)

        for i in range(27):
            item = self._upload(filename=f"big_{i}.png")
            self.tag_repo.set_upload_tags(item.id, [self._tag(f"tag_big_{i}").id])

        counting.statements.clear()
        result = operations.list_items(self.collaborators, self.user_id, limit=100)
        big_page = list(counting.statements)

        self.assertEqual(len(result.items), 30)
        self.assertEqual(
            len(big_page), len(small_page),
            f"query count grew with page size: {len(small_page)} -> {len(big_page)}"
        )
        # Page, count, batched tags - and nothing else.
        self.assertEqual(len(big_page), 3, f"expected 3 statements, got: {big_page}")


class TestLibraryCuration(LibraryTestBase):

    def test_set_tags_replaces(self):
        item = self._upload()
        first = self._tag("first")
        second = self._tag("second")

        operations.set_tags(self.collaborators, item.id, [first.id], self.user_id)
        tags = operations.set_tags(self.collaborators, item.id, [second.id], self.user_id)

        self.assertEqual([t['name'] for t in tags], ["second"])

    def test_set_tags_rejects_a_generation_tag(self):
        """UPLOAD tags only - a GENERATION tag on a library item would put a
        history tag's usage count and facets out of step with its own page."""
        item = self._upload()
        generation_tag = self._tag("history", tag_type="GENERATION")

        with self.assertRaises(ValueError):
            operations.set_tags(self.collaborators, item.id, [generation_tag.id], self.user_id)

    def test_set_tags_on_another_users_item_is_not_found(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)
        tag = self._tag("mine")

        with self.assertRaises(ValueError):
            operations.set_tags(self.collaborators, theirs.id, [tag.id], self.user_id)

    def test_get_item_scoped_to_owner(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        with self.assertRaises(ValueError):
            operations.get_item(self.collaborators, theirs.id, self.user_id)

    def test_facets_count_own_items_only(self):
        self._upload(filename="a.png", media_type="image")
        self._upload(filename="b.mp4", media_type="video")
        self._upload(filename="theirs.png", user_id=self.other_user_id)

        self.assertEqual(
            operations.get_facets(self.collaborators, self.user_id).media_types,
            {"image": 1, "video": 1},
        )


class TestLibraryDelete(LibraryTestBase):

    def test_delete_removes_row_and_file(self):
        item = self._upload(filename="doomed.png")
        on_disk = self.file_resolver.resolve_upload_file("doomed.png", self.user_id)
        self.assertTrue(on_disk.exists())

        operations.delete_item(self.collaborators, item.id, self.user_id)

        self.assertFalse(on_disk.exists())
        self.assertIsNone(self.upload_repo.get_by_id(item.id, self.user_id))

    def test_delete_cascades_tags_and_collection_memberships(self):
        item = self._upload(filename="doomed.png")
        tag = self._tag("cats")
        operations.set_tags(self.collaborators, item.id, [tag.id], self.user_id)
        collection_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collections (id, name, user_id) VALUES (?, ?, ?)",
                (collection_id, "Folder", self.user_id)
            )
            cursor.execute(
                "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)",
                (collection_id, item.id)
            )

        operations.delete_item(self.collaborators, item.id, self.user_id)

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) c FROM upload_tags WHERE upload_id = ?", (item.id,))
            self.assertEqual(cursor.fetchone()['c'], 0)
            cursor.execute("SELECT COUNT(*) c FROM collection_uploads WHERE upload_id = ?", (item.id,))
            self.assertEqual(cursor.fetchone()['c'], 0)

    def test_delete_of_another_users_item_is_not_found_and_leaves_it_alone(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        with self.assertRaises(ValueError):
            operations.delete_item(self.collaborators, theirs.id, self.user_id)

        self.assertIsNotNone(self.upload_repo.get_by_id(theirs.id, self.other_user_id))
        self.assertTrue(self.file_resolver.resolve_upload_file("theirs.png", self.other_user_id).exists())


class TestCopyFromGeneration(LibraryTestBase):

    def test_copy_creates_an_independent_resource(self):
        _, file_record, source = self._generated_file(content=b"generated-pixels")

        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        # New row, new name, new bytes on disk under uploads/.
        self.assertNotEqual(item.filename, source.name)
        copied = self.file_resolver.resolve_upload_file(item.filename, self.user_id)
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), b"generated-pixels")
        self.assertNotEqual(copied.resolve(), source.resolve())
        # Media facts a re-upload would have probed carry over; nothing else does.
        self.assertEqual((item.width, item.height), (1024, 768))
        self.assertEqual(item.media_type, "image")

    def test_copy_carries_no_generation_metadata_or_back_reference(self):
        generation_id, file_record, _ = self._generated_file()

        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        # An uploads row has no column that could hold a generation reference;
        # assert that nothing smuggled one into the text fields either.
        row = self.upload_repo.get_by_id(item.id, self.user_id)
        stored = " ".join(str(v) for v in (row.filename, row.original_filename, row.mime_type))
        self.assertNotIn(generation_id, stored)
        self.assertNotIn(file_record.id, stored)
        self.assertNotIn("preset", stored.lower())

    def test_copy_is_indistinguishable_from_a_direct_upload_in_listings(self):
        _, file_record, _ = self._generated_file()
        self._upload(filename="direct.png")

        operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)
        result = operations.list_items(self.collaborators, self.user_id)

        self.assertEqual(len(result.items), 2)
        # Same shape, same serving route - the copy is just another library item.
        for item in result.items:
            self.assertTrue(item.url.startswith("/api/media/uploads/"))
            self.assertEqual(item.media_type, "image")

    def test_copy_survives_deletion_of_the_generation(self):
        generation_id, file_record, source = self._generated_file(content=b"generated-pixels")
        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        # Delete the generation the way GenerationHistoryArchive.delete does:
        # wipe its directory, then delete the row (generation_files cascades).
        import shutil
        shutil.rmtree(source.parent)
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (generation_id,))

        self.assertFalse(source.exists())
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) c FROM generation_files WHERE generation_id = ?", (generation_id,)
            )
            self.assertEqual(cursor.fetchone()['c'], 0)

        still_there = self.upload_repo.get_by_id(item.id, self.user_id)
        self.assertIsNotNone(still_there)
        copied = self.file_resolver.resolve_upload_file(item.filename, self.user_id)
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), b"generated-pixels")

    def test_copy_of_another_users_file_is_not_found(self):
        _, file_record, _ = self._generated_file(user_id=self.other_user_id)

        with self.assertRaises(ValueError):
            operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        self.assertEqual(operations.list_items(self.collaborators, self.user_id).total, 0)

    def test_copy_of_unknown_file_is_not_found(self):
        with self.assertRaises(ValueError):
            operations.copy_generation_file(self.collaborators, "does-not-exist", self.user_id)

    def test_copy_rejects_a_file_type_the_library_does_not_hold(self):
        _, file_record, _ = self._generated_file()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE files SET file_type = 'MESH' WHERE id = ?", (file_record.id,))

        with self.assertRaises(ValueError):
            operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

    def test_copy_carries_over_the_source_thumbnails(self):
        """The source generation file already paid for its thumbnails - the
        copy carries the bytes over rather than regenerating them."""
        _, file_record, source = self._generated_file(content=b"generated-pixels")
        thumb_dir = source.parent / "thumbnails"
        thumb_dir.mkdir()
        (thumb_dir / "0_small.webp").write_bytes(b"small-thumb-bytes")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE files SET thumbnail_small = ? WHERE id = ?",
                ("thumbnails/0_small.webp", file_record.id),
            )

        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        self.assertIsNotNone(item.thumbnail_small)
        self.assertIsNone(item.thumbnail_medium)
        row = self.upload_repo.get_by_id(item.id, self.user_id)
        thumb_path = self.file_resolver.get_uploads_directory(self.user_id) / row.thumbnail_small
        self.assertTrue(thumb_path.exists())
        self.assertEqual(thumb_path.read_bytes(), b"small-thumb-bytes")

    def test_copy_without_source_thumbnails_leaves_them_unset(self):
        _, file_record, _ = self._generated_file()

        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        self.assertIsNone(item.thumbnail_small)
        self.assertIsNone(item.thumbnail_medium)
        self.assertIsNone(item.thumbnail_large)

    def test_copy_refuses_a_file_path_outside_the_storage_root(self):
        """`files.file_path` is data. A row pointing outside the storage tree
        must not become a readable library resource."""
        outside = Path(self.temp_dir) / "outside-secret.png"
        outside.write_bytes(b"not yours")
        file_record = self.file_repo.create(File(
            file_path="../outside-secret.png",
            file_type="IMAGE",
            user_id=self.user_id,
            mime_type="image/png",
            file_size=9,
        ))

        with self.assertRaises(ValueError):
            operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        self.assertEqual(operations.list_items(self.collaborators, self.user_id).total, 0)


class TestStorageDriverBypassClosed(LibraryTestBase):
    """With a driver that has no local filesystem shortcut (S3-like), the
    library must write and delete through the driver only - never a raw
    `shutil`/`Path` call that would land on (or look at) local disk while a
    direct upload lands in a bucket."""

    def setUp(self):
        super().setUp()
        # The driver's own storage lives OUTSIDE the FilePathResolver-owned
        # `self.storage_dir` tree, so a fallback to a raw filesystem path
        # would miss it entirely - and any raw write would show up in the
        # tree this driver never touches.
        self.driver_root = Path(self.temp_dir) / "bucket"
        self.driver_root.mkdir(parents=True, exist_ok=True)
        self.storage_driver = _NoLocalPathDriver(str(self.driver_root))
        # `LibraryCollaborators` is frozen - swap the one field via `replace`
        # rather than mutating the bundle in place.
        self.collaborators = dataclasses.replace(self.collaborators, storage_driver=self.storage_driver)

    def _raw_uploads_dir_contents(self):
        uploads_dir = self.storage_dir / "uploads"
        if not uploads_dir.exists():
            return []
        return sorted(p.name for p in uploads_dir.iterdir())

    def test_copy_generation_file_publishes_through_the_driver_only(self):
        _, file_record, source = self._generated_file(content=b"generated-pixels")

        item = operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

        key = f"uploads/{item.filename}"
        self.assertTrue(self.storage_driver.exists(key))
        self.assertEqual(self.storage_driver.get_bytes(key), b"generated-pixels")
        # The bug this closes wrote here with `shutil.copyfile` regardless of
        # which driver was configured.
        self.assertEqual(self._raw_uploads_dir_contents(), [])

    def test_delete_item_deletes_through_the_driver_only(self):
        upload = self.upload_repo.create(Upload(
            user_id=self.user_id,
            filename="doomed.png",
            original_filename="doomed.png",
            media_type="image",
            mime_type="image/png",
            file_size=5,
        ))
        self.storage_driver.put_bytes("uploads/doomed.png", b"bytes")

        operations.delete_item(self.collaborators, upload.id, self.user_id)

        self.assertFalse(self.storage_driver.exists("uploads/doomed.png"))
        self.assertIsNone(self.upload_repo.get_by_id(upload.id, self.user_id))


if __name__ == '__main__':
    unittest.main()
