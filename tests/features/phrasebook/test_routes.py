"""Controller tests: category/value reads go straight to the repository; a
single "resolve or raise" (get_category) and every mutation go through
`src.features.phrasebook.operations`, patched here exactly like the retired
manager mock (see tests/features/user_groups/test_routes.py for the
established pattern)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.phrasebook import routes as routes_module
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookStateFilter
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.features.phrasebook.routes import PhrasebookController


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def value_repository():
    return Mock(spec=PhrasebookValueRepository)


@pytest.fixture
def controller(category_repository, value_repository):
    return PhrasebookController(
        category_repository=category_repository,
        value_repository=value_repository,
        plugin_registry=Mock(),
        preview_generator=Mock(),
        generation_orchestrator=Mock(),
    )


@pytest.fixture
def user():
    return SimpleNamespace(id="user-1")


@pytest.fixture
def sample_category():
    return PhrasebookCategory(
        id="cat-1",
        name="Test",
        path="test",
        parent_id=None,
        user_id="user-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


async def test_get_categories_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_all.return_value = [sample_category]

    result = await controller.get_categories(user)

    assert result.success
    assert result.data["categories"][0]["id"] == "cat-1"
    category_repository.get_all.assert_called_once_with("user-1", PhrasebookStateFilter.ALL)


async def test_get_root_categories_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_children.return_value = [sample_category]

    result = await controller.get_categories(user, root_only=True)

    assert result.success
    category_repository.get_children.assert_called_once_with(None, "user-1")


async def test_get_category_children_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_children.return_value = [sample_category]

    result = await controller.get_category_children("parent-1", user)

    assert result.success
    category_repository.get_children.assert_called_once_with("parent-1", "user-1")


async def test_get_category_values_read_from_repository(
    controller, mock_operations, category_repository, value_repository, user, sample_category
):
    mock_operations.get_category.return_value = sample_category
    value_repository.get_by_category.return_value = []

    result = await controller.get_category("cat-1", user)

    assert result.success
    mock_operations.get_category.assert_called_once_with(category_repository, "cat-1", "user-1")
    value_repository.get_by_category.assert_called_once_with("cat-1", "user-1")


async def test_find_delegates_to_operation(controller, mock_operations, category_repository, value_repository, user):
    mock_operations.find_phrasebook.return_value = {
        "query": "dog", "categories": [], "values": [], "total_categories": 0, "total_values": 0,
    }

    result = await controller.find("dog", 20, user)

    assert result.success
    assert result.data["query"] == "dog"
    mock_operations.find_phrasebook.assert_called_once_with(category_repository, value_repository, "user-1", "dog", 20)


async def test_find_reports_failure(controller, mock_operations, user):
    mock_operations.find_phrasebook.side_effect = RuntimeError("boom")

    result = await controller.find("dog", 20, user)

    assert not result.success
    assert result.error == "find_failed"
