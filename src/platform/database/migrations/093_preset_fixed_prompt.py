from src.platform.database.database import db

_NAME = "093_preset_fixed_prompt"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(presets)")
        if "fixed_prompt" not in {row[1] for row in cursor.fetchall()}:
            cursor.execute("ALTER TABLE presets ADD COLUMN fixed_prompt TEXT NOT NULL DEFAULT '{}'")
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS generation_applied_prompts ("
            "generation_id TEXT PRIMARY KEY REFERENCES generations(id) ON DELETE CASCADE, "
            "applied TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
    print(f"Migration {_NAME}: presets.fixed_prompt and generation_applied_prompts ready")
