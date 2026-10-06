import json
import sqlite3
from typing import List, Optional

from src.features.filters.records import UserFilter
from src.platform.database import get_database_connection
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid


class FilterNameTaken(Exception):
    pass


class FilterLimitReached(Exception):
    pass


def _translate(exc: sqlite3.IntegrityError, name: str) -> Exception:
    if "UNIQUE" in str(exc).upper():
        return FilterNameTaken(name)
    return exc


class UserFilterRepository:

    def create(self, user_filter: UserFilter, limit: int) -> UserFilter:
        filter_id = generate_ulid()
        stamp = now_iso()
        try:
            with get_database_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO user_filters (id, owner_id, name, description, group_name, intensity, "
                    "steps_json, schema_version, created_at, updated_at) "
                    "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
                    "WHERE (SELECT COUNT(*) FROM user_filters WHERE owner_id = ?) < ?",
                    (
                        filter_id, user_filter.owner_id, user_filter.name, user_filter.description,
                        user_filter.group_name, user_filter.intensity, json.dumps(user_filter.steps),
                        user_filter.schema_version, stamp, stamp, user_filter.owner_id, limit,
                    ),
                )
                conn.commit()
                inserted = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise _translate(exc, user_filter.name) from exc
        if not inserted:
            raise FilterLimitReached()
        return self.get_for_owner(user_filter.owner_id, filter_id)

    def get_for_owner(self, owner_id: str, filter_id: str) -> Optional[UserFilter]:
        with get_database_connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_filters WHERE id = ? AND owner_id = ?", (filter_id, owner_id)
            ).fetchone()
        return UserFilter.from_row(row) if row else None

    def list_for_owner(self, owner_id: str) -> List[UserFilter]:
        with get_database_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM user_filters WHERE owner_id = ? ORDER BY name COLLATE NOCASE ASC, id ASC",
                (owner_id,),
            ).fetchall()
        return [UserFilter.from_row(row) for row in rows]

    def update(self, user_filter: UserFilter) -> Optional[UserFilter]:
        try:
            with get_database_connection() as conn:
                cursor = conn.execute(
                    "UPDATE user_filters SET name = ?, description = ?, group_name = ?, intensity = ?, "
                    "steps_json = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                    (
                        user_filter.name, user_filter.description, user_filter.group_name,
                        user_filter.intensity, json.dumps(user_filter.steps), now_iso(),
                        user_filter.id, user_filter.owner_id,
                    ),
                )
                conn.commit()
                updated = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise _translate(exc, user_filter.name) from exc
        return self.get_for_owner(user_filter.owner_id, user_filter.id) if updated else None

    def delete(self, owner_id: str, filter_id: str) -> bool:
        with get_database_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM user_filters WHERE id = ? AND owner_id = ?", (filter_id, owner_id)
            )
            conn.commit()
            return cursor.rowcount > 0
