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


from tests.features.api_keys import conftest as api_key_conftest

keys_db = api_key_conftest.keys_db
stack = api_key_conftest.stack
world = api_key_conftest.world
client = api_key_conftest.client
