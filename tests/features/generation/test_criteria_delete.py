"""GenerationDeleteCriteria.is_empty and delete_generations' grouping of
candidates by owner before handing them to the history facade."""

from unittest.mock import Mock

from src.features.generation.criteria_delete import (
    GenerationDeleteCriteria,
    delete_generations,
    preview_generations,
)


class TestCriteriaIsEmpty:

    def test_no_criteria_is_empty(self):
        assert GenerationDeleteCriteria().is_empty()

    def test_older_than_days_is_not_empty(self):
        assert not GenerationDeleteCriteria(older_than_days=30).is_empty()

    def test_created_from_is_not_empty(self):
        assert not GenerationDeleteCriteria(created_from="2026-01-01").is_empty()

    def test_created_to_is_not_empty(self):
        assert not GenerationDeleteCriteria(created_to="2026-01-01").is_empty()

    def test_without_media_is_not_empty(self):
        assert not GenerationDeleteCriteria(without_media=True).is_empty()

    def test_statuses_is_not_empty(self):
        assert not GenerationDeleteCriteria(statuses=["failed"]).is_empty()

    def test_tag_ids_is_not_empty(self):
        assert not GenerationDeleteCriteria(tag_ids=["tag-1"]).is_empty()

    def test_keep_favorites_alone_does_not_count_as_a_criterion(self):
        assert GenerationDeleteCriteria(keep_favorites=False).is_empty()

    def test_user_id_alone_does_not_count_as_a_criterion(self):
        assert GenerationDeleteCriteria(user_id="user-1").is_empty()


class TestPreviewGenerations:

    def test_it_counts_the_repository_matches(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("g1", "u1"), ("g2", "u2")]

        assert preview_generations(repository, GenerationDeleteCriteria(older_than_days=30)) == 2
        repository.find_by_criteria.assert_called_once_with(
            user_id=None, tag_ids=None, older_than_days=30, created_from=None, created_to=None,
            without_media=False, statuses=None, keep_favorites=True,
        )

    def test_it_forwards_owner_scope_and_tags(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("g1", "u1")]

        criteria = GenerationDeleteCriteria(user_id="u1", tag_ids=["tag-a", "tag-b"])
        preview_generations(repository, criteria)

        repository.find_by_criteria.assert_called_once_with(
            user_id="u1", tag_ids=["tag-a", "tag-b"], older_than_days=None,
            created_from=None, created_to=None, without_media=False, statuses=None, keep_favorites=True,
        )


class TestDeleteGenerations:

    def test_candidates_are_grouped_by_owner_before_deletion(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [
            ("g1", "u1"), ("g2", "u1"), ("g3", "u2"),
        ]
        facade = Mock()
        facade.bulk_delete.side_effect = [
            {"deleted_count": 2, "total_files_deleted": 5},
            {"deleted_count": 1, "total_files_deleted": 2},
        ]

        summary = delete_generations(repository, facade, GenerationDeleteCriteria(without_media=True))

        assert summary == {"deleted_count": 3, "files_deleted": 7}
        calls = {call.args[1]: sorted(call.args[0]) for call in facade.bulk_delete.call_args_list}
        assert calls == {"u1": ["g1", "g2"], "u2": ["g3"]}

    def test_a_row_with_no_owner_is_skipped(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("orphan", None)]
        facade = Mock()

        summary = delete_generations(repository, facade, GenerationDeleteCriteria(without_media=True))

        assert summary == {"deleted_count": 0, "files_deleted": 0}
        facade.bulk_delete.assert_not_called()

    def test_owner_scoped_criteria_only_ever_deletes_that_owner(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("g1", "u1")]
        facade = Mock()
        facade.bulk_delete.return_value = {"deleted_count": 1, "total_files_deleted": 0}

        summary = delete_generations(
            repository, facade, GenerationDeleteCriteria(user_id="u1", tag_ids=["tag-1"]),
        )

        assert summary == {"deleted_count": 1, "files_deleted": 0}
        facade.bulk_delete.assert_called_once_with(["g1"], "u1")
