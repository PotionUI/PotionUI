import unittest

from src.features.media.records import Upload
from tests.features.library.test_operations import LibraryTestBase


class TestUploadProvenance(LibraryTestBase):

    def _row(self, **overrides):
        values = dict(user_id=self.user_id, filename="p.png", media_type="image", file_size=10)
        values.update(overrides)
        return self.upload_repo.create(Upload(**values))

    def test_create_round_trips_the_provenance(self):
        mark = {"slice": {"source_item_id": "s1", "index": 2, "rect": {"x": 0.5, "y": 0.0, "w": 0.25, "h": 1.0}}}

        row = self._row(provenance=mark)

        self.assertEqual(row.provenance, mark)
        self.assertEqual(self.upload_repo.get_by_id(row.id, self.user_id).provenance, mark)

    def test_an_upload_with_none_stays_null(self):
        row = self._row()

        self.assertIsNone(self.upload_repo.get_by_id(row.id, self.user_id).provenance)

    def test_unreadable_stored_values_read_back_as_none(self):
        row = self._row()
        for stored in ("{broken", "[1, 2]"):
            with self.db.get_cursor() as cursor:
                cursor.execute("UPDATE uploads SET provenance = ? WHERE id = ?", (stored, row.id))
            self.assertIsNone(self.upload_repo.get_by_id(row.id, self.user_id).provenance)


if __name__ == "__main__":
    unittest.main()
