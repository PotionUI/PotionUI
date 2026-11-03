import pytest

from src.features.presets.fixture_form import inject_unresolvable_defaults, requires_by_reaction
from src.features.presets.templates import FieldTemplate


def _field(name, type_, reactions=None, required=False):
    return FieldTemplate(name=name, type=type_, required=required, reactions=reactions)


REQUIRE = [{"when": {"field": "task", "equals": "style"}, "then": {"update_validation": {"required": True}}}]


def test_a_reaction_that_requires_the_field_is_detected():
    assert requires_by_reaction(_field("a", "string", REQUIRE)) is True
    assert requires_by_reaction(_field("a", "string", [{"when": {}, "then": {"set_visibility": True}}])) is False
    assert requires_by_reaction(_field("a", "string")) is False


def test_fixture_fills_text_fields_that_a_reaction_requires():
    data = {}
    inject_unresolvable_defaults(
        [_field("needed", "string", REQUIRE), _field("optional", "string"), _field("static", "string", required=True)],
        data,
        "tag",
    )
    assert data == {"needed": "tag placeholder text", "static": "tag placeholder text"}


def test_bind_fixture_form_retries_with_a_mask_only_when_binding_demands_one(monkeypatch):
    from src.features.forms.binding import FormBindingError
    from src.features.presets import fixture_form

    class Bound:
        values = {"ok": True}

    seen = []

    def fake_bind(preset, mode, form_name, raw_form_data, user_id, storage_dir):
        seen.append(dict(raw_form_data))
        if "source_image_inpaint_mask" not in raw_form_data:
            raise FormBindingError(["source_image: paint"], {"source_image": ["paint"]}, [], [])
        return Bound()

    monkeypatch.setattr(fixture_form, "bind_form", fake_bind)
    assert fixture_form.bind_fixture_form(None, "m", {"source_image": "a.png"}, mask_prefix="p/") == {"ok": True}
    assert seen[-1]["source_image_inpaint_mask"] == "p/source_image_inpaint_mask.png"
    assert len(seen) == 2

    def always_fail(*args, **kwargs):
        raise FormBindingError(["x"], {}, [], [])

    monkeypatch.setattr(fixture_form, "bind_form", always_fail)
    with pytest.raises(FormBindingError):
        fixture_form.bind_fixture_form(None, "m", {})
