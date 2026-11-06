from src.platform.database.database import db

_NAME = "090_elixir_run_numbers"

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_elixir_runs_history ON elixir_runs (elixir_id, user_id, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_runs_number ON elixir_runs (elixir_id, number) WHERE number IS NOT NULL",
)

def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(elixir_runs)")
        if "number" not in {row[1] for row in cursor.fetchall()}:
            cursor.execute("ALTER TABLE elixir_runs ADD COLUMN number INTEGER")
        cursor.execute("SELECT elixir_id, MAX(number) FROM elixir_runs WHERE elixir_id IS NOT NULL GROUP BY elixir_id")
        last = {row[0]: row[1] or 0 for row in cursor.fetchall()}
        cursor.execute(
            "SELECT id, elixir_id FROM elixir_runs WHERE number IS NULL AND elixir_id IS NOT NULL ORDER BY created_at, id"
        )
        pending = [(row[0], row[1]) for row in cursor.fetchall()]
        for run_id, elixir_id in pending:
            last[elixir_id] = last.get(elixir_id, 0) + 1
            cursor.execute("UPDATE elixir_runs SET number = ? WHERE id = ?", (last[elixir_id], run_id))
        for statement in _INDEXES:
            cursor.execute(statement)
    print(f"Migration {_NAME}: {len(pending)} elixir brews numbered, history index created")
