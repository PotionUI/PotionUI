import pytest

from src.features.presets.schema import FieldSpec, validate_manifest

MANIFEST = {
    "schema": 1,
    "id": "01FORMULASCHEMAAAAAAAAAAAAA",
    "name": "Foo",
    "version": "1.0.0",
    "category": "image",
    "engine": "native",
    "modes": ["txt2img"],
}


def _manifest_errors(formulas):
    _, errors = validate_manifest({**MANIFEST, "formulas": formulas})
    return errors


def test_a_valid_catalog_is_accepted():
    formulas = {"groups": {"speed": {"label": "Speed"}, "models": {"label": "Models", "preselect": False}}}

    manifest, errors = validate_manifest({**MANIFEST, "formulas": formulas})

    assert errors == []
    assert manifest.formulas.groups["models"].preselect is False
    assert manifest.formulas.groups["speed"].preselect is True


def test_an_unknown_key_in_the_catalog_is_rejected():
    errors = _manifest_errors({"groups": {"speed": {"label": "Speed"}}, "group": {}})

    assert any("formulas.group" in e for e in errors)


def test_an_unknown_key_inside_a_group_is_rejected():
    errors = _manifest_errors({"groups": {"models": {"label": "Models", "preselct": False}}})

    assert any("preselct" in e for e in errors)


@pytest.mark.parametrize("value", ["speed", False, None])
def test_a_field_formula_accepts_a_group_id_or_false(value):
    assert FieldSpec(type="slider", name="steps", formula=value).formula == value


@pytest.mark.parametrize("value", [3, True])
def test_a_field_formula_rejects_numbers_and_true(value):
    with pytest.raises(ValueError, match="formula"):
        FieldSpec(type="slider", name="steps", formula=value)
