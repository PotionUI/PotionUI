"""What a backup archive holds, and when a backup is refused."""

import zipfile

import pytest

from src.features.backup.archive import BackupRefused, run_backup
from src.features.backup.manifest import BACKUP_SCHEMA_VERSION
from src.features.backup.snapshot import integrity_check, table_row_counts

from tests.features.backup.conftest import newest_available_migration, set_setting


def _names(archive_path):
    with zipfile.ZipFile(archive_path) as zf:
        return set(zf.namelist())


def test_config_archive_holds_the_state_potionui_owns(install, tmp_path):
    result = run_backup(install, tmp_path / "out")

    names = _names(result.archive_path)
    assert {
        "db.sqlite",
        "secret.key",
        ".env",
        "manifest.json",
        "content/presets/local/example.txt",
        "content/plugins/local/example.txt",
        "content/automation/local/example.txt",
        "storage/llm.yml",
        "storage/saved_prompts.json",
        "storage/avatars/a.png",
    } <= names


def test_config_archive_excludes_caches_scratch_and_the_settings_file(install, tmp_path):
    result = run_backup(install, tmp_path / "out")

    names = _names(result.archive_path)
    for excluded in (
        "storage/tmp/scratch.bin",
        "storage/chromadb/index",
        "storage/settings.json",
        "storage/model_hash_cache.json",
        "storage/db.sqlite.bak-20260101-000000",
    ):
        assert excluded not in names


def test_excluded_names_inside_an_archived_tree_are_dropped(install, tmp_path):
    local = install / "content" / "presets" / "local"
    (local / "example.yml.bak-20260101-000000").write_text("stale\n")
    (local / "tmp").mkdir()
    (local / "tmp" / "scratch.bin").write_bytes(b"scratch")
    (install / "storage" / "avatars" / "a.png.bak-20260101-000000").write_bytes(b"stale")

    result = run_backup(install, tmp_path / "out")

    names = _names(result.archive_path)
    assert "content/presets/local/example.txt" in names
    for excluded in (
        "content/presets/local/example.yml.bak-20260101-000000",
        "content/presets/local/tmp/scratch.bin",
        "storage/avatars/a.png.bak-20260101-000000",
    ):
        assert excluded not in names


def test_the_archived_database_is_a_sound_complete_snapshot(install, tmp_path):
    result = run_backup(install, tmp_path / "out")

    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(result.archive_path) as zf:
        zf.extract("db.sqlite", extracted)

    restored = extracted / "db.sqlite"
    assert integrity_check(restored) == []
    assert table_row_counts(restored) == table_row_counts(install / "storage" / "db.sqlite")


def test_manifest_describes_the_archive(install, tmp_path):
    result = run_backup(install, tmp_path / "out")

    manifest = result.manifest
    assert manifest["schema_version"] == BACKUP_SCHEMA_VERSION
    assert manifest["tier"] == "config"
    assert manifest["tiers"] == ["config"]
    assert manifest["migration_head"] == newest_available_migration()
    assert manifest["storage_backend"] == "local"
    assert manifest["media_mirror"] is None
    assert manifest["items"]["db.sqlite"]["files"] == 1
    assert manifest["items"]["content/presets/local"]["bytes"] > 0
    assert manifest["app_version"]
    assert manifest["created_at"]
    assert manifest["hostname"]


def test_media_tier_mirrors_uploads_and_generations(install, tmp_path):
    out = tmp_path / "out"
    result = run_backup(install, out, tier="media")

    assert (out / "media" / "generations" / "2026-09-09" / "01GEN" / "0.png").read_bytes() == b"original-image"
    assert (out / "media" / "uploads" / "up1.png").read_bytes() == b"upload"
    assert (out / "media" / "uploads" / "thumbnails" / "up1_small.webp").exists()
    assert result.manifest["tiers"] == ["config", "media"]
    assert "generations/2026-09-09" in result.manifest["media_mirror"]["directories"]
    assert "uploads/." in result.manifest["media_mirror"]["directories"]


def test_media_tier_skips_animated_thumbnails_by_default(install, tmp_path):
    out = tmp_path / "out"
    result = run_backup(install, out, tier="media")

    animated = out / "media" / "generations" / "2026-09-09" / "01GEN" / "1_small_animated.webp"
    assert not animated.exists()
    assert result.manifest["media_mirror"]["animated_skipped_files"] == 1
    assert result.manifest["include_animated_thumbnails"] is False


def test_media_tier_keeps_animated_thumbnails_when_asked(install, tmp_path):
    out = tmp_path / "out"
    result = run_backup(install, out, tier="media", include_animated_thumbnails=True)

    animated = out / "media" / "generations" / "2026-09-09" / "01GEN" / "1_small_animated.webp"
    assert animated.exists()
    assert result.manifest["media_mirror"]["animated_skipped_files"] == 0


def test_all_tier_mirrors_the_models_directory(install, tmp_path):
    out = tmp_path / "out"
    result = run_backup(install, out, tier="all")

    assert (out / "models" / "checkpoints" / "m.safetensors").read_bytes() == b"weights"
    assert result.manifest["include_models"] is True
    assert result.manifest["models_mirror"]["files_copied"] == 1


def test_a_second_media_backup_copies_nothing(install, tmp_path):
    from datetime import datetime, timedelta

    out = tmp_path / "out"
    first = datetime(2026, 9, 9, 3, 0, 0)
    run_backup(install, out, tier="media", now=first)
    result = run_backup(install, out, tier="media", now=first + timedelta(days=1))

    assert result.manifest["media_mirror"]["files_copied"] == 0
    assert result.manifest["media_mirror"]["files_skipped"] == 6


def test_s3_backend_refuses_the_media_tier(install, tmp_path):
    set_setting(install / "storage" / "db.sqlite", "storage_backend", "s3")

    with pytest.raises(BackupRefused, match="versioning"):
        run_backup(install, tmp_path / "out", tier="media")


def test_s3_backend_still_allows_a_config_backup(install, tmp_path):
    set_setting(install / "storage" / "db.sqlite", "storage_backend", "s3")

    result = run_backup(install, tmp_path / "out", tier="config")

    assert result.manifest["storage_backend"] == "s3"
    assert "db.sqlite" in _names(result.archive_path)


def test_config_tier_refuses_include_models(install, tmp_path):
    with pytest.raises(BackupRefused, match="--include-models"):
        run_backup(install, tmp_path / "out", tier="config", include_models=True)


def test_an_unknown_tier_is_refused(install, tmp_path):
    with pytest.raises(BackupRefused, match="unknown tier"):
        run_backup(install, tmp_path / "out", tier="everything")


def test_a_missing_database_is_refused(empty_install, tmp_path):
    with pytest.raises(BackupRefused, match="no database"):
        run_backup(empty_install, tmp_path / "out")


def test_no_working_files_are_left_behind(install, tmp_path):
    out = tmp_path / "out"
    run_backup(install, out, tier="media")

    leftovers = [p.name for p in out.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_a_custom_storage_directory_is_honoured(install, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "avatars").mkdir(parents=True)
    (elsewhere / "avatars" / "moved.png").write_bytes(b"moved")
    set_setting(install / "storage" / "db.sqlite", "file_storage_directory", str(elsewhere))

    result = run_backup(install, tmp_path / "out")

    assert "storage/avatars/moved.png" in _names(result.archive_path)
    assert "storage/avatars/a.png" not in _names(result.archive_path)
