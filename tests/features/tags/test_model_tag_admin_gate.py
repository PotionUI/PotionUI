"""Regression: MODEL tags are global and admin-only to create/edit/delete.

GENERATION tags stay per-user owner-scoped exactly as before; only the global
MODEL-type branch is gated to administrators.
"""
from unittest.mock import Mock, patch

import pytest

from src.features.tags import operations
from src.features.tags.dto import Tag, TagType, CreateTagRequest, UpdateTagRequest


def _collaborators():
    return Mock(), Mock()  # tag_repository, plugin_registry


def _tag(tag_type, user_id=None):
    return Tag(id="t1", name="n", type=tag_type, user_id=user_id)


# ---- create -------------------------------------------------------------

def test_create_model_tag_denied_for_non_admin():
    tag_repository, plugin_registry = _collaborators()
    with pytest.raises(ValueError, match="access denied"):
        operations.create_tag(tag_repository, plugin_registry, CreateTagRequest(name="anime", type=TagType.MODEL), "u1", is_admin=False)
    tag_repository.create_tag.assert_not_called()


@patch("src.features.tags.operations.crud.execute_hook", return_value=({}, False))
def test_create_model_tag_allowed_for_admin(mock_execute_hook):
    tag_repository, plugin_registry = _collaborators()
    tag_repository.create_tag.return_value = _tag(TagType.MODEL)
    operations.create_tag(tag_repository, plugin_registry, CreateTagRequest(name="anime", type=TagType.MODEL), "u1", is_admin=True)
    tag_repository.create_tag.assert_called_once()


@patch("src.features.tags.operations.crud.execute_hook", return_value=({}, False))
def test_create_generation_tag_allowed_for_non_admin(mock_execute_hook):
    tag_repository, plugin_registry = _collaborators()
    tag_repository.create_tag.return_value = _tag(TagType.GENERATION, user_id="u1")
    operations.create_tag(tag_repository, plugin_registry, CreateTagRequest(name="fav", type=TagType.GENERATION), "u1", is_admin=False)
    tag_repository.create_tag.assert_called_once()


# ---- update -------------------------------------------------------------

def test_update_model_tag_denied_for_non_admin():
    tag_repository, plugin_registry = _collaborators()
    tag_repository.get_tag_by_id.return_value = _tag(TagType.MODEL)
    with pytest.raises(ValueError, match="access denied"):
        operations.update_tag(tag_repository, plugin_registry, "t1", UpdateTagRequest(name="new"), "u1", is_admin=False)


# ---- delete -------------------------------------------------------------

def test_delete_model_tag_denied_for_non_admin():
    tag_repository, plugin_registry = _collaborators()
    tag_repository.get_tag_by_id.return_value = _tag(TagType.MODEL)
    with pytest.raises(ValueError, match="access denied"):
        operations.delete_tag(tag_repository, plugin_registry, Mock(), Mock(), "t1", "u1", is_admin=False)
