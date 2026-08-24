"""
Rename model_info table to providers and clean up redundant fields

This migration:
1. Renames model_info table to providers (or creates fresh if not exists)
2. Removes images and thumbnail_path columns (tracked via model_files table)
3. Updates all indexes and triggers

Handles multiple scenarios:
- model_info exists: migrate data to providers
- model_civitai_info exists (from migration 006): migrate that instead
- neither exists: create providers fresh
"""

from src.platform.database.database import db

def up():
    """Rename model_info to providers and remove redundant columns"""
    with db.get_cursor() as cursor:
        # Check which source table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_info'")
        has_model_info = cursor.fetchone() is not None

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_civitai_info'")
        has_civitai_info = cursor.fetchone() is not None

        # Create new providers table with cleaned schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'civitai',
                provider_model_id TEXT,
                provider_version_id TEXT,
                name TEXT,
                description TEXT,
                tags TEXT,  -- JSON array
                nsfw BOOLEAN DEFAULT FALSE,
                download_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE,
                UNIQUE(model_id, provider)
            )
        """)

        # Migrate data based on which source table exists
        if has_model_info:
            # Migrate from model_info (intermediate state)
            cursor.execute("""
                INSERT OR IGNORE INTO providers (
                    id, model_id, provider, provider_model_id, provider_version_id,
                    name, description, tags, nsfw, download_url, created_at, updated_at
                )
                SELECT
                    id, model_id, provider, provider_model_id, provider_version_id,
                    name, description, tags, nsfw, download_url, created_at, updated_at
                FROM model_info
            """)
        elif has_civitai_info:
            # Migrate from model_civitai_info (original from migration 006)
            cursor.execute("""
                INSERT OR IGNORE INTO providers (
                    id, model_id, provider, provider_model_id, provider_version_id,
                    name, description, tags, nsfw, download_url, created_at, updated_at
                )
                SELECT
                    model_id, model_id, 'civitai', civitai_model_id, version_id,
                    name, description, tags, nsfw, download_url, created_at, updated_at
                FROM model_civitai_info
            """)

        # Create trigger for updated_at (drop first to be idempotent)
        cursor.execute("DROP TRIGGER IF EXISTS update_providers_updated_at")
        cursor.execute("""
            CREATE TRIGGER update_providers_updated_at
            AFTER UPDATE ON providers
            FOR EACH ROW
            BEGIN
                UPDATE providers SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create indexes for faster queries (use IF NOT EXISTS for idempotency)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_providers_model_id ON providers (model_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_providers_provider ON providers (provider)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_providers_provider_model_id ON providers (provider_model_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_providers_model_provider ON providers (model_id, provider)")

        # Drop old model_info table and its indexes/triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_model_info_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_model_info_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_info_provider")
        cursor.execute("DROP INDEX IF EXISTS idx_model_info_provider_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_info_model_provider")
        cursor.execute("DROP TABLE IF EXISTS model_info")

        # Drop old model_civitai_info table and its indexes/triggers (from migration 006)
        cursor.execute("DROP TRIGGER IF EXISTS update_model_civitai_info_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_model_civitai_info_civitai_model_id")
        cursor.execute("DROP TABLE IF EXISTS model_civitai_info")

def down():
    """Revert to old model_info table structure"""
    with db.get_cursor() as cursor:
        # Recreate old table with images and thumbnail_path
        cursor.execute("""
            CREATE TABLE model_info (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'civitai',
                provider_model_id TEXT,
                provider_version_id TEXT,
                name TEXT,
                description TEXT,
                tags TEXT,  -- JSON array
                nsfw BOOLEAN DEFAULT FALSE,
                images TEXT,  -- JSON array of image URLs - restored as empty
                download_url TEXT,
                thumbnail_path TEXT,  -- Restored as NULL
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE,
                UNIQUE(model_id, provider)
            )
        """)

        # Migrate data back (images and thumbnail_path will be NULL)
        cursor.execute("""
            INSERT INTO model_info (
                id, model_id, provider, provider_model_id, provider_version_id,
                name, description, tags, nsfw, download_url, created_at, updated_at
            )
            SELECT
                id, model_id, provider, provider_model_id, provider_version_id,
                name, description, tags, nsfw, download_url, created_at, updated_at
            FROM providers
        """)

        # Recreate old trigger and indexes
        cursor.execute("""
            CREATE TRIGGER update_model_info_updated_at
            AFTER UPDATE ON model_info
            FOR EACH ROW
            BEGIN
                UPDATE model_info SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        cursor.execute("CREATE INDEX idx_model_info_model_id ON model_info (model_id)")
        cursor.execute("CREATE INDEX idx_model_info_provider ON model_info (provider)")
        cursor.execute("CREATE INDEX idx_model_info_provider_model_id ON model_info (provider_model_id)")
        cursor.execute("CREATE INDEX idx_model_info_model_provider ON model_info (model_id, provider)")

        # Drop new table and related objects
        cursor.execute("DROP TRIGGER IF EXISTS update_providers_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_providers_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_providers_provider")
        cursor.execute("DROP INDEX IF EXISTS idx_providers_provider_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_providers_model_provider")
        cursor.execute("DROP TABLE IF EXISTS providers")
