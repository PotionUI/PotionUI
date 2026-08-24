"""
Migration to make preset_id nullable for uploaded generations
"""

from src.platform.database.database import db

def up():
    """Make preset_id column nullable in generations table"""
    with db.get_cursor() as cursor:
        # Check if generations table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='generations'
        """)
        if not cursor.fetchone():
            # Table doesn't exist yet, skip this migration
            return

        # SQLite doesn't support ALTER COLUMN directly
        # We need to recreate the table with the new schema

        # 1. Create new table with nullable preset_id and removed unused columns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generations_new (
                id TEXT PRIMARY KEY,
                preset_id TEXT,
                preset_version TEXT,
                form_data TEXT NOT NULL,
                user_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Copy data from old table to new table (only keeping needed columns)
        cursor.execute("""
            INSERT INTO generations_new (
                id, preset_id, preset_version, form_data, user_id,
                status, progress, created_at, completed_at, updated_at
            )
            SELECT
                id, preset_id, preset_version, form_data, user_id,
                status, progress, created_at, completed_at, updated_at
            FROM generations
        """)

        # 3. Drop old table
        cursor.execute("DROP TABLE generations")

        # 4. Rename new table to original name
        cursor.execute("ALTER TABLE generations_new RENAME TO generations")

        # 5. Recreate indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_user_id
            ON generations(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_status
            ON generations(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_preset_id
            ON generations(preset_id)
        """)

        # 6. Recreate trigger for updated_at
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_generations_updated_at
            AFTER UPDATE ON generations
            FOR EACH ROW
            BEGIN
                UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Rollback changes - restore old schema with NOT NULL preset_id and all columns"""
    with db.get_cursor() as cursor:
        # Create table with NOT NULL preset_id and all original columns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generations_new (
                id TEXT PRIMARY KEY,
                preset_id TEXT NOT NULL,
                preset_version TEXT,
                form_data TEXT NOT NULL,
                user_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                current_step TEXT,
                current_step_num INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                error_message TEXT,
                output_directory TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tab_id TEXT
            )
        """)

        # Copy data, excluding rows with NULL preset_id
        # Restore removed columns with NULL values
        cursor.execute("""
            INSERT INTO generations_new (
                id, preset_id, preset_version, form_data, user_id,
                status, progress, current_step, current_step_num, total_steps,
                error_message, output_directory, created_at, started_at,
                completed_at, updated_at, tab_id
            )
            SELECT
                id, preset_id, preset_version, form_data, user_id,
                status, progress, NULL, 0, 0,
                NULL, NULL, created_at, NULL,
                completed_at, updated_at, NULL
            FROM generations WHERE preset_id IS NOT NULL
        """)

        # Drop old table
        cursor.execute("DROP TABLE generations")

        # Rename new table
        cursor.execute("ALTER TABLE generations_new RENAME TO generations")

        # Recreate indexes and trigger (same as up())
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_user_id
            ON generations(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_status
            ON generations(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_preset_id
            ON generations(preset_id)
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_generations_updated_at
            AFTER UPDATE ON generations
            FOR EACH ROW
            BEGIN
                UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
