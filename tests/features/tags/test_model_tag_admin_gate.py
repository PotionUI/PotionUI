"""Regression: MODEL tags are global and admin-only to create/edit/delete.

GENERATION tags stay per-user owner-scoped exactly as before; only the global
MODEL-type branch is gated to administrators.
"""
from unittest.mock import Mock, patch

import pytest

from src.features.tags.manager import TagManager
from src.features.tags.dto import Tag, TagType, CreateTagRequest, UpdateTagRequest


def _manager():
    mgr = TagManager(
        tag_repository=Mock(),
        plugin_registry=Mock(),
        database_preset_repository=Mock(),
        file_preset_repository=Mock(),
    )
    return mgr


def _tag(tag_type, user_id=None):
    return Tag(id="t1", name="n", type=tag_type, user_id=user_id)


# ---- create -------------------------------------------------------------

def test_create_model_tag_denied_for_non_admin():
    mgr = _manager()
    with pytest.raises(ValueError, match="access denied"):
        mgr.create_tag(CreateTagRequest(name="anime", type=TagType.MODEL), "u1", is_admin=False)
    mgr.repository.create_tag.assert_not_called()


@patch("src.features.tags.manager.execute_hook", return_value=({}, False))
def test_create_model_tag_allowed_for_admin(mock_execute_hook):
    mgr = _manager()
    mgr.repository.create_tag.return_value = _tag(TagType.MODEL)
    mgr.create_tag(CreateTagRequest(name="anime", type=TagType.MODEL), "u1", is_admin=True)
    mgr.repository.create_tag.assert_called_once()


@patch("src.features.tags.manager.execute_hook", return_value=({}, False))
def test_create_generation_tag_allowed_for_non_admin(mock_execute_hook):
    mgr = _manager()
    mgr.repository.create_tag.return_value = _tag(TagType.GENERATION, user_id="u1")
    mgr.create_tag(CreateTagRequest(name="fav", type=TagType.GENERATION), "u1", is_admin=False)
    mgr.repository.create_tag.assert_called_once()


# ---- update -------------------------------------------------------------

def test_update_model_tag_denied_for_non_admin():
    mgr = _manager()
    mgr.repository.get_tag_by_id.return_value = _tag(TagType.MODEL)
    with pytest.raises(ValueError, match="access denied"):
        mgr.update_tag("t1", UpdateTagRequest(name="new"), "u1", is_admin=False)


# ---- delete -------------------------------------------------------------

def test_delete_model_tag_denied_for_non_admin():
    mgr = _manager()
    mgr.repository.get_tag_by_id.return_value = _tag(TagType.MODEL)
    with pytest.raises(ValueError, match="access denied"):
        mgr.delete_tag("t1", "u1", is_admin=False)
