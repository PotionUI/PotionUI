from pathlib import Path

import pytest

from src.platform.database.database import LIVE_DATABASE_PATH, refuse_live_database_under_pytest


def test_the_live_database_is_refused_while_a_test_runs():
    with pytest.raises(RuntimeError, match="must not open the live database"):
        refuse_live_database_under_pytest(LIVE_DATABASE_PATH)


def test_a_scratch_database_is_allowed(tmp_path):
    refuse_live_database_under_pytest(tmp_path / "test.sqlite")


def test_outside_pytest_the_live_database_is_allowed(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    refuse_live_database_under_pytest(Path("storage/db.sqlite"))
