from typing import List, Optional
from src.features.llm_memory.records import LLMMemoryNote
from src.platform.util.ids import generate_ulid


class LLMMemoryRepository:
    def upsert(self, note: LLMMemoryNote) -> LLMMemoryNote:
        """Insert or update a memory note by (user_id, key, scope, scope_ref)."""
        if not note.id:
            note.id = generate_ulid()

        coalesced_scope_ref = note.scope_ref or ''
        result_id = note.id

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            # Check if a note with this key/scope/scope_ref already exists
            cursor.execute("""
                SELECT id FROM llm_memory
                WHERE user_id = ? AND key = ? AND scope = ? AND COALESCE(scope_ref, '') = ?
            """, (note.user_id, note.key, note.scope, coalesced_scope_ref))
            existing = cursor.fetchone()

            if existing:
                # Update existing note
                result_id = existing['id']
                cursor.execute("""
                    UPDATE llm_memory
                    SET content = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                """, (note.content, existing['id'], note.user_id))
            else:
                # Insert new note
                cursor.execute("""
                    INSERT INTO llm_memory (id, user_id, key, content, scope, scope_ref)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (note.id, note.user_id, note.key, note.content, note.scope, note.scope_ref))

        # Read back after commit so the new connection can see the data
        return self.get_by_id(result_id, note.user_id)

    def get_by_id(self, id: str, user_id: str) -> Optional[LLMMemoryNote]:
        """Get a memory note by ID scoped to user."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM llm_memory WHERE id = ? AND user_id = ?",
                (id, user_id)
            )
            row = cursor.fetchone()
            return LLMMemoryNote.from_row(row) if row else None

    def get_by_key(
        self,
        user_id: str,
        key: str,
        scope: str,
        scope_ref: Optional[str] = None,
    ) -> Optional[LLMMemoryNote]:
        """Get a memory note by its (user_id, key, scope, scope_ref) address."""
        coalesced_scope_ref = scope_ref or ''
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM llm_memory
                WHERE user_id = ? AND key = ? AND scope = ? AND COALESCE(scope_ref, '') = ?
            """, (user_id, key, scope, coalesced_scope_ref))
            row = cursor.fetchone()
            return LLMMemoryNote.from_row(row) if row else None

    def list_notes(
        self,
        user_id: str,
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
    ) -> List[LLMMemoryNote]:
        """List memory notes with optional filters, ordered by updated_at DESC."""
        where_clauses = ["user_id = ?"]
        params: list = [user_id]

        if scope is not None:
            where_clauses.append("scope = ?")
            params.append(scope)

        if scope_ref is not None:
            where_clauses.append("scope_ref = ?")
            params.append(scope_ref)

        query = (
            f"SELECT * FROM llm_memory"
            f" WHERE {' AND '.join(where_clauses)}"
            f" ORDER BY updated_at DESC"
        )

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [LLMMemoryNote.from_row(row) for row in cursor.fetchall()]

    def update(self, note_id: str, user_id: str, key: str, content: str) -> Optional[LLMMemoryNote]:
        """Update a note's key/content by id, scoped to user. Returns the refreshed note."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE llm_memory
                SET key = ?, content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (key, content, note_id, user_id))
            if cursor.rowcount == 0:
                return None

        return self.get_by_id(note_id, user_id)

    def delete(self, id: str, user_id: str) -> bool:
        """Delete a memory note by ID scoped to user."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM llm_memory WHERE id = ? AND user_id = ?",
                (id, user_id)
            )
            return cursor.rowcount > 0
