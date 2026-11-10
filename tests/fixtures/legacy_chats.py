import json
import shutil
from unittest.mock import patch

import pytest

from src.features.llm.harness.store import step_from_row
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from tests.fixtures.db_template import build_template
from tests.fixtures.persistence_base import make_lean_file_database
from tests.platform.database._support import load_migration

NAME = "068_chat_steps"


@pytest.fixture(scope="module")
def previous_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("pre068") / "pre068.sqlite"
    available = MigrationRunner.get_available_migrations
    with patch.object(
        MigrationRunner, "get_available_migrations", lambda self: [name for name in available(self) if name < NAME]
    ):
        build_template(path)
    return path


@pytest.fixture
def env(previous_template, tmp_path):
    Database._instance = None
    database = Database()
    database.db_path = tmp_path / "test.sqlite"
    database._initialized = True
    shutil.copyfile(previous_template, database.db_path)
    make_lean_file_database(database)
    yield database, load_migration(NAME, database)
    Database._instance = None


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-20T10:{self.tick // 60:02d}:{self.tick % 60:02d}+00:00"


class Chats:
    def __init__(self, database):
        self.database = database
        self.clock = Clock()

    def session(self, session_id, config="cfg", mode="assistant"):
        with self.database.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_sessions (id, user_id, mode, name, status, llm_config_id) "
                "VALUES (?, 'u1', ?, 'chat', 'active', ?)",
                (session_id, mode, config),
            )
        return session_id

    def row(self, session_id, role, content, metadata=None, row_id=None, raw_metadata=None):
        row_id = row_id or f"{session_id}-m{self.clock.tick + 1}"
        stored = raw_metadata if raw_metadata is not None else (json.dumps(metadata) if metadata else None)
        with self.database.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (row_id, session_id, role, content, stored, self.clock()),
            )
        return row_id

    def turns(self, session_id):
        with self.database.get_cursor() as cursor:
            cursor.execute("SELECT * FROM chat_turns WHERE session_id = ? ORDER BY created_at, rowid", (session_id,))
            return cursor.fetchall()

    def steps(self, session_id):
        with self.database.get_cursor() as cursor:
            cursor.execute("SELECT * FROM chat_steps WHERE session_id = ? ORDER BY seq", (session_id,))
            return [step_from_row(row) for row in cursor.fetchall()]

    def message(self, row_id):
        with self.database.get_cursor() as cursor:
            cursor.execute("SELECT * FROM chat_messages WHERE id = ?", (row_id,))
            row = cursor.fetchone()
        return row["turn_id"], json.loads(row["metadata"]) if row["metadata"] else None

    def turn_of(self, row_id):
        with self.database.get_cursor() as cursor:
            cursor.execute("SELECT turn_id FROM chat_messages WHERE id = ?", (row_id,))
            return cursor.fetchone()["turn_id"]

    def count(self, table):
        with self.database.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            return cursor.fetchone()[0]


@pytest.fixture
def chats(env):
    database, _ = env
    return Chats(database)


def execution(name, call_id, args=None, data="", status=None, success=True, error=None, skipped=False, preview=None, reason=None):
    return {
        "tool_name": name,
        "arguments": args or {},
        "result": {"success": success, "data": data, "error": error},
        "duration_ms": 5,
        "approval_status": status,
        "approval_reason": reason,
        "skipped": skipped,
        "preview": preview,
        "tool_call_id": call_id,
    }
