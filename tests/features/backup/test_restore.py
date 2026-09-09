"""Restoring an archive: what it refuses, what it keeps, and what it reports."""

import json
import os
import zipfile

import pytest

from src.features.backup.archive import run_backup
from src.features.backup.manifest import MANIFEST_FILENAME, UnsupportedArchiveError
from src.features.backup.restore import RestoreRefused, run_restore
from src.features.backup.snapshot import table_row_counts

from tests.features.backup.conftest import connect


def _backup(install, tmp_path, monkeypatch, **kwargs):
    """Back up the source install. The two fixtures share POTIONUI_DB_PATH, so
    it has to be pointed back at the source before reading it."""
    monkeypatch.setenv("POTIONUI_DB_PATH", str(install / "storage" / "db.sqlite"))
    return run_backup(install, tmp_path / "out", **kwargs)


def _target(empty_install, monkeypatch):
    monkeypatch.setenv("POTIONUI_DB_PATH", str(empty_install / "storage" / "db.sqlite"))
    return empty_install


def _rewrite_manifest(archive_path, tmp_path, changes):
    """Repack the archive with a patched manifest - the only way to produce an
    archive this checkout could not have written itself."""
    rebuilt = tmp_path / f"rebuilt-{archive_path.name}"
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == MANIFEST_FILENAME:
                manifest = json.loads(payload)
                manifest.update(changes)
                payload = json.dumps(manifest).encode("utf-8")
            target.writestr(name, payload)
    return rebuilt


def test_restore_places_every_item(install, empty_install, tmp_path, monkeypatch):
    result = _backup(install, tmp_path, monkeypatch, tier="media")
    target = _target(empty_install, monkeypatch)

    outcome = run_restore(result.archive_path, target)

    assert (target / "storage" / "db.sqlite").is_file()
    assert (target / "storage" / "secret.key").is_file()
    assert (target / ".env").read_text() == "POTIONUI_EXAMPLE=1\n"
    assert (target / "content" / "presets" / "local" / "example.txt").is_file()
    assert (target / "storage" / "avatars" / "a.png").is_file()
    assert table_row_counts(target / "storage" / "db.sqlite") == table_row_counts(
        install / "storage" / "db.sqlite"
    )
    assert {item.name for item in outcome.placed} >= {"db.sqlite", "secret.key", ".env"}


def test_restore_copies_the_media_mirror_back(install, empty_install, tmp_path, monkeypatch):
    result = _backup(install, tmp_path, monkeypatch, tier="media")
    target = _target(empty_install, monkeypatch)

    outcome = run_restore(result.archive_path, target)

    original = target / "storage" / "generations" / "2026-09-09" / "01GEN" / "0.png"
    assert original.read_bytes() == b"original-image"
    assert (target / "storage" / "uploads" / "up1.png").read_bytes() == b"upload"
    assert outcome.media.files_copied == 6


def test_restore_keeps_the_previous_database_and_its_write_ahead_log(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    target = _target(empty_install, monkeypatch)

    existing = target / "storage" / "db.sqlite"
    conn = connect(existing)
    with conn:
        conn.execute("CREATE TABLE local_only (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO local_only (id) VALUES (1)")
    # SQLite folds the log back in and deletes it when the last connection
    # closes, so one has to stay open for there to be a -wal file at all.
    keeper = connect(existing)
    conn.close()
    assert existing.with_name("db.sqlite-wal").exists()

    try:
        outcome = run_restore(result.archive_path, target)

        kept = [p for p in outcome.previous_paths if p.name.startswith("db.sqlite.pre-restore-")]
        assert len(kept) == 1
        assert kept[0].with_name(kept[0].name + "-wal").exists()
        assert not existing.with_name("db.sqlite-wal").exists()
        assert "local_only" not in table_row_counts(existing)
    finally:
        keeper.close()

    assert table_row_counts(kept[0])["local_only"] == 1


def test_restore_keeps_the_previous_key_and_content_trees(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    target = _target(empty_install, monkeypatch)

    (target / "storage" / "secret.key").write_text("old key\n")
    local = target / "content" / "presets" / "local"
    local.mkdir(parents=True)
    (local / "mine.txt").write_text("mine\n")

    outcome = run_restore(result.archive_path, target)

    kept = {path.name.split(".pre-restore-")[0] for path in outcome.previous_paths}
    assert {"secret.key", "local"} <= kept
    assert (target / "content" / "presets" / "local" / "example.txt").is_file()
    assert not (target / "content" / "presets" / "local" / "mine.txt").exists()
    preserved = next(p for p in outcome.previous_paths if p.name.startswith("local.pre-restore-"))
    assert (preserved / "mine.txt").read_text() == "mine\n"


def test_restore_is_refused_while_potionui_is_running(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    target = _target(empty_install, monkeypatch)

    runtime = target / ".runtime"
    runtime.mkdir()
    (runtime / "state.json").write_text(json.dumps({"backend": {"pid": os.getpid()}}))

    with pytest.raises(RestoreRefused, match="running"):
        run_restore(result.archive_path, target)
    assert not (target / "storage" / "db.sqlite").exists()


def test_restore_is_refused_when_the_archive_schema_is_ahead(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    ahead = _rewrite_manifest(result.archive_path, tmp_path, {"migration_head": "999_from_the_future"})
    target = _target(empty_install, monkeypatch)

    with pytest.raises(RestoreRefused, match="999_from_the_future"):
        run_restore(ahead, target)
    assert not (target / "storage" / "db.sqlite").exists()


def test_restore_is_refused_for_an_unknown_archive_schema_version(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    future = _rewrite_manifest(result.archive_path, tmp_path, {"schema_version": 99})
    target = _target(empty_install, monkeypatch)

    with pytest.raises(UnsupportedArchiveError, match="99"):
        run_restore(future, target)


def test_a_zip_without_a_manifest_is_not_a_backup(empty_install, tmp_path, monkeypatch):
    stranger = tmp_path / "stranger.zip"
    with zipfile.ZipFile(stranger, "w") as zf:
        zf.writestr("hello.txt", "hi")
    target = _target(empty_install, monkeypatch)

    with pytest.raises(RestoreRefused, match="not a PotionUI backup"):
        run_restore(stranger, target)


def test_dry_run_writes_nothing(install, empty_install, tmp_path, monkeypatch):
    result = _backup(install, tmp_path, monkeypatch, tier="media")
    target = _target(empty_install, monkeypatch)
    before = sorted(path.relative_to(target) for path in target.rglob("*"))

    outcome = run_restore(result.archive_path, target, dry_run=True)

    assert outcome.dry_run is True
    assert outcome.placed == []
    assert sorted(path.relative_to(target) for path in target.rglob("*")) == before
    assert {item.name for item in outcome.plan.items} >= {"db.sqlite", "secret.key"}
    assert outcome.verify.checked > 0


def test_dry_run_reports_blockers_instead_of_raising(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    target = _target(empty_install, monkeypatch)
    runtime = target / ".runtime"
    runtime.mkdir()
    (runtime / "state.json").write_text(json.dumps({"backend": {"pid": os.getpid()}}))

    outcome = run_restore(result.archive_path, target, dry_run=True)

    assert any("running" in blocker for blocker in outcome.plan.blockers)


def test_verify_reports_a_missing_original_and_counts_missing_animated(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="media")
    (tmp_path / "out" / "media" / "generations" / "2026-09-09" / "01GEN" / "0.png").unlink()
    target = _target(empty_install, monkeypatch)

    outcome = run_restore(result.archive_path, target)

    missing = [item.key for item in outcome.verify.missing]
    assert missing == ["generations/2026-09-09/01GEN/0.png"]
    assert outcome.verify.missing[0].owner == "01GEN"
    # The video's thumbnail has no animated sibling in a default backup.
    assert outcome.verify.missing_animated == 1


def test_verify_is_clean_when_every_file_came_across(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="media", include_animated_thumbnails=True)
    target = _target(empty_install, monkeypatch)

    outcome = run_restore(result.archive_path, target)

    assert outcome.verify.missing == []
    assert outcome.verify.missing_animated == 0
    assert outcome.verify.ok


def test_an_explicit_media_directory_that_does_not_exist_is_refused(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="config")
    target = _target(empty_install, monkeypatch)

    with pytest.raises(RestoreRefused, match="no media mirror"):
        run_restore(result.archive_path, target, media_dir=tmp_path / "absent")


def test_restore_leaves_no_staging_directory_behind(
    install, empty_install, tmp_path, monkeypatch
):
    result = _backup(install, tmp_path, monkeypatch, tier="media")
    target = _target(empty_install, monkeypatch)

    run_restore(result.archive_path, target)

    assert [path.name for path in target.glob(".potionui-restore-*")] == []
