"""Backups taken from the admin panel: the same archive the command line
writes, retention that only ever prunes archives, and a destination that has
to be writable before it can be saved.
"""

import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from src.features.backup import admin as admin_module
from src.features.backup.admin import BackupRunning, BackupRuns
from src.features.backup.archive import run_backup
from src.features.backup.manifest import MANIFEST_FILENAME
from src.features.backup.settings import (
    SETTING_DEFAULT_TIER,
    SETTING_DESTINATION,
    SETTING_RETENTION,
    destination_dir,
    validate_setting,
)
from tests.features.backup.conftest import set_setting

VOLATILE = ("created_at", "hostname")


class FakeSettings:
    """The two calls `load_backup_settings` makes, over a plain dict."""

    def __init__(self, **values):
        self.values = values

    def get_setting(self, key, default=None):
        return self.values.get(key, default)


def runs_for(install: Path, **values) -> BackupRuns:
    return BackupRuns(FakeSettings(**values), install)


def wait_for_job(runs: BackupRuns, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runs.current()
        if job is not None and job.status != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the backup job never finished")


def write_archive(destination: Path, stamp: str, *, tier="config", media=None) -> Path:
    """An archive with nothing but a manifest - enough for listing, sorting
    and pruning, which never open anything else."""
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"potionui-backup-{stamp}.zip"
    manifest = {
        "schema_version": 1,
        "app_version": "0.0.4",
        "migration_head": "023_backup_settings",
        "created_at": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:00:00+00:00",
        "hostname": "box",
        "tier": tier,
        "tiers": ["config"] if tier == "config" else ["config", "media"],
        "storage_backend": "local",
        "include_models": False,
        "include_animated_thumbnails": False,
        "items": {},
        "media_mirror": media,
        "models_mirror": None,
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST_FILENAME, json.dumps(manifest))
    return path


class TestRun:

    def test_the_archive_matches_the_one_the_command_line_writes(self, install, tmp_path):
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        runs.start("config")
        job = wait_for_job(runs)
        assert job.status == "done", job.last_error

        from_cli = run_backup(install, tmp_path / "cli", tier="config")
        with zipfile.ZipFile(job.archive) as zf:
            from_panel = json.loads(zf.read(MANIFEST_FILENAME))

        assert {k: v for k, v in from_panel.items() if k not in VOLATILE} == {
            k: v for k, v in from_cli.manifest.items() if k not in VOLATILE
        }
        assert job.bytes == Path(job.archive).stat().st_size

    def test_a_media_run_records_the_mirror_and_writes_it_beside_the_archive(self, install, tmp_path):
        destination = tmp_path / "panel"
        runs = runs_for(install, backup_destination=str(destination))

        runs.start("media")
        job = wait_for_job(runs)

        assert job.status == "done", job.last_error
        assert job.mirror["files_copied"] > 0
        assert (destination / "media" / "generations").is_dir()

    def test_a_second_run_is_refused_while_one_is_in_flight(self, install, tmp_path, monkeypatch):
        released = threading.Event()
        started = threading.Event()

        def blocking(*args, **kwargs):
            started.set()
            released.wait(30)
            return run_backup(*args, **kwargs)

        monkeypatch.setattr(admin_module, "run_backup", blocking)
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        runs.start("config")
        assert started.wait(10)
        try:
            with pytest.raises(BackupRunning):
                runs.start("config")
        finally:
            released.set()
        assert wait_for_job(runs).status == "done"

    def test_an_unknown_tier_is_refused_before_anything_is_written(self, install, tmp_path):
        destination = tmp_path / "panel"
        runs = runs_for(install, backup_destination=str(destination))

        with pytest.raises(ValueError):
            runs.start("everything")

        assert runs.current() is None
        assert not destination.exists()

    def test_a_failed_run_records_the_error_and_finishes(self, install, tmp_path, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(admin_module, "run_backup", explode)
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        runs.start("config")
        job = wait_for_job(runs)

        assert job.status == "failed"
        assert "no space left" in job.last_error
        assert job.finished_at is not None


class TestRetention:

    def test_only_the_newest_archives_survive(self, install, tmp_path):
        destination = tmp_path / "panel"
        for stamp in ("20260901-030000", "20260902-030000", "20260903-030000", "20260904-030000"):
            write_archive(destination, stamp)
        runs = runs_for(install, backup_destination=str(destination), backup_retention=2)

        pruned = runs.prune(2)

        assert pruned == ["potionui-backup-20260902-030000.zip", "potionui-backup-20260901-030000.zip"]
        assert [entry["name"] for entry in runs.list_archives()] == [
            "potionui-backup-20260904-030000.zip",
            "potionui-backup-20260903-030000.zip",
        ]

    def test_zero_keeps_everything(self, install, tmp_path):
        destination = tmp_path / "panel"
        for stamp in ("20260901-030000", "20260902-030000"):
            write_archive(destination, stamp)
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.prune(0) == []
        assert len(runs.list_archives()) == 2

    def test_the_media_mirror_is_never_pruned(self, install, tmp_path):
        destination = tmp_path / "panel"
        for stamp in ("20260901-030000", "20260902-030000"):
            write_archive(destination, stamp)
        mirrored = destination / "media" / "generations" / "2026-09-01" / "0.png"
        mirrored.parent.mkdir(parents=True)
        mirrored.write_bytes(b"image")
        runs = runs_for(install, backup_destination=str(destination))

        runs.prune(1)

        assert mirrored.read_bytes() == b"image"
        assert (destination / "media").is_dir()

    def test_a_run_prunes_to_the_configured_retention(self, install, tmp_path):
        destination = tmp_path / "panel"
        for stamp in ("20260901-030000", "20260902-030000"):
            write_archive(destination, stamp)
        runs = runs_for(install, backup_destination=str(destination), backup_retention=1)

        runs.start("config")
        job = wait_for_job(runs)

        assert job.status == "done", job.last_error
        assert [entry["name"] for entry in runs.list_archives()] == [Path(job.archive).name]
        assert job.pruned == [
            "potionui-backup-20260902-030000.zip",
            "potionui-backup-20260901-030000.zip",
        ]


class TestListing:

    def test_archives_are_newest_first_and_foreign_files_are_ignored(self, install, tmp_path):
        destination = tmp_path / "panel"
        write_archive(destination, "20260901-030000")
        write_archive(destination, "20260903-030000")
        write_archive(destination, "20260902-030000")
        (destination / "notes.txt").write_text("not an archive")
        (destination / "something-else.zip").write_bytes(b"not ours")
        (destination / "media").mkdir()
        runs = runs_for(install, backup_destination=str(destination))

        archives = runs.list_archives()

        assert [entry["name"] for entry in archives] == [
            "potionui-backup-20260903-030000.zip",
            "potionui-backup-20260902-030000.zip",
            "potionui-backup-20260901-030000.zip",
        ]
        assert archives[0]["app_version"] == "0.0.4"
        assert archives[0]["migration_head"] == "023_backup_settings"
        assert archives[0]["readable"] is True

    def test_an_unreadable_archive_is_listed_without_a_manifest(self, install, tmp_path):
        destination = tmp_path / "panel"
        destination.mkdir()
        (destination / "potionui-backup-20260905-030000.zip").write_bytes(b"truncated")
        runs = runs_for(install, backup_destination=str(destination))

        entry = runs.list_archives()[0]

        assert entry["readable"] is False
        assert entry["created_at"] is None
        assert entry["bytes"] == len(b"truncated")

    def test_the_mirror_line_comes_from_the_newest_media_archive(self, install, tmp_path):
        destination = tmp_path / "panel"
        write_archive(
            destination,
            "20260902-030000",
            tier="media",
            media={"directories": {"generations/2026-09-01": {"files": 3, "bytes": 900, "copied": 3}}},
        )
        write_archive(destination, "20260903-030000")
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.scan()[1] == {
            "last_synced": "2026-09-02T03:00:00+00:00",
            "day_count": 1,
            "bytes": 900,
        }

    def test_no_media_archive_means_no_mirror_line(self, install, tmp_path):
        destination = tmp_path / "panel"
        write_archive(destination, "20260903-030000")
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.scan()[1] is None


class TestDelete:

    def test_an_archive_in_the_destination_is_removed(self, install, tmp_path):
        destination = tmp_path / "panel"
        archive = write_archive(destination, "20260903-030000")
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.delete_archive(archive.name) is True
        assert not archive.exists()

    @pytest.mark.parametrize(
        "name",
        ["../outside.zip", "media/../../outside.zip", "/etc/passwd", "notes.txt", ""],
    )
    def test_a_name_that_reaches_outside_the_destination_is_refused(self, install, tmp_path, name):
        destination = tmp_path / "panel"
        destination.mkdir()
        outside = tmp_path / "outside.zip"
        outside.write_bytes(b"keep me")
        (destination / "notes.txt").write_text("keep me too")
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.delete_archive(name) is False
        assert outside.exists()
        assert (destination / "notes.txt").exists()

    def test_a_symlink_pointing_out_of_the_destination_is_refused(self, install, tmp_path):
        destination = tmp_path / "panel"
        destination.mkdir()
        outside = tmp_path / "outside.zip"
        outside.write_bytes(b"keep me")
        (destination / "potionui-backup-20260903-030000.zip").symlink_to(outside)
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.delete_archive("potionui-backup-20260903-030000.zip") is False
        assert outside.exists()

    def test_a_missing_archive_is_refused(self, install, tmp_path):
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        assert runs.delete_archive("potionui-backup-20260101-000000.zip") is False


class TestOverview:

    def test_the_panel_gets_the_settings_the_destination_and_the_cron_line(self, install, tmp_path):
        destination = tmp_path / "panel"
        write_archive(destination, "20260903-030000")
        runs = runs_for(
            install,
            backup_destination=str(destination),
            backup_retention=3,
            backup_default_tier="media",
        )

        overview = runs.overview()

        assert overview["settings"] == {
            "destination": str(destination),
            "retention": 3,
            "default_tier": "media",
        }
        assert overview["destination_abs"] == str(destination)
        assert overview["exists"] is True
        assert overview["writable"] is True
        assert overview["job"] is None
        assert overview["last_backup"] == {
            "time": "2026-09-03T03:00:00+00:00",
            "bytes": overview["archives"][0]["bytes"],
            "tier": "config",
        }
        assert overview["cron_line"] == (
            f"0 3 * * * {install / 'potionui'} backup --tier media --out {destination}"
        )

    def test_the_cron_line_is_absolute_for_a_relative_destination(self, install):
        runs = runs_for(install, backup_destination="backups")

        assert runs.cron_line() == (
            f"0 3 * * * {install / 'potionui'} backup --tier config --out {install / 'backups'}"
        )

    def test_an_empty_install_reports_no_archives_and_no_last_backup(self, install, tmp_path):
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        overview = runs.overview()

        assert overview["archives"] == []
        assert overview["last_backup"] is None
        assert overview["mirror"] is None

    def test_reading_the_overview_does_not_create_the_destination(self, install, tmp_path):
        destination = tmp_path / "panel" / "nested"
        runs = runs_for(install, backup_destination=str(destination))

        overview = runs.overview()

        assert not destination.exists()
        assert not destination.parent.exists()
        assert overview["exists"] is False
        assert overview["writable"] is True

    def test_a_destination_that_is_not_there_yet_reads_back_as_creatable(self, install, tmp_path):
        runs = runs_for(install, backup_destination=str(tmp_path / "panel"))

        assert runs.state() == (False, True)

    def test_a_destination_that_exists_reads_back_as_writable(self, install, tmp_path):
        destination = tmp_path / "panel"
        destination.mkdir()
        runs = runs_for(install, backup_destination=str(destination))

        assert runs.state() == (True, True)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
    def test_a_destination_under_a_read_only_parent_is_not_writable(self, install, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        runs = runs_for(install, backup_destination=str(locked / "panel"))
        try:
            state = runs.state()
        finally:
            locked.chmod(0o700)

        assert state == (False, False)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
    def test_an_existing_read_only_destination_is_not_writable(self, install, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        runs = runs_for(install, backup_destination=str(locked))
        try:
            state = runs.state()
        finally:
            locked.chmod(0o700)

        assert state == (True, False)

    def test_the_first_run_creates_the_destination(self, install, tmp_path):
        destination = tmp_path / "panel" / "nested"
        runs = runs_for(install, backup_destination=str(destination))

        runs.start("config")
        job = wait_for_job(runs)

        assert job.status == "done", job.last_error
        assert Path(job.archive).parent == destination


class TestSettingsValidation:

    def test_a_relative_destination_is_created_and_accepted(self, install):
        assert validate_setting(SETTING_DESTINATION, "backups/nightly", install) is None
        assert (install / "backups" / "nightly").is_dir()

    def test_an_empty_destination_is_refused(self, install):
        assert validate_setting(SETTING_DESTINATION, "  ", install) == "must name a directory"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
    def test_a_destination_that_cannot_be_created_is_refused(self, install, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            reason = validate_setting(SETTING_DESTINATION, str(locked / "backups"), install)
        finally:
            locked.chmod(0o700)

        assert reason is not None
        assert "could not be created" in reason

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
    def test_a_destination_that_exists_but_cannot_be_written_to_is_refused(self, install, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            reason = validate_setting(SETTING_DESTINATION, str(locked), install)
        finally:
            locked.chmod(0o700)

        assert reason == f"{locked} is not writable"

    @pytest.mark.parametrize("value", [-1, 366, "seven", True])
    def test_retention_outside_the_range_is_refused(self, value):
        assert validate_setting(SETTING_RETENTION, value) is not None

    @pytest.mark.parametrize("value", [0, 7, 365, "30"])
    def test_retention_inside_the_range_is_accepted(self, value):
        assert validate_setting(SETTING_RETENTION, value) is None

    def test_an_unknown_tier_is_refused(self):
        assert validate_setting(SETTING_DEFAULT_TIER, "everything") is not None
        assert validate_setting(SETTING_DEFAULT_TIER, "all") is None

    def test_a_key_this_module_does_not_own_is_left_alone(self):
        assert validate_setting("tmp_retention_days", "not a number") is None


class TestCommandLineAgreement:

    def test_the_default_out_directory_is_the_stored_destination(self, install, tmp_path):
        set_setting(install / "storage" / "db.sqlite", SETTING_DESTINATION, str(tmp_path / "elsewhere"))

        assert destination_dir(install) == tmp_path / "elsewhere"

    def test_a_relative_stored_destination_resolves_against_the_install(self, install):
        set_setting(install / "storage" / "db.sqlite", SETTING_DESTINATION, "backups/nightly")

        assert destination_dir(install) == install / "backups" / "nightly"

    def test_an_install_without_the_setting_falls_back_to_the_default(self, install):
        assert destination_dir(install) == install / "backups"
