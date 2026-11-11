import copy
import pickle

import pytest

from scripts.preset_render import ROOT, build_processor, load_all_presets, render_preset_mode
from src.features.presets.processor import PresetProcessor, _RuntimeUser
from src.platform.templating import TemplateProcessor
from src.platform.templating.errors import TemplateEvaluationError
from tests.features.presets.test_processor_context import _generation_data, _preset
from unittest.mock import Mock


def render(template, acts_as_admin=False):
    probe = []
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(
        _preset(pipe_configuration={"value": template}), _generation_data(),
        user_id="u1", acts_as_admin=acts_as_admin, authority_probe=probe,
    )
    return pipes, probe


READS = [
    "{{ runtime.user.is_admin }}",
    "{{ runtime.user['is_admin'] }}",
    "{{ runtime.user.get('is_admin') }}",
    "{{ runtime.user.items() | list }}",
    "{{ runtime.user.values() | list }}",
    "{{ runtime.user.keys() | list }}",
    "{% for key in runtime.user %}{{ key }}{% endfor %}",
    "{{ dict(runtime.user) }}",
    "{{ runtime.user | dictsort }}",
    "{{ runtime.user | list }}",
    "{{ runtime.user | length }}",
    "{% if runtime.user %}yes{% endif %}",
    "{{ runtime.user }}",
    "{{ runtime.user | string }}",
    "{{ 'is_admin' in runtime.user }}",
    "{{ runtime.user == 1 }}",
    "{{ runtime.user | attr('is_admin') }}",
    "{% set r = runtime %}{{ r.user.is_admin }}",
    "{% set u = runtime.user %}{{ u.is_admin }}",
    "{% set u = runtime.user %}{{ u['is_admin'] }}",
    "{{ [runtime.user] }}",
    "{{ {'k': runtime.user} }}",
]


@pytest.mark.parametrize("template", READS)
def test_every_way_a_template_can_read_the_value_is_detected(template):
    try:
        _, probe = render(template)
    except TemplateEvaluationError:
        pytest.fail(f"the template should render: {template}")

    assert probe, template


@pytest.mark.parametrize("template", ["{{ runtime.user | tojson }}", "{{ runtime.user | pprint }}", "{% set u = runtime.user %}{{ u.anything_at_all }}"])
def test_a_serialisation_that_cannot_touch_the_object_either_renders_detected_or_does_not_render(template):
    try:
        _, probe = render(template)
    except (TemplateEvaluationError, TypeError):
        return

    assert probe, template


@pytest.mark.parametrize("template", ["{{ 4 }}", "{{ runtime.settings.nsfw }}", "{{ preset.name }}", "{% set u = runtime.user %}x"])
def test_a_template_that_does_not_read_it_is_not_flagged(template):
    _, probe = render(template)

    assert probe == []


def test_the_value_follows_the_explicit_authority_and_the_read_is_recorded_either_way():
    denied, denied_probe = render("{{ runtime.user.is_admin }}", acts_as_admin=False)
    granted, granted_probe = render("{{ runtime.user.is_admin }}", acts_as_admin=True)

    assert denied[0]["config"]["value"] is False and granted[0]["config"]["value"] is True
    assert denied_probe and granted_probe


class TestTheObjectItself:
    @pytest.mark.parametrize("use", [
        lambda user: user.is_admin,
        lambda user: user.anything,
        lambda user: user["is_admin"],
        lambda user: list(user),
        lambda user: len(user),
        lambda user: "is_admin" in user,
        lambda user: bool(user),
        lambda user: user == 1,
        lambda user: user != 1,
        lambda user: hash(user),
        lambda user: str(user),
        lambda user: repr(user),
        lambda user: format(user, ""),
        lambda user: f"{user}",
        lambda user: dir(user),
        lambda user: copy.copy(user),
        lambda user: copy.deepcopy(user),
        lambda user: dict(user),
        lambda user: hasattr(user, "__html__"),
        lambda user: vars(user),
    ])
    def test_any_use_marks_the_shared_probe(self, use):
        probe = []
        user = _RuntimeUser(True, probe)

        try:
            use(user)
        except (AttributeError, KeyError, TypeError):
            pass

        assert probe

    def test_pickling_is_refused_and_marks_the_probe(self):
        probe = []

        with pytest.raises(TypeError):
            pickle.dumps(_RuntimeUser(True, probe))

        assert probe

    def test_a_copy_shares_the_probe_so_a_read_of_the_copy_is_still_seen(self):
        probe = []
        user = _RuntimeUser(True, probe)
        duplicate = copy.copy(user)
        del probe[:]

        duplicate.is_admin

        assert probe

    def test_it_carries_the_value_it_was_given(self):
        assert _RuntimeUser(True, []).is_admin is True
        assert _RuntimeUser(False, [])["is_admin"] is False


def test_no_shipped_preset_mode_reads_it_except_the_model_tools_modes(monkeypatch):
    presets, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    processor = build_processor()
    original = processor.process
    reads = set()
    current = {}

    def spying(preset_template, generation_data, user_id=None, acts_as_admin=None, authority_probe=None):
        probe = []
        try:
            return original(preset_template, generation_data, user_id=user_id, acts_as_admin=False, authority_probe=probe)
        finally:
            if probe:
                reads.add(current["key"])

    monkeypatch.setattr(processor, "process", spying)
    for preset in presets:
        for mode in preset.modes:
            current["key"] = (preset.id, mode)
            render_preset_mode(processor, preset, mode)

    model_tools = {p.id for p in presets if str(p.path).replace("\\", "/").endswith("marketplace/ModelTools")}
    assert model_tools
    assert {preset_id for preset_id, _ in reads} == model_tools
    assert {mode for _, mode in reads} == {"quantize", "merge_into_model", "merge_loras", "merge_and_quantize"}
