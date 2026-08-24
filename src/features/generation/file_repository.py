from typing import List, Optional, Dict, Any
from datetime import datetime
from src.platform.database import db
from src.features.generation.records import File, GenerationFile
from src.platform.util.ids import generate_ulid

# Kept under SQLite's default 999 host-parameter limit, with room left for the
# optional trailing user_id parameter appended after the chunk's placeholders.
_SQLITE_IN_CHUNK_SIZE = 900

class FileRepository:
    def create(self, file: File) -> File:
        """Create a new file"""
        # Generate ULID for the file if not provided
        if not file.id:
            file.id = generate_ulid()
            
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO files (
                    id, file_path, file_type, user_id, mime_type, file_size,
                    pipe_name, is_final, is_derived, thumbnail_small, thumbnail_medium, thumbnail_large,
                    width, height, duration_seconds, fps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file.id,
                file.file_path,
                file.file_type,
                file.user_id,
                file.mime_type,
                file.file_size,
                file.pipe_name,
                file.is_final,
                file.is_derived,
                file.thumbnail_small,
                file.thumbnail_medium,
                file.thumbnail_large,
                file.width,
                file.height,
                file.duration_seconds,
                file.fps
            ))
            
            # Retrieve the file within the same transaction
            cursor.execute("SELECT * FROM files WHERE id = ?", (file.id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return File.from_row(row)
    
    def get_by_id(self, file_id: str, user_id: Optional[str] = None) -> Optional[File]:
        """Get file by ID, optionally filtered by user"""
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user_id))
            else:
                cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return File.from_row(row)

    def get_all(self, user_id: Optional[str] = None, file_type: Optional[str] = None, 
               limit: Optional[int] = None, offset: int = 0) -> List[File]:
        """Get all files with optional filtering"""
        query = "SELECT * FROM files"
        params = []
        conditions = []
        
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY created_at DESC"
        
        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [File.from_row(row) for row in cursor.fetchall()]
    
    def delete(self, file_id: str) -> bool:
        """Delete file"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
            return cursor.rowcount > 0
    
    def associate_with_generation(self, generation_id: str, file_id: str) -> GenerationFile:
        """Associate a file with a generation"""
        generation_file = GenerationFile(
            id=generate_ulid(),
            generation_id=generation_id,
            file_id=file_id
        )
        
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generation_files (id, generation_id, file_id)
                VALUES (?, ?, ?)
            """, (generation_file.id, generation_id, file_id))
            
            cursor.execute("SELECT * FROM generation_files WHERE id = ?", (generation_file.id,))
            return GenerationFile.from_row(cursor.fetchone())
    
    def get_generation_files(self, generation_id: str, user_id: Optional[str] = None,
                           file_type: Optional[str] = None, is_final: Optional[bool] = None) -> List[File]:
        """Get files associated with a generation.

        Position in this list IS the file's index: parameter rows, the export
        endpoint and the frontend carousels all address files by it. `created_at`
        is a SQLite CURRENT_TIMESTAMP with one-second resolution, so files saved
        by two pipes of the same generation routinely tie on it - `id` (a ULID,
        lexicographically sortable by creation time) breaks the tie back to save
        order instead of leaving it to the query plan.
        """
        query = """
            SELECT f.* FROM files f
            JOIN generation_files gf ON f.id = gf.file_id
            WHERE gf.generation_id = ?
        """
        params = [generation_id]
        
        if user_id:
            query += " AND f.user_id = ?"
            params.append(user_id)
        
        if file_type:
            query += " AND f.file_type = ?"
            params.append(file_type)
        
        if is_final is not None:
            query += " AND f.is_final = ?"
            params.append(is_final)
        
        query += " ORDER BY f.created_at ASC, f.id ASC"

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [File.from_row(row) for row in cursor.fetchall()]

    def get_generation_files_bulk(self, generation_ids: List[str],
                                   user_id: Optional[str] = None) -> Dict[str, List[File]]:
        """Batch equivalent of `get_generation_files` for a page of generations.

        One query per chunk of `generation_ids` (chunked under SQLite's host
        parameter limit) instead of one query per generation. The per-generation
        order must match `get_generation_files` exactly - see that method's
        docstring for why. Ordering the batched query by generation_id first,
        then created_at, then id reproduces that order: grouping rows by
        generation_id in the order they come back yields each generation's
        files in `created_at ASC, id ASC` order.

        Every id in `generation_ids` is present in the result, mapped to `[]`
        if it has no files.
        """
        result: Dict[str, List[File]] = {generation_id: [] for generation_id in generation_ids}
        if not generation_ids:
            return result

        with db.get_cursor() as cursor:
            for start in range(0, len(generation_ids), _SQLITE_IN_CHUNK_SIZE):
                chunk = generation_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
                placeholders = ','.join('?' * len(chunk))
                query = f"""
                    SELECT f.*, gf.generation_id AS _bulk_generation_id FROM files f
                    JOIN generation_files gf ON f.id = gf.file_id
                    WHERE gf.generation_id IN ({placeholders})
                """
                params: List[Any] = list(chunk)

                if user_id:
                    query += " AND f.user_id = ?"
                    params.append(user_id)

                query += " ORDER BY gf.generation_id ASC, f.created_at ASC, f.id ASC"

                cursor.execute(query, params)
                for row in cursor.fetchall():
                    result[row['_bulk_generation_id']].append(File.from_row(row))

        return result

    def remove_generation_association(self, generation_id: str, file_id: str) -> bool:
        """Remove association between generation and file"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM generation_files WHERE generation_id = ? AND file_id = ?",
                (generation_id, file_id)
            )
            return cursor.rowcount > 0
    
    def debug_recent_generation_files(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Raw dump of the most recent `generation_files` rows, for the admin
        debug endpoint. Note: `generation_files` is the junction table (id,
        generation_id, file_id, created_at) since migration 010 - it carries
        none of file_path/file_type/file_size/pipe_name/is_final, so row
        access on those keys raises and the caller reports it as an error.
        Preserved as-is; not the caller's job to redesign a debug endpoint.
        """
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM generation_files ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [
                {
                    "id": row['id'],
                    "generation_id": row['generation_id'],
                    "file_path": row['file_path'],
                    "file_type": row['file_type'],
                    "file_size": row['file_size'],
                    "pipe_name": row['pipe_name'],
                    "is_final": bool(row['is_final']),
                    "created_at": row['created_at']
                }
                for row in rows
            ]

    def set_thumbnail_paths(self, file_ids: List[str], thumbnail_small: Optional[str],
                          thumbnail_medium: Optional[str], thumbnail_large: Optional[str]) -> int:
        """Set thumbnail paths on every file in `file_ids` in one transaction.

        Async thumbnail generation can find more than one matching video file
        record (duplicates), so all of them are updated together or not at all.
        Returns the number of files updated.
        """
        with db.get_cursor() as cursor:
            for file_id in file_ids:
                cursor.execute(
                    """
                    UPDATE files
                    SET thumbnail_small = ?, thumbnail_medium = ?, thumbnail_large = ?
                    WHERE id = ?
                    """,
                    (thumbnail_small, thumbnail_medium, thumbnail_large, file_id)
                )
            return len(file_ids)

    def get_generation_file_by_file_id(self, file_id: str) -> Optional[GenerationFile]:
        """Get GenerationFile junction record by file_id"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM generation_files WHERE file_id = ?",
                (file_id,)
            )
            row = cursor.fetchone()
            return GenerationFile.from_row(row) if row else None

# Global repository instance
file_repo = FileRepository()