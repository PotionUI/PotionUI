"""LibraryDeleteCriteria.is_empty, owner_criteria's validation, and
delete_items' per-item delegation to the same path a single delete uses."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from src.features.library.criteria_delete import (
    LibraryDeleteCriteria,
    delete_items,
    owner_criteria,
    preview_items,
)


def _request(**overrides):
    defaults = dict(
        tag_ids=None, older_than_days=None, created_from=None, created_to=None, media_type=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCriteriaIsEmpty:

    def test_no_criteria_is_empty(self):
        assert LibraryDeleteCriteria(user_id="u1").is_empty()

    def test_tag_ids_is_not_empty(self):
        assert not LibraryDeleteCriteria(user_id="u1", tag_ids=["tag-1"]).is_empty()

    def test_older_than_days_is_not_empty(self):
        assert not LibraryDeleteCriteria(user_id="u1", older_than_days=30).is_empty()

    def test_created_from_is_not_empty(self):
        assert not LibraryDeleteCriteria(user_id="u1", created_from="2026-01-01").is_empty()

    def test_created_to_is_not_empty(self):
        assert not LibraryDeleteCriteria(user_id="u1", created_to="2026-01-01").is_empty()

    def test_media_type_is_not_empty(self):
        assert not LibraryDeleteCriteria(user_id="u1", media_type="video").is_empty()

    def test_user_id_alone_does_not_count_as_a_criterion(self):
        assert LibraryDeleteCriteria(user_id="u1").is_empty()


class TestOwnerCriteria:

    def _collaborators(self, tag=None):
        collaborators = Mock()
        collaborators.tag_repository.get_tag_by_id.return_value = tag
        return collaborators

    def test_builds_criteria_scoped_to_the_user(self):
        collaborators = self._collaborators()
        request = _request(older_than_days=30)

        criteria = owner_criteria(collaborators, request, "u1")

        assert criteria == LibraryDeleteCriteria(user_id="u1", older_than_days=30)

    def test_validates_tag_ownership(self):
        tag = SimpleNamespace(type="UPLOAD", user_id="u1")
        collaborators = self._collaborators(tag=tag)
        request = _request(tag_ids=["tag-1"])

        criteria = owner_criteria(collaborators, request, "u1")

        assert criteria.tag_ids == ["tag-1"]
        collaborators.tag_repository.get_tag_by_id.assert_called_once_with("tag-1")

    def test_rejects_a_tag_owned_by_someone_else(self):
        tag = SimpleNamespace(type="UPLOAD", user_id="someone-else")
        collaborators = self._collaborators(tag=tag)
        request = _request(tag_ids=["tag-1"])

        with pytest.raises(ValueError):
            owner_criteria(collaborators, request, "u1")

    def test_rejects_an_unknown_media_type(self):
        collaborators = self._collaborators()
        request = _request(media_type="mesh")

        with pytest.raises(ValueError):
            owner_criteria(collaborators, request, "u1")

    def test_rejects_a_negative_older_than_days(self):
        collaborators = self._collaborators()
        request = _request(older_than_days=-1)

        with pytest.raises(ValueError):
            owner_criteria(collaborators, request, "u1")

    def test_rejects_a_malformed_date(self):
        collaborators = self._collaborators()
        request = _request(created_from="not-a-date")

        with pytest.raises(ValueError):
            owner_criteria(collaborators, request, "u1")

    def test_rejects_created_from_after_created_to(self):
        collaborators = self._collaborators()
        request = _request(created_from="2026-06-01", created_to="2026-01-01")

        with pytest.raises(ValueError):
            owner_criteria(collaborators, request, "u1")


class TestPreviewItems:

    def test_it_counts_the_repository_matches(self):
        collaborators = Mock()
        collaborators.repository.find_ids_by_criteria.return_value = ["a", "b"]

        count = preview_items(collaborators, LibraryDeleteCriteria(user_id="u1", older_than_days=30))

        assert count == 2
        collaborators.repository.find_ids_by_criteria.assert_called_once_with(
            user_id="u1", tag_ids=None, older_than_days=30, created_from=None, created_to=None, media_type=None,
        )


class TestDeleteItems:

    def test_deletes_every_candidate_through_the_single_delete_path(self, monkeypatch):
        collaborators = Mock()
        collaborators.repository.find_ids_by_criteria.return_value = ["a", "b"]
        mock_delete = Mock()
        monkeypatch.setattr("src.features.library.criteria_delete.delete_item", mock_delete)

        summary = delete_items(collaborators, LibraryDeleteCriteria(user_id="u1", media_type="video"))

        assert summary == {"deleted_count": 2, "files_deleted": 2}
        mock_delete.assert_has_calls([call(collaborators, "a", "u1"), call(collaborators, "b", "u1")])

    def test_a_candidate_that_fails_to_delete_is_not_counted(self, monkeypatch):
        collaborators = Mock()
        collaborators.repository.find_ids_by_criteria.return_value = ["a", "b"]
        mock_delete = Mock(side_effect=[None, ValueError("gone")])
        monkeypatch.setattr("src.features.library.criteria_delete.delete_item", mock_delete)

        summary = delete_items(collaborators, LibraryDeleteCriteria(user_id="u1", media_type="video"))

        assert summary == {"deleted_count": 1, "files_deleted": 1}
