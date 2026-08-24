"""
Migration to create the automation module tables.

Persists user-authored automations (trigger -> condition -> action node
graphs), their runs, and per-node run status. See `src/core/automation/`
for the engine that executes these graphs and
`src/persistence/models/automation.py` / `automation_repository.py` for the
model/repository pair that reads and writes these tables.
"""

from src.platform.database.database import db


def up():
    """Create automation tables and their indexes."""
    with db.get_cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                graph TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_run_at TIMESTAMP,
                last_run_status TEXT
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_automations_enabled
            ON automations(enabled)
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS automation_runs (
                id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL,
                trigger_node_id TEXT,
                trigger_type TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                event_payload TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                duration_ms INTEGER,
                FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_automation_runs_automation_started
            ON automation_runs(automation_id, started_at DESC)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_automation_runs_status
            ON automation_runs(status)
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS automation_run_nodes (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                input TEXT,
                output TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                duration_ms INTEGER,
                FOREIGN KEY (run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_automation_run_nodes_run
            ON automation_run_nodes(run_id)
        ''')


def down():
    """Rollback the migration - drop the automation tables."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS automation_run_nodes")
        cursor.execute("DROP TABLE IF EXISTS automation_runs")
        cursor.execute("DROP TABLE IF EXISTS automations")
