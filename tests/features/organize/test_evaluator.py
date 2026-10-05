import pytest

from src.features.organize.evaluator import compare, evaluate, sql_filter
from src.platform.plugins.organize import OrganizeFactDefinition, OrganizeItem, OrganizeRegistry


@pytest.mark.parametrize("operator,actual,expected,result", [
    ("is", ["a", "b"], "a", True),
    ("is", ["a"], "c", False),
    ("is_any_of", ["a"], ["c", "a"], True),
    ("is_any_of", [], ["a"], False),
    ("is_not", ["a"], "b", True),
    ("is_not", ["a"], "a", False),
    ("is_not", [], ["a"], True),
])
def test_model_ref_operators(operator, actual, expected, result):
    assert compare("model_ref", operator, actual, expected) is result


def test_model_refs_compare_exactly_while_enums_ignore_case():
    assert compare("model_ref", "is", ["ABC"], "abc") is False
    assert compare("enum", "is", "TXT2IMG", "txt2img") is True


@pytest.mark.parametrize("operator,sizes,target,result", [
    ("is", [{"width": 1344, "height": 768}], {"width": 1344, "height": 768}, True),
    ("is", [{"width": 768, "height": 1344}], {"width": 1344, "height": 768}, False),
    ("at_least", [{"width": 2048, "height": 1024}], {"width": 1920, "height": 1024}, True),
    ("at_least", [{"width": 2048, "height": 900}], {"width": 1920, "height": 1024}, False),
    ("at_most", [{"width": 512, "height": 512}], {"width": 1024, "height": 1024}, True),
    ("is", [{"width": 512, "height": 512}, {"width": 1344, "height": 768}], {"width": 1344, "height": 768}, True),
    ("is", [{"width": None, "height": 768}], {"width": 1344, "height": 768}, False),
])
def test_size_operators_match_when_any_output_matches(operator, sizes, target, result):
    assert compare("size", operator, sizes, target) is result


def test_number_operators():
    assert compare("number", "at_least", [3.0, 12.0], 10) is True
    assert compare("number", "at_most", [12.0], 10) is False
    assert compare("number", "is", 4, 4) is True
    assert compare("number", "at_least", [], 1) is False
    assert compare("number", "at_least", [True], 0) is False


def test_text_is_a_case_insensitive_substring():
    assert compare("text", "contains", ["A Sunset over the sea"], "sunset") is True
    assert compare("text", "contains", "nothing here", "sunset") is False
    assert compare("text", "not_contains", ["a dog"], "cat") is True
    assert compare("text", "not_contains", ["a CAT"], "cat") is False
    assert compare("text", "contains", ["anything"], "") is False


def test_tag_list_has_means_every_tag_and_has_not_means_none():
    assert compare("tag_list", "has", ["Cat", "pet"], ["cat", "PET"]) is True
    assert compare("tag_list", "has", ["cat"], ["cat", "pet"]) is False
    assert compare("tag_list", "has_not", ["cat"], ["dog", "bird"]) is True
    assert compare("tag_list", "has_not", ["cat"], ["dog", "cat"]) is False


def test_bool_and_unknown_operators():
    assert compare("bool", "is", True, True) is True
    assert compare("bool", "is", False, True) is False
    assert compare("enum", "at_least", "a", "a") is False
    assert compare("nope", "is", "a", "a") is False


def _registry():
    registry = OrganizeRegistry()
    registry.register_fact(OrganizeFactDefinition(
        key="color", label="Color", subjects=("generation",), kind="enum",
        extract=lambda item: item.get("color"), sql=lambda op, value, alias: (f"{alias}.color = ?", [value]),
    ))
    registry.register_fact(OrganizeFactDefinition(
        key="size", label="Size", subjects=("generation",), kind="number", extract=lambda item: item.get("size"),
    ))

    def broken(item):
        raise RuntimeError("boom")

    registry.register_fact(OrganizeFactDefinition(
        key="broken", label="Broken", subjects=("generation",), kind="text", extract=broken,
    ))
    return registry


def item(**data):
    return OrganizeItem(subject="generation", item_id="g1", user_id="u1", data=data)


def test_all_needs_every_condition_and_any_needs_one():
    registry = _registry()
    conditions = [{"fact": "color", "operator": "is", "value": "red"}, {"fact": "size", "operator": "at_least", "value": 5}]

    assert evaluate(registry, "all", conditions, item(color="red", size=7)) is True
    assert evaluate(registry, "all", conditions, item(color="red", size=2)) is False
    assert evaluate(registry, "any", conditions, item(color="blue", size=7)) is True
    assert evaluate(registry, "any", conditions, item(color="blue", size=1)) is False


def test_no_conditions_matches_everything():
    assert evaluate(_registry(), "all", [], item()) is True


def test_unknown_or_failing_facts_never_match():
    registry = _registry()
    assert evaluate(registry, "any", [{"fact": "gone", "operator": "is", "value": "x"}], item()) is False
    assert evaluate(registry, "all", [{"fact": "broken", "operator": "contains", "value": "x"}], item()) is False


def test_a_fact_for_another_subject_never_matches():
    registry = _registry()
    upload = OrganizeItem(subject="upload", item_id="u", user_id="u1", data={"color": "red"})
    assert evaluate(registry, "all", [{"fact": "color", "operator": "is", "value": "red"}], upload) is False


def test_sql_filter_is_complete_only_when_every_fact_has_a_predicate():
    registry = _registry()
    color = {"fact": "color", "operator": "is", "value": "red"}
    size = {"fact": "size", "operator": "at_least", "value": 5}

    assert sql_filter(registry, "generation", "all", [color]) == ("(g.color = ?)", ["red"], True)
    assert sql_filter(registry, "generation", "all", [color, size]) == ("(g.color = ?)", ["red"], False)
    assert sql_filter(registry, "generation", "any", [color, size]) == ("", [], False)
    assert sql_filter(registry, "generation", "any", [color, color]) == ("(g.color = ?) OR (g.color = ?)", ["red", "red"], True)
    assert sql_filter(registry, "generation", "all", []) == ("", [], True)
