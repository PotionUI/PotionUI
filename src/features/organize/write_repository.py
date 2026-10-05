from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_TAG_LINKS = {
    "generation": ("generation_tags", "generation_id", "generations"),
    "upload": ("upload_tags", "upload_id", "uploads"),
}


def _db():
    from src.platform.database.database import db
    return db


@dataclass(frozen=True)
class CollectionRef:
    id: str
    name: str
    parent_id: Optional[str]


class OrganizeWriteRepository:

    def find_collection(self, scope: str, collection_id: str, user_id: str) -> Optional[CollectionRef]:
        with _db().get_cursor() as cursor:
            if scope == "models":
                cursor.execute(
                    "SELECT id, name, parent_id FROM model_collections WHERE id = ? AND user_id = ?",
                    (collection_id, user_id),
                )
            else:
                cursor.execute(
                    "SELECT id, name, parent_id FROM collections WHERE id = ? AND user_id = ? AND scope = ?",
                    (collection_id, user_id, scope),
                )
            row = cursor.fetchone()
        return CollectionRef(row["id"], row["name"], row["parent_id"]) if row else None

    def collection_names(self, scope: str, collection_ids: Iterable[str], user_id: str) -> Dict[str, str]:
        ids = [cid for cid in dict.fromkeys(collection_ids) if cid]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with _db().get_cursor() as cursor:
            if scope == "models":
                cursor.execute(
                    f"SELECT id, name FROM model_collections WHERE user_id = ? AND id IN ({placeholders})",
                    (user_id, *ids),
                )
            else:
                cursor.execute(
                    f"SELECT id, name FROM collections WHERE user_id = ? AND scope = ? AND id IN ({placeholders})",
                    (user_id, scope, *ids),
                )
            return {row["id"]: row["name"] for row in cursor.fetchall()}

    def find_collection_by_name(self, scope: str, name: str, parent_id: Optional[str],
                                user_id: str) -> Optional[CollectionRef]:
        with _db().get_cursor() as cursor:
            if scope == "models":
                cursor.execute(
                    "SELECT id, name, parent_id FROM model_collections WHERE user_id = ? AND LOWER(name) = LOWER(?) "
                    "AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?) ORDER BY created_at, id LIMIT 1",
                    (user_id, name, parent_id, parent_id),
                )
            else:
                cursor.execute(
                    "SELECT id, name, parent_id FROM collections WHERE user_id = ? AND scope = ? AND LOWER(name) = LOWER(?) "
                    "AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?) ORDER BY created_at, id LIMIT 1",
                    (user_id, scope, name, parent_id, parent_id),
                )
            row = cursor.fetchone()
        return CollectionRef(row["id"], row["name"], row["parent_id"]) if row else None

    def create_collection(self, scope: str, name: str, parent_id: Optional[str], user_id: str) -> CollectionRef:
        if parent_id and self.find_collection(scope, parent_id, user_id) is None:
            parent_id = None
        collection_id = generate_ulid()
        with _db().get_cursor() as cursor:
            if scope == "models":
                cursor.execute(
                    "INSERT INTO model_collections (id, name, user_id, parent_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (collection_id, name, user_id, parent_id, now_iso()),
                )
            else:
                cursor.execute(
                    "INSERT INTO collections (id, name, user_id, parent_id, created_at, scope) VALUES (?, ?, ?, ?, ?, ?)",
                    (collection_id, name, user_id, parent_id, now_iso(), scope),
                )
        return CollectionRef(collection_id, name, parent_id)

    def add_member(self, scope: str, collection_id: str, item_id: str, user_id: str, bump: bool = True) -> bool:
        with _db().get_cursor() as cursor:
            if scope == "history":
                cursor.execute(
                    "INSERT OR IGNORE INTO collection_generations (collection_id, generation_id, created_at) "
                    "SELECT c.id, g.id, CURRENT_TIMESTAMP FROM collections c JOIN generations g ON g.user_id = c.user_id "
                    "WHERE c.id = ? AND c.user_id = ? AND c.scope = 'history' AND g.id = ?",
                    (collection_id, user_id, item_id),
                )
            elif scope == "library":
                cursor.execute(
                    "INSERT OR IGNORE INTO collection_uploads (collection_id, upload_id, created_at) "
                    "SELECT c.id, u.id, CURRENT_TIMESTAMP FROM collections c JOIN uploads u ON u.user_id = c.user_id "
                    "WHERE c.id = ? AND c.user_id = ? AND c.scope = 'library' AND u.id = ?",
                    (collection_id, user_id, item_id),
                )
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO model_collection_members (collection_id, model_id, created_at) "
                    "SELECT c.id, m.id, CURRENT_TIMESTAMP FROM model_collections c JOIN models m "
                    "WHERE c.id = ? AND c.user_id = ? AND m.id = ?",
                    (collection_id, user_id, item_id),
                )
            added = cursor.rowcount > 0
            if added and bump and scope == "history":
                self._bump(cursor, user_id)
        return added

    def remove_member(self, scope: str, collection_id: str, item_id: str, user_id: str) -> bool:
        with _db().get_cursor() as cursor:
            if scope == "history":
                cursor.execute(
                    "DELETE FROM collection_generations WHERE collection_id = ? AND generation_id = ? "
                    "AND collection_id IN (SELECT id FROM collections WHERE id = ? AND user_id = ?)",
                    (collection_id, item_id, collection_id, user_id),
                )
            elif scope == "library":
                cursor.execute(
                    "DELETE FROM collection_uploads WHERE collection_id = ? AND upload_id = ? "
                    "AND collection_id IN (SELECT id FROM collections WHERE id = ? AND user_id = ?)",
                    (collection_id, item_id, collection_id, user_id),
                )
            else:
                cursor.execute(
                    "DELETE FROM model_collection_members WHERE collection_id = ? AND model_id = ? "
                    "AND collection_id IN (SELECT id FROM model_collections WHERE id = ? AND user_id = ?)",
                    (collection_id, item_id, collection_id, user_id),
                )
            removed = cursor.rowcount > 0
            if removed and scope == "history":
                self._bump(cursor, user_id)
        return removed

    def ensure_tag(self, name: str, tag_type: str, user_id: str) -> Tuple[str, str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT id, name FROM tags WHERE LOWER(name) = LOWER(?) AND type = ? AND user_id = ? ORDER BY created_at, id LIMIT 1",
                (name, tag_type, user_id),
            )
            row = cursor.fetchone()
            if row:
                return row["id"], row["name"]
            tag_id = generate_ulid()
            cursor.execute(
                "INSERT INTO tags (id, name, type, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (tag_id, name, tag_type, user_id, now_iso()),
            )
        return tag_id, name

    def attach_tag(self, subject: str, item_id: str, tag_id: str, user_id: str, bump: bool = True) -> bool:
        link, column, items = _TAG_LINKS[subject]
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"INSERT OR IGNORE INTO {link} ({column}, tag_id, created_at) "
                f"SELECT i.id, t.id, CURRENT_TIMESTAMP FROM {items} i JOIN tags t ON t.user_id = i.user_id "
                f"WHERE i.id = ? AND i.user_id = ? AND t.id = ?",
                (item_id, user_id, tag_id),
            )
            added = cursor.rowcount > 0
            if added and bump and subject == "generation":
                self._bump(cursor, user_id)
        return added

    def detach_tag(self, subject: str, item_id: str, tag_id: str, user_id: str) -> bool:
        link, column, items = _TAG_LINKS[subject]
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {link} WHERE {column} = ? AND tag_id = ? "
                f"AND {column} IN (SELECT id FROM {items} WHERE id = ? AND user_id = ?)",
                (item_id, tag_id, item_id, user_id),
            )
            removed = cursor.rowcount > 0
            if removed and subject == "generation":
                self._bump(cursor, user_id)
        return removed

    def bump_history(self, user_id: str) -> None:
        with _db().get_cursor() as cursor:
            self._bump(cursor, user_id)

    def _bump(self, cursor, user_id: str) -> None:
        from src.features.generation.history_revision_repository import bump_history_revision
        bump_history_revision(cursor, user_id)

    def existing_collections(self, scope: str, user_id: str) -> List[CollectionRef]:
        with _db().get_cursor() as cursor:
            if scope == "models":
                cursor.execute("SELECT id, name, parent_id FROM model_collections WHERE user_id = ?", (user_id,))
            else:
                cursor.execute(
                    "SELECT id, name, parent_id FROM collections WHERE user_id = ? AND scope = ?", (user_id, scope)
                )
            return [CollectionRef(row["id"], row["name"], row["parent_id"]) for row in cursor.fetchall()]
