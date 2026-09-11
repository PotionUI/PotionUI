"""Tests for the library export path: `operations.export_zip` and the
`POST /api/library/export` route built on it.

Runs against the same real migrated scratch DB / temp storage tree as
`test_operations.py` and `test_routes.py` - real `uploads` rows, real bytes
on disk.
"""
import io
import zipfile

import pytest

from src.features.library import operations
from tests.features.library.test_operations import LibraryTestBase
from tests.features.library.test_routes import LibraryRoutesTestBase


class TestExportZipOperation(LibraryTestBase):

    def test_contains_the_items_original_filenames(self):
        a = self._upload(filename="a.png", original_filename="cat.png")
        b = self._upload(filename="b.png", original_filename="dog.png")

        zip_file, filename = operations.export_zip(self.collaborators, [a.id, b.id], self.user_id)

        assert filename == "potionui-library-export.zip"
        zf = zipfile.ZipFile(zip_file)
        assert set(zf.namelist()) == {"cat.png", "dog.png"}
        assert zf.read("cat.png") == b"upload-bytes"

    def test_dedupes_colliding_original_filenames(self):
        a = self._upload(filename="a.png", original_filename="same.png")
        b = self._upload(filename="b.png", original_filename="same.png")

        zip_file, _ = operations.export_zip(self.collaborators, [a.id, b.id], self.user_id)

        names = set(zipfile.ZipFile(zip_file).namelist())
        assert names == {"same.png", "same (2).png"}

    def test_skips_a_file_missing_on_disk(self):
        present = self._upload(filename="present.png", original_filename="present.png")
        gone = self._upload(filename="gone.png")
        (self.file_resolver.get_uploads_directory(self.user_id) / "gone.png").unlink()

        zip_file, _ = operations.export_zip(
            self.collaborators, [present.id, gone.id], self.user_id
        )

        assert zipfile.ZipFile(zip_file).namelist() == ["present.png"]

    def test_raises_when_an_item_is_not_owned(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        with pytest.raises(ValueError):
            operations.export_zip(self.collaborators, [theirs.id], self.user_id)

    def test_returns_a_seeked_closable_spooled_file(self):
        import tempfile

        item = self._upload()
        zip_file, _ = operations.export_zip(self.collaborators, [item.id], self.user_id)

        assert isinstance(zip_file, tempfile.SpooledTemporaryFile)
        assert zip_file.tell() == 0
        assert not zip_file.closed

        zip_file.close()
        assert zip_file.closed


class TestExportRoute(LibraryRoutesTestBase):

    def test_export_returns_a_zip_of_the_callers_items(self):
        item = self._upload(filename="mine.png", original_filename="mine.png")

        response = self.client.post("/api/library/export", json={"item_ids": [item.id]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(zf.namelist(), ["mine.png"])
        self.assertEqual(zf.read("mine.png"), b"upload-bytes")

    def test_export_of_another_users_item_is_404_not_403(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        response = self.client.post("/api/library/export", json={"item_ids": [theirs.id]})

        self.assertEqual(response.status_code, 404)

    def test_export_with_no_item_ids_is_refused(self):
        response = self.client.post("/api/library/export", json={"item_ids": []})

        self.assertEqual(response.status_code, 400)
