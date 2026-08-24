"""Controller tests: category/value reads go straight to the repository, not the manager."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookStateFilter
from src.features.phrasebook.manager import PhrasebookManager
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.features.phrasebook.routes import PhrasebookController


@pytest.fixture
def manager():
    # spec'd so a call to a removed pass-through (e.g. get_categories) raises
    # AttributeError instead of silently succeeding on an unconstrained Mock.
    return Mock(spec=PhrasebookManager)


@pytest.fixture
def category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def value_repository():
    return Mock(spec=PhrasebookValueRepository)


@pytest.fixture
def controller(manager, category_repository, value_repository):
    return PhrasebookController(
        phrasebook_manager=manager,
        category_repository=category_repository,
        value_repository=value_repository,
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
    controller, manager, value_repository, user, sample_category
):
    manager.get_category_by_id.return_value = sample_category
    value_repository.get_by_category.return_value = []

    result = await controller.get_category("cat-1", user)

    assert result.success
    manager.get_category_by_id.assert_called_once_with("cat-1", "user-1")
    value_repository.get_by_category.assert_called_once_with("cat-1", "user-1")
