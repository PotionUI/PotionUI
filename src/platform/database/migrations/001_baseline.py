"""Migration 001: the squashed baseline schema.

This migration replaces the 143 files that used to live in this directory
(001_create_configurations.py .. 140_add_inspirations_technique.py, including
the handful of duplicate-numbered ones the old chain had accumulated:
015_*/020_*/057_* each had two files sharing a number). It creates the exact
schema those 143 files produced when run in sequence against a fresh
database - every table, index, and trigger, byte-identical DDL, dumped from a
real run of the old chain and pinned here - plus reseeds the handful of
tables that carried default data rather than pure schema on a fresh install:
`settings` (SYSTEM/USER defaults), `keybinding_defaults` (the built-in
shortcuts), and `user_groups` (the two built-in groups, `all_admins` and
`all_users`). Every other table starts empty on a fresh install, exactly as
it did after the old chain - there is nothing else to reseed.

WAL is set explicitly here even though `Database.get_connection()` already
sets `PRAGMA journal_mode=WAL` on every connection (including the very first
one this migration itself uses, since `get_applied_migrations()` opens a
connection before any migration runs) - `journal_mode` is the one pragma of
the three the old 015_enable_wal_mode.py set that is actually persisted in
the database file rather than reset per-connection, so setting it here is
what future-proofs a fresh install against that per-connection default ever
changing. Its two siblings, `synchronous` and `cache_size`/`mmap_size`, are
NOT ported: `synchronous` resets to SQLite's compiled default on every new
connection (see `Database.get_connection()`'s own comment on this - it has to
be set per-connection, never in a migration), and `cache_size`/`mmap_size`
are exactly as connection-scoped, so setting them once during a migration's
single connection never had any lasting effect in the old chain either - it
only warmed the one connection the migration itself was running on.

`keybinding_defaults` seeds 15 rows here, not the 16 the old chain produced -
`quick_search` was dropped by `003_remove_quick_search_keybinding.py` for
every database that already ran this baseline, so a fresh install is seeded
without it from the start rather than seeded-then-deleted.

WHY A SQUASH: 143 sequential files is 143 imports, 143 tiny transactions, and
143 opportunities for drift between what a file says today and what actually
ran against the one production database that matters - this project has
exactly one existing installation, the maintainer's, so there is no fleet of
already-migrated databases to stay compatible with beyond that single one.
Squashing loses nothing a fresh install needs and removes a lot a fresh
install doesn't.

THE GUARD - this is the part that makes the squash safe for that one existing
database. `migration_runner.MigrationRunner.run_migrations()` computes
`pending = available - applied` and runs `pending` in filename-sorted order;
"001_baseline" always sorts before every other migration stem, so this file's
`up()` is always the first thing that runs when there is anything to run at
all. On a truly fresh database, `applied_migrations` is empty the first time
`up()` executes (the table itself was just created by
`get_applied_migrations()`, but nothing has been recorded into it yet) -
`up()` builds the full schema and reseeds. On the maintainer's live database,
`applied_migrations` already holds `001_create_configurations` through
`140_add_inspirations_technique` (and the duplicate-numbered files) from
every migration that ran before this squash existed - `up()` detects any
pre-squash row, prints a one-line notice, and returns without creating a
single table or touching a single row. The runner then marks
`001_baseline` applied regardless of what `up()` did, which is exactly the
right outcome on that database too: its schema already IS this schema (this
file's DDL was dumped from a real run of the exact chain that database went
through), so nothing further needs to run, and `002_*` onward can proceed
normally next time there's something to add.
"""

import logging
import random
import string
import time

from src.platform.database.database import db

logger = logging.getLogger(__name__)


def _has_pre_squash_history(cursor) -> bool:
    """True if `applied_migrations` already holds a row from the old,
    pre-squash chain (i.e. any stem other than this file's own).

    Creates the table first if it is missing, mirroring
    `MigrationRunner.get_applied_migrations()` - the runner always creates it
    before running any pending migration, so this is a no-op there, but it
    lets this migration's `up()` also be called directly against a bare
    connection the way tests load individual migration files.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applied_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            migration_name TEXT UNIQUE NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "SELECT 1 FROM applied_migrations WHERE migration_name != '001_baseline' LIMIT 1"
    )
    return cursor.fetchone() is not None


def _settings_id() -> str:
    """The id shape most of the old settings-adding migrations generated
    inline (each redefined this same snippet locally rather than sharing a
    helper): a 13-digit millisecond timestamp followed by 10 random
    uppercase-alphanumeric characters. Kept here rather than switched to
    `generate_ulid()` for fidelity with what actually ran - nothing in the
    codebase parses or validates a setting's id shape, it is only ever
    looked up by its unique `key`."""
    timestamp = int(time.time() * 1000)
    randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{timestamp:013d}{randomness}"



from datetime import datetime, timezone


TABLE_DDL = [
    "CREATE TABLE automation_run_nodes (\n                id TEXT PRIMARY KEY,\n                run_id TEXT NOT NULL,\n                node_id TEXT NOT NULL,\n                node_type TEXT NOT NULL,\n                status TEXT NOT NULL DEFAULT 'running',\n                input TEXT,\n                output TEXT,\n                error TEXT,\n                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                finished_at TIMESTAMP,\n                duration_ms INTEGER,\n                FOREIGN KEY (run_id) REFERENCES automation_runs(id) ON DELETE CASCADE\n            )",
    "CREATE TABLE automation_runs (\n                id TEXT PRIMARY KEY,\n                automation_id TEXT NOT NULL,\n                trigger_node_id TEXT,\n                trigger_type TEXT,\n                status TEXT NOT NULL DEFAULT 'running',\n                event_payload TEXT,\n                error TEXT,\n                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                finished_at TIMESTAMP,\n                duration_ms INTEGER,\n                FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE\n            )",
    'CREATE TABLE automations (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                description TEXT,\n                enabled INTEGER NOT NULL DEFAULT 0,\n                graph TEXT NOT NULL,\n                version INTEGER NOT NULL DEFAULT 1,\n                user_id TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                last_run_at TIMESTAMP,\n                last_run_status TEXT\n            )',
    "CREATE TABLE backends (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                engine TEXT NOT NULL,\n                enabled INTEGER NOT NULL DEFAULT 1,\n                is_default INTEGER NOT NULL DEFAULT 0,\n                config TEXT NOT NULL DEFAULT '{}',\n                description TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , driver TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE chat_llm_call_traces (\n                id TEXT PRIMARY KEY,\n                session_id TEXT NOT NULL,\n                user_id TEXT,\n                message_id TEXT,\n                purpose TEXT NOT NULL DEFAULT 'chat',\n                iteration INTEGER NOT NULL DEFAULT 1,\n                provider TEXT,\n                model TEXT,\n                request_system TEXT,\n                request_messages TEXT,\n                request_params TEXT,\n                request_tools TEXT,\n                response_text TEXT,\n                response_tool_calls TEXT,\n                prompt_tokens INTEGER,\n                completion_tokens INTEGER,\n                duration_ms INTEGER,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE\n            )",
    'CREATE TABLE chat_messages (\n                id TEXT PRIMARY KEY,\n                session_id TEXT NOT NULL,\n                role TEXT NOT NULL,\n                content TEXT NOT NULL,\n                parsed_content TEXT,\n                metadata TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE\n            )',
    "CREATE TABLE chat_sessions (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                mode TEXT NOT NULL DEFAULT 'segments',\n                name TEXT,\n                status TEXT NOT NULL DEFAULT 'active',\n                llm_config_id TEXT,\n                original_text TEXT,\n                metadata TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                closed_at TIMESTAMP\n            , title_generated INTEGER NOT NULL DEFAULT 0)",
    'CREATE TABLE collection_generations (\n                    collection_id TEXT NOT NULL,\n                    generation_id TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    PRIMARY KEY (collection_id, generation_id),\n                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,\n                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE\n                )',
    'CREATE TABLE collection_prompts (\n            collection_id TEXT NOT NULL,\n            prompt_id TEXT NOT NULL,\n            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n            UNIQUE(collection_id, prompt_id),\n            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,\n            FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE\n        )',
    'CREATE TABLE collection_uploads (\n                collection_id TEXT NOT NULL,\n                upload_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (collection_id, upload_id),\n                FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,\n                FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE\n            )',
    "CREATE TABLE collections (\n                    id TEXT PRIMARY KEY,\n                    name TEXT NOT NULL,\n                    user_id TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, parent_id TEXT REFERENCES collections(id) ON DELETE CASCADE, scope TEXT NOT NULL DEFAULT 'history',\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n                )",
    'CREATE TABLE "configurations" (\n                id TEXT PRIMARY KEY,\n                key TEXT UNIQUE NOT NULL,\n                value TEXT NOT NULL,\n                value_type TEXT NOT NULL DEFAULT \'string\',\n                description TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )',
    "CREATE TABLE downloads (\n                id TEXT PRIMARY KEY,\n                type TEXT NOT NULL DEFAULT 'model',\n                url TEXT NOT NULL,\n                destination_path TEXT NOT NULL,\n                filename TEXT NOT NULL,\n                status TEXT NOT NULL DEFAULT 'pending',\n                progress REAL DEFAULT 0.0,\n                total_bytes INTEGER,\n                downloaded_bytes INTEGER DEFAULT 0,\n                speed_bytes_per_sec REAL,\n                error_message TEXT,\n                provider_id TEXT,\n                tags TEXT,\n                checksum_sha256 TEXT,\n                retry_count INTEGER DEFAULT 0,\n                group_id TEXT,\n                repo_id TEXT,\n                revision TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                started_at TIMESTAMP,\n                completed_at TIMESTAMP,\n                created_by TEXT,\n                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,\n                FOREIGN KEY (group_id) REFERENCES downloads(id) ON DELETE CASCADE,\n                CHECK (status IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'cancelled'))\n            )",
    'CREATE TABLE enhancement_feedback (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                session_id TEXT NOT NULL,\n                message_id TEXT NOT NULL,\n                prompt_text TEXT NOT NULL,\n                verdict TEXT NOT NULL,\n                model_id TEXT,\n                reason TEXT,\n                prompt_id TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , mode TEXT)',
    "CREATE TABLE files (\n                id TEXT PRIMARY KEY,                    -- ULID primary key\n                file_path TEXT NOT NULL,               -- Relative path to the file\n                file_type TEXT NOT NULL,               -- Type: 'image', 'video', 'avatar', 'model', 'metadata', etc.\n                file_size INTEGER,                     -- File size in bytes\n                pipe_name TEXT,                        -- Name of the pipe that generated this file\n                is_final BOOLEAN DEFAULT FALSE,        -- Whether this is a final output file\n                user_id TEXT,                          -- References users(id) with CASCADE delete\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, hash TEXT, filename TEXT, mime_type TEXT, thumbnail_small TEXT, thumbnail_medium TEXT, thumbnail_large TEXT, width INTEGER, height INTEGER, duration_seconds REAL, fps REAL, is_derived INTEGER NOT NULL DEFAULT 0,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n            )",
    'CREATE TABLE "generation_files" (\n                id TEXT PRIMARY KEY,                   -- ULID primary key\n                generation_id TEXT NOT NULL,          -- References generations(id) with CASCADE delete\n                file_id TEXT NOT NULL,                -- References files(id) with CASCADE delete\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,\n                UNIQUE(generation_id, file_id)        -- Prevent duplicate associations\n            )',
    'CREATE TABLE generation_models (\n                id TEXT PRIMARY KEY,\n                generation_id TEXT NOT NULL,\n                model_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n                UNIQUE(generation_id, model_id)\n            )',
    "CREATE TABLE generation_parameters (\n                id TEXT PRIMARY KEY,                    -- ULID primary key\n                generation_id TEXT NOT NULL,            -- References generations(id)\n                parameter_name TEXT NOT NULL,           -- Name of the parameter (e.g., 'seed', 'cfg')\n                parameter_value TEXT NOT NULL,          -- JSON-encoded value\n                parameter_index INTEGER DEFAULT 0,      -- Index of the generated image (0, 1, 2, etc.)\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                UNIQUE(generation_id, parameter_name, parameter_index)\n            )",
    'CREATE TABLE generation_run_reports (\n                generation_id TEXT PRIMARY KEY,\n                report TEXT NOT NULL,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE "generation_segment_phrasebook" (\n                id TEXT PRIMARY KEY,\n                segment_id TEXT NOT NULL,\n                generation_id TEXT NOT NULL,\n                phrasebook_value_id TEXT,\n                category_path TEXT,\n                value TEXT,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (segment_id) REFERENCES generation_segments(id) ON DELETE CASCADE,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                FOREIGN KEY (phrasebook_value_id) REFERENCES "phrasebook_values"(id) ON DELETE SET NULL\n            )',
    "CREATE TABLE generation_segments (\n                id TEXT PRIMARY KEY,\n                generation_id TEXT NOT NULL,\n                channel TEXT NOT NULL DEFAULT 'positive'\n                    CHECK (channel IN ('positive', 'negative')),\n                prompt_index INTEGER NOT NULL DEFAULT 0,\n                segment_index INTEGER NOT NULL DEFAULT 0,\n                segment_type TEXT NOT NULL DEFAULT 'content'\n                    CHECK (segment_type IN ('content', 'break')),\n                text TEXT NOT NULL DEFAULT '',\n                name TEXT,\n                color TEXT,\n                description TEXT,\n                is_disabled INTEGER NOT NULL DEFAULT 0,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE\n            )",
    'CREATE TABLE generation_sources (\n                id TEXT PRIMARY KEY,\n                generation_id TEXT NOT NULL,\n                field_name TEXT NOT NULL,\n                source_generation_id TEXT NOT NULL,\n                source_file_index INTEGER NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                FOREIGN KEY (source_generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                UNIQUE(generation_id, field_name)\n            )',
    'CREATE TABLE generation_stats (\n                id TEXT PRIMARY KEY,\n                generation_id TEXT NOT NULL,\n                preset_id TEXT,\n                preset_name TEXT,\n                engine TEXT,\n                backend_id TEXT,\n                duration_ms INTEGER,\n                cold_start INTEGER,\n                model_load_ms REAL,\n                peak_vram_mb REAL,\n                peak_ram_mb REAL,\n                cpu_percent REAL,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP\n            )',
    'CREATE TABLE generation_tags (\n                generation_id TEXT NOT NULL,\n                tag_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (generation_id, tag_id),\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,\n                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE "generations" (\n                id TEXT PRIMARY KEY,\n                preset_id TEXT,\n                preset_version TEXT,\n                form_data TEXT NOT NULL,\n                user_id TEXT,\n                status TEXT NOT NULL DEFAULT \'pending\',\n                progress REAL DEFAULT 0.0,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                completed_at TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , mode TEXT NOT NULL DEFAULT \'txt2img\', prompt_state TEXT, rating INTEGER NOT NULL DEFAULT 0, is_favorite INTEGER NOT NULL DEFAULT 0, backend_id TEXT, duration_ms INTEGER, started_at TIMESTAMP, tab_id TEXT, form_name TEXT, source_prompt_id TEXT DEFAULT NULL, error_message TEXT)',
    'CREATE TABLE inspiration_collection_items (\n                    collection_id TEXT NOT NULL,\n                    inspiration_id TEXT NOT NULL,\n                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    UNIQUE(collection_id, inspiration_id),\n                    FOREIGN KEY (collection_id) REFERENCES inspiration_collections(id) ON DELETE CASCADE,\n                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE\n                )',
    'CREATE TABLE inspiration_collections (\n                    id TEXT PRIMARY KEY,\n                    user_id TEXT NOT NULL,\n                    name TEXT NOT NULL,\n                    parent_id TEXT,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                    FOREIGN KEY (parent_id) REFERENCES inspiration_collections(id) ON DELETE CASCADE\n                )',
    'CREATE TABLE inspiration_comments (\n                    id TEXT PRIMARY KEY,\n                    inspiration_id TEXT NOT NULL,\n                    user_id TEXT NOT NULL,\n                    body TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE,\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n                )',
    'CREATE TABLE inspiration_saves (\n                    user_id TEXT NOT NULL,\n                    inspiration_id TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    UNIQUE(user_id, inspiration_id),\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE\n                )',
    "CREATE TABLE inspirations (\n                    id TEXT PRIMARY KEY,\n                    user_id TEXT NOT NULL,\n                    title TEXT NOT NULL,\n                    description TEXT,\n                    media TEXT NOT NULL DEFAULT '[]',\n                    params_snapshot TEXT NOT NULL DEFAULT '{}',\n                    preset_id TEXT,\n                    preset_name TEXT,\n                    source_generation_id TEXT,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, technique TEXT,\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n                )",
    'CREATE TABLE instance_claim (\n                id INTEGER PRIMARY KEY CHECK (id = 1),\n                owner_user_id TEXT,\n                owner_username TEXT,\n                claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP\n            )',
    "CREATE TABLE keybinding_defaults (\n                id TEXT PRIMARY KEY,\n                key TEXT NOT NULL,\n                modifiers TEXT DEFAULT '',\n                label TEXT NOT NULL,\n                category TEXT DEFAULT 'general',\n                context TEXT DEFAULT 'global',\n                description TEXT,\n                enabled INTEGER DEFAULT 1,\n                source TEXT DEFAULT 'system',\n                sort_order INTEGER DEFAULT 0\n            )",
    'CREATE TABLE llm_commands (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                description TEXT NOT NULL,\n                prompt TEXT NOT NULL,\n                enabled BOOLEAN NOT NULL DEFAULT 1,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , user_id TEXT REFERENCES users(id) ON DELETE CASCADE)',
    'CREATE TABLE llm_configurations (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                type TEXT NOT NULL,\n                enabled BOOLEAN NOT NULL DEFAULT 1,\n                base_url TEXT NOT NULL,\n                api_key TEXT,\n                model TEXT NOT NULL,\n                system_message TEXT NOT NULL,\n                temperature REAL NOT NULL DEFAULT 0.7,\n                max_tokens INTEGER NOT NULL DEFAULT 1000,\n                timeout INTEGER NOT NULL DEFAULT 30,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , supports_vision BOOLEAN NOT NULL DEFAULT 0, provider_options TEXT DEFAULT NULL, disable_system_prompt BOOLEAN NOT NULL DEFAULT 0, memory_reflection BOOLEAN NOT NULL DEFAULT 1)',
    'CREATE TABLE "llm_memory" (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                key TEXT NOT NULL,\n                content TEXT NOT NULL,\n                scope TEXT NOT NULL DEFAULT \'global\',\n                scope_ref TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )',
    'CREATE TABLE mcp_tokens (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                name TEXT NOT NULL,\n                token_hash TEXT NOT NULL UNIQUE,\n                token_prefix TEXT NOT NULL,\n                created_at TEXT NOT NULL,\n                last_used_at TEXT,\n                revoked_at TEXT\n            )',
    "CREATE TABLE media_index_queue (\n                id TEXT PRIMARY KEY,\n                file_id TEXT NOT NULL,\n                pass_type TEXT NOT NULL,\n                status TEXT NOT NULL DEFAULT 'pending'\n                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),\n                attempts INTEGER NOT NULL DEFAULT 0,\n                last_error TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP,\n                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,\n                UNIQUE (file_id, pass_type)\n            )",
    "CREATE TABLE media_system_tags (\n                id TEXT PRIMARY KEY,\n                file_id TEXT NOT NULL,\n                generation_id TEXT,\n                tag TEXT NOT NULL,\n                category TEXT NOT NULL DEFAULT 'general',\n                confidence REAL NOT NULL,\n                provenance TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,\n                UNIQUE (file_id, category, tag)\n            )",
    "CREATE TABLE model_attribute_definitions (\n                    id TEXT PRIMARY KEY,\n                    key TEXT NOT NULL UNIQUE,\n                    label TEXT NOT NULL,\n                    field_type TEXT NOT NULL,\n                    model_types TEXT NOT NULL DEFAULT '[]',\n                    config TEXT NOT NULL DEFAULT '{}',\n                    default_value TEXT,\n                    description TEXT,\n                    per_user INTEGER NOT NULL DEFAULT 0,\n                    admin_only INTEGER NOT NULL DEFAULT 0,\n                    system INTEGER NOT NULL DEFAULT 0,\n                    source TEXT NOT NULL DEFAULT 'user',\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n                )",
    "CREATE TABLE model_availability (\n    id TEXT PRIMARY KEY,\n    model_id TEXT NOT NULL,\n    backend_id TEXT NOT NULL,\n    ref TEXT NOT NULL,\n    size INTEGER,\n    confidence TEXT NOT NULL DEFAULT 'reported',\n    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, digest TEXT,\n    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n    FOREIGN KEY (backend_id) REFERENCES backends(id) ON DELETE CASCADE,\n    UNIQUE(model_id, backend_id)\n)",
    'CREATE TABLE model_collection_members (\n                collection_id TEXT NOT NULL,\n                model_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (collection_id, model_id),\n                FOREIGN KEY (collection_id) REFERENCES model_collections(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE model_collections (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                user_id TEXT NOT NULL,\n                parent_id TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (parent_id) REFERENCES model_collections(id) ON DELETE CASCADE\n            )',
    "CREATE TABLE model_files (\n                id TEXT PRIMARY KEY,                   -- ULID primary key\n                model_id TEXT NOT NULL,               -- References models(id) with CASCADE delete\n                file_id TEXT NOT NULL,                -- References files(id) with CASCADE delete\n                file_type TEXT NOT NULL DEFAULT 'image', -- Type: 'image', 'thumbnail', 'preview'\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, display_order INTEGER DEFAULT 0,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,\n                UNIQUE(model_id, file_id)             -- Prevent duplicate associations\n            )",
    'CREATE TABLE model_hash_cache (\n                path TEXT PRIMARY KEY,\n                size INTEGER NOT NULL,\n                mtime_ns INTEGER NOT NULL,\n                sha256 TEXT NOT NULL,\n                hashed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )',
    "CREATE TABLE model_preview_media (\n                id TEXT PRIMARY KEY,\n                model_id TEXT NOT NULL,\n                file_id TEXT,\n                url TEXT NOT NULL,\n                type TEXT NOT NULL,  -- 'image' | 'video' | 'audio'\n                name TEXT,\n                position INTEGER NOT NULL DEFAULT 0,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE\n            )",
    'CREATE TABLE model_tags (\n                model_id TEXT NOT NULL,\n                tag_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (model_id, tag_id),\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE "models" (\n    id TEXT PRIMARY KEY,\n    filename TEXT NOT NULL,\n    file_path TEXT,\n    file_size INTEGER,\n    sha256 TEXT UNIQUE,\n    model_type TEXT NOT NULL,\n    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n    user_notes TEXT DEFAULT NULL,\n    description TEXT,\n    prompting_guidance TEXT, preview_media TEXT, is_directory INTEGER NOT NULL DEFAULT 0, is_available INTEGER NOT NULL DEFAULT 1, unavailable_at TIMESTAMP DEFAULT NULL, model_metadata TEXT,\n    UNIQUE(model_type, filename)\n)',
    "CREATE TABLE notifications (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                category TEXT NOT NULL DEFAULT 'system',\n                level TEXT NOT NULL,\n                title TEXT NOT NULL,\n                message TEXT NOT NULL DEFAULT '',\n                metadata TEXT,\n                source TEXT NOT NULL DEFAULT 'core',\n                read INTEGER NOT NULL DEFAULT 0,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , type TEXT NOT NULL DEFAULT '')",
    'CREATE TABLE "phrasebook_categories" (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                path TEXT NOT NULL,\n                parent_id TEXT REFERENCES "phrasebook_categories"(id) ON DELETE CASCADE,\n                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n                description TEXT DEFAULT \'\',\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_active BOOLEAN DEFAULT 1 NOT NULL,\n                UNIQUE(path, user_id)\n            )',
    'CREATE TABLE "phrasebook_values" (\n                id TEXT PRIMARY KEY,\n                category_id TEXT NOT NULL REFERENCES "phrasebook_categories"(id) ON DELETE CASCADE,\n                label TEXT NOT NULL,\n                value TEXT NOT NULL,\n                sort_order INTEGER DEFAULT 0,\n                is_active BOOLEAN DEFAULT 1 NOT NULL,\n                preview_file_id TEXT DEFAULT NULL REFERENCES files(id) ON DELETE SET NULL,\n                preview_generation_id TEXT DEFAULT NULL,\n                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )',
    "CREATE TABLE plugin_hooks (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                plugin_id TEXT NOT NULL,\n                hook_name TEXT NOT NULL,\n                hook_type TEXT NOT NULL,\n                handler_path TEXT,\n                component_path TEXT,\n                position TEXT,\n                sort_order INTEGER DEFAULT 0,\n                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE,\n                CHECK (hook_type IN ('backend', 'frontend'))\n            )",
    'CREATE TABLE plugin_pages (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                plugin_id TEXT NOT NULL,\n                route TEXT NOT NULL UNIQUE,\n                component_path TEXT NOT NULL,\n                label TEXT NOT NULL,\n                icon_svg TEXT,\n                sidebar_order INTEGER DEFAULT 100,\n                show_in_sidebar BOOLEAN DEFAULT 1,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, require_role TEXT,\n                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE plugin_setting_audit (\n                id TEXT PRIMARY KEY,\n                plugin_id TEXT NOT NULL,\n                setting_key TEXT NOT NULL,\n                scope_user_id TEXT,\n                actor_user_id TEXT,\n                actor_username TEXT,\n                action TEXT NOT NULL,\n                is_secret INTEGER NOT NULL DEFAULT 0,\n                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )',
    'CREATE TABLE plugin_settings (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                plugin_id TEXT NOT NULL,\n                user_id TEXT,\n                setting_key TEXT NOT NULL,\n                setting_value TEXT,\n                is_secret INTEGER DEFAULT 0,\n                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                UNIQUE(plugin_id, user_id, setting_key)\n            )',
    "CREATE TABLE plugins (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL,\n                version TEXT NOT NULL,\n                type TEXT NOT NULL DEFAULT 'backend-only',\n                description TEXT,\n                author TEXT,\n                enabled INTEGER NOT NULL DEFAULT 1,\n                manifest_path TEXT NOT NULL,\n                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                CHECK (type IN ('frontend-only', 'backend-only', 'full-stack'))\n            )",
    "CREATE TABLE presets (\n                id TEXT PRIMARY KEY,\n                preset_id TEXT UNIQUE NOT NULL,\n                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , configuration TEXT NOT NULL DEFAULT '{}', form_overrides TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE prompt_segments (\n                id TEXT PRIMARY KEY,\n                prompt_id TEXT NOT NULL,\n                position INTEGER NOT NULL,\n                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),\n                content TEXT NOT NULL DEFAULT '',\n                chips TEXT NOT NULL DEFAULT '{}',\n                is_enabled INTEGER NOT NULL DEFAULT 1,\n                name TEXT,\n                color TEXT,\n                description TEXT,\n                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,\n                UNIQUE (prompt_id, position)\n            )",
    "CREATE TABLE prompts (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                name TEXT COLLATE NOCASE,\n                flattened_text TEXT NOT NULL DEFAULT '',\n                usage_hint TEXT CHECK (usage_hint IS NULL OR usage_hint IN ('positive', 'negative')),\n                source_group_id TEXT,\n                source_provider TEXT,\n                source_id TEXT,\n                source_url TEXT,\n                model_id TEXT,\n                model_name TEXT,\n                base_model TEXT,\n                cfg_scale REAL,\n                steps INTEGER,\n                sampler TEXT,\n                width INTEGER,\n                height INTEGER,\n                heart_count INTEGER NOT NULL DEFAULT 0,\n                like_count INTEGER NOT NULL DEFAULT 0,\n                laugh_count INTEGER NOT NULL DEFAULT 0,\n                cry_count INTEGER NOT NULL DEFAULT 0,\n                comment_count INTEGER NOT NULL DEFAULT 0,\n                tags TEXT NOT NULL DEFAULT '[]',\n                nsfw INTEGER NOT NULL DEFAULT 0,\n                metadata TEXT NOT NULL DEFAULT '{}',\n                embedded INTEGER NOT NULL DEFAULT 0,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL\n            )",
    "CREATE TABLE providers (\n                id TEXT PRIMARY KEY,\n                model_id TEXT NOT NULL,\n                provider TEXT NOT NULL DEFAULT 'civitai',\n                provider_model_id TEXT,\n                provider_version_id TEXT,\n                name TEXT,\n                description TEXT,\n                tags TEXT,  -- JSON array\n                nsfw BOOLEAN DEFAULT FALSE,\n                download_url TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE,\n                UNIQUE(model_id, provider)\n            )",
    'CREATE TABLE remote_execution_events (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                execution_id TEXT NOT NULL,\n                cursor INTEGER NOT NULL,\n                kind TEXT NOT NULL,\n                pipe_id TEXT,\n                emitted_at TIMESTAMP NOT NULL,\n                received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                payload TEXT NOT NULL,\n                FOREIGN KEY (execution_id) REFERENCES remote_executions(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE remote_executions (\n                id TEXT PRIMARY KEY,\n                generation_id TEXT,\n                provider TEXT NOT NULL,\n                backend_id TEXT,\n                state TEXT NOT NULL,\n                protocol_version INTEGER NOT NULL DEFAULT 1,\n                idempotency_key TEXT NOT NULL,\n                request_digest TEXT NOT NULL,\n                provider_job_id TEXT,\n                worker_id TEXT,\n                event_cursor INTEGER NOT NULL DEFAULT 0,\n                lease_owner TEXT,\n                lease_expires_at_ms INTEGER,\n                lease_epoch INTEGER NOT NULL DEFAULT 0,\n                attempt INTEGER NOT NULL DEFAULT 0,\n                error_code TEXT,\n                error_message TEXT,\n                metadata TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                dispatched_at TIMESTAMP,\n                started_at TIMESTAMP,\n                completed_at TIMESTAMP, expires_at_ms INTEGER, lease_lapses INTEGER NOT NULL DEFAULT 0,\n                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE\n            )',
    "CREATE TABLE saved_segments (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                category_id TEXT NOT NULL,\n                name TEXT NOT NULL COLLATE NOCASE,\n                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),\n                content TEXT NOT NULL DEFAULT '',\n                chips TEXT NOT NULL DEFAULT '{}',\n                is_enabled INTEGER NOT NULL DEFAULT 1,\n                color TEXT,\n                description TEXT,\n                tags TEXT NOT NULL DEFAULT '[]',\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (category_id) REFERENCES segment_categories(id) ON DELETE RESTRICT,\n                UNIQUE (user_id, name)\n            )",
    "CREATE TABLE segment_categories (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                name TEXT NOT NULL COLLATE NOCASE,\n                description TEXT NOT NULL DEFAULT '',\n                color TEXT NOT NULL DEFAULT '#3B82F6',\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                UNIQUE (user_id, name)\n            )",
    "CREATE TABLE segment_template_segments (\n                id TEXT PRIMARY KEY,\n                template_id TEXT NOT NULL,\n                position INTEGER NOT NULL,\n                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),\n                content TEXT NOT NULL DEFAULT '',\n                chips TEXT NOT NULL DEFAULT '{}',\n                is_enabled INTEGER NOT NULL DEFAULT 1,\n                name TEXT,\n                color TEXT,\n                description TEXT,\n                FOREIGN KEY (template_id) REFERENCES segment_templates(id) ON DELETE CASCADE,\n                UNIQUE (template_id, position)\n            )",
    "CREATE TABLE segment_templates (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                name TEXT NOT NULL COLLATE NOCASE,\n                description TEXT NOT NULL DEFAULT '',\n                tags TEXT NOT NULL DEFAULT '[]',\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                UNIQUE (user_id, name)\n            )",
    'CREATE TABLE session_versions (\n                id TEXT PRIMARY KEY,\n                session_id TEXT NOT NULL,\n                version_number INTEGER NOT NULL,\n                payload TEXT NOT NULL,  -- JSON string, snapshot of session.data at save time\n                summary TEXT,           -- denormalized human-relevant label (e.g. preset name)\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,\n                UNIQUE (session_id, version_number)\n            )',
    'CREATE TABLE sessions (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                preset_id TEXT NOT NULL,\n                name TEXT NOT NULL,\n                data TEXT NOT NULL,  -- JSON string containing all session data\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,\n                UNIQUE(user_id, preset_id, name)  -- Unique session names per user per preset\n            )',
    "CREATE TABLE settings (\n                id TEXT PRIMARY KEY,\n                key TEXT UNIQUE NOT NULL,\n                value TEXT NOT NULL,\n                value_type TEXT NOT NULL CHECK (value_type IN ('string', 'integer', 'float', 'boolean', 'json')),\n                description TEXT,\n                type TEXT NOT NULL CHECK (type IN ('USER', 'SYSTEM')),\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )",
    "CREATE TABLE setup_runs (\n                id TEXT PRIMARY KEY,\n                recipe_id TEXT NOT NULL,\n                recipe_version INTEGER NOT NULL DEFAULT 1,\n                scope TEXT NOT NULL DEFAULT 'instance',\n                status TEXT NOT NULL DEFAULT 'pending',\n                current_step TEXT,\n                safe_input TEXT,\n                safe_output TEXT,\n                error_code TEXT,\n                safe_error_detail TEXT,\n                active_marker INTEGER,\n                created_by TEXT,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                completed_at TIMESTAMP\n            )",
    'CREATE TABLE setup_step_attempts (\n                id TEXT PRIMARY KEY,\n                run_id TEXT NOT NULL,\n                step_key TEXT NOT NULL,\n                attempt INTEGER NOT NULL,\n                status TEXT NOT NULL,\n                progress_current INTEGER,\n                progress_total INTEGER,\n                progress_unit TEXT,\n                safe_input TEXT,\n                safe_output TEXT,\n                error_code TEXT,\n                safe_error_detail TEXT,\n                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                finished_at TIMESTAMP,\n                FOREIGN KEY (run_id) REFERENCES setup_runs (id) ON DELETE CASCADE,\n                UNIQUE (run_id, step_key, attempt)\n            )',
    "CREATE TABLE tags (\n                id TEXT PRIMARY KEY,\n                name TEXT NOT NULL ,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , type TEXT NOT NULL DEFAULT 'MODEL', user_id TEXT)",
    'CREATE TABLE tool_governance (\n                llm_config_id TEXT NOT NULL,\n                tool_name TEXT NOT NULL,\n                enabled INTEGER NOT NULL DEFAULT 1,\n                locked INTEGER NOT NULL DEFAULT 0,\n                updated_at TEXT NOT NULL,\n                PRIMARY KEY (llm_config_id, tool_name)\n            )',
    'CREATE TABLE upload_tags (\n                upload_id TEXT NOT NULL,\n                tag_id TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (upload_id, tag_id),\n                FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE,\n                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE\n            )',
    "CREATE TABLE uploads (\n                id TEXT PRIMARY KEY,                    -- ULID primary key\n                user_id TEXT NOT NULL,                  -- Owning user\n                filename TEXT NOT NULL UNIQUE,           -- Unique on-disk name in storage/uploads/\n                original_filename TEXT,                  -- Filename as sent by the browser, for display only\n                media_type TEXT NOT NULL,                -- 'image' | 'video' | 'audio'\n                mime_type TEXT,\n                width INTEGER,\n                height INTEGER,\n                duration_seconds REAL,\n                fps REAL,\n                file_size INTEGER,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, purpose TEXT NOT NULL DEFAULT 'user_upload', thumbnail_small TEXT, thumbnail_medium TEXT, thumbnail_large TEXT,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE\n            )",
    'CREATE TABLE user_disabled_tools (\n                user_id TEXT NOT NULL,\n                tool_name TEXT NOT NULL,\n                created_at TEXT NOT NULL,\n                PRIMARY KEY (user_id, tool_name)\n            )',
    'CREATE TABLE user_group_llms (\n                id TEXT PRIMARY KEY,\n                group_id TEXT NOT NULL,\n                llm_config_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,\n                FOREIGN KEY (llm_config_id) REFERENCES llm_configurations(id) ON DELETE CASCADE,\n                UNIQUE(group_id, llm_config_id)\n            )',
    'CREATE TABLE user_group_members (\n                id TEXT PRIMARY KEY,\n                group_id TEXT NOT NULL,\n                user_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                UNIQUE(group_id, user_id)\n            )',
    'CREATE TABLE user_group_models (\n                id TEXT PRIMARY KEY,\n                group_id TEXT NOT NULL,\n                model_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n                UNIQUE(group_id, model_id)\n            )',
    'CREATE TABLE user_group_presets (\n                id TEXT PRIMARY KEY,\n                group_id TEXT NOT NULL,\n                preset_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,\n                FOREIGN KEY (preset_id) REFERENCES presets(id) ON DELETE CASCADE,\n                UNIQUE(group_id, preset_id)\n            )',
    'CREATE TABLE user_groups (\n                id TEXT PRIMARY KEY,\n                name TEXT UNIQUE NOT NULL,\n                description TEXT,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            , is_system INTEGER NOT NULL DEFAULT 0)',
    "CREATE TABLE user_keybindings (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                user_id TEXT NOT NULL,\n                action_id TEXT NOT NULL,\n                key TEXT,\n                modifiers TEXT DEFAULT '',\n                enabled INTEGER DEFAULT 1,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (action_id) REFERENCES keybinding_defaults(id) ON DELETE CASCADE,\n                UNIQUE(user_id, action_id)\n            )",
    'CREATE TABLE user_llms (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                llm_config_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,\n                FOREIGN KEY (llm_config_id) REFERENCES llm_configurations (id) ON DELETE CASCADE,\n                UNIQUE(user_id, llm_config_id)\n            )',
    'CREATE TABLE user_model_attributes (\n                    user_id TEXT NOT NULL,\n                    model_id TEXT NOT NULL,\n                    key TEXT NOT NULL,\n                    value TEXT,\n                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    UNIQUE(user_id, model_id, key),\n                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE\n                )',
    'CREATE TABLE user_model_meta (\n                user_id TEXT NOT NULL,\n                model_id TEXT NOT NULL,\n                custom_name TEXT,\n                is_favorite INTEGER NOT NULL DEFAULT 0,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY (user_id, model_id),\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE\n            )',
    'CREATE TABLE user_models (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                model_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,\n                UNIQUE(user_id, model_id)\n            )',
    "CREATE TABLE user_onboarding_state (\n                user_id TEXT PRIMARY KEY,\n                version INTEGER NOT NULL DEFAULT 1,\n                status TEXT NOT NULL DEFAULT 'pending',\n                dismissed_at TIMESTAMP,\n                completed_at TIMESTAMP,\n                first_generation_id TEXT,\n                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE\n            )",
    'CREATE TABLE user_presets (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                preset_id TEXT NOT NULL,\n                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,\n                FOREIGN KEY (preset_id) REFERENCES presets (id) ON DELETE CASCADE,\n                UNIQUE(user_id, preset_id)\n            )',
    'CREATE TABLE user_settings (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                setting_id TEXT NOT NULL,\n                value TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,\n                FOREIGN KEY (setting_id) REFERENCES settings (id) ON DELETE CASCADE,\n                UNIQUE(user_id, setting_id)\n            )',
    "CREATE TABLE users (\n                id TEXT PRIMARY KEY,\n                username TEXT UNIQUE NOT NULL,\n                email TEXT UNIQUE NOT NULL,\n                password_hash TEXT NOT NULL,\n                account_type TEXT NOT NULL DEFAULT 'USER',\n                last_login TIMESTAMP,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, avatar_filename TEXT,\n                CHECK (account_type IN ('USER', 'ADMIN'))\n            )",
    'CREATE TABLE workspaces (\n                id TEXT PRIMARY KEY,\n                user_id TEXT NOT NULL,\n                name TEXT NOT NULL,\n                data TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n            )',
]

INDEX_DDL = [
    'CREATE INDEX idx_automation_run_nodes_run\n            ON automation_run_nodes(run_id)',
    'CREATE INDEX idx_automation_runs_automation_started\n            ON automation_runs(automation_id, started_at DESC)',
    'CREATE INDEX idx_automation_runs_status\n            ON automation_runs(status)',
    'CREATE INDEX idx_automations_enabled\n            ON automations(enabled)',
    'CREATE INDEX idx_availability_backend ON model_availability (backend_id)',
    'CREATE INDEX idx_availability_digest\n            ON model_availability (digest)',
    'CREATE INDEX idx_availability_model ON model_availability (model_id)',
    'CREATE UNIQUE INDEX idx_backends_default\n            ON backends (engine)\n            WHERE is_default = 1',
    'CREATE INDEX idx_chat_llm_call_traces_created_at ON chat_llm_call_traces(created_at)',
    'CREATE INDEX idx_chat_llm_call_traces_message ON chat_llm_call_traces(message_id)',
    'CREATE INDEX idx_chat_llm_call_traces_session ON chat_llm_call_traces(session_id, created_at)',
    'CREATE INDEX idx_chat_messages_created ON chat_messages(session_id, created_at)',
    'CREATE INDEX idx_chat_messages_session ON chat_messages(session_id)',
    'CREATE INDEX idx_chat_sessions_created ON chat_sessions(created_at DESC)',
    'CREATE INDEX idx_chat_sessions_status ON chat_sessions(user_id, status)',
    'CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id, mode)',
    'CREATE INDEX idx_collection_generations_collection_id ON collection_generations (collection_id)',
    'CREATE INDEX idx_collection_generations_generation_id ON collection_generations (generation_id)',
    'CREATE INDEX idx_collection_prompts_collection_id ON collection_prompts (collection_id)',
    'CREATE INDEX idx_collection_prompts_prompt_id ON collection_prompts (prompt_id)',
    'CREATE INDEX idx_collection_uploads_collection_id ON collection_uploads (collection_id)',
    'CREATE INDEX idx_collection_uploads_upload_id ON collection_uploads (upload_id)',
    'CREATE INDEX idx_collections_parent_id ON collections (parent_id)',
    'CREATE INDEX idx_collections_scope ON collections (scope)',
    'CREATE INDEX idx_collections_user_id ON collections (user_id)',
    'CREATE INDEX idx_downloads_created_at ON downloads (created_at)',
    'CREATE INDEX idx_downloads_created_by ON downloads (created_by)',
    'CREATE INDEX idx_downloads_group_id ON downloads (group_id)',
    'CREATE INDEX idx_downloads_status ON downloads (status)',
    'CREATE INDEX idx_downloads_type ON downloads (type)',
    'CREATE INDEX idx_enhancement_feedback_message\n            ON enhancement_feedback(message_id)',
    'CREATE INDEX idx_enhancement_feedback_user_model_verdict\n            ON enhancement_feedback(user_id, model_id, verdict)',
    'CREATE INDEX idx_files_created_at ON files (created_at)',
    'CREATE INDEX idx_files_file_type ON files (file_type)',
    'CREATE INDEX idx_files_hash ON files(hash)',
    'CREATE INDEX idx_files_mime_type ON files (mime_type)',
    'CREATE INDEX idx_files_user_id ON files (user_id)',
    'CREATE INDEX idx_generation_files_new_file_id ON "generation_files" (file_id)',
    'CREATE INDEX idx_generation_files_new_generation_id ON "generation_files" (generation_id)',
    'CREATE INDEX idx_generation_models_generation_id ON generation_models (generation_id)',
    'CREATE INDEX idx_generation_models_model_id ON generation_models (model_id)',
    'CREATE INDEX idx_generation_parameters_generation_id ON generation_parameters (generation_id)',
    'CREATE INDEX idx_generation_parameters_index ON generation_parameters (parameter_index)',
    'CREATE INDEX idx_generation_parameters_name ON generation_parameters (parameter_name)',
    'CREATE INDEX idx_generation_segment_phrasebook_generation ON generation_segment_phrasebook(generation_id)',
    'CREATE INDEX idx_generation_segment_phrasebook_segment ON generation_segment_phrasebook(segment_id)',
    'CREATE INDEX idx_generation_segment_phrasebook_value ON generation_segment_phrasebook(phrasebook_value_id)',
    'CREATE INDEX idx_generation_segments_generation ON generation_segments(generation_id)',
    'CREATE INDEX idx_generation_sources_generation_id\n            ON generation_sources(generation_id)',
    'CREATE INDEX idx_generation_sources_source_generation_id\n            ON generation_sources(source_generation_id)',
    'CREATE INDEX idx_generation_stats_created_at ON generation_stats (created_at)',
    'CREATE INDEX idx_generation_stats_preset ON generation_stats (preset_id, cold_start)',
    'CREATE INDEX idx_generation_tags_generation_id\n            ON generation_tags(generation_id)',
    'CREATE INDEX idx_generation_tags_tag_id\n            ON generation_tags(tag_id)',
    'CREATE INDEX idx_generations_backend_id ON generations(backend_id)',
    'CREATE INDEX idx_generations_preset_id\n            ON generations(preset_id)',
    'CREATE INDEX idx_generations_source_prompt_id ON generations(source_prompt_id)',
    'CREATE INDEX idx_generations_status\n            ON generations(status)',
    'CREATE INDEX idx_generations_user_id\n            ON generations(user_id)',
    'CREATE INDEX idx_generations_user_tab_status\n            ON generations (user_id, tab_id, status)',
    'CREATE INDEX idx_inspiration_collection_items_collection_id ON inspiration_collection_items (collection_id)',
    'CREATE INDEX idx_inspiration_collection_items_inspiration_id ON inspiration_collection_items (inspiration_id)',
    'CREATE INDEX idx_inspiration_collections_parent_id ON inspiration_collections (parent_id)',
    'CREATE INDEX idx_inspiration_collections_user_id ON inspiration_collections (user_id)',
    'CREATE INDEX idx_inspiration_comments_inspiration_id ON inspiration_comments (inspiration_id)',
    'CREATE INDEX idx_inspiration_saves_inspiration_id ON inspiration_saves (inspiration_id)',
    'CREATE INDEX idx_inspiration_saves_user_id ON inspiration_saves (user_id)',
    'CREATE INDEX idx_inspirations_created_at ON inspirations (created_at)',
    'CREATE INDEX idx_inspirations_user_id ON inspirations (user_id)',
    'CREATE INDEX idx_llm_commands_enabled ON llm_commands(enabled)',
    'CREATE INDEX idx_llm_configurations_enabled ON llm_configurations(enabled)',
    'CREATE INDEX idx_llm_configurations_vision\n            ON llm_configurations(supports_vision)',
    "CREATE UNIQUE INDEX idx_llm_memory_key_scope\n            ON llm_memory (user_id, key, scope, COALESCE(scope_ref, ''))",
    'CREATE INDEX idx_llm_memory_user_scope\n            ON llm_memory (user_id, scope)',
    'CREATE INDEX idx_llm_memory_user_scope_ref\n            ON llm_memory (user_id, scope, scope_ref)',
    'CREATE INDEX idx_mcp_tokens_hash ON mcp_tokens(token_hash)',
    'CREATE INDEX idx_mcp_tokens_user ON mcp_tokens(user_id)',
    'CREATE INDEX idx_media_index_queue_drain ON media_index_queue (pass_type, status, created_at)',
    'CREATE INDEX idx_media_system_tags_file ON media_system_tags (file_id)',
    'CREATE INDEX idx_media_system_tags_generation ON media_system_tags (generation_id)',
    'CREATE INDEX idx_media_system_tags_provenance ON media_system_tags (provenance)',
    'CREATE INDEX idx_media_system_tags_tag ON media_system_tags (tag)',
    'CREATE INDEX idx_model_attribute_definitions_source ON model_attribute_definitions (source)',
    'CREATE INDEX idx_model_collection_members_collection_id ON model_collection_members (collection_id)',
    'CREATE INDEX idx_model_collection_members_model_id ON model_collection_members (model_id)',
    'CREATE INDEX idx_model_collections_parent_id ON model_collections (parent_id)',
    'CREATE INDEX idx_model_collections_user_id ON model_collections (user_id)',
    'CREATE INDEX idx_model_files_file_id ON model_files (file_id)',
    'CREATE INDEX idx_model_files_model_id ON model_files (model_id)',
    'CREATE INDEX idx_model_files_model_type ON model_files (model_id, file_type)',
    'CREATE INDEX idx_model_files_type ON model_files (file_type)',
    'CREATE INDEX idx_model_preview_media_model\n            ON model_preview_media (model_id, position)',
    'CREATE INDEX idx_model_tags_model_id \n            ON model_tags(model_id)',
    'CREATE INDEX idx_model_tags_tag_id \n            ON model_tags(tag_id)',
    'CREATE INDEX idx_models_filename ON models (filename)',
    'CREATE INDEX idx_models_indexed_at ON models (indexed_at)',
    'CREATE INDEX idx_models_model_type ON models (model_type)',
    'CREATE INDEX idx_models_sha256 ON models (sha256)',
    'CREATE INDEX idx_notifications_user_created\n            ON notifications(user_id, id DESC)',
    'CREATE INDEX idx_notifications_user_read\n            ON notifications(user_id, read)',
    'CREATE INDEX idx_phrasebook_categories_is_active ON phrasebook_categories(is_active)',
    'CREATE INDEX idx_phrasebook_categories_parent_id ON phrasebook_categories(parent_id)',
    'CREATE INDEX idx_phrasebook_categories_path ON phrasebook_categories(path)',
    'CREATE INDEX idx_phrasebook_categories_user_id ON phrasebook_categories(user_id)',
    'CREATE INDEX idx_phrasebook_values_category_id ON phrasebook_values(category_id)',
    'CREATE INDEX idx_phrasebook_values_is_active ON phrasebook_values(is_active)',
    'CREATE INDEX idx_phrasebook_values_preview_file_id ON phrasebook_values(preview_file_id)',
    'CREATE INDEX idx_phrasebook_values_sort_order ON phrasebook_values(sort_order)',
    'CREATE INDEX idx_phrasebook_values_user_id ON phrasebook_values(user_id)',
    'CREATE INDEX idx_plugin_hooks_hook_name ON plugin_hooks (hook_name)',
    'CREATE INDEX idx_plugin_hooks_hook_type ON plugin_hooks (hook_type)',
    'CREATE INDEX idx_plugin_hooks_plugin_id ON plugin_hooks (plugin_id)',
    'CREATE INDEX idx_plugin_setting_audit_plugin ON plugin_setting_audit (plugin_id, changed_at)',
    'CREATE INDEX idx_plugin_settings_key ON plugin_settings (setting_key)',
    'CREATE INDEX idx_plugin_settings_plugin_id ON plugin_settings (plugin_id)',
    'CREATE INDEX idx_plugin_settings_user_id ON plugin_settings (user_id)',
    'CREATE INDEX idx_plugins_enabled ON plugins (enabled)',
    'CREATE INDEX idx_plugins_name ON plugins (name)',
    'CREATE INDEX idx_plugins_type ON plugins (type)',
    'CREATE INDEX idx_presets_installed_at ON presets (installed_at)',
    'CREATE INDEX idx_presets_preset_id ON presets (preset_id)',
    'CREATE INDEX idx_prompt_segments_parent ON prompt_segments(prompt_id, position)',
    'CREATE INDEX idx_prompts_flattened ON prompts(user_id, flattened_text)',
    'CREATE INDEX idx_prompts_model ON prompts(user_id, model_id)',
    'CREATE UNIQUE INDEX idx_prompts_source\n            ON prompts(user_id, source_provider, source_id, usage_hint)\n            WHERE source_id IS NOT NULL AND usage_hint IS NOT NULL',
    'CREATE INDEX idx_prompts_user_created ON prompts(user_id, created_at DESC)',
    'CREATE INDEX idx_providers_model_id ON providers (model_id)',
    'CREATE INDEX idx_providers_model_provider ON providers (model_id, provider)',
    'CREATE INDEX idx_providers_provider ON providers (provider)',
    'CREATE INDEX idx_providers_provider_model_id ON providers (provider_model_id)',
    'CREATE UNIQUE INDEX idx_remote_execution_events_cursor\n            ON remote_execution_events(execution_id, cursor)',
    'CREATE INDEX idx_remote_executions_expires_at\n            ON remote_executions(state, expires_at_ms)',
    'CREATE INDEX idx_remote_executions_generation_id\n            ON remote_executions(generation_id)',
    'CREATE UNIQUE INDEX idx_remote_executions_idempotency_key\n            ON remote_executions(idempotency_key)',
    'CREATE INDEX idx_remote_executions_lease\n            ON remote_executions(state, lease_expires_at_ms)',
    'CREATE UNIQUE INDEX idx_remote_executions_provider_job\n            ON remote_executions(provider, provider_job_id)\n            WHERE provider_job_id IS NOT NULL',
    'CREATE INDEX idx_remote_executions_state\n            ON remote_executions(state, created_at)',
    'CREATE INDEX idx_saved_segments_category ON saved_segments(user_id, category_id)',
    'CREATE INDEX idx_session_versions_session ON session_versions (session_id, version_number)',
    'CREATE INDEX idx_sessions_user_preset \n            ON sessions (user_id, preset_id)',
    'CREATE INDEX idx_settings_created_at ON settings (created_at)',
    'CREATE INDEX idx_settings_key ON settings (key)',
    'CREATE INDEX idx_settings_type ON settings (type)',
    'CREATE INDEX idx_setup_runs_status ON setup_runs (status, created_at)',
    'CREATE INDEX idx_setup_step_attempts_run ON setup_step_attempts (run_id, step_key, attempt)',
    'CREATE INDEX idx_tags_name \n            ON tags(name)',
    "CREATE UNIQUE INDEX idx_tags_name_type_user\n            ON tags(name, type, COALESCE(user_id, ''))",
    'CREATE INDEX idx_tags_type_user\n            ON tags(type, user_id)',
    'CREATE INDEX idx_tags_user_id ON tags(user_id)',
    'CREATE INDEX idx_template_segments_parent ON segment_template_segments(template_id, position)',
    'CREATE INDEX idx_upload_tags_tag_id ON upload_tags (tag_id)',
    'CREATE INDEX idx_upload_tags_upload_id ON upload_tags (upload_id)',
    'CREATE INDEX idx_uploads_user_created ON uploads (user_id, created_at)',
    'CREATE INDEX idx_uploads_user_media_type ON uploads (user_id, media_type)',
    'CREATE INDEX idx_user_disabled_tools_user ON user_disabled_tools(user_id)',
    'CREATE INDEX idx_user_group_llms_group_id ON user_group_llms (group_id)',
    'CREATE INDEX idx_user_group_llms_llm_config_id ON user_group_llms (llm_config_id)',
    'CREATE INDEX idx_user_group_members_group_id ON user_group_members (group_id)',
    'CREATE INDEX idx_user_group_members_user_id ON user_group_members (user_id)',
    'CREATE INDEX idx_user_group_models_group_id ON user_group_models (group_id)',
    'CREATE INDEX idx_user_group_models_model_id ON user_group_models (model_id)',
    'CREATE INDEX idx_user_group_presets_group_id ON user_group_presets (group_id)',
    'CREATE INDEX idx_user_group_presets_preset_id ON user_group_presets (preset_id)',
    'CREATE INDEX idx_user_groups_name ON user_groups (name)',
    'CREATE INDEX idx_user_keybindings_user_id ON user_keybindings(user_id)',
    'CREATE INDEX idx_user_llms_assigned_at ON user_llms (assigned_at)',
    'CREATE INDEX idx_user_llms_llm_config_id ON user_llms (llm_config_id)',
    'CREATE INDEX idx_user_llms_user_id ON user_llms (user_id)',
    'CREATE INDEX idx_user_model_attributes_user_model ON user_model_attributes (user_id, model_id)',
    'CREATE INDEX idx_user_model_meta_fav ON user_model_meta (user_id, is_favorite)',
    'CREATE INDEX idx_user_models_model_id ON user_models (model_id)',
    'CREATE INDEX idx_user_models_user_id ON user_models (user_id)',
    'CREATE INDEX idx_user_presets_assigned_at ON user_presets (assigned_at)',
    'CREATE INDEX idx_user_presets_preset_id ON user_presets (preset_id)',
    'CREATE INDEX idx_user_presets_user_id ON user_presets (user_id)',
    'CREATE INDEX idx_user_settings_created_at ON user_settings (created_at)',
    'CREATE INDEX idx_user_settings_setting_id ON user_settings (setting_id)',
    'CREATE INDEX idx_user_settings_user_id ON user_settings (user_id)',
    'CREATE INDEX idx_users_email ON users (email)',
    'CREATE INDEX idx_users_username ON users (username)',
    'CREATE INDEX idx_workspaces_user\n            ON workspaces(user_id)',
    'CREATE UNIQUE INDEX uq_setup_runs_single_active ON setup_runs (active_marker)',
]

TRIGGER_DDL = [
    'CREATE TRIGGER update_backends_updated_at \n            AFTER UPDATE ON backends \n            FOR EACH ROW \n            BEGIN \n                UPDATE backends SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_chat_sessions_updated_at\n            AFTER UPDATE ON chat_sessions\n            FOR EACH ROW\n            BEGIN\n                UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_configurations_updated_at \n            AFTER UPDATE ON configurations \n            FOR EACH ROW \n            BEGIN \n                UPDATE configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_generations_updated_at\n    AFTER UPDATE ON generations\n    FOR EACH ROW\n    BEGIN\n        UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n    END',
    'CREATE TRIGGER update_llm_commands_updated_at \n            AFTER UPDATE ON llm_commands \n            FOR EACH ROW \n            BEGIN \n                UPDATE llm_commands SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_llm_configurations_updated_at \n            AFTER UPDATE ON llm_configurations \n            FOR EACH ROW \n            BEGIN \n                UPDATE llm_configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_phrasebook_categories_updated_at\n                AFTER UPDATE ON phrasebook_categories\n                FOR EACH ROW\n                BEGIN\n                    UPDATE phrasebook_categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n                END',
    'CREATE TRIGGER update_phrasebook_values_updated_at\n                AFTER UPDATE ON phrasebook_values\n                FOR EACH ROW\n                BEGIN\n                    UPDATE phrasebook_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n                END',
    'CREATE TRIGGER update_plugins_updated_at\n            AFTER UPDATE ON plugins\n            FOR EACH ROW\n            BEGIN\n                UPDATE plugins SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_presets_updated_at \n            AFTER UPDATE ON presets \n            FOR EACH ROW \n            BEGIN \n                UPDATE presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_providers_updated_at\n            AFTER UPDATE ON providers\n            FOR EACH ROW\n            BEGIN\n                UPDATE providers SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_settings_updated_at \n            AFTER UPDATE ON settings \n            FOR EACH ROW \n            BEGIN \n                UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_group_llms_updated_at\n            AFTER UPDATE ON user_group_llms\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_group_llms SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_group_members_updated_at\n            AFTER UPDATE ON user_group_members\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_group_members SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_group_models_updated_at\n            AFTER UPDATE ON user_group_models\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_group_models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_group_presets_updated_at\n            AFTER UPDATE ON user_group_presets\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_group_presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_groups_updated_at\n            AFTER UPDATE ON user_groups\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_llms_updated_at\n            AFTER UPDATE ON user_llms\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_llms SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_models_updated_at\n            AFTER UPDATE ON user_models\n            FOR EACH ROW\n            BEGIN\n                UPDATE user_models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_presets_updated_at \n            AFTER UPDATE ON user_presets \n            FOR EACH ROW \n            BEGIN \n                UPDATE user_presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_user_settings_updated_at \n            AFTER UPDATE ON user_settings \n            FOR EACH ROW \n            BEGIN \n                UPDATE user_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
    'CREATE TRIGGER update_users_updated_at \n            AFTER UPDATE ON users \n            FOR EACH ROW \n            BEGIN \n                UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;\n            END',
]

KEYBINDING_ROWS = [('show_help', '?', '', 'Show Keyboard Shortcuts', 'general', 'global', 'Display all available keyboard shortcuts', 0), ('open_chat', 'c', '', 'Open AI Chat', 'general', 'global', 'Toggle the AI chat panel', 1), ('toggle_sidebar', 'b', '', 'Toggle Sidebar', 'general', 'global', 'Show or hide the sidebar', 3), ('open_quick_actions', 'a', '', 'Open Quick Actions', 'general', 'global', 'Open the plugin quick-actions palette', 4), ('start_generation', 'g', '', 'Start Generation', 'generation', 'generate', 'Start image generation', 10), ('new_tab', 't', '', 'New Tab', 'generation', 'generate', 'Open a new generation tab', 11), ('close_tab', 'x', '', 'Close Tab', 'generation', 'generate', 'Close current generation tab', 12), ('toggle_left_panel', 'f', '', 'Toggle Form Panel', 'generation', 'generate', 'Fold or unfold the left generation form panel', 13), ('go_generate', '1', '', 'Go to Generate', 'navigation', 'global', 'Navigate to Generate page', 20), ('go_history', '2', '', 'Go to History', 'navigation', 'global', 'Navigate to History page', 21), ('go_library', '3', '', 'Go to Library', 'navigation', 'global', 'Navigate to Library page', 22), ('go_models', '4', '', 'Go to Models', 'navigation', 'global', 'Navigate to Models page', 23), ('go_phrasebook', '5', '', 'Go to Phrasebook', 'navigation', 'global', 'Navigate to Phrasebook page', 24), ('go_prompts', '6', '', 'Go to Prompts', 'navigation', 'global', 'Navigate to Prompts page', 25), ('go_inspirations', '7', '', 'Go to Inspirations', 'navigation', 'global', 'Navigate to Inspirations page', 26)]

USER_GROUP_ROWS = [('all_admins', 'All Admins', "Every administrator on this instance. Built in - can't be deleted."), ('all_users', 'All Users', "Every account on this instance. Built in - can't be deleted.")]

SETTINGS_ROWS = [('file_storage_directory', 'storage', 'string', 'SYSTEM', 'Base directory for all file storage (generations, tmp, models)', None), ('models_dir', 'models', 'string', 'SYSTEM', 'Directory path for storing models', 'setting_models_dir'), ('cache_dir', 'cache', 'string', 'SYSTEM', 'Directory path for caching temporary files', 'setting_cache_dir'), ('output_directory', 'outputs', 'string', 'SYSTEM', 'DEPRECATED: Use file_storage_directory instead. Directory where generated images and files are stored', 'setting_output_directory'), ('temp_directory', 'outputs/tmp', 'string', 'SYSTEM', 'DEPRECATED: Use file_storage_directory instead. Directory where temporary files are stored', 'setting_temp_directory'), ('storage_backend', 'local', 'string', 'SYSTEM', "Where new file-storage writes go: 'local' (default) or 's3'.", None), ('s3_bucket', '', 'string', 'SYSTEM', 'S3 bucket name for the optional S3 storage backend.', None), ('s3_region', 'us-east-1', 'string', 'SYSTEM', 'S3 region.', None), ('s3_endpoint_url', '', 'string', 'SYSTEM', 'S3-compatible endpoint URL (MinIO, Cloudflare R2, ...). Empty uses AWS S3.', None), ('s3_access_key_id', '', 'string', 'SYSTEM', 'S3 access key ID.', None), ('s3_secret_key', '', 'string', 'SYSTEM', 'S3 secret access key, stored encrypted.', None), ('s3_path_style', 'false', 'boolean', 'SYSTEM', 'Use path-style addressing (bucket in the URL path). Required by most non-AWS S3-compatible services.', None), ('s3_prefix', '', 'string', 'SYSTEM', 'Key prefix inside the S3 bucket (no leading/trailing slash).', None), ('nsfw_filter', 'false', 'boolean', 'USER', 'Allow NSFW content generation', 'setting_nsfw_filter'), ('media_nsfw_filter_mode', 'blur', 'string', 'USER', 'How gallery media rated NSFW by the tagger is shown: blur, show, or hide. Per-user preference.', None), ('media_nsfw_blur_threshold', '0.6', 'float', 'SYSTEM', 'Blur a gallery item when its questionable + explicit rating scores reach this value.', None), ('native_attention_backend', '', 'string', 'SYSTEM', 'Pinned attention backend for the native engine (empty = auto): sdpa, sage, sage2, or flash', None), ('native_torch_compile', '', 'string', 'SYSTEM', 'Regional torch.compile for the native engine (empty = follow $NATIVE_TORCH_COMPILE): on or off', None), ('native_stream_prefetch', '', 'string', 'SYSTEM', 'Streaming layer prefetch under partial residency (empty = follow $NATIVE_STREAM_PREFETCH): on or off', None), ('model_cache_scope', 'preset', 'string', 'SYSTEM', "How the native model RAM cache is scoped across preset switches: preset (evict the previous preset's models on switch) or global (keep all cached until RAM pressure)", None), ('media_tagger_model', 'SmilingWolf/wd-vit-tagger-v3', 'string', 'SYSTEM', 'Hugging Face model id of the local WD tagger that produces system tags.', None), ('media_tagger_device', 'cpu', 'string', 'SYSTEM', 'Device the tagger model runs on.', None), ('media_tagger_auto_download', 'false', 'boolean', 'SYSTEM', 'Whether the tagger may download its weights from Hugging Face Hub on first use.', None), ('media_tagger_tag_threshold', '0.35', 'float', 'SYSTEM', 'Minimum confidence for a general tag to be stored as a system tag.', None), ('media_tagger_character_threshold', '0.75', 'float', 'SYSTEM', 'Minimum confidence for a character tag to be stored as a system tag.', None), ('media_vision_model', 'google/siglip-base-patch16-224', 'string', 'SYSTEM', 'Hugging Face model id of the SigLIP checkpoint used for gallery visual search.', None), ('media_vision_device', 'cpu', 'string', 'SYSTEM', 'Device the gallery vision embedder runs on.', None), ('media_vision_auto_download', 'false', 'boolean', 'SYSTEM', 'Whether the gallery vision embedder may download its weights from Hugging Face Hub on first use.', None), ('prompt_embedding_provider', 'local', 'string', 'SYSTEM', "Prompt-embedding backend for semantic prompt search: 'local' (in-process transformers, no external service) or 'ollama'.", None), ('prompt_embedding_model', 'BAAI/bge-small-en-v1.5', 'string', 'SYSTEM', 'Hugging Face model id used by the local prompt-embedding provider.', None), ('prompt_embedding_device', 'cpu', 'string', 'SYSTEM', 'Device the local prompt-embedding model runs on.', None), ('prompt_embedding_auto_download', 'false', 'boolean', 'SYSTEM', 'Whether the local prompt-embedding provider may download its model weights from Hugging Face Hub on first use.', None), ('prompt_embedding_ollama_base_url', 'http://localhost:11434', 'string', 'SYSTEM', "Base URL of the Ollama server used when prompt_embedding_provider is 'ollama'.", None), ('prompt_embedding_ollama_model', 'nomic-embed-text', 'string', 'SYSTEM', "Ollama model used when prompt_embedding_provider is 'ollama'.", None), ('registration_policy', 'closed', 'string', 'SYSTEM', "Whether new-account registration is accepted once the instance has an owner: 'closed' (default, invitation-only) or 'open' (anyone may register). Ignored while the instance is unclaimed.", None), ('mcp_enabled', 'false', 'boolean', 'SYSTEM', 'Whether the MCP (Model Context Protocol) server endpoint is reachable at all.', None), ('mcp_user_enabled', 'true', 'boolean', 'USER', "Whether this user's MCP tokens are allowed to authenticate. Admin-controlled per user.", None), ('chat_llm_call_tracing', 'true', 'boolean', 'SYSTEM', 'Persist every LLM call made during chat (exact request/response, provider/model, tokens, timing) for the admin session-debug viewer', None), ('notification_preferences', '{}', 'json', 'USER', 'Per-user notification preferences (enabled types + sound)', 'setting_notification_preferences')]


def up():
    with db.get_cursor() as cursor:
        if _has_pre_squash_history(cursor):
            print(
                "Migration 001_baseline: pre-squash history detected in "
                "applied_migrations, schema already exists - nothing to do"
            )
            return

        # journal_mode is the one WAL-related pragma that persists in the
        # database file itself (see module docstring) - everything else the
        # old 015_enable_wal_mode.py set was connection-scoped and is not
        # ported.
        cursor.execute("PRAGMA journal_mode=WAL")

        for statement in TABLE_DDL:
            cursor.execute(statement)
        for statement in INDEX_DDL:
            cursor.execute(statement)
        for statement in TRIGGER_DDL:
            cursor.execute(statement)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        cursor.executemany(
            """
            INSERT INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, enabled, source, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'system', ?)
            """,
            KEYBINDING_ROWS,
        )

        cursor.executemany(
            """
            INSERT INTO user_groups (id, name, description, created_at, updated_at, is_system)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            [(gid, name, description, now, now) for gid, name, description in USER_GROUP_ROWS],
        )

        cursor.executemany(
            """
            INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (explicit_id or _settings_id(), key, value, value_type, description, setting_type, now, now)
                for key, value, value_type, setting_type, description, explicit_id in SETTINGS_ROWS
            ],
        )

        print(
            f"Migration 001_baseline: created {len(TABLE_DDL)} tables, "
            f"{len(INDEX_DDL)} indexes, {len(TRIGGER_DDL)} triggers; "
            f"seeded {len(SETTINGS_ROWS)} settings, {len(KEYBINDING_ROWS)} "
            f"keybinding defaults, {len(USER_GROUP_ROWS)} user groups"
        )


def down():
    print("Migration 001_baseline: no-op (the baseline is not revertible)")

