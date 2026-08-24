"""Scratch (in-memory, migrated) database fixture for MCP tests, mirroring
tests/features/llm/tools/test_governance.py's `governance_db` fixture.
"""

import io
import sys
from unittest.mock import patch

import pytest

from tests.conftest import TestDatabase


@pytest.fixture
def mcp_db():
    test_database = TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database), \
         patch("src.features.mcp.repository.db", test_database), \
         patch("src.platform.settings.repository.db", test_database), \
         patch("src.features.users.repository.db", test_database), \
         patch("src.features.llm.tools.governance_repository.db", test_database):
        from src.platform.database.migration_runner import MigrationManager

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationManager().run_migrations()
        finally:
            sys.stdout = old_stdout

        yield test_database
    test_database.close()
