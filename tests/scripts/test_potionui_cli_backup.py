"""The `backup` / `restore` subcommands of the bootstrap CLI: parsing and exit codes.

The archive and restore behaviour itself is covered in tests/features/backup;
what matters here is that the CLI hands the right arguments down and maps a
refusal onto exit 1.
"""
import json
import os

import pytest

from scripts import potionui_cli as cli

from tests.features.backup.conftest import build_database, build_storage, newest_available_migration


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    storage = root / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    build_storage(storage)
    (root / ".env").write_text("POTIONUI_EXAMPLE=1\n")
    monkeypatch.setenv("POTIONUI_DB_PATH", str(storage / "db.sqlite"))
    monkeypatch.delenv("POTIONUI_SECRET_KEY_FILE", raising=False)
    monkeypatch.setattr(cli, "REPO_ROOT", root)
    return root


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_backup_defaults():
    args = cli.build_parser().parse_args(["backup"])

    assert args.command == "backup"
    assert args.tier == "config"
    assert args.out is None
    assert args.include_models is False
    assert args.include_animated_thumbnails is False


def test_backup_accepts_every_tier():
    for tier in ("config", "media", "all"):
        assert cli.build_parser().parse_args(["backup", "--tier", tier]).tier == tier


def test_backup_rejects_an_unknown_tier():
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["backup", "--tier", "everything"])
    assert exit_info.value.code == 2


def test_restore_requires_an_archive():
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["restore"])
    assert exit_info.value.code == 2


def test_restore_flags():
    args = cli.build_parser().parse_args(["restore", "a.zip", "--media", "/m", "--dry-run"])

    assert (args.archive, args.media, args.dry_run) == ("a.zip", "/m", True)


def test_backup_and_restore_are_wired_into_main(monkeypatch):
    seen = {}

    def record(key, value):
        seen[key] = value
        return 0

    monkeypatch.setattr(cli, "cmd_backup", lambda args: record("backup", args.tier))
    monkeypatch.setattr(cli, "cmd_restore", lambda args: record("restore", args.archive))

    assert cli.main(["backup", "--tier", "media"]) == 0
    assert cli.main(["restore", "x.zip"]) == 0
    assert seen == {"backup": "media", "restore": "x.zip"}


def test_format_bytes():
    assert cli.format_bytes(512) == "512 B"
    assert cli.format_bytes(2048) == "2.0 KB"
    assert cli.format_bytes(5 * 1024 ** 3) == "5.0 GB"


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

def test_backup_exits_zero_and_writes_an_archive(install, tmp_path, capsys):
    out = tmp_path / "out"

    code = cli.main(["backup", "--out", str(out), "--tier", "media"])

    assert code == 0
    archives = list(out.glob("potionui-backup-*.zip"))
    assert len(archives) == 1
    assert (out / "media" / "uploads" / "up1.png").exists()
    printed = capsys.readouterr().out
    assert "db.sqlite" in printed
    assert "credential encryption key" in printed


def test_backup_defaults_to_a_backups_directory_in_the_install(install, capsys):
    code = cli.main(["backup"])

    assert code == 0
    assert list((install / "backups").glob("potionui-backup-*.zip"))


def test_backup_exits_one_when_refused(install, tmp_path, capsys):
    (install / "storage" / "db.sqlite").unlink()

    code = cli.main(["backup", "--out", str(tmp_path / "out")])

    assert code == 1
    assert "no database" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="directory mode bits do not make a directory unwritable on Windows")
def test_backup_exits_one_when_the_storage_tree_is_unusable(tmp_path, monkeypatch, capsys):
    root = tmp_path / "bare"
    root.mkdir()
    monkeypatch.setattr(cli, "REPO_ROOT", root)
    root.chmod(0o500)

    try:
        code = cli.main(["backup", "--out", str(tmp_path / "out")])
    finally:
        root.chmod(0o700)

    assert code == 1
    assert "does not exist or is not writable" in capsys.readouterr().out


def test_restore_dry_run_exits_zero_and_writes_nothing(install, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    assert cli.main(["backup", "--out", str(out), "--tier", "media"]) == 0
    archive = next(out.glob("*.zip"))

    target = tmp_path / "target"
    (target / "storage").mkdir(parents=True)
    monkeypatch.setenv("POTIONUI_DB_PATH", str(target / "storage" / "db.sqlite"))
    monkeypatch.setattr(cli, "REPO_ROOT", target)
    capsys.readouterr()

    code = cli.main(["restore", str(archive), "--dry-run"])

    assert code == 0
    assert "Dry run" in capsys.readouterr().out
    assert not (target / "storage" / "db.sqlite").exists()


def test_restore_dry_run_exits_one_when_blocked(install, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    assert cli.main(["backup", "--out", str(out)]) == 0
    archive = next(out.glob("*.zip"))

    target = tmp_path / "target"
    (target / "storage").mkdir(parents=True)
    (target / ".runtime").mkdir()
    (target / ".runtime" / "state.json").write_text(json.dumps({"backend": {"pid": os.getpid()}}))
    monkeypatch.setenv("POTIONUI_DB_PATH", str(target / "storage" / "db.sqlite"))
    monkeypatch.setattr(cli, "REPO_ROOT", target)
    capsys.readouterr()

    code = cli.main(["restore", str(archive), "--dry-run"])

    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_restore_exits_zero_and_reports_verification(install, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    assert cli.main(["backup", "--out", str(out), "--tier", "media"]) == 0
    archive = next(out.glob("*.zip"))

    target = tmp_path / "target"
    (target / "storage").mkdir(parents=True)
    monkeypatch.setenv("POTIONUI_DB_PATH", str(target / "storage" / "db.sqlite"))
    monkeypatch.setattr(cli, "REPO_ROOT", target)
    capsys.readouterr()

    code = cli.main(["restore", str(archive)])

    printed = capsys.readouterr().out
    assert code == 0
    assert (target / "storage" / "db.sqlite").is_file()
    assert "verified" in printed
    assert "animated thumbnail" in printed


def test_restore_exits_one_for_a_missing_archive(install, tmp_path, capsys):
    code = cli.main(["restore", str(tmp_path / "absent.zip")])

    assert code == 1
    assert "no archive" in capsys.readouterr().out
