"""Driver-bypass coverage for `MediaEditor`.

With a driver double whose `local_path()` returns `None` (the shape
`S3FileStorageDriver` has), editing must stage the source through
`local_copy` and publish the result through `put_file` - never a raw
`Path`/`FilePathResolver` shortcut that would read or write local disk while
the rest of the uploads namespace lives in a bucket.
"""

import asyncio
import shutil
import unittest
from pathlib import Path

from src.features.media.editing.dto import CropOperation
from src.features.media.editing.editor import MediaEditor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.platform.filesystem.storage_driver import LocalFileStorageDriver

from tests.fixtures.persistence_base import PersistenceTestBase
from tests.features.media.editing.test_operations import make_image

import src.features.media.upload_repository as upload_repository_module


class _NoLocalPathDriver(LocalFileStorageDriver):
    """Same storage as `LocalFileStorageDriver`, but reports no local file -
    the one behavioural difference `S3FileStorageDriver` callers must handle."""

    def local_path(self, key):
        return None


class TestDriverBypassClosed(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        upload_repository_module.db = self.db
        self.upload_repo = UploadRepository()

        # The driver's own storage lives OUTSIDE any FilePathResolver-owned
        # tree, so a fallback to a raw filesystem path would miss it entirely.
        self.driver_root = Path(self.temp_dir) / "bucket"
        self.driver_root.mkdir(parents=True, exist_ok=True)
        self.storage_driver = _NoLocalPathDriver(str(self.driver_root))

        self.manager = MediaEditor(
            upload_repository=self.upload_repo,
            media_type_resolver=MediaTypeResolver(),
            storage_driver=self.storage_driver,
        )

        self.user_id = self.create_test_user()

    def tearDown(self):
        for child in Path(self.temp_dir).iterdir():
            if child == self.temp_db_path:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        super().tearDown()

    def _seed_source(self, filename="source.png", width=200, height=100):
        """An `uploads` row whose bytes exist ONLY through the driver."""
        scratch = Path(self.temp_dir) / f"scratch-{filename}"
        make_image(scratch, width, height)
        key = f"uploads/{filename}"
        size = self.storage_driver.put_file(key, scratch)
        scratch.unlink()

        return self.upload_repo.create(Upload(
            user_id=self.user_id,
            filename=filename,
            original_filename=filename,
            media_type="image",
            mime_type="image/png",
            width=width,
            height=height,
            file_size=size,
        ))

    def test_edit_reads_via_staging_and_publishes_through_the_driver(self):
        upload = self._seed_source()

        result = asyncio.run(self.manager.edit_item(
            upload.id, self.user_id,
            [CropOperation(type="crop", x=0, y=0, width=50, height=40)],
            mode="new",
        ))

        self.assertFalse(result.replaced)
        key = f"uploads/{result.item.filename}"
        self.assertTrue(self.storage_driver.exists(key))
        self.assertEqual((result.item.width, result.item.height), (50, 40))

    def test_replace_publishes_the_new_file_and_deletes_the_old_through_the_driver(self):
        upload = self._seed_source()
        old_key = f"uploads/{upload.filename}"

        result = asyncio.run(self.manager.edit_item(
            upload.id, self.user_id,
            [CropOperation(type="crop", x=0, y=0, width=50, height=40)],
            mode="replace",
        ))

        self.assertTrue(result.replaced)
        self.assertFalse(self.storage_driver.exists(old_key))
        self.assertTrue(self.storage_driver.exists(f"uploads/{result.item.filename}"))

    def test_a_resource_only_the_driver_knows_about_is_still_editable(self):
        """Proves `_resolve_owned_source` checks `storage_driver.exists`, not
        a `FilePathResolver`-built path that has no bytes behind it here."""
        upload = self._seed_source()

        # If ownership resolution fell back to a raw filesystem path, this
        # would report "not found" - there is nothing at that path to find.
        result = asyncio.run(self.manager.edit_item(
            upload.id, self.user_id,
            [CropOperation(type="crop", x=0, y=0, width=10, height=10)],
        ))

        self.assertEqual((result.item.width, result.item.height), (10, 10))


if __name__ == '__main__':
    unittest.main()
