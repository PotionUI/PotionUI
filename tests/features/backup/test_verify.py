"""Media verification against the two relative-path conventions that coexist."""

from src.features.backup.verify import verify_media

from tests.features.backup.conftest import build_database, build_storage, newest_available_migration


def test_generation_and_upload_rows_resolve_against_the_storage_root(tmp_path):
    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)

    report = verify_media(storage / "db.sqlite", [storage])

    assert report.missing == []
    assert report.checked == 6


def test_a_cwd_relative_row_still_resolves(tmp_path):
    """Uploads were written with the storage directory inside the value; those
    rows must not be reported missing just because of the extra prefix."""
    import sqlite3

    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    conn = sqlite3.connect(storage / "db.sqlite")
    with conn:
        conn.execute(
            "UPDATE files SET file_path = ? WHERE id = '01FILE'",
            ("storage/generations/2026-09-09/01GEN/0.png",),
        )
    conn.close()

    report = verify_media(storage / "db.sqlite", [storage])

    assert report.missing == []


def test_a_deleted_original_is_reported_with_its_generation(tmp_path):
    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    (storage / "generations" / "2026-09-09" / "01GEN" / "1.mp4").unlink()

    report = verify_media(storage / "db.sqlite", [storage])

    assert [(item.kind, item.key, item.owner) for item in report.missing] == [
        ("generation file", "generations/2026-09-09/01GEN/1.mp4", "01GEN")
    ]
    assert not report.ok


def test_a_missing_upload_is_reported(tmp_path):
    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    (storage / "uploads" / "up1.png").unlink()

    report = verify_media(storage / "db.sqlite", [storage])

    assert [item.key for item in report.missing] == ["uploads/up1.png"]


def test_a_missing_animated_thumbnail_is_counted_not_listed(tmp_path):
    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    (storage / "generations" / "2026-09-09" / "01GEN" / "1_small_animated.webp").unlink()

    report = verify_media(storage / "db.sqlite", [storage])

    assert report.missing == []
    assert report.missing_animated == 1


def test_several_roots_are_searched(tmp_path):
    storage = tmp_path / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    mirror = tmp_path / "mirror"
    moved = storage / "uploads" / "up1.png"
    (mirror / "uploads").mkdir(parents=True)
    moved.rename(mirror / "uploads" / "up1.png")

    assert verify_media(storage / "db.sqlite", [storage]).missing
    assert verify_media(storage / "db.sqlite", [mirror, storage]).missing == []
