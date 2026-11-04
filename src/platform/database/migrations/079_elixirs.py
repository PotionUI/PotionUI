from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "079_elixirs"

_RUN_STATUSES = (
    "'pending', 'running', 'awaiting_selection', 'needs_attention', 'cancelling', "
    "'completed', 'partial', 'cancelled', 'failed', 'interrupted'"
)
_INVOCATION_STATUSES = (
    "'pending', 'claimed', 'submitted', 'running', 'completed', 'failed', 'cancelled', 'interrupted', 'superseded'"
)

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS elixirs ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE, "
    "name TEXT NOT NULL, "
    "run_mode TEXT NOT NULL DEFAULT 'review' CHECK (run_mode IN ('automatic', 'review')), "
    "revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1), "
    "definition TEXT NOT NULL, "
    "results TEXT NOT NULL DEFAULT '{}', "
    "result_revision INTEGER NOT NULL DEFAULT 0 CHECK (result_revision >= 0), "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_elixirs_user ON elixirs (user_id, updated_at)",
    "CREATE TABLE IF NOT EXISTS elixir_runs ("
    "id TEXT PRIMARY KEY, "
    "elixir_id TEXT REFERENCES elixirs (id) ON DELETE SET NULL, "
    "user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE, "
    "elixir_revision INTEGER NOT NULL, "
    "scope TEXT NOT NULL CHECK (scope IN ('graph', 'node', 'branch')), "
    "scope_node_id TEXT, "
    "scope_edge_id TEXT, "
    "base_run_id TEXT REFERENCES elixir_runs (id) ON DELETE SET NULL, "
    "run_mode TEXT NOT NULL CHECK (run_mode IN ('automatic', 'review')), "
    f"status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({_RUN_STATUSES})), "
    "graph TEXT NOT NULL, "
    "templates TEXT NOT NULL DEFAULT '{}', "
    "capability_digests TEXT NOT NULL DEFAULT '{}', "
    "pinned_inputs TEXT NOT NULL DEFAULT '{}', "
    "node_states TEXT NOT NULL DEFAULT '{}', "
    "checkpoints TEXT NOT NULL DEFAULT '{}', "
    "idempotency_key TEXT, "
    "request_fingerprint TEXT, "
    "error TEXT, "
    "cancel_requested_at TEXT, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "finished_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_runs_idempotency "
    "ON elixir_runs (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_elixir_runs_elixir ON elixir_runs (elixir_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_elixir_runs_status ON elixir_runs (status)",
    "CREATE TABLE IF NOT EXISTS elixir_invocations ("
    "id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL REFERENCES elixir_runs (id) ON DELETE CASCADE, "
    "user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE, "
    "node_id TEXT NOT NULL, "
    "edge_id TEXT, "
    "ordinal INTEGER NOT NULL CHECK (ordinal >= 0), "
    "input_key TEXT NOT NULL, "
    "input_ref TEXT, "
    "source_node_id TEXT, "
    "source_run_id TEXT, "
    "source_invocation_id TEXT REFERENCES elixir_invocations (id) ON DELETE SET NULL, "
    "source_ordinal INTEGER, "
    "source_file_index INTEGER, "
    "parent_invocation_id TEXT REFERENCES elixir_invocations (id) ON DELETE SET NULL, "
    "attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1), "
    "idempotency_key TEXT NOT NULL, "
    "request TEXT, "
    "effective_seed INTEGER, "
    "generation_id TEXT, "
    f"status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({_INVOCATION_STATUSES})), "
    "accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0, 1)), "
    "error TEXT, "
    "outputs TEXT NOT NULL DEFAULT '[]', "
    "claimed_at TEXT, "
    "submitted_at TEXT, "
    "submitted_boot TEXT, "
    "finished_at TEXT, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_invocations_key ON elixir_invocations (idempotency_key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_invocations_slot "
    "ON elixir_invocations (run_id, node_id, COALESCE(edge_id, ''), input_key, attempt)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_invocations_accepted "
    "ON elixir_invocations (run_id, node_id, COALESCE(edge_id, ''), input_key) WHERE accepted = 1",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_elixir_invocations_generation "
    "ON elixir_invocations (generation_id) WHERE generation_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_elixir_invocations_run ON elixir_invocations (run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_elixir_invocations_status ON elixir_invocations (status)",
)

_LIMITS = (
    ("elixirs_max_nodes", "24", "The most preset and media nodes one elixir may hold."),
    ("elixirs_max_edges", "48", "The most connections one elixir may hold."),
    ("elixirs_max_branches", "8", "The most outgoing connections one elixir node may have."),
    ("elixirs_max_media_per_source", "1000", "The most files one elixir media source node may hold."),
    ("elixirs_max_outputs_per_connection", "1000", "The most files one elixir connection may pass on in a single brew."),
    ("elixirs_max_invocations", "200", "The most generations one elixir brew may start across all branches."),
)


def up():
    with db.get_cursor() as cursor:
        for statement in _STATEMENTS:
            cursor.execute(statement)
        now = now_iso()
        for key, value, description in _LIMITS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, 'integer', ?, 'SYSTEM', ?, ?)
                """,
                (generate_ulid(), key, value, description, now, now),
            )
    print(f"Migration {_NAME}: elixirs, elixir_runs and elixir_invocations created, elixir limits seeded")
