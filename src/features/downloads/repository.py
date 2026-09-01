"""Repository for managing download records in the database."""
from typing import List, Optional, Dict, Tuple
from datetime import datetime
import json

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

from src.features.downloads.models import Download, DownloadStatus, DownloadType


class DownloadRepository:
    """Repository for download CRUD operations"""

    def create(self, download: Download) -> Download:
        """Create a new download entry"""
        if not download.id:
            download.id = generate_ulid()

        if not download.created_at:
            download.created_at = datetime.now()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO downloads (
                    id, type, url, destination_path, filename, status, progress,
                    total_bytes, downloaded_bytes, speed_bytes_per_sec, error_message,
                    provider_id, tags, checksum_sha256, retry_count, group_id,
                    repo_id, revision, created_at, started_at, completed_at, created_by,
                    destination_backend_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                download.id,
                download.type.value,
                download.url,
                download.destination_path,
                download.filename,
                download.status.value,
                download.progress,
                download.total_bytes,
                download.downloaded_bytes,
                download.speed_bytes_per_sec,
                download.error_message,
                download.provider_id,
                json.dumps(download.tags) if download.tags else None,
                download.checksum_sha256,
                download.retry_count,
                download.group_id,
                download.repo_id,
                download.revision,
                download.created_at.isoformat() if download.created_at else None,
                download.started_at.isoformat() if download.started_at else None,
                download.completed_at.isoformat() if download.completed_at else None,
                download.created_by,
                download.destination_backend_id
            ))

        return self.get_by_id(download.id)

    def get_by_id(self, download_id: str) -> Optional[Download]:
        """Get download by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM downloads WHERE id = ?", (download_id,))
            row = cursor.fetchone()
            return Download.from_row(row) if row else None

    def get_all(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        status: Optional[DownloadStatus] = None,
        download_type: Optional[DownloadType] = None,
        created_by: Optional[str] = None,
        top_level_only: bool = False
    ) -> List[Download]:
        """Get all downloads with optional filtering.

        `top_level_only` hides grouped children (rows with a `group_id`) so a
        grouped job reads as one logical history entry.
        """
        query = "SELECT * FROM downloads"
        params = []
        where_clauses = []

        if status:
            where_clauses.append("status = ?")
            params.append(status.value)

        if download_type:
            where_clauses.append("type = ?")
            params.append(download_type.value)

        if created_by:
            where_clauses.append("created_by = ?")
            params.append(created_by)

        if top_level_only:
            where_clauses.append("group_id IS NULL")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY created_at DESC"

        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [Download.from_row(row) for row in cursor.fetchall()]

    def get_pending(self, limit: Optional[int] = None) -> List[Download]:
        """Get pending downloads (ready to be processed)"""
        return self.get_all(limit=limit, status=DownloadStatus.PENDING)

    def get_active(self) -> List[Download]:
        """Get currently downloading downloads"""
        return self.get_all(status=DownloadStatus.DOWNLOADING)

    def get_paused(self) -> List[Download]:
        """Get paused downloads"""
        return self.get_all(status=DownloadStatus.PAUSED)

    def get_completed(self, limit: Optional[int] = 50) -> List[Download]:
        """Get completed downloads"""
        return self.get_all(limit=limit, status=DownloadStatus.COMPLETED)

    def get_failed(self, limit: Optional[int] = 50) -> List[Download]:
        """Get failed downloads"""
        return self.get_all(limit=limit, status=DownloadStatus.FAILED)

    def find_active_by_repo_id(self, repo_id: str) -> Optional[Download]:
        """The most recent in-flight top-level `hf_repo` download for `repo_id`
        (pending/downloading/paused), or None.

        Lets a caller reconstruct "a fetch for this asset is already running"
        from the backend alone - no page-local `downloadId -> asset` map
        required, so the state survives a reload/reconnect. Only top-level
        rows (`group_id IS NULL`) are considered; per-file children never
        carry `repo_id` themselves.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM downloads
                WHERE repo_id = ? AND group_id IS NULL
                  AND status IN ('pending', 'downloading', 'paused')
                ORDER BY created_at DESC LIMIT 1
                """,
                (repo_id,)
            )
            row = cursor.fetchone()
            return Download.from_row(row) if row else None

    def get_children(self, group_id: str) -> List[Download]:
        """Get the child downloads of a grouped download, oldest first."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM downloads WHERE group_id = ? ORDER BY created_at ASC, id ASC",
                (group_id,)
            )
            return [Download.from_row(row) for row in cursor.fetchall()]

    def update(self, download: Download) -> bool:
        """Update existing download"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE downloads
                SET type = ?, url = ?, destination_path = ?, filename = ?, status = ?,
                    progress = ?, total_bytes = ?, downloaded_bytes = ?, speed_bytes_per_sec = ?,
                    error_message = ?, provider_id = ?, tags = ?, checksum_sha256 = ?,
                    retry_count = ?, group_id = ?, repo_id = ?, revision = ?,
                    started_at = ?, completed_at = ?
                WHERE id = ?
            """, (
                download.type.value,
                download.url,
                download.destination_path,
                download.filename,
                download.status.value,
                download.progress,
                download.total_bytes,
                download.downloaded_bytes,
                download.speed_bytes_per_sec,
                download.error_message,
                download.provider_id,
                json.dumps(download.tags) if download.tags else None,
                download.checksum_sha256,
                download.retry_count,
                download.group_id,
                download.repo_id,
                download.revision,
                download.started_at.isoformat() if download.started_at else None,
                download.completed_at.isoformat() if download.completed_at else None,
                download.id
            ))
            return cursor.rowcount > 0

    def update_progress(
        self,
        download_id: str,
        progress: float,
        downloaded_bytes: int,
        speed_bytes_per_sec: Optional[float] = None
    ) -> bool:
        """Update download progress"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE downloads
                SET progress = ?, downloaded_bytes = ?, speed_bytes_per_sec = ?
                WHERE id = ?
            """, (progress, downloaded_bytes, speed_bytes_per_sec, download_id))
            return cursor.rowcount > 0

    def update_total_bytes(self, download_id: str, total_bytes: Optional[int]) -> bool:
        """Update a download's known total size"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE downloads SET total_bytes = ? WHERE id = ?",
                (total_bytes, download_id)
            )
            return cursor.rowcount > 0

    def update_filename(
        self,
        download_id: str,
        filename: str,
        destination_path: str
    ) -> bool:
        """Update download filename and destination path"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE downloads
                SET filename = ?, destination_path = ?
                WHERE id = ?
            """, (filename, destination_path, download_id))
            return cursor.rowcount > 0

    def update_status(
        self,
        download_id: str,
        status: DownloadStatus,
        error_message: Optional[str] = None
    ) -> bool:
        """Update download status"""
        with db.get_cursor() as cursor:
            if status == DownloadStatus.DOWNLOADING:
                cursor.execute("""
                    UPDATE downloads
                    SET status = ?, started_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status.value, download_id))
            elif status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                cursor.execute("""
                    UPDATE downloads
                    SET status = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status.value, error_message, download_id))
            else:
                cursor.execute("""
                    UPDATE downloads
                    SET status = ?, error_message = ?
                    WHERE id = ?
                """, (status.value, error_message, download_id))
            return cursor.rowcount > 0

    def reset_for_retry(self, download_id: str) -> bool:
        """Reset a terminal (failed/cancelled/completed) download back to
        pending for a re-fetch - clears bytes/progress/speed/error so a stale
        completed or cancelled row doesn't leak into the retried attempt."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE downloads
                SET status = 'pending', progress = 0.0, downloaded_bytes = 0,
                    speed_bytes_per_sec = NULL, error_message = NULL
                WHERE id = ?
            """, (download_id,))
            return cursor.rowcount > 0

    def increment_retry(self, download_id: str) -> int:
        """Increment retry count and return new count"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE downloads
                SET retry_count = retry_count + 1, status = 'pending', error_message = NULL
                WHERE id = ?
            """, (download_id,))

            cursor.execute("SELECT retry_count FROM downloads WHERE id = ?", (download_id,))
            row = cursor.fetchone()
            return row['retry_count'] if row else 0

    def refresh_group(self, group_id: str) -> Tuple[Optional[Download], bool]:
        """Recompute a grouped parent's aggregate progress/status from its children.

        Returns (parent after refresh, whether the parent's status changed).
        """
        parent = self.get_by_id(group_id)
        if parent is None:
            return None, False

        children = self.get_children(group_id)
        if not children:
            return parent, False

        statuses = {child.status for child in children}
        downloaded = sum(child.downloaded_bytes or 0 for child in children)
        if all(child.total_bytes for child in children):
            total = sum(child.total_bytes for child in children)
            progress = min(downloaded / total, 1.0) if total else 0.0
        else:
            total = None
            progress = sum(child.progress for child in children) / len(children)

        speeds = [
            child.speed_bytes_per_sec for child in children
            if child.status == DownloadStatus.DOWNLOADING and child.speed_bytes_per_sec
        ]
        speed = sum(speeds) if speeds else None

        started = any(
            child.status != DownloadStatus.PENDING or child.downloaded_bytes
            for child in children
        )
        if DownloadStatus.DOWNLOADING in statuses:
            new_status = DownloadStatus.DOWNLOADING
        elif DownloadStatus.PENDING in statuses:
            new_status = DownloadStatus.DOWNLOADING if started else DownloadStatus.PENDING
        elif DownloadStatus.FAILED in statuses:
            new_status = DownloadStatus.FAILED
        elif DownloadStatus.PAUSED in statuses:
            new_status = DownloadStatus.PAUSED
        elif DownloadStatus.CANCELLED in statuses:
            new_status = DownloadStatus.CANCELLED
        else:
            new_status = DownloadStatus.COMPLETED
            progress = 1.0

        error = None
        if new_status == DownloadStatus.FAILED:
            errors = [c.error_message for c in children if c.error_message]
            error = errors[0] if errors else None

        status_changed = new_status != parent.status

        self.update_progress(group_id, progress, downloaded, speed)
        self.update_total_bytes(group_id, total)
        if status_changed:
            self.update_status(group_id, new_status, error)

        return self.get_by_id(group_id), status_changed

    def delete(self, download_id: str) -> bool:
        """Delete a download record (and any grouped children)."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM downloads WHERE id = ? OR group_id = ?",
                (download_id, download_id)
            )
            return cursor.rowcount > 0

    def delete_completed(self) -> int:
        """Delete all completed top-level downloads (with their children)."""
        return self._delete_top_level_by_status(DownloadStatus.COMPLETED)

    def delete_cancelled(self) -> int:
        """Delete all cancelled top-level downloads (with their children)."""
        return self._delete_top_level_by_status(DownloadStatus.CANCELLED)

    def _delete_top_level_by_status(self, status: DownloadStatus) -> int:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM downloads WHERE group_id IN (
                    SELECT id FROM downloads WHERE status = ? AND group_id IS NULL
                )
                """,
                (status.value,)
            )
            cursor.execute(
                "DELETE FROM downloads WHERE status = ? AND group_id IS NULL",
                (status.value,)
            )
            return cursor.rowcount

    def count_by_status(self, top_level_only: bool = False) -> Dict[str, int]:
        """Count downloads by status"""
        query = "SELECT status, COUNT(*) as count FROM downloads"
        if top_level_only:
            query += " WHERE group_id IS NULL"
        query += " GROUP BY status"
        with db.get_cursor() as cursor:
            cursor.execute(query)
            return {row['status']: row['count'] for row in cursor.fetchall()}

    def count_total(self, top_level_only: bool = False) -> int:
        """Count total downloads"""
        query = "SELECT COUNT(*) as count FROM downloads"
        if top_level_only:
            query += " WHERE group_id IS NULL"
        with db.get_cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchone()['count']
