"""
Create generation_models junction table to link generations with models used.
This table stores which models were used for each generation (checkpoints, loras, etc).
"""

from src.platform.database.database import db

def up():
    """Create generation_models junction table"""
    with db.get_cursor() as cursor:
        # Create generation_models table
        cursor.execute("""
            CREATE TABLE generation_models (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                UNIQUE(generation_id, model_id)
            )
        """)

        # Create indexes for faster queries
        cursor.execute("CREATE INDEX idx_generation_models_generation_id ON generation_models (generation_id)")
        cursor.execute("CREATE INDEX idx_generation_models_model_id ON generation_models (model_id)")

def down():
    """Drop generation_models table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_generation_models_generation_id")
        cursor.execute("DROP INDEX IF EXISTS idx_generation_models_model_id")
        cursor.execute("DROP TABLE IF EXISTS generation_models")
