import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.features.content_safety.constants import (
    DEFAULT_THRESHOLD,
    SETTING_THRESHOLD,
    STATE_FLAGGED,
    STATE_SAFE,
    STATE_UNRATED,
)
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

_CHUNK = 400
_RATED_TYPES = ("IMAGE", "VIDEO")


def derive_state(score: Optional[float], threshold: float) -> str:
    if score is None:
        return STATE_UNRATED
    return STATE_FLAGGED if score >= threshold else STATE_SAFE


def _chunks(items: Sequence[str]):
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


class ContentLedger:
    def __init__(self, settings: Any):
        self.settings = settings

    def threshold(self) -> float:
        try:
            return float(self.settings.get_setting(SETTING_THRESHOLD, DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            return DEFAULT_THRESHOLD

    def record_score(self, key: str, score: float, rater: Optional[str] = None, source: str = "gate") -> str:
        state = derive_state(score, self.threshold())
        self._upsert(key, score, state, rater, source)
        return state

    def record_tagger_score(self, key: str, score: float, rater: Optional[str] = None) -> None:
        state = derive_state(score, self.threshold())
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO content_ratings (key, nsfw_score, state, rater, source, rated_at)
                VALUES (?, ?, ?, ?, 'tagger', ?)
                ON CONFLICT(key) DO UPDATE SET
                    nsfw_score = excluded.nsfw_score,
                    state = excluded.state,
                    rater = excluded.rater,
                    source = excluded.source,
                    rated_at = excluded.rated_at
                WHERE content_ratings.source != 'gate' OR content_ratings.nsfw_score IS NULL
                """,
                (key, score, state, rater, now_iso()),
            )

    def record_unrated(self, key: str, source: str = "gate") -> None:
        self._upsert(key, None, STATE_UNRATED, None, source)

    def _upsert(self, key: str, score: Optional[float], state: str, rater: Optional[str], source: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO content_ratings (key, nsfw_score, state, rater, source, rated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    nsfw_score = excluded.nsfw_score,
                    state = excluded.state,
                    rater = excluded.rater,
                    source = excluded.source,
                    rated_at = excluded.rated_at
                """,
                (key, score, state, rater, source, now_iso()),
            )

    def scores(self, keys: Iterable[str]) -> Dict[str, Optional[float]]:
        unique = list(dict.fromkeys(key for key in keys if key))
        found: Dict[str, Optional[float]] = {}
        if not unique:
            return found
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT key, nsfw_score FROM content_ratings WHERE key IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    found[row["key"]] = row["nsfw_score"]
        return found

    def states(self, keys: Iterable[str]) -> Dict[str, str]:
        unique = list(dict.fromkeys(key for key in keys if key))
        scores = self.scores(unique)
        threshold = self.threshold()
        return {key: derive_state(scores.get(key), threshold) for key in unique}

    def log_event(
        self,
        kind: str,
        user_id: Optional[str] = None,
        generation_id: Optional[str] = None,
        mode: Optional[str] = None,
        score: Optional[float] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        from src.platform.database.database import db
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO content_safety_events (id, ts, user_id, generation_id, kind, mode, score, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_ulid(), now_iso(), user_id, generation_id, kind, mode, score,
                        json.dumps(detail) if detail else None,
                    ),
                )
        except Exception:
            logger.exception("content safety event %s could not be recorded", kind)

    def generation_states(self, generation_ids: Iterable[str]) -> Dict[str, List[str]]:
        unique = list(dict.fromkeys(gid for gid in generation_ids if gid))
        paths: Dict[str, List[str]] = {}
        if not unique:
            return {}
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    SELECT gf.generation_id AS generation_id, f.file_path AS file_path
                    FROM generation_files gf
                    JOIN files f ON f.id = gf.file_id
                    WHERE gf.generation_id IN ({placeholders}) AND f.file_type IN ('IMAGE', 'VIDEO')
                    """,
                    chunk,
                )
                for row in cursor.fetchall():
                    paths.setdefault(row["generation_id"], []).append(row["file_path"])
        states = self.states(path for group in paths.values() for path in group)
        return {gid: [states.get(path, STATE_UNRATED) for path in group] for gid, group in paths.items()}

    def backfill_progress(self) -> Dict[str, int]:
        placeholders = ",".join("?" * len(_RATED_TYPES))
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM files WHERE file_type IN ({placeholders})", _RATED_TYPES
            )
            total = cursor.fetchone()["c"]
            cursor.execute(
                f"""
                SELECT COUNT(*) AS c FROM files f
                JOIN content_ratings c ON c.key = f.file_path
                WHERE f.file_type IN ({placeholders}) AND c.nsfw_score IS NOT NULL
                """,
                _RATED_TYPES,
            )
            rated = cursor.fetchone()["c"]
        return {"total": total, "rated": rated}

    def unrated_files(self, limit: int, after: str = "") -> List[Dict[str, Any]]:
        placeholders = ",".join("?" * len(_RATED_TYPES))
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT f.id, f.file_path, f.file_type, f.thumbnail_medium
                FROM files f
                LEFT JOIN content_ratings c ON c.key = f.file_path
                WHERE f.file_type IN ({placeholders}) AND (c.key IS NULL OR c.nsfw_score IS NULL) AND f.id > ?
                ORDER BY f.id
                LIMIT ?
                """,
                (*_RATED_TYPES, after, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
