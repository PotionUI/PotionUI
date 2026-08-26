"""
Preview == execution invariance.

The whole point of the C3 pipeline consolidation is that the graph preview
(operations.get_pipeline) and real generation execution
(GenerationOrchestrator) both go through the exact same PipelineBuilder, so
the processed pipe list a graph is built from is *identical* to the one a
backend would execute for the same preset + form data + mode - by
construction, not by convention.

This test exercises both paths against one shared PipelineBuilder instance
(same as production DI wiring: the composition root builds ONE PipelineBuilder and
hands it to both GenerationOrchestrator and the PresetCollaborators bundle)
and asserts the graph's node configuration/order matches the pipes execution
would receive.
"""

from unittest.mock import Mock, patch

import pytest

from src.features.generation.pipeline_builder import PipelineBuilder
from src.pipelines.graph import build_graph
from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import PresetTemplateLoader, PresetProcessor


PROCESSED_PIPES = [
    {
        'name': 'downloader',
        'id': 'downloader',
        'config': {'model': 'test_model'},
        'enabled': True,
        'input': [],
    },
    {
        'name': 'generator',
        'id': 'generator',
        'config': {'steps': 20, 'cfg_scale': 7.5},
        'enabled': True,
        'input': [
            {'name': 'model', 'provider': 'downloader', 'output_var': 'model', 'enabled': True}
        ],
    },
]


@pytest.fixture
def mock_preset_processor():
    processor = Mock(spec=PresetProcessor)
    processor.process = Mock(return_value=PROCESSED_PIPES)
    return processor


@pytest.fixture
def mock_preset_template_loader():
    return Mock(spec=PresetTemplateLoader)


@pytest.fixture
def shared_pipeline_builder(mock_preset_template_loader, mock_preset_processor):
    """The ONE PipelineBuilder instance shared by execution and preview,
    mirroring the composition root's DI wiring."""
    return PipelineBuilder(
        preset_template_loader=mock_preset_template_loader,
        preset_processor=mock_preset_processor,
    )


@pytest.fixture
def preset_template():
    template = Mock()
    template.id = 'test-preset'
    template.name = 'Test Preset'
    return template


@pytest.fixture
def preset_collaborators(shared_pipeline_builder):
    with patch('src.features.presets.collaborators.PresetFormSerializer'):
        return PresetCollaborators(
            preset_loader=Mock(),
            preset_processor=Mock(),
            template_processor=Mock(),
            file_repo=Mock(),
            db_repo=Mock(),
            user_repo=Mock(),
            group_repo=Mock(),
            pipeline_builder=shared_pipeline_builder,
            pipe_catalog=Mock(),
            plugins=Mock(),
            settings_manager=Mock(),
        )


def test_graph_pipes_match_execution_pipes(
    shared_pipeline_builder,
    preset_collaborators,
    preset_template,
    mock_preset_processor,
):
    """The pipes backing the graph preview must be the exact same pipes an
    execution build for the same preset/mode/form_data would produce."""
    from src.features.presets.templates import ModeTemplate
    preset_template.modes = {'txt2img': Mock(spec=ModeTemplate)}
    preset_collaborators.file_repo.find_preset_by_id.return_value = preset_template

    form_data = {'steps': 20, 'cfg_scale': 7.5}
    mode = 'txt2img'

    # "Execution" path: build directly, as GenerationOrchestrator would.
    executed = shared_pipeline_builder.build_pipeline(
        preset_id=preset_template,
        form_data=form_data,
        mode=mode,
    )

    # "Preview" path: through operations.get_pipeline -> build_graph.
    preset_collaborators.pipe_catalog.get_pipe.return_value = None  # status irrelevant here
    graph = operations.get_pipeline(preset_collaborators, 'test-preset', mode, form_data)

    # Same processed pipe list backs both - not merely equal by coincidence,
    # but because both calls funnel through the same PipelineBuilder.
    assert executed.pipes == PROCESSED_PIPES
    assert [n.name for n in graph.nodes] == [p['name'] for p in executed.pipes]
    assert [n.configuration for n in graph.nodes] == [p['config'] for p in executed.pipes]
    assert [n.enabled for n in graph.nodes] == [p['enabled'] for p in executed.pipes]

    # PresetProcessor.process must have been invoked with the SAME
    # mode/form_data generation_data shape both times (no divergent
    # p_prompt/n_prompt/generation_settings preview-only shape).
    calls = mock_preset_processor.process.call_args_list
    assert len(calls) == 2
    for call in calls:
        generation_data = call[0][1]
        assert generation_data['mode'] == mode
        assert generation_data['form_data'] == form_data
