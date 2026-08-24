"""
The per-image prompt contract between the orchestrator and the pipes.

The orchestrator replaces `prompts` with one expanded pair per image. The preset
processor exposes that list to pipe configs, and the real SDXL preset picks it up
via `@object:` so `prompt_encoder` gets `pairs` and `param_emitter` gets per-index
prompt arrays. This test drives the real preset, not a fixture, because the wiring
lives in the YAML.
"""

import pytest
from unittest.mock import Mock

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor


PROMPTS = [
    {'positive': 'a red dress', 'negative': 'blurry'},
    {'positive': 'a blue dress', 'negative': 'ugly'},
    {'positive': 'a green dress', 'negative': 'blurry'},
]


@pytest.fixture(scope='module')
def sdxl_template():
    loader = PresetTemplateLoader(['content/presets'])
    loader.load_presets()
    template = next(
        (p for p in loader.presets if 'native/SDXL' in str(p.path)), None
    )
    if template is None:
        pytest.skip('SDXL/realistic preset not present')
    return template


@pytest.fixture(scope='module')
def processed_pipes(sdxl_template):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    # PresetProcessor now assumes form_data is already bound (typed defaults
    # applied -- see src/core/form/binding.py). A partial hand-built dict
    # like the old `{'quantity': 3, 'seed': 5}` no longer works: fields the
    # pipeline reads without `| default(...)` (e.g. checkpoint_loader's
    # `form.model`) would raise StrictUndefined. Bind it the way production
    # does; no storage_dir/user is needed here since this preset's media
    # fields aren't exercised by these tests.
    bound = bind_form(sdxl_template, 'txt2img', form_name=None, raw_form_data={'quantity': 3, 'seed': 5})
    generation_data = {
        'prompts': PROMPTS,
        'mode': 'txt2img',
        'form_data': bound.values,
    }
    return processor.process(sdxl_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p['name'] == name)


class TestPromptEncoderPairs:
    def test_pairs_arrive_as_a_real_list_not_a_rendered_string(self, processed_pipes):
        # `@object:` returns the object from context; a `{{ }}` template would
        # have stringified it and the pipe would silently see no pairs.
        pairs = _pipe(processed_pipes, 'prompt_encoder')['config']['pairs']
        assert isinstance(pairs, list)
        assert pairs == PROMPTS

    def test_scalar_p_prompt_still_points_at_the_first_pair(self, processed_pipes):
        cfg = _pipe(processed_pipes, 'prompt_encoder')['config']
        assert cfg['p_prompt']['input'] == 'a red dress'
        assert cfg['n_prompt']['input'] == 'blurry'

    def test_the_output_wrapper_contains_the_first_pair_verbatim(self, processed_pipes):
        # _substitute_pair splits the wrapper on this exact substring; if the
        # authored prompt were absent, per-image expansion would silently fall back.
        cfg = _pipe(processed_pipes, 'prompt_encoder')['config']
        assert 'a red dress' in cfg['p_prompt']['output']

    def test_wildcards_config_is_gone_from_the_preset(self, processed_pipes):
        cfg = _pipe(processed_pipes, 'prompt_encoder')['config']
        assert 'wildcards' not in cfg


class TestParamEmitterPerImagePrompts:
    def _params(self, pipes):
        entries = _pipe(pipes, 'param_emitter')['config']['parameters']
        return {
            e[0]: e[1]
            for e in entries
            if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str)
        }

    def test_positive_prompt_is_a_per_image_array(self, processed_pipes):
        params = self._params(processed_pipes)
        assert params['positive_prompt'] == [p['positive'] for p in PROMPTS]

    def test_negative_prompt_is_a_per_image_array(self, processed_pipes):
        params = self._params(processed_pipes)
        assert params['negative_prompt'] == [p['negative'] for p in PROMPTS]

    def test_arrays_match_the_image_count(self, processed_pipes):
        params = self._params(processed_pipes)
        # param_emitter keeps an array of exactly `quantity` entries per-index
        # and broadcasts anything shorter, so a mismatch would silently reuse
        # image 0's prompt for the whole batch.
        assert len(params['positive_prompt']) == 3
