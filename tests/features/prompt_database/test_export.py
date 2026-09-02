"""`export_styles_csv` round trip against a real repository on a scratch DB.

Same fixture-composition approach as `test_importing.py` - see its module
docstring for why this isn't a `unittest.TestCase`.
"""
import csv
import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.operations.importing import export_styles_csv, import_prompts
from src.features.prompt_database.operations.mutations import add_prompt
from src.features.prompt_database.repository import PromptRepository
from tests.fixtures.persistence_base import PersistenceTestBase


@pytest.fixture
def persistence():
    base = PersistenceTestBase()
    base.setUp()
    try:
        yield base
    finally:
        base.tearDown()


@pytest.fixture
def user_id(persistence):
    return persistence.create_test_user("export-user-1", "exportuser1", "export1@example.com")


@pytest.fixture
def collaborators(persistence):
    embedding_provider = MagicMock()
    embedding_provider.embed = AsyncMock(return_value=[[0.1, 0.2]])
    embedding_provider.is_available = AsyncMock(return_value=False)
    return PromptDatabaseCollaborators(
        repository=PromptRepository(),
        vector_store=MagicMock(),
        embedding_provider=embedding_provider,
        plugin_registry=MagicMock(),
    )


def _rows(csv_text: str):
    return list(csv.DictReader(io.StringIO(csv_text)))


async def test_round_trips_a_paired_row_through_import_and_export(collaborators, user_id):
    csv_bytes = (
        b'name,prompt,negative_prompt\n'
        b'Cinematic,a hero on a cliff,"blurry, low quality"\n'
    )
    await import_prompts(collaborators, user_id, [("styles.csv", csv_bytes)])

    exported = export_styles_csv(collaborators, user_id)
    rows = _rows(exported)

    assert len(rows) == 1
    assert rows[0]["name"] == "Cinematic"
    assert rows[0]["prompt"] == "a hero on a cliff"
    assert rows[0]["negative_prompt"] == "blurry, low quality"


async def test_ungrouped_positive_prompt_exports_with_blank_negative_column(collaborators, user_id):
    await add_prompt(collaborators, user_id, "a lone standing prompt", name="Solo", usage_hint="positive")

    rows = _rows(export_styles_csv(collaborators, user_id))

    assert rows[0]["name"] == "Solo"
    assert rows[0]["prompt"] == "a lone standing prompt"
    assert rows[0]["negative_prompt"] == ""


async def test_ungrouped_negative_prompt_exports_with_blank_prompt_column(collaborators, user_id):
    await add_prompt(collaborators, user_id, "ugly, deformed", name="BadStuff", usage_hint="negative")

    rows = _rows(export_styles_csv(collaborators, user_id))

    assert rows[0]["name"] == "BadStuff"
    assert rows[0]["prompt"] == ""
    assert rows[0]["negative_prompt"] == "ugly, deformed"


async def test_multiline_and_comma_bearing_text_round_trips_through_a_quoted_cell(collaborators, user_id):
    csv_bytes = (
        b'name,prompt,negative_prompt\n'
        b'"Multiline","a hero,\nstanding on a cliff","blurry, low, quality"\n'
    )
    await import_prompts(collaborators, user_id, [("styles.csv", csv_bytes)])

    rows = _rows(export_styles_csv(collaborators, user_id))

    assert rows[0]["prompt"] == "a hero,\nstanding on a cliff"
    assert rows[0]["negative_prompt"] == "blurry, low, quality"


async def test_export_paginates_past_a_single_page_of_prompts(collaborators, user_id, monkeypatch):
    from src.features.prompt_database.operations import importing as importing_module

    monkeypatch.setattr(importing_module, "EXPORT_PAGE_SIZE", 2)
    for index in range(5):
        await add_prompt(collaborators, user_id, f"prompt {index}", name=f"P{index}", usage_hint="positive")

    rows = _rows(export_styles_csv(collaborators, user_id))

    assert len(rows) == 5
    assert {row["name"] for row in rows} == {f"P{i}" for i in range(5)}
