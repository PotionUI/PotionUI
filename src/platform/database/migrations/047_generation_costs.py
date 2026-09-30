from src.platform.database.database import db

_NAME = "047_generation_costs"

_CREATE_COSTS = (
    "CREATE TABLE IF NOT EXISTS generation_costs ("
    "id TEXT PRIMARY KEY, "
    "generation_id TEXT NOT NULL, "
    "backend_id TEXT, "
    "model_id TEXT, "
    "user_id TEXT, "
    "amount_usd TEXT, "
    "source TEXT NOT NULL, "
    "detail TEXT NOT NULL DEFAULT '{}', "
    "created_at TEXT NOT NULL)"
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(_CREATE_COSTS)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generation_costs_generation ON generation_costs (generation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generation_costs_created ON generation_costs (created_at)")
    print(f"Migration {_NAME}: generation costs created")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_generation_costs_created")
        cursor.execute("DROP INDEX IF EXISTS idx_generation_costs_generation")
        cursor.execute("DROP TABLE IF EXISTS generation_costs")
    print(f"Migration {_NAME}: generation costs dropped")
