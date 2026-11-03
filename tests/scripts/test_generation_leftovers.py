import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tests.fixtures.db_template import copy_template_db

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generation_leftovers.py"

LIVE = "01LIVEGENERATION0000000000"
GONE = "01GONEGENERATION0000000000"
SHARED = "01SHAREDGENERATION00000000"
DAY = "2026-10-28"


def _load():
    spec = importlib.util.spec_from_file_location("generation_leftovers_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(storage, key, data=b"x"):
    path = storage / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def install(tmp_path):
    storage = tmp_path / "storage"
    database = storage / "db.sqlite"
    copy_template_db(database)
    connection = sqlite3.connect(database)
    run = connection.execute
    run("INSERT INTO users (id, username, email, password_hash) VALUES ('u', 'u', 'u@example.com', 'h')")
    run("INSERT INTO generations (id, preset_id, form_data, user_id, status) VALUES (?, 'p', '{}', 'u', 'completed')", (LIVE,))
    for file_id, key, thumbnail in (
        ("live-file", f"generations/{DAY}/{LIVE}/1.mp4", "thumbnails/1_medium.jpg"),
        ("gone-file", f"generations/{DAY}/{GONE}/1.mp4", "thumbnails/1_medium.jpg"),
        ("preview-file", f"generations/{DAY}/{SHARED}/1.png", None),
        ("phrasebook-file", f"generations/{DAY}/{SHARED}/3.png", None),
    ):
        run(
            "INSERT INTO files (id, file_path, file_type, user_id, thumbnail_medium) VALUES (?, ?, 'VIDEO', 'u', ?)",
            (file_id, key, thumbnail),
        )
    run("INSERT INTO generation_files (id, generation_id, file_id) VALUES ('gf', ?, 'live-file')", (LIVE,))
    run("INSERT INTO model_preview_media (id, model_id, file_id, url, type) VALUES ('mp', 'm', 'preview-file', '/x', 'image')")
    run("INSERT INTO media_index_queue (id, file_id, pass_type) VALUES ('q', 'gone-file', 'tags')")
    run("INSERT INTO phrasebook_categories (id, name, path, user_id) VALUES ('c', 'c', 'c', 'u')")
    run("INSERT INTO phrasebook_values (id, category_id, label, value, user_id, preview_file_id) VALUES ('v', 'c', 'l', 'v', 'u', 'phrasebook-file')")
    for notification_id, type_, generation_id in (
        ("n-gone", "generation.completed", GONE),
        ("n-live", "generation.completed", LIVE),
        ("n-alert", "generation.failed_admin_alert", GONE),
    ):
        run(
            "INSERT INTO notifications (id, user_id, category, level, title, metadata, type) VALUES (?, 'u', 'generation', 'info', 't', ?, ?)",
            (notification_id, json.dumps({"generation_id": generation_id}), type_),
        )
    run("INSERT INTO generation_stats (id, generation_id) VALUES ('s', ?)", (GONE,))
    run("INSERT INTO limit_events (id, user_id, kind, created_at, ref_id) VALUES ('l', 'u', 'generations_per_day', '2026-10-28', ?)", (GONE,))
    connection.commit()
    connection.close()
    _write(storage, f"generations/{DAY}/{LIVE}/1.mp4")
    _write(storage, f"generations/{DAY}/{LIVE}/thumbnails/1_medium_animated.webp")
    _write(storage, f"generations/{DAY}/{GONE}/thumbnails/1_medium_animated.webp", b"webp")
    _write(storage, f"generations/{DAY}/{GONE}/thumbnails/9_small.jpg", b"jpg")
    _write(storage, f"generations/{DAY}/{SHARED}/1.png")
    _write(storage, f"generations/{DAY}/{SHARED}/thumbnails/2_small.jpg")
    return storage, database


def _rows(database, sql):
    connection = sqlite3.connect(database)
    try:
        return sorted(row[0] for row in connection.execute(sql))
    finally:
        connection.close()


def test_the_dry_run_lists_the_leftovers_and_deletes_nothing(install, capsys):
    storage, database = install

    assert _load().main(["--storage", str(storage)]) == 0

    output = capsys.readouterr().out
    assert "files rows no generation lists: 1" in output
    assert "gone-file" in output and "preview-file" not in output and "phrasebook-file" not in output
    assert "notifications about deleted generations: 1" in output and "n-gone" in output
    assert f"generations/{DAY}/{GONE}/thumbnails/1_medium_animated.webp" in output
    assert f"generations/{DAY}/{GONE}/thumbnails/9_small.jpg" in output
    assert f"generations/{DAY}/{SHARED}/thumbnails/2_small.jpg" in output
    assert f"generations/{DAY}/{SHARED}/1.png" not in output
    assert "in 1 folders" in output
    assert "kept on purpose: 1 generation_stats rows" in output and "and 1 limit_events rows" in output
    assert "Dry run: nothing was deleted" in output
    assert _rows(database, "SELECT id FROM files") == ["gone-file", "live-file", "phrasebook-file", "preview-file"]
    assert _rows(database, "SELECT id FROM notifications") == ["n-alert", "n-gone", "n-live"]
    assert (storage / f"generations/{DAY}/{GONE}/thumbnails/1_medium_animated.webp").is_file()


def test_apply_deletes_exactly_what_the_dry_run_lists(install, capsys):
    storage, database = install

    assert _load().main(["--storage", str(storage), "--apply"]) == 0

    assert "Deleted 1 files rows, 1 notifications and 3 stored files." in capsys.readouterr().out
    assert _rows(database, "SELECT id FROM files") == ["live-file", "phrasebook-file", "preview-file"]
    assert _rows(database, "SELECT id FROM media_index_queue") == []
    assert _rows(database, "SELECT id FROM notifications") == ["n-alert", "n-live"]
    assert _rows(database, "SELECT id FROM generation_stats") == ["s"]
    assert _rows(database, "SELECT id FROM limit_events") == ["l"]
    assert not (storage / f"generations/{DAY}/{GONE}").exists()
    assert (storage / f"generations/{DAY}/{SHARED}/1.png").is_file()
    assert not (storage / f"generations/{DAY}/{SHARED}/thumbnails").exists()
    assert (storage / f"generations/{DAY}/{LIVE}/thumbnails/1_medium_animated.webp").is_file()


def test_a_missing_database_is_reported(tmp_path, capsys):
    assert _load().main(["--storage", str(tmp_path)]) == 2
    assert "No database" in capsys.readouterr().err
