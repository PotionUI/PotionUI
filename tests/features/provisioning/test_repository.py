"""`ProvisionedComputeRepository` against a real scratch SQLite DB (migration
004), proving the `backends(id)` foreign key and every CRUD path work against
the real schema - the fakes in `test_operations.py` never touch a real table.
"""

import importlib

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.provisioning.repository import ProvisionedComputeRepository


class ProvisionedComputeRepositoryTestBase(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        importlib.import_module("src.features.provisioning.repository").db = self.db
        self.repo = ProvisionedComputeRepository()


class TestCreateAndRead(ProvisionedComputeRepositoryTestBase):
    def test_create_then_get_by_id_round_trips(self):
        row = self.repo.create(
            provider_id="fake", handle="prof-1", profile_name="prof-1",
            status="running", resource_ref="res-1", gpu_type_id="fake-gpu",
            region="eu-1", created_by="user-1",
        )

        fetched = self.repo.get_by_id(row.id)

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.provider_id, "fake")
        self.assertEqual(fetched.handle, "prof-1")
        self.assertEqual(fetched.status, "running")
        self.assertEqual(fetched.resource_ref, "res-1")
        self.assertEqual(fetched.gpu_type_id, "fake-gpu")
        self.assertEqual(fetched.region, "eu-1")
        self.assertEqual(fetched.created_by, "user-1")
        self.assertIsNone(fetched.backend_id)

    def test_get_by_id_missing_row_returns_none(self):
        self.assertIsNone(self.repo.get_by_id("does-not-exist"))

    def test_get_by_backend_id_missing_returns_none(self):
        self.assertIsNone(self.repo.get_by_backend_id("does-not-exist"))

    def test_get_by_backend_id_returns_the_linked_row(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO backends (id, name, engine) VALUES ('remote-1', 'remote', 'native')"
            )
        row = self.repo.create(
            provider_id="fake", handle="prof-1", profile_name="prof-1",
            status="running", backend_id="remote-1",
        )

        fetched = self.repo.get_by_backend_id("remote-1")

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, row.id)

    def test_list_all_orders_newest_first(self):
        first = self.repo.create(provider_id="fake", handle="a", profile_name="a", status="running")
        second = self.repo.create(provider_id="fake", handle="b", profile_name="b", status="running")

        rows = self.repo.list_all()

        self.assertEqual([r.id for r in rows], [second.id, first.id])


class TestUpdateAndDelete(ProvisionedComputeRepositoryTestBase):
    def test_update_status_changes_the_stored_status(self):
        row = self.repo.create(provider_id="fake", handle="prof-1", profile_name="prof-1", status="running")

        changed = self.repo.update_status(row.id, "stopped")

        self.assertTrue(changed)
        self.assertEqual(self.repo.get_by_id(row.id).status, "stopped")

    def test_update_status_missing_row_returns_false(self):
        self.assertFalse(self.repo.update_status("does-not-exist", "stopped"))

    def test_delete_removes_the_row(self):
        row = self.repo.create(provider_id="fake", handle="prof-1", profile_name="prof-1", status="running")

        deleted = self.repo.delete(row.id)

        self.assertTrue(deleted)
        self.assertIsNone(self.repo.get_by_id(row.id))
