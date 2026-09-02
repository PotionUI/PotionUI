"""`ProvisionedComputeRepository` against a real scratch SQLite DB (migrations
004 + 007), proving the `backends(id)` foreign key and every CRUD path work
against the real schema - the fakes in `test_operations.py` never touch a
real table.
"""

import importlib
from datetime import datetime, timezone

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.provisioning.repository import PROGRESS_CAP, ProvisionedComputeRepository


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
        self.assertIsNone(fetched.status_detail)
        self.assertIsNone(fetched.status_checked_at)
        self.assertEqual(fetched.progress, [])

    def test_create_stores_the_initial_status_detail(self):
        row = self.repo.create(
            provider_id="fake", handle="", profile_name="p", status="provisioning", status_detail="Starting",
        )

        self.assertEqual(self.repo.get_by_id(row.id).status_detail, "Starting")
        self.assertEqual(self.repo.get_by_id(row.id).to_dict()["progress"], [])

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

    def test_update_status_writes_detail_and_checked_at_and_clears_a_stale_detail(self):
        row = self.repo.create(provider_id="fake", handle="prof-1", profile_name="prof-1", status="running")
        checked = datetime(2026, 9, 2, 10, 30, 15, tzinfo=timezone.utc)

        self.repo.update_status(row.id, "stopped", detail="Pod pod-1 is EXITED (stopped)", checked_at=checked)
        stopped = self.repo.get_by_id(row.id)
        self.assertEqual(stopped.status_detail, "Pod pod-1 is EXITED (stopped)")
        self.assertEqual(stopped.status_checked_at, checked)
        self.assertEqual(stopped.to_dict()["status_checked_at"], "2026-09-02T10:30:15+00:00")

        self.repo.update_status(row.id, "running")
        running = self.repo.get_by_id(row.id)
        self.assertIsNone(running.status_detail)
        self.assertEqual(running.status_checked_at, checked)  # untouched when not given

    def test_update_handle_fills_handle_and_resource_ref(self):
        row = self.repo.create(provider_id="fake", handle="", profile_name="prof-1", status="provisioning")

        self.assertTrue(self.repo.update_handle(row.id, "prof-1", "pod-1"))

        fetched = self.repo.get_by_id(row.id)
        self.assertEqual(fetched.handle, "prof-1")
        self.assertEqual(fetched.resource_ref, "pod-1")

    def test_append_progress_keeps_order_mirrors_the_message_and_caps(self):
        row = self.repo.create(provider_id="fake", handle="", profile_name="prof-1", status="provisioning")

        for index in range(PROGRESS_CAP + 5):
            self.repo.append_progress(row.id, {
                "stage": "starting", "message": f"poll {index}", "percent": None, "at": "2026-09-02T10:00:00+00:00",
            })

        fetched = self.repo.get_by_id(row.id)
        self.assertEqual(len(fetched.progress), PROGRESS_CAP)
        self.assertEqual(fetched.progress[0]["message"], "poll 5")  # the oldest five fell off
        self.assertEqual(fetched.progress[-1]["message"], f"poll {PROGRESS_CAP + 4}")
        self.assertEqual(fetched.status_detail, f"poll {PROGRESS_CAP + 4}")

    def test_append_progress_missing_row_returns_false(self):
        self.assertFalse(self.repo.append_progress("does-not-exist", {"stage": "x", "message": "y"}))

    def test_delete_removes_the_row(self):
        row = self.repo.create(provider_id="fake", handle="prof-1", profile_name="prof-1", status="running")

        deleted = self.repo.delete(row.id)

        self.assertTrue(deleted)
        self.assertIsNone(self.repo.get_by_id(row.id))
