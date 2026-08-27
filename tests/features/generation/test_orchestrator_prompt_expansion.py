"""
Tests for GenerationOrchestrator._expand_prompts_per_image.

The orchestrator resolves the seed and expands the authored prompt template into
one realization per image before the pipeline is built, so every engine receives
the same `prompts` contract and `seed_generator` inherits the pinned seed.
"""

import pytest
from unittest.mock import Mock, patch


@pytest.fixture
def orchestrator():
    """A GenerationOrchestrator with all constructor deps mocked."""
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=Mock(),
        backend_registry=Mock(),
        connection_hub=Mock(),
        settings=Mock(),
        output_processor=Mock(),
        preset_template_loader=Mock(),
    )


def _request(form_data=None, variables=None):
    request = Mock()
    request.form_data = {} if form_data is None else form_data
    request.variables = variables
    return request


class TestExpansion:
    def test_expands_one_pair_per_image(self, orchestrator):
        request = _request({'quantity': 4, 'seed': 100})
        prompts = [{'positive': '{a|b|c}', 'negative': 'blurry'}]

        result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        assert len(result) == 4
        assert all(set(p) == {'positive', 'negative'} for p in result)
        assert all(p['positive'] in ('a', 'b', 'c') for p in result)
        assert all(p['negative'] == 'blurry' for p in result)

    def test_is_reproducible_for_a_fixed_seed(self, orchestrator):
        prompts = [{'positive': '{a|b|c|d}', 'negative': ''}]
        first = orchestrator._expand_prompts_per_image(
            'gen1', _request({'quantity': 4, 'seed': 77}), prompts
        )
        second = orchestrator._expand_prompts_per_image(
            'gen2', _request({'quantity': 4, 'seed': 77}), prompts
        )
        assert first == second

    def test_variables_reach_the_expander(self, orchestrator):
        request = _request({'quantity': 1, 'seed': 1}, variables={'mood': 'moody'})
        prompts = [{'positive': 'a ${mood} shot', 'negative': ''}]

        result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        assert result[0]['positive'] == 'a moody shot'

    def test_only_the_first_authored_pair_is_used_as_the_template(self, orchestrator):
        """Multi-prompt tabs must not multiply the image count."""
        request = _request({'quantity': 2, 'seed': 1})
        prompts = [
            {'positive': 'first', 'negative': ''},
            {'positive': 'second', 'negative': ''},
        ]

        result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        assert len(result) == 2
        assert all(p['positive'] == 'first' for p in result)


class TestSeedResolution:
    def test_seed_minus_one_is_resolved_and_written_back_to_form_data(self, orchestrator):
        request = _request({'quantity': 2, 'seed': -1})

        with patch('src.features.generation.prompt_expansion.generate_seed', return_value=4242):
            orchestrator._expand_prompts_per_image(
                'gen1', request, [{'positive': 'x', 'negative': ''}]
            )

        # seed_generator reads form_data; without the write-back it would draw its
        # own random seeds and the expansion would be unreproducible.
        assert request.form_data['seed'] == 4242

    def test_explicit_seed_is_left_alone(self, orchestrator):
        request = _request({'quantity': 1, 'seed': 5})

        with patch('src.features.generation.prompt_expansion.generate_seed') as gen_seed:
            orchestrator._expand_prompts_per_image(
                'gen1', request, [{'positive': 'x', 'negative': ''}]
            )

        gen_seed.assert_not_called()
        assert request.form_data['seed'] == 5

    def test_resolved_seed_drives_the_expansion(self, orchestrator):
        """A resolved -1 must expand exactly as an explicit seed of the same value."""
        prompts = [{'positive': '{a|b|c|d}', 'negative': ''}]

        with patch('src.features.generation.prompt_expansion.generate_seed', return_value=999):
            rolled = orchestrator._expand_prompts_per_image(
                'gen1', _request({'quantity': 3, 'seed': -1}), prompts
            )
        explicit = orchestrator._expand_prompts_per_image(
            'gen2', _request({'quantity': 3, 'seed': 999}), prompts
        )

        assert rolled == explicit


class TestDefaultsAndFailures:
    def test_missing_quantity_defaults_to_one_image(self, orchestrator):
        request = _request({'seed': 1})
        result = orchestrator._expand_prompts_per_image(
            'gen1', request, [{'positive': 'x', 'negative': ''}]
        )
        assert len(result) == 1

    def test_non_numeric_quantity_falls_back_to_one(self, orchestrator):
        request = _request({'quantity': 'lots', 'seed': 1})
        result = orchestrator._expand_prompts_per_image(
            'gen1', request, [{'positive': 'x', 'negative': ''}]
        )
        assert len(result) == 1

    def test_non_numeric_seed_is_treated_as_unset(self, orchestrator):
        request = _request({'quantity': 1, 'seed': 'abc'})
        with patch('src.features.generation.prompt_expansion.generate_seed', return_value=7):
            orchestrator._expand_prompts_per_image(
                'gen1', request, [{'positive': 'x', 'negative': ''}]
            )
        assert request.form_data['seed'] == 7

    def test_empty_prompts_pass_through(self, orchestrator):
        request = _request({'quantity': 2, 'seed': 1})
        assert orchestrator._expand_prompts_per_image('gen1', request, None) is None
        assert orchestrator._expand_prompts_per_image('gen1', request, []) == []

    def test_expander_failure_falls_back_to_the_authored_template(self, orchestrator):
        request = _request({'quantity': 2, 'seed': 1})
        prompts = [{'positive': 'x', 'negative': ''}]

        with patch(
            'src.features.generation.prompt_expansion.expand_prompts',
            side_effect=RuntimeError('boom'),
        ):
            result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        assert result == prompts
