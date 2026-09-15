"""Scratch (in-memory, migrated) database fixture for MCP tests, mirroring
tests/features/llm/tools/test_governance.py's `governance_db` fixture.
"""

from unittest.mock import patch

import pytest

from tests.conftest import TestDatabase


@pytest.fixture
def mcp_db():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        yield test_database
    test_database.close()
