import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.downloads.models import Download, DownloadType, DownloadStatus, DownloadSettings
from src.features.downloads.repository import DownloadRepository


class TestDownloadRepository(PersistenceTestBase):
    """Test cases for DownloadRepository"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        super().setUp()
        self.repository = DownloadRepository()

        # Create downloads table if not exists
        self._create_downloads_table()

    def _create_downloads_table(self):
        """Create downloads table for testing"""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'model',
                    url TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL DEFAULT 0.0,
                    total_bytes INTEGER,
                    downloaded_bytes INTEGER DEFAULT 0,
                    speed_bytes_per_sec REAL,
                    error_message TEXT,
                    provider_id TEXT,
                    tags TEXT,
                    checksum_sha256 TEXT,
                    retry_count INTEGER DEFAULT 0,
                    group_id TEXT,
                    repo_id TEXT,
                    revision TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_by TEXT,
                    CHECK (status IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'cancelled'))
                )
            """)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_type ON downloads(type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_created_by ON downloads(created_by)")

            # Create download_settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _create_test_download(self, download_id: str = "test-dl-1",
                               status: str = "pending",
                               download_type: str = "model") -> Download:
        """Create a test download record"""
        download = Download(
            id=download_id,
            type=DownloadType(download_type),
            url="https://example.com/model.safetensors",
            destination_path="/models/model.safetensors",
            filename="model.safetensors",
            status=DownloadStatus(status),
            progress=0.0,
            total_bytes=1000000,
            downloaded_bytes=0,
            created_at=datetime.now()
        )
        return self.repository.create(download)

    # ========== Create Tests ==========

    def test_create_download(self):
        """Test creating a download record"""
        download = Download(
            type=DownloadType.MODEL,
            url="https://example.com/model.safetensors",
            destination_path="/models/model.safetensors",
            filename="model.safetensors",
            status=DownloadStatus.PENDING
        )

        result = self.repository.create(download)

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.id)
        self.assertEqual(result.filename, "model.safetensors")
        self.assertEqual(result.status, DownloadStatus.PENDING)

    def test_create_download_with_tags(self):
        """Test creating a download with tags"""
        download = Download(
            type=DownloadType.MODEL,
            url="https://example.com/model.safetensors",
            destination_path="/models/model.safetensors",
            filename="model.safetensors",
            tags=["sdxl", "checkpoint", "anime"]
        )

        result = self.repository.create(download)

        self.assertIsNotNone(result)
        self.assertEqual(result.tags, ["sdxl", "checkpoint", "anime"])

    # ========== Get Tests ==========

    def test_get_by_id(self):
        """Test getting a download by ID"""
        created = self._create_test_download("get-test-1")

        result = self.repository.get_by_id("get-test-1")

        self.assertIsNotNone(result)
        self.assertEqual(result.id, "get-test-1")
        self.assertEqual(result.filename, "model.safetensors")

    def test_get_by_id_not_found(self):
        """Test getting a non-existent download"""
        result = self.repository.get_by_id("non-existent")

        self.assertIsNone(result)

    # ========== find_active_by_repo_id Tests ==========
    # A reloading admin client reconstructs "a fetch is already running" from
    # this query alone, so it must distinguish an in-flight job from a
    # finished one and from an unrelated repo.

    def _create_hf_repo_download(self, download_id: str, repo_id: str, status: str) -> Download:
        return self.repository.create(Download(
            id=download_id,
            type=DownloadType.HF_REPO,
            url=f"https://huggingface.co/{repo_id}",
            destination_path=f"/models/{repo_id}",
            filename=repo_id,
            status=DownloadStatus(status),
            repo_id=repo_id,
            created_at=datetime.now(),
        ))

    def test_find_active_by_repo_id_returns_running_job(self):
        self._create_hf_repo_download("active-1", "BAAI/bge-small-en-v1.5", "downloading")

        result = self.repository.find_active_by_repo_id("BAAI/bge-small-en-v1.5")

        self.assertIsNotNone(result)
        self.assertEqual(result.id, "active-1")
        self.assertEqual(result.status, DownloadStatus.DOWNLOADING)

    def test_find_active_by_repo_id_ignores_completed_job(self):
        self._create_hf_repo_download("done-1", "BAAI/bge-small-en-v1.5", "completed")

        result = self.repository.find_active_by_repo_id("BAAI/bge-small-en-v1.5")

        self.assertIsNone(result)

    def test_find_active_by_repo_id_ignores_other_repo(self):
        self._create_hf_repo_download("active-2", "SmilingWolf/wd-vit-tagger-v3", "pending")

        result = self.repository.find_active_by_repo_id("BAAI/bge-small-en-v1.5")

        self.assertIsNone(result)

    def test_find_active_by_repo_id_ignores_grouped_children(self):
        # Children never carry repo_id themselves, but the group_id filter is
        # asserted explicitly here so a future schema change can't silently
        # surface a child row as if it were the top-level job.
        parent = self._create_hf_repo_download("parent-1", "BAAI/bge-small-en-v1.5", "downloading")
        self.repository.create(Download(
            id="child-1",
            type=DownloadType.MODEL,
            url="https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/model.safetensors",
            destination_path="/models/BAAI/bge-small-en-v1.5/model.safetensors",
            filename="model.safetensors",
            status=DownloadStatus.DOWNLOADING,
            repo_id="BAAI/bge-small-en-v1.5",
            group_id=parent.id,
            created_at=datetime.now(),
        ))

        result = self.repository.find_active_by_repo_id("BAAI/bge-small-en-v1.5")

        self.assertIsNotNone(result)
        self.assertEqual(result.id, "parent-1")

    def test_find_active_by_repo_id_returns_none_when_no_job_exists(self):
        result = self.repository.find_active_by_repo_id("nobody/here")

        self.assertIsNone(result)

    def test_get_all(self):
        """Test getting all downloads"""
        self._create_test_download("all-test-1")
        self._create_test_download("all-test-2")

        result = self.repository.get_all()

        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 2)

    def test_get_pending(self):
        """Test getting pending downloads"""
        self._create_test_download("pending-1", status="pending")
        self._create_test_download("downloading-1", status="downloading")

        result = self.repository.get_pending()

        self.assertIsInstance(result, list)
        for download in result:
            self.assertEqual(download.status, DownloadStatus.PENDING)

    def test_get_active(self):
        """Test getting active (downloading) downloads"""
        self._create_test_download("active-1", status="downloading")
        self._create_test_download("pending-1", status="pending")

        result = self.repository.get_active()

        self.assertIsInstance(result, list)
        for download in result:
            self.assertEqual(download.status, DownloadStatus.DOWNLOADING)

    def test_get_paused(self):
        """Test getting paused downloads"""
        self._create_test_download("paused-1", status="paused")

        result = self.repository.get_paused()

        self.assertIsInstance(result, list)
        for download in result:
            self.assertEqual(download.status, DownloadStatus.PAUSED)

    def test_get_completed(self):
        """Test getting completed downloads"""
        self._create_test_download("completed-1", status="completed")

        result = self.repository.get_completed()

        self.assertIsInstance(result, list)
        for download in result:
            self.assertEqual(download.status, DownloadStatus.COMPLETED)

    def test_get_failed(self):
        """Test getting failed downloads"""
        self._create_test_download("failed-1", status="failed")

        result = self.repository.get_failed()

        self.assertIsInstance(result, list)
        for download in result:
            self.assertEqual(download.status, DownloadStatus.FAILED)

    # ========== Update Tests ==========

    def test_update_status(self):
        """Test updating download status"""
        created = self._create_test_download("status-test-1", status="pending")

        result = self.repository.update_status("status-test-1", DownloadStatus.DOWNLOADING)

        self.assertTrue(result)
        updated = self.repository.get_by_id("status-test-1")
        self.assertEqual(updated.status, DownloadStatus.DOWNLOADING)

    def test_update_progress(self):
        """Test updating download progress"""
        created = self._create_test_download("progress-test-1", status="downloading")

        result = self.repository.update_progress(
            "progress-test-1",
            progress=0.5,
            downloaded_bytes=500000,
            speed_bytes_per_sec=1000000
        )

        self.assertTrue(result)
        updated = self.repository.get_by_id("progress-test-1")
        self.assertEqual(updated.progress, 0.5)
        self.assertEqual(updated.downloaded_bytes, 500000)

    def test_increment_retry(self):
        """Test incrementing retry count"""
        created = self._create_test_download("retry-test-1", status="failed")

        result = self.repository.increment_retry("retry-test-1")

        self.assertTrue(result)
        updated = self.repository.get_by_id("retry-test-1")
        self.assertEqual(updated.retry_count, 1)

    def test_reset_for_retry_clears_terminal_state(self):
        """A "download again" reset zeroes progress/bytes/speed/error and
        drops the row back to pending, whatever terminal status it came from."""
        created = self._create_test_download("reset-retry-1", status="completed")
        self.repository.update_progress(
            "reset-retry-1", progress=1.0, downloaded_bytes=1000000, speed_bytes_per_sec=2000000
        )
        self.repository.update_status("reset-retry-1", DownloadStatus.COMPLETED, error_message=None)

        result = self.repository.reset_for_retry("reset-retry-1")

        self.assertTrue(result)
        updated = self.repository.get_by_id("reset-retry-1")
        self.assertEqual(updated.status, DownloadStatus.PENDING)
        self.assertEqual(updated.progress, 0.0)
        self.assertEqual(updated.downloaded_bytes, 0)
        self.assertIsNone(updated.speed_bytes_per_sec)
        self.assertIsNone(updated.error_message)

    def test_set_error(self):
        """Test setting error message"""
        created = self._create_test_download("error-test-1", status="downloading")

        result = self.repository.update_status(
            "error-test-1",
            DownloadStatus.FAILED,
            error_message="Connection timeout"
        )

        self.assertTrue(result)
        updated = self.repository.get_by_id("error-test-1")
        self.assertEqual(updated.status, DownloadStatus.FAILED)
        self.assertEqual(updated.error_message, "Connection timeout")

    # ========== Delete Tests ==========

    def test_delete(self):
        """Test deleting a download record"""
        created = self._create_test_download("delete-test-1", status="completed")

        result = self.repository.delete("delete-test-1")

        self.assertTrue(result)
        deleted = self.repository.get_by_id("delete-test-1")
        self.assertIsNone(deleted)

    def test_delete_completed(self):
        """Test deleting all completed downloads"""
        self._create_test_download("completed-del-1", status="completed")
        self._create_test_download("completed-del-2", status="completed")
        self._create_test_download("pending-del-1", status="pending")

        count = self.repository.delete_completed()

        self.assertGreaterEqual(count, 2)
        # Pending should still exist
        pending = self.repository.get_by_id("pending-del-1")
        self.assertIsNotNone(pending)

class TestDownloadRepositoryEdgeCases(PersistenceTestBase):
    """Edge case tests for DownloadRepository"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        super().setUp()
        self.repository = DownloadRepository()

        # Create downloads table
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'model',
                    url TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL DEFAULT 0.0,
                    total_bytes INTEGER,
                    downloaded_bytes INTEGER DEFAULT 0,
                    speed_bytes_per_sec REAL,
                    error_message TEXT,
                    provider_id TEXT,
                    tags TEXT,
                    checksum_sha256 TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_by TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def test_update_nonexistent_download(self):
        """Test updating a download that doesn't exist"""
        result = self.repository.update_status("nonexistent", DownloadStatus.DOWNLOADING)

        # Should return False or handle gracefully
        self.assertFalse(result)

    def test_delete_nonexistent_download(self):
        """Test deleting a download that doesn't exist"""
        result = self.repository.delete("nonexistent")

        # Should return False or handle gracefully
        self.assertFalse(result)

    def test_create_download_with_special_characters(self):
        """Test creating a download with special characters in filename"""
        download = Download(
            type=DownloadType.MODEL,
            url="https://example.com/model (v2.1) [final].safetensors",
            destination_path="/models/model (v2.1) [final].safetensors",
            filename="model (v2.1) [final].safetensors"
        )

        result = self.repository.create(download)

        self.assertIsNotNone(result)
        retrieved = self.repository.get_by_id(result.id)
        self.assertEqual(retrieved.filename, "model (v2.1) [final].safetensors")


if __name__ == '__main__':
    unittest.main()


class TestDownloadGroups(PersistenceTestBase):
    """Grouped (hf_repo) parents aggregate their children."""

    def setUp(self):
        super().setUp()
        self.repository = DownloadRepository()

    def _make_group(self):
        parent = self.repository.create(Download(
            type=DownloadType.HF_REPO,
            url="https://huggingface.co/org/tiny",
            destination_path="/models/org--tiny",
            filename="org/tiny",
            repo_id="org/tiny",
        ))
        children = [
            self.repository.create(Download(
                type=DownloadType.MODEL,
                url=f"https://huggingface.co/org/tiny/resolve/main/f{i}.bin",
                destination_path=f"/models/org--tiny/f{i}.bin",
                filename=f"f{i}.bin",
                total_bytes=100,
                group_id=parent.id,
            ))
            for i in range(2)
        ]
        return parent, children

    def test_children_hidden_from_top_level_listing(self):
        parent, children = self._make_group()
        top = self.repository.get_all(top_level_only=True)
        self.assertEqual([d.id for d in top], [parent.id])
        self.assertEqual(len(self.repository.get_children(parent.id)), 2)
        self.assertEqual(self.repository.count_total(top_level_only=True), 1)

    def test_refresh_group_aggregates_progress(self):
        parent, children = self._make_group()
        self.repository.update_status(children[0].id, DownloadStatus.COMPLETED)
        self.repository.update_progress(children[0].id, 1.0, 100)
        self.repository.update_status(children[1].id, DownloadStatus.DOWNLOADING)
        self.repository.update_progress(children[1].id, 0.5, 50, 1024.0)

        refreshed, status_changed = self.repository.refresh_group(parent.id)

        self.assertTrue(status_changed)
        self.assertEqual(refreshed.status, DownloadStatus.DOWNLOADING)
        self.assertEqual(refreshed.downloaded_bytes, 150)
        self.assertEqual(refreshed.total_bytes, 200)
        self.assertAlmostEqual(refreshed.progress, 0.75)
        self.assertAlmostEqual(refreshed.speed_bytes_per_sec, 1024.0)

    def test_refresh_group_completes_when_all_children_complete(self):
        parent, children = self._make_group()
        for child in children:
            self.repository.update_status(child.id, DownloadStatus.COMPLETED)
            self.repository.update_progress(child.id, 1.0, 100)

        refreshed, status_changed = self.repository.refresh_group(parent.id)

        self.assertTrue(status_changed)
        self.assertEqual(refreshed.status, DownloadStatus.COMPLETED)
        self.assertAlmostEqual(refreshed.progress, 1.0)

    def test_refresh_group_fails_when_a_child_fails_and_none_active(self):
        parent, children = self._make_group()
        self.repository.update_status(children[0].id, DownloadStatus.COMPLETED)
        self.repository.update_status(children[1].id, DownloadStatus.FAILED, "boom")

        refreshed, _ = self.repository.refresh_group(parent.id)

        self.assertEqual(refreshed.status, DownloadStatus.FAILED)
        self.assertEqual(refreshed.error_message, "boom")

    def test_delete_cascades_to_children(self):
        parent, children = self._make_group()
        self.repository.delete(parent.id)
        self.assertIsNone(self.repository.get_by_id(parent.id))
        for child in children:
            self.assertIsNone(self.repository.get_by_id(child.id))

    def test_delete_completed_removes_group_with_children(self):
        parent, children = self._make_group()
        for child in children:
            self.repository.update_status(child.id, DownloadStatus.COMPLETED)
        self.repository.refresh_group(parent.id)

        self.repository.delete_completed()

        self.assertIsNone(self.repository.get_by_id(parent.id))
        for child in children:
            self.assertIsNone(self.repository.get_by_id(child.id))
