"""The native Krea-2 preset's Speed selector must offer a ``custom`` value that
hands the sampling fields back to the user.

Every other Krea-2 profile ``set_value``s ``steps``/``sampler``/``cfg`` through
the Advanced tab's reactions, so a user who wants an off-profile combination has
no way to keep it: whatever they type is overwritten the moment the form
re-evaluates. ``custom`` is the escape hatch, and it works by *setting no value*
-- its branch carries ``set_disabled`` alone, and both evaluators
(``processFieldReactions`` in ``frontend/src/lib/form/reactions.ts`` and
``bind_form``) rewrite a value only for an action whose ``set_value`` is not
None, so a value-less branch is inert by construction. The reaction matrix below
mirrors those evaluators (``equals`` / ``in``) against the loaded FieldTemplates,
since reactions are evaluated client-side and have no backend evaluator to call.

Being inert in the form is only half of it: the pipeline templates spell the
profile fallback as ``form.steps | default(get_speed_profile(...)['steps'])``,
and Jinja evaluates a filter's arguments eagerly -- ``get_speed_profile`` runs
even when ``form.steps`` is present. A profile name missing from ``preset.yml``'s
``speed_profiles:`` therefore raises ``ValueError`` and kills the render whatever
the user submitted, which is why ``custom`` is declared there too.

Covers the txt2img mode and the ``edit`` mode krea2-edit contributes onto the
same preset -- that mode reads the TARGET's ``speed_profiles:`` (pinned by
``test_plugin_preset_mode_contributions.py``), so both selectors have to agree
with one declaration.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.plugins.loader import PluginLoader
from src.platform.templating.processor import TemplateProcessor

KREA2_EDIT_PLUGIN_ID = "krea2-edit"

_UNTOUCHED = object()

_BASE_FORM = {
    "diffusion_model": "/models/krea2.safetensors",
    "text_encoder": "/models/qwen3vl.safetensors",
    "vae": "/models/qwen_image_vae.safetensors",
    "resolution": "1024x1024",
}

# An off-profile combination: no declared profile pairs 33 steps with unipc and
# cfg 7.5, so any of them leaking through would be visible.
_OFF_PROFILE = {"steps": 33, "sampler": "unipc", "cfg": 7.5}


@pytest.fixture(scope="module")
def krea2_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "native/Krea2" in str(p.path)), None)
    if template is None:
        pytest.skip("native/Krea2 preset not present")
    return template


@pytest.fixture(scope="module")
def krea2_with_edit_mode():
    manifest = next(
        (m for m in PluginLoader().discover_plugins() if m.id == KREA2_EDIT_PLUGIN_ID), None
    )
    if manifest is None:
        pytest.skip("krea2-edit plugin not present")
    registry = SimpleNamespace(get_enabled_plugins=lambda: [manifest])
    loader = PresetTemplateLoader(["content/presets"], plugin_registry=registry)
    loader.load_presets()
    template = next((p for p in loader.presets if "native/Krea2" in str(p.path)), None)
    if template is None or "edit" not in template.modes:
        pytest.skip("krea2-edit's edit mode not contributed")
    return template


def _fields(template, mode):
    return template.modes[mode].forms[0].fields


def _find_field(fields, name):
    for field in fields:
        if field.name == name:
            return field
        children = field.children
        if isinstance(children, list):
            found = _find_field(children, name)
            if found is not None:
                return found
    return None


def _option_values(field):
    return [option["value"] for option in field.configuration["options"]]


def _when_matches(when, profile):
    """The `when` half of frontend/src/lib/form/reactions.ts' evaluateCondition,
    restricted to the operators these tabs actually use."""
    assert when["field"] == "speed_profile", when
    if "equals" in when:
        return when["equals"] == profile
    if "in" in when:
        return profile in when["in"]
    raise AssertionError(f"unhandled reaction operator: {when}")


def _reacted_value(field, profile):
    """What the field's value becomes for `profile`: the last matching
    reaction's set_value, or _UNTOUCHED when nothing matches. The loader fills
    every unset action key with None, so `set_value` counts only when it is not
    None -- the same test bind_form applies (src/features/forms/binding.py)."""
    value = _UNTOUCHED
    for reaction in field.reactions or []:
        if _when_matches(reaction["when"], profile) and reaction["then"].get("set_value") is not None:
            value = reaction["then"]["set_value"]
    return value


def _process(template, mode, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = dict(_BASE_FORM)
    if form_over:
        form_data.update(form_over)
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


# -- the selector + the declaration behind it ---------------------------------

def test_custom_is_declared_alongside_the_named_profiles(krea2_template):
    assert set(krea2_template.speed_profiles) == {
        "turbo", "balanced", "quality", "base", "custom",
    }


def test_txt2img_speed_select_offers_custom(krea2_template):
    field = _find_field(_fields(krea2_template, "txt2img"), "speed_profile")
    assert _option_values(field) == ["turbo", "balanced", "quality", "base", "custom"]


def test_contributed_edit_speed_select_offers_custom(krea2_with_edit_mode):
    field = _find_field(_fields(krea2_with_edit_mode, "edit"), "speed_profile")
    assert _option_values(field) == ["turbo", "balanced", "quality", "custom"]


def test_every_offered_profile_is_declared(krea2_with_edit_mode):
    declared = set(krea2_with_edit_mode.speed_profiles)
    for mode in ("txt2img", "edit"):
        offered = set(_option_values(_find_field(_fields(krea2_with_edit_mode, mode), "speed_profile")))
        assert offered <= declared, mode


# -- reactions: custom fires none, the named profiles still fire theirs -------

def test_custom_leaves_the_txt2img_sampling_fields_untouched(krea2_template):
    fields = _fields(krea2_template, "txt2img")
    assert "custom" in _option_values(_find_field(fields, "speed_profile"))
    for name in ("steps", "sampler", "cfg"):
        assert _reacted_value(_find_field(fields, name), "custom") is _UNTOUCHED, name


def test_custom_leaves_the_edit_sampling_fields_untouched(krea2_with_edit_mode):
    fields = _fields(krea2_with_edit_mode, "edit")
    assert "custom" in _option_values(_find_field(fields, "speed_profile"))
    for name in ("steps", "sampler"):
        assert _reacted_value(_find_field(fields, name), "custom") is _UNTOUCHED, name


@pytest.mark.parametrize("profile", ["turbo", "balanced", "quality", "base"])
def test_named_profiles_still_impose_their_sampling_values(krea2_template, profile):
    fields = _fields(krea2_template, "txt2img")
    declared = krea2_template.speed_profiles[profile]
    assert _reacted_value(_find_field(fields, "steps"), profile) == declared["steps"]
    assert _reacted_value(_find_field(fields, "sampler"), profile) == declared["sampler"]
    assert _reacted_value(_find_field(fields, "cfg"), profile) == declared["guidance"]


# -- the disabled axis -------------------------------------------------------

def test_every_txt2img_profile_reaction_spells_out_set_disabled(krea2_template):
    """`applyAction` (frontend/src/lib/form/reactions.ts) only rewrites
    `disabled` when the action carries `set_disabled`, so a branch that omits it
    inherits whatever the previously selected profile left behind. Every other
    speed_profile preset (QwenImage, ZImage, Wan, LTX-2/2.5, MiniMax-H3) states
    the flag on every branch; Krea-2 states it too."""
    fields = _fields(krea2_template, "txt2img")
    for name in ("steps", "sampler", "cfg"):
        field = _find_field(fields, name)
        assert field.reactions, name
        for reaction in field.reactions:
            assert reaction["then"]["set_disabled"] is False, (name, reaction["when"])


@pytest.mark.parametrize("name", ["steps", "sampler", "cfg"])
def test_every_offered_profile_reaches_a_set_disabled_branch(krea2_template, name):
    """Including `custom`, whose only job is the flag - it deliberately carries
    no `set_value` (see the reaction-matrix tests above)."""
    fields = _fields(krea2_template, "txt2img")
    field = _find_field(fields, name)
    for profile in _option_values(_find_field(fields, "speed_profile")):
        assert any(
            _when_matches(reaction["when"], profile) and reaction["then"]["set_disabled"] is False
            for reaction in field.reactions or []
        ), (name, profile)


# -- the request boundary ----------------------------------------------------

@pytest.mark.parametrize("profile", ["custom", "quality"])
def test_bind_form_admits_custom_and_keeps_the_submitted_sampling_values(krea2_template, profile):
    """`bind_form` validates a statically-optioned select against its declared
    options, so an undeclared `custom` would be rejected with a 422 before any
    template ran - the select entry is what makes the value submittable at all.
    A wire value always wins over a field's reactions, whichever profile it
    arrives with."""
    bound = bind_form(
        krea2_template, "txt2img", None,
        {**_BASE_FORM, "speed_profile": profile, **_OFF_PROFILE},
    )
    assert bound.values["speed_profile"] == profile
    assert bound.values["steps"] == 33
    assert bound.values["sampler"] == "unipc"
    assert bound.values["cfg"] == 7.5


# -- rendering ---------------------------------------------------------------

def test_custom_renders_the_submitted_sampling_values(krea2_template):
    pipes = _process(krea2_template, "txt2img", {"speed_profile": "custom", **_OFF_PROFILE})
    generator = _pipe(pipes, "generator")
    assert generator["config"]["steps"] == 33
    assert generator["config"]["sampler"] == "unipc"
    assert generator["config"]["guidance"] == 7.5
    # The encoder's negative pass and the recorded parameters must agree with
    # the generator, or history/Civitai export drifts from what actually ran.
    assert _pipe(pipes, "prompt_encoder")["config"]["guidance_scale"] == 7.5
    # Skip the empties an inactive @loop (no LoRAs) leaves behind, and let the
    # repeated "model" rows collapse - only the sampling rows are read here.
    rows = _pipe(pipes, "param_emitter")["config"]["parameters"]
    params = dict(row for row in rows if len(row) == 2)
    assert params["steps"] == 33
    assert params["sampler"] == "unipc"
    assert params["cfg"] == 7.5


def test_custom_renders_a_baseline_when_no_sampling_fields_are_submitted(krea2_template):
    """API callers can submit a profile and nothing else; `custom`'s declared
    entry is what they fall back to."""
    pipes = _process(krea2_template, "txt2img", {"speed_profile": "custom"})
    declared = krea2_template.speed_profiles["custom"]
    generator = _pipe(pipes, "generator")
    assert generator["config"]["steps"] == declared["steps"]
    assert generator["config"]["sampler"] == declared["sampler"]
    assert generator["config"]["guidance"] == declared["guidance"]


def test_custom_keeps_the_distilled_fixed_mu_schedule(krea2_template):
    """Only `base` (the raw/non-distilled checkpoint profile) switches to the
    resolution-dynamic mu schedule; `custom` stays on the turbo-distilled
    fixed_mu the checkpoint pickers default to."""
    custom = _process(krea2_template, "txt2img", {"speed_profile": "custom", **_OFF_PROFILE})
    assert _pipe(custom, "generator")["config"]["mu_schedule"] == "fixed"
    base = _process(krea2_template, "txt2img", {"speed_profile": "base"})
    assert _pipe(base, "generator")["config"]["mu_schedule"] == "dynamic"


def test_custom_renders_the_contributed_edit_mode(krea2_with_edit_mode):
    # Bound rather than hand-built: several of this mode's pipeline entries read
    # grounding fields with no `| default()` guard, so they rely on the
    # server-owned defaults bind_form applies in the real request path.
    bound = bind_form(krea2_with_edit_mode, "edit", None, {
        **_BASE_FORM,
        "speed_profile": "custom",
        "source_image": "/storage/uploads/scene.png",
        "steps": _OFF_PROFILE["steps"],
        "sampler": _OFF_PROFILE["sampler"],
    })
    pipes = _process(krea2_with_edit_mode, "edit", bound.values)
    generator = _pipe(pipes, "generator/krea2_edit")
    assert generator["config"]["steps"] == 33
    assert generator["config"]["sampler"] == "unipc"
