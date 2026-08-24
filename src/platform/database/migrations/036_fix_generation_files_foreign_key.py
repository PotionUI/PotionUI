"""
Migration to fix generation_files foreign key reference.

This migration fixes an issue where the generation_files table's foreign key
references 'files_old' instead of 'files' due to a failed/interrupted migration 035.

The fix recreates the generation_files table with the correct foreign key reference.
"""

from src.platform.database.database import db


def up():
    """Fix generation_files foreign key to reference 'files' instead of 'files_old'"""
    with db.get_connection() as conn:
        # Check if we need to fix the foreign key
        cursor = conn.execute("PRAGMA foreign_key_list(generation_files)")
        fk_list = cursor.fetchall()

        needs_fix = False
        for fk in fk_list:
            # fk format: (id, seq, table, from, to, on_update, on_delete, match)
            if fk[2] == 'files_old':
                needs_fix = True
                break

        if not needs_fix:
            # Foreign key is correct, nothing to do
            return

        # Disable foreign keys temporarily
        conn.execute("PRAGMA foreign_keys = OFF")

        # Get existing data
        cursor = conn.execute("SELECT id, generation_id, file_id, created_at FROM generation_files")
        data = cursor.fetchall()

        # Drop the old table
        conn.execute("DROP TABLE IF EXISTS generation_files")

        # Recreate with correct foreign key
        conn.execute("""
            CREATE TABLE generation_files (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
        """)

        # Restore data
        for row in data:
            conn.execute(
                "INSERT INTO generation_files (id, generation_id, file_id, created_at) VALUES (?, ?, ?, ?)",
                row
            )

        # Re-enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


def down():
    """No rollback needed - this fixes a bug, not a schema change"""
    pass
