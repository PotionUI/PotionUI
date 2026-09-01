"""
User Model Meta Repository

Handles per-user metadata overlay on models: favorite flag and custom display
name. One row per (user_id, model_id), upserted via SQLite's
`ON CONFLICT ... DO UPDATE`.
"""
from typing import Dict, List, Optional
from src.features.model_library.records.user_model_meta import UserModelMeta
import logging

logger = logging.getLogger(__name__)


class UserModelMetaRepository:
    """Repository for managing per-user model metadata (favorites, custom names)."""

    def get(self, user_id: str, model_id: str) -> Optional[UserModelMeta]:
        """Get the metadata row for a user/model pair, if any."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM user_model_meta WHERE user_id = ? AND model_id = ?
            """, (user_id, model_id))
            row = cursor.fetchone()
            return UserModelMeta.from_row(row) if row else None

    def set_favorite(self, user_id: str, model_id: str, is_favorite: bool) -> UserModelMeta:
        """Set (or clear) the favorite flag for a user/model pair."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_model_meta (user_id, model_id, is_favorite, created_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, model_id) DO UPDATE SET
                    is_favorite = excluded.is_favorite,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, model_id, int(is_favorite)))

        return self.get(user_id, model_id)

    def set_custom_name(self, user_id: str, model_id: str, name: Optional[str]) -> UserModelMeta:
        """Set (or clear, when name is None) the custom display name for a user/model pair."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_model_meta (user_id, model_id, custom_name, created_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, model_id) DO UPDATE SET
                    custom_name = excluded.custom_name,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, model_id, name))

        return self.get(user_id, model_id)

    def get_map(self, user_id: str, model_ids: List[str]) -> Dict[str, UserModelMeta]:
        """Batch-fetch metadata for a set of models, keyed by model_id."""
        if not model_ids:
            return {}

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(model_ids))
            cursor.execute(f"""
                SELECT * FROM user_model_meta
                WHERE user_id = ? AND model_id IN ({placeholders})
            """, (user_id, *model_ids))
            return {row['model_id']: UserModelMeta.from_row(row) for row in cursor.fetchall()}

    def favorite_model_ids(self, user_id: str) -> set:
        """Return the set of model IDs the user has favorited."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT model_id FROM user_model_meta WHERE user_id = ? AND is_favorite = 1
            """, (user_id,))
            return {row['model_id'] for row in cursor.fetchall()}


# Global repository instance - for backward compatibility only.
# Prefer using the DI-injected UserModelMetaRepository instead.
user_model_meta_repo = UserModelMetaRepository()
