"""Database access for system tags and the media index queue."""

import logging
from typing import Any, Dict, List, Optional

from src.platform.database import db
from src.platform.util.ids import generate_ulid

from src.features.media_index.records import MediaIndexQueueItem
from src.features.media_index.tagger import SystemTagPrediction

logger = logging.getLogger(__name__)

_QUEUE_ITEM_SELECT = """
    SELECT q.id, q.file_id, q.pass_type, q.status, q.attempts, q.last_error,
           q.created_at, q.updated_at,
           f.file_path, f.file_type, f.thumbnail_medium AS thumbnail_path,
           f.user_id AS file_user_id,
           (SELECT gf.generation_id FROM generation_files gf
            WHERE gf.file_id = q.file_id LIMIT 1) AS generation_id,
           (SELECT json_extract(g.form_data, '$.prompt') FROM generation_files gf
            JOIN generations g ON g.id = gf.generation_id
            WHERE gf.file_id = q.file_id LIMIT 1) AS prompt_text
    FROM media_index_queue q
    JOIN files f ON f.id = q.file_id
"""


class MediaIndexRepository:
    """Owns ``media_system_tags`` and ``media_index_queue`` (migration 098)."""

    # --- Queue -----------------------------------------------------------------

    def enqueue_files(self, file_ids: List[str], pass_type: str, requeue: bool = False) -> int:
        if not file_ids:
            return 0
        enqueued = 0
        with db.get_cursor() as cursor:
            for file_id in file_ids:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO media_index_queue
                        (id, file_id, pass_type, status, attempts)
                    VALUES (?, ?, ?, 'pending', 0)
                    """,
                    (generate_ulid(), file_id, pass_type),
                )
                enqueued += cursor.rowcount
                if requeue and cursor.rowcount == 0:
                    cursor.execute(
                        """
                        UPDATE media_index_queue
                        SET status = 'pending', attempts = 0, last_error = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE file_id = ? AND pass_type = ?
                          AND status IN ('done', 'failed')
                        """,
                        (file_id, pass_type),
                    )
                    enqueued += cursor.rowcount
        return enqueued

    def enqueue_generation_files(self, generation_id: str, pass_type: str) -> int:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT f.id FROM files f
                JOIN generation_files gf ON gf.file_id = f.id
                WHERE gf.generation_id = ? AND f.is_final = 1
                """,
                (generation_id,),
            )
            file_ids = [row["id"] for row in cursor.fetchall()]
        return self.enqueue_files(file_ids, pass_type)

    def enqueue_backfill(self, pass_type: str) -> int:
        """Queue every final file that has no queue row for this pass yet."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT f.id FROM files f
                WHERE f.is_final = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM media_index_queue q
                    WHERE q.file_id = f.id AND q.pass_type = ?
                  )
                """,
                (pass_type,),
            )
            file_ids = [row["id"] for row in cursor.fetchall()]
        return self.enqueue_files(file_ids, pass_type)

    def claim_batch(
        self, pass_type: str, batch_size: int, max_attempts: int
    ) -> List[MediaIndexQueueItem]:
        """Move up to ``batch_size`` pending items to processing and return them."""
        with db.get_cursor() as cursor:
            cursor.execute(
                _QUEUE_ITEM_SELECT
                + """
                WHERE q.pass_type = ? AND q.status = 'pending' AND q.attempts < ?
                ORDER BY q.created_at ASC
                LIMIT ?
                """,
                (pass_type, max_attempts, batch_size),
            )
            items = [MediaIndexQueueItem.from_row(row) for row in cursor.fetchall()]
            if items:
                placeholders = ",".join("?" * len(items))
                cursor.execute(
                    f"""
                    UPDATE media_index_queue
                    SET status = 'processing', updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                    """,
                    [item.id for item in items],
                )
        return items

    def mark_done(self, item_id: str) -> None:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE media_index_queue
                SET status = 'done', last_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (item_id,),
            )

    def mark_failed(self, item_id: str, error: str, max_attempts: int) -> None:
        """Count the attempt; back to pending until attempts exhaust, then failed."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE media_index_queue
                SET attempts = attempts + 1,
                    status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (max_attempts, error[:1000], item_id),
            )

    def done_file_ids_for_user(self, user_id: str, pass_type: str) -> List[str]:
        """File ids of a user's queue rows already completed for this pass."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT q.file_id FROM media_index_queue q
                JOIN files f ON f.id = q.file_id
                WHERE q.pass_type = ? AND q.status = 'done' AND f.user_id = ?
                """,
                (pass_type, user_id),
            )
            return [row["file_id"] for row in cursor.fetchall()]

    def has_unfinished_queue_rows(self, user_id: str, pass_type: str) -> bool:
        """Whether any of this user's queue rows for ``pass_type`` are not
        yet ``done`` - pending, processing, OR permanently ``failed``.

        Used to gate stale gallery-collection pruning: a rebuild is only
        "complete" once every requeued file has actually landed in the new
        collection. A permanently failed row (attempts exhausted) blocks
        pruning indefinitely rather than being treated as settled - dropping
        the old collection while even one file never made it into the new
        one would lose data that must survive a failure.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM media_index_queue q
                JOIN files f ON f.id = q.file_id
                WHERE q.pass_type = ? AND f.user_id = ? AND q.status != 'done'
                LIMIT 1
                """,
                (pass_type, user_id),
            )
            return cursor.fetchone() is not None

    def file_summaries(self, file_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch-fetch display context for files (thumbnail, path, type)."""
        if not file_ids:
            return {}
        placeholders = ",".join("?" * len(file_ids))
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, file_path, file_type, thumbnail_medium
                FROM files
                WHERE id IN ({placeholders})
                """,
                file_ids,
            )
            return {
                row["id"]: {
                    "file_path": row["file_path"],
                    "file_type": row["file_type"],
                    "thumbnail": row["thumbnail_medium"],
                }
                for row in cursor.fetchall()
            }

    def queue_counts(self, pass_type: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        query = """
            SELECT pass_type, status, COUNT(*) AS count
            FROM media_index_queue
        """
        params: List[Any] = []
        if pass_type:
            query += " WHERE pass_type = ?"
            params.append(pass_type)
        query += " GROUP BY pass_type, status"

        counts: Dict[str, Dict[str, int]] = {}
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            for row in cursor.fetchall():
                counts.setdefault(row["pass_type"], {})[row["status"]] = row["count"]
        return counts

    # --- System tags -----------------------------------------------------------

    def set_thumbnails(
        self, file_id: str, small: Optional[str], medium: Optional[str], large: Optional[str]
    ) -> None:
        """Write `files.thumbnail_small/medium/large` directly - used by the
        mesh-preview render path, which needs to persist a thumbnail outside
        the `generation` feature's own write path (`file_repository.py`)."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE files
                SET thumbnail_small = ?, thumbnail_medium = ?, thumbnail_large = ?
                WHERE id = ?
                """,
                (small, medium, large, file_id),
            )

    def replace_file_tags(
        self,
        file_id: str,
        generation_id: Optional[str],
        provenance: str,
        tags: List[SystemTagPrediction],
        ratings: Dict[str, float],
    ) -> None:
        """Replace a file's system tags with the current model's output.

        Ratings live in the same table as ``category='rating'`` rows so they
        carry the same provenance/confidence semantics; serialization splits
        them back out, and tag filters exclude the rating category.
        """
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM media_system_tags WHERE file_id = ?", (file_id,))
            for prediction in tags:
                cursor.execute(
                    """
                    INSERT INTO media_system_tags
                        (id, file_id, generation_id, tag, category, confidence, provenance)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_ulid(),
                        file_id,
                        generation_id,
                        prediction.tag,
                        prediction.category,
                        prediction.confidence,
                        provenance,
                    ),
                )
            for rating, score in ratings.items():
                cursor.execute(
                    """
                    INSERT INTO media_system_tags
                        (id, file_id, generation_id, tag, category, confidence, provenance)
                    VALUES (?, ?, ?, ?, 'rating', ?, ?)
                    """,
                    (generate_ulid(), file_id, generation_id, rating, score, provenance),
                )

    def get_for_files(self, file_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch-fetch ``{file_id: {system_tags, rating_scores, provenance}}``."""
        if not file_ids:
            return {}
        placeholders = ",".join("?" * len(file_ids))
        result: Dict[str, Dict[str, Any]] = {}
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT file_id, tag, category, confidence, provenance
                FROM media_system_tags
                WHERE file_id IN ({placeholders})
                ORDER BY confidence DESC
                """,
                file_ids,
            )
            for row in cursor.fetchall():
                entry = result.setdefault(
                    row["file_id"],
                    {"system_tags": [], "rating_scores": {}, "provenance": row["provenance"]},
                )
                if row["category"] == "rating":
                    entry["rating_scores"][row["tag"]] = row["confidence"]
                else:
                    entry["system_tags"].append(
                        {
                            "tag": row["tag"],
                            "category": row["category"],
                            "confidence": row["confidence"],
                        }
                    )
        return result

    def stale_file_ids(self, current_provenance: str) -> List[str]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT file_id FROM media_system_tags WHERE provenance != ?",
                (current_provenance,),
            )
            return [row["file_id"] for row in cursor.fetchall()]

    def delete_not_provenance(self, provenance: str) -> int:
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM media_system_tags WHERE provenance != ?", (provenance,)
            )
            return cursor.rowcount

    def tagged_file_count(self) -> int:
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(DISTINCT file_id) AS c FROM media_system_tags")
            return cursor.fetchone()["c"]
