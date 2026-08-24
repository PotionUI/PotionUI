"""
Tag Repository

Handles database operations for tags. Returns DTOs, not database models.
"""
from typing import List, Optional, Dict
from datetime import datetime
from src.platform.database import db
from src.features.tags.dto import Tag, TagWithCount, TagType
from src.platform.util.ids import generate_ulid
import logging

logger = logging.getLogger(__name__)

# Kept under SQLite's default 999 host-parameter limit.
_SQLITE_IN_CHUNK_SIZE = 900


class TagRepository:
    """Repository for managing tags"""

    def _row_to_tag(self, row) -> Optional[Tag]:
        """Convert a database row to a Tag DTO."""
        if not row:
            return None

        created_at = None
        if row['created_at']:
            try:
                if isinstance(row['created_at'], str):
                    created_at = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
                else:
                    created_at = row['created_at']
            except (ValueError, TypeError):
                created_at = datetime.now()

        # Handle optional type field
        try:
            tag_type = TagType(row['type']) if row['type'] else TagType.MODEL
        except (KeyError, IndexError, ValueError):
            tag_type = TagType.MODEL

        # Handle optional user_id field
        try:
            user_id = row['user_id']
        except (KeyError, IndexError):
            user_id = None

        return Tag(
            id=row['id'],
            name=row['name'],
            type=tag_type,
            user_id=user_id,
            created_at=created_at or datetime.now()
        )

    def _row_to_tag_with_count(self, row, count_type: Optional[str] = None) -> Optional[TagWithCount]:
        """Convert a database row to a TagWithCount DTO."""
        if not row:
            return None

        tag = self._row_to_tag(row)
        if not tag:
            return None

        tag_dict = tag.model_dump()

        if count_type:
            try:
                tag_dict['usage_count'] = row['usage_count'] or 0
            except (KeyError, IndexError):
                tag_dict['usage_count'] = 0
        else:
            try:
                tag_dict['model_count'] = row['model_count'] or 0
            except (KeyError, IndexError):
                tag_dict['model_count'] = 0
            try:
                tag_dict['generation_count'] = row['generation_count'] or 0
            except (KeyError, IndexError):
                tag_dict['generation_count'] = 0
            try:
                tag_dict['upload_count'] = row['upload_count'] or 0
            except (KeyError, IndexError):
                tag_dict['upload_count'] = 0
            tag_dict['usage_count'] = (
                (tag_dict['model_count'] or 0)
                + (tag_dict['generation_count'] or 0)
                + (tag_dict['upload_count'] or 0)
            )

        return TagWithCount(**tag_dict)

    def create_tag(self, name: str, type: str = 'MODEL', user_id: Optional[str] = None) -> Optional[Tag]:
        """
        Create a new tag with type and user.

        Returns the created tag directly from the INSERT to avoid a second DB connection.
        """
        tag_id = generate_ulid()
        now = datetime.now()

        with db.get_cursor() as cursor:
            # Check if tag already exists (case-insensitive, same type and user)
            cursor.execute("""
                SELECT id, name, type, user_id, created_at FROM tags
                WHERE LOWER(name) = LOWER(?)
                AND type = ?
                AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))
            """, (name, type, user_id, user_id))
            existing = cursor.fetchone()

            if existing:
                return self._row_to_tag(existing)

            # Create new tag
            cursor.execute("""
                INSERT INTO tags (id, name, type, user_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (tag_id, name, type, user_id, now.isoformat()))

        # Return the tag directly without a second DB query
        return Tag(
            id=tag_id,
            name=name,
            type=TagType(type),
            user_id=user_id,
            created_at=now
        )

    def get_tag_by_id(self, tag_id: str) -> Optional[Tag]:
        """Get tag by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT id, name, type, user_id, created_at FROM tags WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            return self._row_to_tag(row)

    def get_tag_by_name(self, name: str, type: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Tag]:
        """Get tag by name (case-insensitive) with optional type and user filters"""
        with db.get_cursor() as cursor:
            query = "SELECT id, name, type, user_id, created_at FROM tags WHERE LOWER(name) = LOWER(?)"
            params = [name]

            if type:
                query += " AND type = ?"
                params.append(type)

            if user_id is not None:
                query += " AND user_id = ?"
                params.append(user_id)

            cursor.execute(query, params)
            row = cursor.fetchone()
            return self._row_to_tag(row)

    def get_all_tags(self, type: Optional[str] = None, user_id: Optional[str] = None) -> List[Tag]:
        """Get all tags filtered by type and user"""
        query = "SELECT id, name, type, user_id, created_at FROM tags WHERE 1=1"
        params = []

        if type:
            query += " AND type = ?"
            params.append(type)

        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " ORDER BY name ASC"

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [self._row_to_tag(row) for row in cursor.fetchall()]

    def get_tags_with_counts(self, type: Optional[str] = None, user_id: Optional[str] = None) -> List[TagWithCount]:
        """Get all tags with usage counts based on type"""
        if type == 'MODEL':
            count_table = 'model_tags'
            count_column = 'model_id'
        elif type == 'GENERATION':
            count_table = 'generation_tags'
            count_column = 'generation_id'
        elif type == 'UPLOAD':
            count_table = 'upload_tags'
            count_column = 'upload_id'
        else:
            count_table = None

        with db.get_cursor() as cursor:
            if count_table:
                query = f"""
                    SELECT t.id, t.name, t.type, t.user_id, t.created_at,
                           COUNT(mt.{count_column}) as usage_count
                    FROM tags t
                    LEFT JOIN {count_table} mt ON t.id = mt.tag_id
                    WHERE 1=1
                """
            else:
                query = """
                    SELECT t.id, t.name, t.type, t.user_id, t.created_at,
                           COUNT(DISTINCT mt.model_id) as model_count,
                           COUNT(DISTINCT gt.generation_id) as generation_count,
                           COUNT(DISTINCT ut.upload_id) as upload_count
                    FROM tags t
                    LEFT JOIN model_tags mt ON t.id = mt.tag_id
                    LEFT JOIN generation_tags gt ON t.id = gt.tag_id
                    LEFT JOIN upload_tags ut ON t.id = ut.tag_id
                    WHERE 1=1
                """

            params = []

            if type:
                query += " AND t.type = ?"
                params.append(type)

            if user_id is not None:
                query += " AND t.user_id = ?"
                params.append(user_id)

            query += " GROUP BY t.id ORDER BY t.name ASC"

            cursor.execute(query, params)
            return [self._row_to_tag_with_count(row, count_table) for row in cursor.fetchall()]

    def search_tags(self, query: str, type: Optional[str] = None, user_id: Optional[str] = None, limit: int = 10) -> List[Tag]:
        """Search tags by name with type and user filter"""
        sql = "SELECT id, name, type, user_id, created_at FROM tags WHERE LOWER(name) LIKE LOWER(?)"
        params = [f"%{query}%"]

        if type:
            sql += " AND type = ?"
            params.append(type)

        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)

        sql += " ORDER BY name ASC LIMIT ?"
        params.append(limit)

        with db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [self._row_to_tag(row) for row in cursor.fetchall()]

    def update_tag(self, tag_id: str, name: str) -> bool:
        """Update tag name"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE tags SET name = ? WHERE id = ?
            """, (name, tag_id))
            return cursor.rowcount > 0

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag (will cascade delete model associations)"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            return cursor.rowcount > 0

    # Model-Tag relationship methods

    def add_tag_to_model(self, model_id: str, tag_id: str) -> bool:
        """Add a tag to a model"""
        with db.get_cursor() as cursor:
            try:
                cursor.execute("""
                    INSERT INTO model_tags (model_id, tag_id, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (model_id, tag_id))
                return True
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    return True
                logger.error(f"Error adding tag to model: {e}")
                return False

    def get_model_tags(self, model_id: str) -> List[Tag]:
        """Get all tags for a model"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT t.id, t.name, t.type, t.user_id, t.created_at FROM tags t
                JOIN model_tags mt ON t.id = mt.tag_id
                WHERE mt.model_id = ?
                ORDER BY t.name ASC
            """, (model_id,))
            return [self._row_to_tag(row) for row in cursor.fetchall()]

    def set_model_tags(self, model_id: str, tag_ids: List[str]) -> bool:
        """Replace all tags for a model"""
        with db.get_cursor() as cursor:
            try:
                cursor.execute("DELETE FROM model_tags WHERE model_id = ?", (model_id,))

                for tag_id in tag_ids:
                    cursor.execute("""
                        INSERT INTO model_tags (model_id, tag_id, created_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    """, (model_id, tag_id))

                return True
            except Exception as e:
                logger.error(f"Error setting model tags: {e}")
                return False

    # Generation-Tag relationship methods

    def add_tag_to_generation(self, generation_id: str, tag_id: str) -> bool:
        """Add a tag to a generation"""
        with db.get_cursor() as cursor:
            try:
                cursor.execute("""
                    INSERT INTO generation_tags (generation_id, tag_id, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (generation_id, tag_id))
                return True
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    return True
                logger.error(f"Error adding tag to generation: {e}")
                return False

    def remove_tag_from_generation(self, generation_id: str, tag_id: str) -> bool:
        """Remove a tag from a generation"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM generation_tags
                WHERE generation_id = ? AND tag_id = ?
            """, (generation_id, tag_id))
            return cursor.rowcount > 0

    def get_generation_tags(self, generation_id: str) -> List[Tag]:
        """Get all tags for a generation"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT t.id, t.name, t.type, t.user_id, t.created_at FROM tags t
                JOIN generation_tags gt ON t.id = gt.tag_id
                WHERE gt.generation_id = ?
                ORDER BY t.name ASC
            """, (generation_id,))
            return [self._row_to_tag(row) for row in cursor.fetchall()]

    def get_generation_tags_bulk(self, generation_ids: List[str]) -> Dict[str, List[Tag]]:
        """Batch equivalent of `get_generation_tags` for a page of generations.

        One query per chunk of `generation_ids` instead of one query per
        generation. Every id in `generation_ids` is present in the result,
        mapped to `[]` if it has no tags.
        """
        result: Dict[str, List[Tag]] = {generation_id: [] for generation_id in generation_ids}
        if not generation_ids:
            return result

        with db.get_cursor() as cursor:
            for start in range(0, len(generation_ids), _SQLITE_IN_CHUNK_SIZE):
                chunk = generation_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
                placeholders = ','.join('?' * len(chunk))
                cursor.execute(f"""
                    SELECT t.id, t.name, t.type, t.user_id, t.created_at,
                           gt.generation_id AS _bulk_generation_id
                    FROM tags t
                    JOIN generation_tags gt ON t.id = gt.tag_id
                    WHERE gt.generation_id IN ({placeholders})
                    ORDER BY t.name ASC
                """, chunk)
                for row in cursor.fetchall():
                    result[row['_bulk_generation_id']].append(self._row_to_tag(row))

        return result

    def get_generations_by_tags(self, tag_ids: List[str], user_id: Optional[str] = None) -> List[str]:
        """Get generation IDs that have ALL specified tags"""
        if not tag_ids:
            return []

        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(tag_ids))
            params = list(tag_ids)

            if user_id:
                query = f"""
                    SELECT gt.generation_id
                    FROM generation_tags gt
                    JOIN generations g ON gt.generation_id = g.id
                    WHERE gt.tag_id IN ({placeholders})
                    AND g.user_id = ?
                """
                params.append(user_id)
            else:
                query = f"""
                    SELECT generation_id
                    FROM generation_tags
                    WHERE tag_id IN ({placeholders})
                """

            query += """
                GROUP BY generation_id
                HAVING COUNT(DISTINCT tag_id) = ?
            """
            params.append(len(tag_ids))

            cursor.execute(query, params)
            return [row['generation_id'] if 'generation_id' in row.keys() else row[0] for row in cursor.fetchall()]

    def set_generation_tags(self, generation_id: str, tag_ids: List[str]) -> bool:
        """Replace all tags for a generation"""
        with db.get_cursor() as cursor:
            try:
                cursor.execute("DELETE FROM generation_tags WHERE generation_id = ?", (generation_id,))

                for tag_id in tag_ids:
                    cursor.execute("""
                        INSERT INTO generation_tags (generation_id, tag_id, created_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    """, (generation_id, tag_id))

                return True
            except Exception as e:
                logger.error(f"Error setting generation tags: {e}")
                return False

    # Upload-Tag relationship methods (library resources, migration 115)

    def get_upload_tags(self, upload_id: str) -> List[Tag]:
        """Get all tags for one library upload"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT t.id, t.name, t.type, t.user_id, t.created_at FROM tags t
                JOIN upload_tags ut ON t.id = ut.tag_id
                WHERE ut.upload_id = ?
                ORDER BY t.name ASC
            """, (upload_id,))
            return [self._row_to_tag(row) for row in cursor.fetchall()]

    def get_upload_tags_bulk(self, upload_ids: List[str]) -> Dict[str, List[Tag]]:
        """Batch equivalent of `get_upload_tags` for a page of library items.

        One query per chunk of `upload_ids` instead of one query per upload -
        the library list path must stay at a constant query count however many
        rows a page holds. Every id in `upload_ids` is present in the result,
        mapped to `[]` if it has no tags.
        """
        result: Dict[str, List[Tag]] = {upload_id: [] for upload_id in upload_ids}
        if not upload_ids:
            return result

        with db.get_cursor() as cursor:
            for start in range(0, len(upload_ids), _SQLITE_IN_CHUNK_SIZE):
                chunk = upload_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
                placeholders = ','.join('?' * len(chunk))
                cursor.execute(f"""
                    SELECT t.id, t.name, t.type, t.user_id, t.created_at,
                           ut.upload_id AS _bulk_upload_id
                    FROM tags t
                    JOIN upload_tags ut ON t.id = ut.tag_id
                    WHERE ut.upload_id IN ({placeholders})
                    ORDER BY t.name ASC
                """, chunk)
                for row in cursor.fetchall():
                    result[row['_bulk_upload_id']].append(self._row_to_tag(row))

        return result

    def set_upload_tags(self, upload_id: str, tag_ids: List[str]) -> bool:
        """Replace all tags for one library upload"""
        with db.get_cursor() as cursor:
            try:
                cursor.execute("DELETE FROM upload_tags WHERE upload_id = ?", (upload_id,))

                for tag_id in tag_ids:
                    cursor.execute("""
                        INSERT INTO upload_tags (upload_id, tag_id, created_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    """, (upload_id, tag_id))

                return True
            except Exception as e:
                logger.error(f"Error setting upload tags: {e}")
                return False


# Global repository instance - for backward compatibility only
# Prefer using DI-injected TagRepository instead
tag_repo = TagRepository()
