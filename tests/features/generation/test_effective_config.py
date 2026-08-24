"""The effective config a pipe runs with, computed on the dispatching side.

The property that matters is equality with what the executor computes: the
package is only authoritative if merging the class defaults early produces
exactly the dict `PipelineExecutor` would have produced late, and if the
executor re-running its merge over that dict changes nothing.
"""

import copy

import pytest

from src.features.generation.effective_config import merge_pipe_defaults
from src.features.generation.generation import deep_update


class FakeCatalog:
    """Stands in for PipeCatalog: name -> pipe class, or None for a pipe this
    installation cannot resolve."""

    def __init__(self, classes):
        self._classes = classes

    def get_pipe(self, name):
        return self._classes.get(name)


class DetectorPipe:
    """A pipe whose defaults carry host paths - the reason this exists at all.
    Modelled on the detailer family (src/pipelines/pipes/detailer/...)."""

    _DEFAULTS = {
        'model': 'models/detection_bbox/face_yolov12m.pt',
        'detections': {
            'face': {'model': 'models/mediapipe/face_landmarker.task', 'threshold': 0.3},
            'hand': {'model': 'models/detection_bbox/hand_yolov9c.pt', 'threshold': 0.5},
        },
        'steps': 20,
        'tags': ['a', 'b'],
    }

    @classmethod
    def get_default_config(cls):
        return copy.deepcopy(cls._DEFAULTS)


class LeakyPipe:
    """Returns its class-level dict itself, not a copy - merging must not
    corrupt it for the next generation in this process."""

    DEFAULTS = {'nested': {'keep': 1}, 'scalar': 'default'}

    @classmethod
    def get_default_config(cls):
        return cls.DEFAULTS


class EmptyPipe:
    @classmethod
    def get_default_config(cls):
        return None


@pytest.fixture
def catalog():
    return FakeCatalog({
        'detector': DetectorPipe,
        'leaky': LeakyPipe,
        'empty': EmptyPipe,
    })


def pipe(name, config, *, enabled=True, pipe_id=None, inputs=None):
    return {
        'id': pipe_id,
        'name': name,
        'enabled': enabled,
        'config': config,
        'input': inputs or [],
        'cache': [],
    }


class TestMergedConfig:
    def test_class_defaults_reach_the_merged_config(self, catalog):
        merged = merge_pipe_defaults([pipe('detector', {'steps': 40})], catalog)

        config = merged[0]['config']
        assert config['steps'] == 40
        assert config['model'] == 'models/detection_bbox/face_yolov12m.pt'
        assert config['detections']['face']['model'] == 'models/mediapipe/face_landmarker.task'

    def test_preset_config_wins_over_class_defaults(self, catalog):
        merged = merge_pipe_defaults(
            [pipe('detector', {'model': 'models/checkpoints/mine.safetensors'})],
            catalog,
        )

        assert merged[0]['config']['model'] == 'models/checkpoints/mine.safetensors'

    def test_merge_is_deep_not_shallow(self, catalog):
        merged = merge_pipe_defaults(
            [pipe('detector', {'detections': {'face': {'threshold': 0.9}}})],
            catalog,
        )

        detections = merged[0]['config']['detections']
        assert detections['face'] == {
            'model': 'models/mediapipe/face_landmarker.task',
            'threshold': 0.9,
        }
        assert detections['hand']['model'] == 'models/detection_bbox/hand_yolov9c.pt'

    def test_pipe_with_no_defaults_keeps_its_config(self, catalog):
        merged = merge_pipe_defaults([pipe('empty', {'a': 1})], catalog)

        assert merged[0]['config'] == {'a': 1}


class TestExecutorEquality:
    """The proof that moving the merge does not change what runs."""

    def test_matches_what_the_executor_computes(self, catalog):
        preset_config = {'steps': 40, 'detections': {'face': {'threshold': 0.9}}}

        merged = merge_pipe_defaults([pipe('detector', copy.deepcopy(preset_config))], catalog)

        # Verbatim generation.py:601-604.
        executor_config = deep_update(
            DetectorPipe.get_default_config() or {},
            copy.deepcopy(preset_config),
        )
        assert merged[0]['config'] == executor_config

    def test_executor_merge_over_the_result_is_a_no_op(self, catalog):
        """What makes it safe for the executor's merge to stay where it is: a
        worker re-running it over a package config contributes nothing."""
        merged = merge_pipe_defaults(
            [pipe('detector', {'steps': 40, 'tags': ['z']})], catalog
        )
        shipped = copy.deepcopy(merged[0]['config'])

        re_merged = deep_update(DetectorPipe.get_default_config() or {}, copy.deepcopy(shipped))

        assert re_merged == shipped


class TestPurity:
    def test_input_pipes_are_not_mutated(self, catalog):
        pipes = [pipe('detector', {'steps': 40})]
        before = copy.deepcopy(pipes)

        merge_pipe_defaults(pipes, catalog)

        assert pipes == before

    def test_class_default_dict_is_not_corrupted(self, catalog):
        before = copy.deepcopy(LeakyPipe.DEFAULTS)

        merge_pipe_defaults(
            [pipe('leaky', {'nested': {'added': 2}, 'scalar': 'from-preset'})], catalog
        )

        assert LeakyPipe.DEFAULTS == before

    def test_merged_config_does_not_alias_the_source_config(self, catalog):
        source = {'detections': {'face': {'threshold': 0.9}}}
        pipes = [pipe('detector', source)]

        merged = merge_pipe_defaults(pipes, catalog)
        merged[0]['config']['detections']['face']['threshold'] = 0.1

        assert source['detections']['face']['threshold'] == 0.9


class TestPassThrough:
    def test_disabled_pipes_keep_their_config(self, catalog):
        """The executor never resolves a disabled pipe's class either."""
        merged = merge_pipe_defaults([pipe('detector', {}, enabled=False)], catalog)

        assert merged[0]['config'] == {}

    def test_unresolvable_pipe_is_passed_through(self, catalog):
        merged = merge_pipe_defaults([pipe('from-a-plugin-we-lack', {'a': 1})], catalog)

        assert merged[0]['config'] == {'a': 1}

    def test_pipe_order_is_preserved(self, catalog):
        merged = merge_pipe_defaults(
            [pipe('empty', {}), pipe('detector', {}), pipe('leaky', {})], catalog
        )

        assert [p['name'] for p in merged] == ['empty', 'detector', 'leaky']
