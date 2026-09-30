import pytest

from src.features.cloud.testing.fake import fake_specs
from src.features.forms.binding import FormBindingError, bind_form
from src.features.models.form_refs import make_model_ref
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate

IMAGE_ID = "model-image"
VIDEO_ID = "model-video"
SPECS = {IMAGE_ID: fake_specs()[0], VIDEO_ID: fake_specs()[1]}


def resolver(model_id, driver):
    return SPECS.get(model_id)


def field(name, type_, capability=None, default=None, configuration=None, required=False):
    return FieldTemplate(
        type=type_, name=name, default=default, capability=capability,
        configuration=configuration, required=required,
    )


def form_fields(options_config=None):
    return [
        field("model", "model", default=make_model_ref(IMAGE_ID)),
        field("aspect_ratio", "select", {"model_field": "model", "param": "aspect_ratio"}),
        field("quality", "slider", {"model_field": "model", "param": "quality"}),
        field("background", "checkbox", {"model_field": "model", "param": "background"}),
        field("duration", "slider", {"model_field": "model", "param": "duration_s"}),
        field("references", "image", {"model_field": "model", "input": "reference"}),
        field("provider_options", "cloud_options", {"model_field": "model"}, configuration=options_config),
    ]


def preset(task="txt2img", fields=None):
    pipes = [PipeTemplate(name="cloud_generate", configuration={"task": task})] if task else []
    form = FormTemplate(name="custom", fields=fields or form_fields(), default=True, order=0)
    return PresetTemplate(
        id="preset_1", name="Cloud", version="1.0.0", path="/presets/preset_1", engine="cloud",
        driver="cloud.fake", modes={"txt2img": ModeTemplate(forms=[form], pipes=pipes)},
    )


def bind(raw, *, model=IMAGE_ID, task="txt2img", fields=None, use_resolver=True):
    data = {"model": make_model_ref(model), **raw}
    return bind_form(
        preset(task, fields), "txt2img", None, data, "user_1",
        cloud_capabilities=resolver if use_resolver else None,
    )


def field_errors(raw, **kwargs):
    with pytest.raises(FormBindingError) as raised:
        bind(raw, **kwargs)
    return raised.value.field_errors


def test_supported_values_pass_and_become_the_canonical_params():
    bound = bind({"aspect_ratio": "16:9", "quality": "7", "background": True})

    assert bound.cloud_params == {"aspect_ratio": "16:9", "quality": 7, "background": True}
    assert bound.values["aspect_ratio"] == "16:9"
    assert {"duration", "references"} <= set(bound.stripped)


def test_a_value_for_a_param_the_model_lacks_is_stripped_not_kept():
    bound = bind({"duration": 5, "aspect_ratio": "1:1"})

    assert bound.values["duration"] is None
    assert "duration" in bound.stripped
    assert "duration_s" not in bound.cloud_params
    assert bound.cloud_params["aspect_ratio"] == "1:1"


def test_changing_the_model_changes_what_is_kept_and_what_is_stripped():
    as_image = bind({"aspect_ratio": "1:1", "duration": 5}, model=IMAGE_ID)
    as_video = bind({"aspect_ratio": "1:1", "duration": 5}, model=VIDEO_ID, task="txt2video")

    assert "aspect_ratio" in as_image.cloud_params and "duration_s" not in as_image.cloud_params
    assert as_video.cloud_params == {"duration_s": 5}
    assert "aspect_ratio" in as_video.stripped and as_video.values["aspect_ratio"] is None


def test_an_enum_value_the_model_does_not_offer_is_a_field_error():
    errors = field_errors({"aspect_ratio": "21:9"})

    assert "aspect_ratio" in errors
    assert "Choose one of: 1:1, 16:9" in errors["aspect_ratio"][0]


@pytest.mark.parametrize(("value", "needle"), [(11, "at most 10"), (0, "at least 1"), (2.5, "whole number")])
def test_a_range_value_outside_the_models_span_is_a_field_error(value, needle):
    errors = field_errors({"quality": value})

    assert needle in errors["quality"][0]


def test_a_boolean_param_must_be_a_boolean():
    errors = field_errors({"background": "maybe"}, fields=[
        field("model", "model", default=make_model_ref(IMAGE_ID)),
        field("background", "string", {"model_field": "model", "param": "background"}),
    ])

    assert "true or false" in errors["background"][0]


def test_an_unset_optional_param_is_not_an_error_and_not_a_param():
    bound = bind({})

    assert bound.cloud_params == {}


def test_a_required_param_the_model_insists_on_must_be_given():
    from dataclasses import replace

    from src.features.cloud.contracts import ParamSpec

    required = replace(SPECS[IMAGE_ID], params=(ParamSpec(name="quality", kind="range", minimum=1, maximum=10, required=True),))
    SPECS["model-required"] = required
    try:
        errors = field_errors({"quality": None}, model="model-required")
    finally:
        del SPECS["model-required"]

    assert "required by this model" in errors["quality"][0]


def test_a_media_field_the_model_has_no_role_for_is_stripped():
    bound = bind({"references": "a.png"})

    assert bound.values["references"] is None
    assert "references" in bound.stripped


def test_a_media_field_is_kept_when_the_model_takes_that_role_for_the_task():
    bound = bind({"references": "a.png"}, task="img_edit")

    assert bound.values["references"] == "a.png"
    assert "references" not in bound.stripped


def test_a_param_limited_to_other_tasks_is_stripped_for_this_one():
    from dataclasses import replace
    from src.features.cloud.contracts import ParamSpec

    scoped = replace(SPECS[IMAGE_ID], params=(
        ParamSpec(name="quality", kind="range", minimum=1, maximum=10, tasks=frozenset({"img_edit"})),
    ))
    SPECS["model-scoped"] = scoped
    try:
        stripped_for_txt2img = bind({"quality": 5}, model="model-scoped", task="txt2img")
        kept_for_edit = bind({"quality": 5}, model="model-scoped", task="img_edit")
    finally:
        del SPECS["model-scoped"]

    assert "quality" in stripped_for_txt2img.stripped
    assert kept_for_edit.cloud_params == {"quality": 5}


def test_provider_options_accept_the_models_extras_and_unbound_canonical_params():
    bound = bind({"provider_options": {"x.style": "noir"}})

    assert bound.values["provider_options"] == {"x.style": "noir"}
    assert bound.cloud_params["x.style"] == "noir"


def test_provider_options_drop_unknown_keys_and_params_already_bound_to_a_field():
    bound = bind({"provider_options": {"x.style": "noir", "x.nope": 1, "quality": 3}})

    assert bound.values["provider_options"] == {"x.style": "noir"}
    assert {"provider_options.x.nope", "provider_options.quality"} <= set(bound.stripped)


def test_provider_options_value_is_checked_against_the_spec():
    errors = field_errors({"provider_options": {"x.style": 5}})

    assert "x.style: must be text" in errors["provider_options"][0]


def test_provider_options_must_be_an_object():
    errors = field_errors({"provider_options": "x.style=noir"})

    assert "object" in errors["provider_options"][0]


def test_provider_options_can_be_limited_to_the_providers_own_extras():
    video_fields = form_fields({"include_unbound": False})
    bound = bind({"provider_options": {"x.style": "a", "background": True}}, fields=[
        f for f in video_fields if f.name in {"model", "provider_options"}
    ])

    assert bound.values["provider_options"] == {"x.style": "a"}
    assert "provider_options.background" in bound.stripped


def test_provider_options_offer_an_unbound_canonical_param_when_no_field_binds_it():
    bound = bind({"provider_options": {"background": True}}, fields=[
        f for f in form_fields() if f.name in {"model", "provider_options"}
    ])

    assert bound.values["provider_options"] == {"background": True}


def test_nothing_is_stripped_or_validated_without_a_resolver():
    bound = bind({"aspect_ratio": "anything", "duration": 99}, use_resolver=False)

    assert bound.values["aspect_ratio"] == "anything"
    assert bound.values["duration"] == 99
    assert bound.cloud_params == {}


def test_nothing_is_stripped_when_the_chosen_model_is_unknown_to_the_catalog():
    bound = bind({"aspect_ratio": "anything"}, model="unknown-model")

    assert bound.values["aspect_ratio"] == "anything"


def test_the_bound_form_lists_each_capability_binding_for_the_policy():
    bound = bind({})

    assert {"field": "references", "model_field": "model", "param": None, "input": "reference"} in bound.capabilities
    assert {"field": "quality", "model_field": "model", "param": "quality", "input": None} in bound.capabilities


def test_a_range_value_sent_as_text_reaches_the_params_as_a_number():
    bound = bind({"quality": "7"}, fields=[
        field("model", "model", default=make_model_ref(IMAGE_ID)),
        field("quality", "string", {"model_field": "model", "param": "quality"}),
    ])

    assert bound.cloud_params == {"quality": 7} and isinstance(bound.cloud_params["quality"], int)


def test_a_provider_option_the_model_insists_on_must_be_given():
    from dataclasses import replace

    from src.features.cloud.contracts import ParamSpec

    SPECS["model-needy"] = replace(SPECS[IMAGE_ID], params=(ParamSpec(name="x.mode", kind="text", required=True),))
    try:
        errors = field_errors({"provider_options": {}}, model="model-needy")
    finally:
        del SPECS["model-needy"]

    assert "x.mode" in errors["provider_options"][0]


def test_the_resolver_is_asked_with_the_presets_driver():
    asked = []

    def recording(model_id, driver):
        asked.append((model_id, driver))
        return SPECS.get(model_id)

    bind_form(preset(), "txt2img", None, {"model": make_model_ref(IMAGE_ID)}, "user_1", cloud_capabilities=recording)

    assert asked and set(asked) == {(IMAGE_ID, "cloud.fake")}
