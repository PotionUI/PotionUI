"""find_phrasebook: parameter handling, span reporting and ranking over
repository-supplied candidates."""
from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.phrasebook import operations
from src.features.phrasebook.dto import PhrasebookCategory
from src.features.phrasebook.operations.find import InvalidFields, parse_fields
from src.features.phrasebook.operations.matching import InvalidPattern
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


@pytest.fixture
def category_repository():
    repo = Mock(spec=PhrasebookCategoryRepository)
    repo.list_for_find.return_value = []
    return repo


@pytest.fixture
def value_repository():
    repo = Mock(spec=PhrasebookValueRepository)
    repo.list_for_find.return_value = []
    return repo


def category(cat_id, name, path, description="", is_active=True):
    return PhrasebookCategory(
        id=cat_id, name=name, path=path, parent_id=None, description=description,
        is_active=is_active, user_id="user-1",
        created_at=datetime.now(), updated_at=datetime.now(),
    )


def value(val_id, label, text, category_path="animals", is_active=True):
    return {
        "id": val_id, "category_id": "cat-1", "label": label, "value": text,
        "sort_order": 0, "is_active": is_active, "user_id": "user-1",
        "category_path": category_path, "category_name": "Animals", "category_is_active": True,
    }


def find(category_repository, value_repository, query, **kwargs):
    return operations.find_phrasebook(category_repository, value_repository, "user-1", query, **kwargs)


def test_blank_query_returns_empty_without_touching_repositories(category_repository, value_repository):
    result = find(category_repository, value_repository, "   ", mode="word", scope="values")

    assert result == {
        "query": "", "mode": "word", "case_sensitive": False, "scope": "values",
        "categories": [], "values": [], "total_categories": 0, "total_values": 0,
    }
    category_repository.list_for_find.assert_not_called()
    value_repository.list_for_find.assert_not_called()


def test_contains_prefilters_with_trimmed_query_and_reports_spans(category_repository, value_repository):
    category_repository.list_for_find.return_value = [category("c1", "Dogs", "animals.dogs", "dog photos")]
    value_repository.list_for_find.return_value = [value("v1", "Puppy", "a small DOG")]

    result = find(category_repository, value_repository, "  dog ")

    category_repository.list_for_find.assert_called_once_with("user-1", "dog", "", True)
    value_repository.list_for_find.assert_called_once_with("user-1", "dog", "", True)
    assert result["query"] == "dog"
    assert result["categories"][0]["matches"] == [
        {"field": "name", "start": 0, "end": 3},
        {"field": "path", "start": 8, "end": 11},
        {"field": "description", "start": 0, "end": 3},
    ]
    assert result["values"][0]["matches"] == [{"field": "value", "start": 8, "end": 11}]
    assert result["total_categories"] == 1
    assert result["total_values"] == 1


def test_candidates_without_a_span_are_dropped(category_repository, value_repository):
    value_repository.list_for_find.return_value = [value("v1", "Kitten", "a small cat")]

    result = find(category_repository, value_repository, "dog", scope="values")

    assert result["values"] == []
    assert result["total_values"] == 0


def test_word_mode_prefilters_by_substring_but_matches_whole_words(category_repository, value_repository):
    value_repository.list_for_find.return_value = [
        value("hot", "Hotdog", "x"),
        value("plain", "Dog", "x"),
    ]

    result = find(category_repository, value_repository, "dog", mode="word", scope="values")

    value_repository.list_for_find.assert_called_once_with("user-1", "dog", "", True)
    assert [v["id"] for v in result["values"]] == ["plain"]


def test_regex_mode_skips_the_sql_prefilter(category_repository, value_repository):
    value_repository.list_for_find.return_value = [value("v1", "Puppy", "dog dug")]

    result = find(category_repository, value_repository, r"d.g", mode="regex", scope="values")

    value_repository.list_for_find.assert_called_once_with("user-1", None, "", True)
    assert result["values"][0]["matches"] == [
        {"field": "value", "start": 0, "end": 3},
        {"field": "value", "start": 4, "end": 7},
    ]


def test_invalid_regex_raises(category_repository, value_repository):
    with pytest.raises(InvalidPattern):
        find(category_repository, value_repository, "(dog", mode="regex")
    value_repository.list_for_find.assert_not_called()


def test_case_sensitive(category_repository, value_repository):
    value_repository.list_for_find.return_value = [value("v1", "Dog", "dog")]

    result = find(category_repository, value_repository, "dog", case_sensitive=True, scope="values")

    assert result["case_sensitive"] is True
    assert result["values"][0]["matches"] == [{"field": "value", "start": 0, "end": 3}]


def test_scope_values_only_queries_values(category_repository, value_repository):
    find(category_repository, value_repository, "dog", scope="values")
    category_repository.list_for_find.assert_not_called()
    value_repository.list_for_find.assert_called_once()


def test_scope_categories_only_queries_categories(category_repository, value_repository):
    find(category_repository, value_repository, "dog", scope="categories")
    category_repository.list_for_find.assert_called_once()
    value_repository.list_for_find.assert_not_called()


def test_unknown_scope_is_rejected(category_repository, value_repository):
    with pytest.raises(ValueError):
        find(category_repository, value_repository, "dog", scope="everything")


def test_include_inactive_and_path_prefix_are_forwarded(category_repository, value_repository):
    find(category_repository, value_repository, "dog", include_inactive=False, path_prefix="animals")

    category_repository.list_for_find.assert_called_once_with("user-1", "dog", "animals", False)
    value_repository.list_for_find.assert_called_once_with("user-1", "dog", "animals", False)


def test_fields_restrict_which_value_texts_match(category_repository, value_repository):
    value_repository.list_for_find.return_value = [
        value("by-label", "Dog", "x"),
        value("by-value", "Puppy", "a dog"),
    ]

    result = find(category_repository, value_repository, "dog", scope="values", fields=["value"])

    assert [v["id"] for v in result["values"]] == ["by-value"]
    assert result["values"][0]["matches"] == [{"field": "value", "start": 2, "end": 5}]


def test_ranks_exact_then_prefix_then_substring_then_label_then_path(category_repository, value_repository):
    value_repository.list_for_find.return_value = [
        value("sub", "Hotdog", "x"),
        value("prefix-b", "Dogma", "x", category_path="z"),
        value("prefix-a", "Dogma", "x", category_path="a"),
        value("exact", "dog", "x"),
        value("exact-value", "Zebra", "Dog"),
    ]
    category_repository.list_for_find.return_value = [
        category("c-sub", "Hotdog", "food.hotdog"),
        category("c-exact", "dog", "z.dog"),
        category("c-prefix", "Doggo", "a.doggo"),
    ]

    result = find(category_repository, value_repository, "dog")

    assert [v["id"] for v in result["values"]] == ["exact", "exact-value", "prefix-a", "prefix-b", "sub"]
    assert [c["id"] for c in result["categories"]] == ["c-exact", "c-prefix", "c-sub"]


def test_limit_slices_each_kind_but_totals_count_every_hit(category_repository, value_repository):
    value_repository.list_for_find.return_value = [value(f"v{i}", f"Dog {i}", "x") for i in range(5)]
    category_repository.list_for_find.return_value = [category(f"c{i}", f"Dog {i}", f"dog.{i}") for i in range(3)]

    result = find(category_repository, value_repository, "dog", limit=2)

    assert len(result["values"]) == 2
    assert result["total_values"] == 5
    assert len(result["categories"]) == 2
    assert result["total_categories"] == 3


def test_limit_is_clamped(category_repository, value_repository):
    value_repository.list_for_find.return_value = [value(f"v{i}", f"Dog {i}", "x") for i in range(3)]

    assert len(find(category_repository, value_repository, "dog", scope="values", limit=0)["values"]) == 1
    assert len(find(category_repository, value_repository, "dog", scope="values", limit=5000)["values"]) == 3


class TestParseFields:
    def test_default_is_both(self):
        assert parse_fields(None) == ["label", "value"]
        assert parse_fields("  ") == ["label", "value"]

    def test_subset_and_dedupe(self):
        assert parse_fields("value") == ["value"]
        assert parse_fields("value, label, value") == ["value", "label"]

    def test_unknown_field_rejected(self):
        with pytest.raises(InvalidFields):
            parse_fields("label,description")
