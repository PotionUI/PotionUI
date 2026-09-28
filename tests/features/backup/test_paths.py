import sqlite3
from pathlib import Path

import pytest

from src.features.backup.paths import resolve_layout
from tests.features.backup.conftest import build_database, newest_available_migration, set_setting


def _install(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "install"
    storage = root / "storage"
    build_database(storage / "db.sqlite", migration=newest_available_migration())
    monkeypatch.setenv("POTIONUI_DB_PATH", str(storage / "db.sqlite"))
    monkeypatch.delenv("POTIONUI_SECRET_KEY_FILE", raising=False)
    return root


def _add_model_roots_table(db_path: Path, rows) -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "CREATE TABLE model_roots (id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO model_roots (id, path, kind) VALUES (?, ?, ?)", rows
        )
    conn.close()


class TestModelRootsTable:
    def test_home_path_comes_from_model_roots(self, tmp_path, monkeypatch):
        root = _install(tmp_path, monkeypatch)
        home_dir = root / "external" / "home-models"
        _add_model_roots_table(
            root / "storage" / "db.sqlite",
            [("home", str(home_dir), "home")],
        )

        layout = resolve_layout(root)

        assert layout.models_dir == home_dir
        assert layout.excluded_roots == ()

    def test_library_roots_are_listed_and_excluded(self, tmp_path, monkeypatch):
        root = _install(tmp_path, monkeypatch)
        home_dir = root / "models"
        library_dir = root / "external" / "library"
        _add_model_roots_table(
            root / "storage" / "db.sqlite",
            [
                ("home", str(home_dir), "home"),
                ("lib-1", str(library_dir), "library"),
            ],
        )

        layout = resolve_layout(root)

        assert layout.models_dir == home_dir
        assert layout.excluded_roots == (library_dir,)
        assert library_dir not in (layout.models_dir,)

    def test_relative_home_path_is_resolved_against_repo_root(self, tmp_path, monkeypatch):
        root = _install(tmp_path, monkeypatch)
        _add_model_roots_table(
            root / "storage" / "db.sqlite",
            [("home", "custom-models", "home")],
        )

        layout = resolve_layout(root)

        assert layout.models_dir == root / "custom-models"


class TestPreMigrationFallback:
    def test_missing_table_falls_back_to_the_models_dir_setting(self, tmp_path, monkeypatch):
        root = _install(tmp_path, monkeypatch)
        set_setting(root / "storage" / "db.sqlite", "models_dir", "legacy-models")

        layout = resolve_layout(root)

        assert layout.models_dir == root / "legacy-models"
        assert layout.excluded_roots == ()

    def test_missing_table_and_missing_setting_defaults_to_models(self, tmp_path, monkeypatch):
        root = _install(tmp_path, monkeypatch)

        layout = resolve_layout(root)

        assert layout.models_dir == root / "models"
        assert layout.excluded_roots == ()

    def test_missing_database_falls_back_without_raising(self, tmp_path, monkeypatch):
        root = tmp_path / "fresh-install"
        monkeypatch.setenv("POTIONUI_DB_PATH", str(root / "storage" / "db.sqlite"))
        monkeypatch.delenv("POTIONUI_SECRET_KEY_FILE", raising=False)

        layout = resolve_layout(root)

        assert layout.models_dir == root / "models"
        assert layout.excluded_roots == ()
