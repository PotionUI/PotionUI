import json
from typing import Any, Dict, List

from src.platform.database import db


class UserModelAttributeRepository:
    """Per-user attribute value overlay: one row per `(user_id, model_id, key)`,
    upserted via SQLite's `ON CONFLICT ... DO UPDATE` (mirrors
    `UserModelMetaRepository`)."""

    def get_map(self, user_id: str, model_id: str) -> Dict[str, Any]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT key, value FROM user_model_attributes WHERE user_id = ? AND model_id = ?",
                (user_id, model_id),
            )
            return {row["key"]: json.loads(row["value"]) if row["value"] is not None else None
                    for row in cursor.fetchall()}

    def get_maps(self, user_id: str, model_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch-fetch overlays for a set of models, keyed by model_id (a model
        with no overlay rows is simply absent - callers default it to `{}`)."""
        if not model_ids:
            return {}

        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(model_ids))
            cursor.execute(
                f"SELECT model_id, key, value FROM user_model_attributes "
                f"WHERE user_id = ? AND model_id IN ({placeholders})",
                (user_id, *model_ids),
            )
            maps: Dict[str, Dict[str, Any]] = {}
            for row in cursor.fetchall():
                maps.setdefault(row["model_id"], {})[row["key"]] = (
                    json.loads(row["value"]) if row["value"] is not None else None
                )
            return maps

    def upsert(self, user_id: str, model_id: str, key: str, value: Any) -> None:
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_model_attributes (user_id, model_id, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, model_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, model_id, key, json.dumps(value)))

    def upsert_many(self, user_id: str, model_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in values.items():
            self.upsert(user_id, model_id, key, value)
        return self.get_map(user_id, model_id)
