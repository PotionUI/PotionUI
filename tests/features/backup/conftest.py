"""A scratch install to back up: a real SQLite database and a real storage tree.

Nothing here touches the live database or the live storage directory - every
fixture is rooted in `tmp_path`, and `POTIONUI_DB_PATH` is pointed at it.
"""

import sqlite3
from pathlib import Path

import pytest

from src.platform.database.migration_runner import MigrationRunner

DAY = "2026-09-09"
GENERATION_ID = "01GEN"

SCHEMA = (
    """CREATE TABLE settings (
        id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, value TEXT NOT NULL,
        value_type TEXT NOT NULL, type TEXT NOT NULL
    )""",
    "CREATE TABLE applied_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, migration_name TEXT UNIQUE NOT NULL)",
    "CREATE TABLE generations (id TEXT PRIMARY KEY, user_id TEXT, status TEXT)",
    """CREATE TABLE files (
        id TEXT PRIMARY KEY, file_path TEXT NOT NULL, file_type TEXT,
        thumbnail_small TEXT, thumbnail_medium TEXT, thumbnail_large TEXT
    )""",
    "CREATE TABLE generation_files (id TEXT PRIMARY KEY, generation_id TEXT, file_id TEXT)",
    """CREATE TABLE uploads (
        id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT,
        thumbnail_small TEXT, thumbnail_medium TEXT, thumbnail_large TEXT
    )""",
)


def newest_available_migration() -> str:
    return MigrationRunner().get_available_migrations()[-1]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL").close()
    return conn


def set_setting(db_path: Path, key: str, value: str) -> None:
    conn = connect(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (id, key, value, value_type, type) "
            "VALUES (?, ?, ?, 'string', 'SYSTEM')",
            (key, key, value),
        )
    conn.close()


def build_database(db_path: Path, *, migration: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    with conn:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT INTO applied_migrations (migration_name) VALUES (?)", (migration,))
        conn.execute(
            "INSERT INTO generations (id, user_id, status) VALUES (?, 'u', 'completed')",
            (GENERATION_ID,),
        )
        conn.execute(
            "INSERT INTO files (id, file_path, file_type, thumbnail_small) VALUES (?, ?, ?, ?)",
            (
                "01FILE",
                f"generations/{DAY}/{GENERATION_ID}/0.png",
                "image",
                f"generations/{DAY}/{GENERATION_ID}/0_small.webp",
            ),
        )
        conn.execute(
            "INSERT INTO files (id, file_path, file_type, thumbnail_small) VALUES (?, ?, ?, ?)",
            (
                "01VID",
                f"generations/{DAY}/{GENERATION_ID}/1.mp4",
                "video",
                f"generations/{DAY}/{GENERATION_ID}/1_small.webp",
            ),
        )
        conn.executemany(
            "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
            [("01GF1", GENERATION_ID, "01FILE"), ("01GF2", GENERATION_ID, "01VID")],
        )
        conn.execute(
            "INSERT INTO uploads (id, filename, media_type, thumbnail_small) VALUES (?, ?, ?, ?)",
            ("01UP", "up1.png", "image", "thumbnails/up1_small.webp"),
        )
    conn.close()


def build_storage(storage: Path) -> None:
    generation = storage / "generations" / DAY / GENERATION_ID
    generation.mkdir(parents=True, exist_ok=True)
    (generation / "0.png").write_bytes(b"original-image")
    (generation / "0_small.webp").write_bytes(b"thumb")
    (generation / "1.mp4").write_bytes(b"original-video")
    (generation / "1_small.webp").write_bytes(b"video-thumb")
    (generation / "1_small_animated.webp").write_bytes(b"animated" * 50)

    uploads = storage / "uploads" / "thumbnails"
    uploads.mkdir(parents=True, exist_ok=True)
    (storage / "uploads" / "up1.png").write_bytes(b"upload")
    (uploads / "up1_small.webp").write_bytes(b"upload-thumb")

    (storage / "secret.key").write_text("# key\nQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWY=\n")
    (storage / "llm.yml").write_text("models: []\n")
    (storage / "saved_prompts.json").write_text("[]\n")
    (storage / "avatars").mkdir(exist_ok=True)
    (storage / "avatars" / "a.png").write_bytes(b"avatar")

    # Everything below must never reach an archive.
    (storage / "tmp").mkdir(exist_ok=True)
    (storage / "tmp" / "scratch.bin").write_bytes(b"scratch")
    (storage / "chromadb").mkdir(exist_ok=True)
    (storage / "chromadb" / "index").write_bytes(b"index")
    (storage / "settings.json").write_text("{}")
    (storage / "model_hash_cache.json").write_text("{}")
    (storage / "db.sqlite.bak-20260101-000000").write_bytes(b"stale")


@pytest.fixture
def install(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "install"
    storage = root / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)

    for tree in ("presets", "plugins", "automation"):
        local = root / "content" / tree / "local"
        local.mkdir(parents=True, exist_ok=True)
        (local / "example.txt").write_text(f"{tree}\n")
    (root / ".env").write_text("POTIONUI_EXAMPLE=1\n")
    (root / "models" / "checkpoints").mkdir(parents=True, exist_ok=True)
    (root / "models" / "checkpoints" / "m.safetensors").write_bytes(b"weights")

    monkeypatch.setenv("POTIONUI_DB_PATH", str(storage / "db.sqlite"))
    monkeypatch.delenv("POTIONUI_SECRET_KEY_FILE", raising=False)
    return root


@pytest.fixture
def empty_install(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "target"
    (root / "storage").mkdir(parents=True)
    monkeypatch.setenv("POTIONUI_DB_PATH", str(root / "storage" / "db.sqlite"))
    monkeypatch.delenv("POTIONUI_SECRET_KEY_FILE", raising=False)
    return root
