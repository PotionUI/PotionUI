"""Regression for the module-level `db` import hole (see
tests/architecture/test_db_import_hole.py): `DownloadRepository` used to bind
`db` at its own top-level `from ... import db`, so once that module had been
imported once - collection alone is enough - patching
`src.platform.database.database.db` never reached it again. `db` is now
imported at call time inside each method, so re-pointing `database.db`
mid-test must be picked up on the very next call."""

from unittest.mock import patch

import pytest

from src.features.downloads.models import Download, DownloadStatus, DownloadType
from src.features.downloads.repository import DownloadRepository


@pytest.fixture
def repository(mock_db):
    return DownloadRepository()


def test_mock_db_actually_isolates_this_repository(repository, mock_db):
    from tests.conftest import TestDatabase
    from src.platform.database.migration_runner import MigrationRunner

    second_db = TestDatabase()
    with patch("src.platform.database.database.db", second_db), \
         patch("src.platform.database.migration_runner.db", second_db):
        MigrationRunner().run_migrations()

    download = Download(
        id="", type=DownloadType.MODEL, url="https://example.test/model.safetensors",
        destination_path="models/example.safetensors", filename="example.safetensors",
        status=DownloadStatus.PENDING,
    )

    with patch("src.platform.database.database.db", second_db):
        created = repository.create(download)

    with mock_db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM downloads WHERE id = ?", (created.id,))
        assert cursor.fetchone() is None, "write leaked into the fixture's own db, not the re-pointed one"

    with second_db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM downloads WHERE id = ?", (created.id,))
        assert cursor.fetchone() is not None, "write did not land on the re-pointed db"
