from src.features.forms.binding import bind_form, FormBindingError
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _category(key, label=None, multi=False, allow_custom=None, tags=None):
    entry = {"key": key, "label": label or key, "multi": multi, "tags": tags or []}
    if allow_custom is not None:
        entry["allow_custom"] = allow_custom
    return entry


def _tags_field(name="style", required=False, categories=None, **extra_config):
    configuration = {"separator": ", ", "categories": categories or [], **extra_config}
    return FieldTemplate(type="tags", name=name, required=required, configuration=configuration)


def _preset(fields, mode="txt2img", preset_configuration=None):
    forms = [FormTemplate(name="default", fields=fields, default=True, order=0)]
    return PresetTemplate(
        id="preset_1", name="Preset One", version="1.0.0", path="/presets/preset_1",
        modes={mode: ModeTemplate(forms=forms, pipes=[])},
        configuration=preset_configuration,
    )


class TestJoinedStringBinding:
    def test_string_submission_is_reordered_into_declared_category_order(self):
        categories = [
            _category("colour", tags=["red", "blue"]),
            _category("shape", multi=True, tags=["round"]),
            _category("more", multi=True, tags=[]),
        ]
        preset = _preset([_tags_field(categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": "round, red, mystery"})
        assert bound.values["style"] == "red, round, mystery"

    def test_structured_map_submission_binds_the_joined_string(self):
        categories = [_category("colour", tags=["red", "blue"]), _category("shape", multi=True, tags=["round"])]
        preset = _preset([_tags_field(categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": {"shape": ["round"], "colour": ["red"]}})
        assert bound.values["style"] == "red, round"

    def test_default_separator_is_comma_space(self):
        categories = [_category("a", multi=True, tags=[])]
        preset = _preset([_tags_field(categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": {"a": ["x", "y"]}})
        assert bound.values["style"] == "x, y"


class TestStructuredMapBinding:
    def test_name_tags_key_carries_the_structured_map(self):
        categories = [_category("colour", tags=["red"]), _category("more", multi=True, tags=[])]
        preset = _preset([_tags_field(categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": "red, mystery"})
        assert bound.values["style_tags"] == {"colour": ["red"], "more": ["mystery"]}

    def test_no_submission_binds_an_empty_map_and_empty_string(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(categories=categories)])
        bound = bind_form(preset, "txt2img", None, {})
        assert bound.values["style"] == ""
        assert bound.values["style_tags"] == {"colour": []}


class TestRequiredValidation:
    def test_required_with_no_tags_anywhere_fails(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(required=True, categories=categories)])
        try:
            bind_form(preset, "txt2img", None, {"style": {"colour": []}})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "required" in e.field_errors["style"][0]

    def test_required_with_a_blank_string_fails(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(required=True, categories=categories)])
        try:
            bind_form(preset, "txt2img", None, {"style": "   "})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "required" in e.field_errors["style"][0]

    def test_required_with_at_least_one_tag_passes(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(required=True, categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": {"colour": ["red"]}})
        assert bound.values["style"] == "red"

    def test_not_required_with_no_tags_passes(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(required=False, categories=categories)])
        bound = bind_form(preset, "txt2img", None, {"style": {}})
        assert bound.values["style"] == ""


class TestFieldLevelValidation:
    def test_max_tags_exceeded_is_an_error(self):
        categories = [_category("a", multi=True, tags=[])]
        preset = _preset([_tags_field(categories=categories, max_tags=1)])
        try:
            bind_form(preset, "txt2img", None, {"style": {"a": ["x", "y"]}})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "too many tags" in e.field_errors["style"][0]

    def test_multi_false_category_with_two_tags_is_an_error(self):
        categories = [_category("colour", multi=False, tags=["red", "blue"])]
        preset = _preset([_tags_field(categories=categories)])
        try:
            bind_form(preset, "txt2img", None, {"style": {"colour": ["red", "blue"]}})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "allows only one tag" in e.field_errors["style"][0]

    def test_allow_custom_false_rejects_undeclared_tags(self):
        categories = [_category("colour", allow_custom=False, tags=["red"])]
        preset = _preset([_tags_field(categories=categories)])
        try:
            bind_form(preset, "txt2img", None, {"style": {"colour": ["purple"]}})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "does not allow custom tags" in e.field_errors["style"][0]

    def test_unknown_category_key_is_an_error(self):
        categories = [_category("colour", tags=["red"])]
        preset = _preset([_tags_field(categories=categories)])
        try:
            bind_form(preset, "txt2img", None, {"style": {"nope": ["x"]}})
            assert False, "expected FormBindingError"
        except FormBindingError as e:
            assert "unknown categor" in e.field_errors["style"][0]


class TestConfigIndirection:
    def test_config_key_resolves_against_stored_preset_configuration(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(
            presets_repository.preset_repo,
            "get_preset_configuration",
            lambda preset_id: {"style_categories": [_category("mood", multi=True, tags=["dark"])]},
        )
        preset = _preset(
            [_tags_field(categories="@config:style_categories")],
            preset_configuration={"style_categories": {"type": "tag_categories"}},
        )
        bound = bind_form(preset, "txt2img", None, {"style": {"mood": ["dark"]}})
        assert bound.values["style"] == "dark"

    def test_unset_config_key_falls_back_to_declared_default(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(presets_repository.preset_repo, "get_preset_configuration", lambda preset_id: {})
        default_categories = [_category("language", tags=["English"])]
        preset = _preset(
            [_tags_field(categories="@config:style_categories")],
            preset_configuration={"style_categories": {"type": "tag_categories", "default": default_categories}},
        )
        bound = bind_form(preset, "txt2img", None, {"style": {"language": ["English"]}})
        assert bound.values["style"] == "English"
