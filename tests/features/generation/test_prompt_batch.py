from tests.fixtures.kits import inert_kit_expander
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from src.features.backends.base_backend import BaseBackend
from src.features.backends.native_backend import NativeBackend
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.features.cloud.backend import CloudBackend
from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.generation.prompt_batch import (
    MAX_BATCH_IMAGES,
    PromptBatch,
    PromptBatchError,
    check_prompt_batch,
    is_prompt_batch,
)
from src.features.generation.prompt_expansion import PromptExpander
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)


def _request(prompts, quantity=2, seed=100, batch=True):
    return GenerationRequest(
        preset_id='p',
        prompts=[PromptPair(positive=text, negative=f'neg-{text}') for text in prompts],
        form_data={'quantity': quantity, 'seed': seed},
        batch_prompts=batch,
    )


def _pairs(request):
    return [{'positive': p.positive, 'negative': p.negative} for p in request.prompts]


def _backend(supports):
    backend = Mock()
    backend.name = 'b'
    backend.engine = 'eng'
    backend.supports_prompt_batch = supports
    return backend


class TestExpansion:
    def test_each_prompt_is_expanded_quantity_times_in_prompt_order(self):
        request = _request(['cat', 'dog', 'owl'], quantity=2)

        result = PromptExpander().expand_per_image('g', request, _pairs(request))

        assert [p['positive'] for p in result] == ['cat', 'cat', 'dog', 'dog', 'owl', 'owl']
        assert [p['negative'] for p in result] == ['neg-cat'] * 2 + ['neg-dog'] * 2 + ['neg-owl'] * 2

    def test_quantity_is_rewritten_to_the_total(self):
        request = _request(['a', 'b', 'c'], quantity=4)

        PromptExpander().expand_per_image('g', request, _pairs(request))

        assert request.form_data['quantity'] == 12

    def test_dynamic_syntax_is_expanded_per_prompt_with_continuing_seeds(self):
        request = _request(['{x|y|z|w}', '{x|y|z|w}'], quantity=3, seed=50)

        result = PromptExpander().expand_per_image('g', request, _pairs(request))

        from src.features.prompt.expander import expand_prompts
        first = expand_prompts('{x|y|z|w}', '', count=3, base_seed=50)
        second = expand_prompts('{x|y|z|w}', '', count=3, base_seed=53)
        assert [p['positive'] for p in result] == [e.positive for e in first + second]

    def test_a_random_seed_is_pinned_once_for_the_whole_batch(self):
        request = _request(['a', 'b'], quantity=2, seed=-1)

        with patch('src.features.generation.prompt_expansion.generate_seed', return_value=900) as roll:
            PromptExpander().expand_per_image('g', request, _pairs(request))

        roll.assert_called_once()
        assert request.form_data['seed'] == 900

    def test_a_single_prompt_keeps_the_original_behaviour(self):
        request = _request(['{a|b}'], quantity=3, seed=7)

        result = PromptExpander().expand_per_image('g', request, _pairs(request))

        assert len(result) == 3
        assert request.form_data['quantity'] == 3

    def test_extra_prompts_without_the_flag_are_still_ignored(self):
        request = _request(['first', 'second'], quantity=2, batch=False)

        result = PromptExpander().expand_per_image('g', request, _pairs(request))

        assert [p['positive'] for p in result] == ['first', 'first']
        assert request.form_data['quantity'] == 2

    def test_a_failing_template_falls_back_to_the_literal_prompt(self):
        request = _request(['a', 'b'], quantity=2)

        with patch('src.features.generation.prompt_expansion.expand_prompts', side_effect=RuntimeError('boom')):
            result = PromptExpander().expand_per_image('g', request, _pairs(request))

        assert [p['positive'] for p in result] == ['a', 'a', 'b', 'b']


class TestFlag:
    def test_requires_the_flag_and_more_than_one_prompt(self):
        assert is_prompt_batch(_request(['a', 'b'])) is True
        assert is_prompt_batch(_request(['a', 'b'], batch=False)) is False
        assert is_prompt_batch(_request(['a'])) is False


class TestLimits:
    def test_total_over_the_cap_is_refused_with_the_numbers(self):
        request = _request(['a'] * 6, quantity=4)

        with pytest.raises(PromptBatchError) as raised:
            check_prompt_batch(request, _backend(True))

        assert str(MAX_BATCH_IMAGES) in str(raised.value)
        assert '24' in str(raised.value)

    def test_total_at_the_cap_is_accepted(self):
        check_prompt_batch(_request(['a'] * 5, quantity=4), _backend(True))

    def test_a_backend_without_batch_support_is_refused(self):
        with pytest.raises(PromptBatchError) as raised:
            check_prompt_batch(_request(['a', 'b']), _backend(False))

        assert 'one prompt per generation' in str(raised.value)

    def test_the_error_is_a_value_error_so_the_route_answers_400(self):
        assert issubclass(PromptBatchError, ValueError)

    @pytest.mark.parametrize('document', ['video_director', 'music_director'])
    def test_director_forms_are_refused(self, document):
        request = _request(['a', 'b'])
        request.form_data[document] = {'segments': []}

        with pytest.raises(PromptBatchError) as raised:
            check_prompt_batch(request, _backend(True))

        assert 'Director' in str(raised.value)

    def test_non_batch_requests_skip_every_check(self):
        check_prompt_batch(_request(['a'] * 30, quantity=9, batch=False), _backend(False))

    def test_backend_capability_by_class(self):
        assert BaseBackend.supports_prompt_batch is False
        assert NativeBackend.supports_prompt_batch is True
        assert RemoteNativeBackend.supports_prompt_batch is True
        assert CloudBackend.supports_prompt_batch is True


def _final_image(seed=None, **extra):
    return ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=False, seed=seed, **extra)


class TestStamping:
    def _batch(self):
        batch = PromptBatch(per_prompt=2)
        batch.pairs = [{'positive': t, 'negative': ''} for t in ('a1', 'a2', 'b1', 'b2')]
        batch.base_seed = 10
        return batch

    def test_seed_selects_the_prompt(self):
        batch = self._batch()
        image = _final_image(seed=12)

        batch.stamp(image)

        assert (image.prompt_index, image.prompt_text) == (1, 'b1')

    def test_images_without_seed_follow_emit_order(self):
        batch = self._batch()
        gallery = GalleryGenerationOutput(images=[_final_image() for _ in range(4)])

        batch.stamp(gallery)

        assert [i.prompt_index for i in gallery.images] == [0, 0, 1, 1]
        assert [i.prompt_text for i in gallery.images] == ['a1', 'a2', 'b1', 'b2']

    def test_order_continues_across_separate_outputs(self):
        batch = self._batch()
        first, second = _final_image(), _final_image()

        batch.stamp(first)
        batch.stamp(second)

        assert (first.prompt_text, second.prompt_text) == ('a1', 'a2')

    def test_previews_are_left_alone_and_do_not_advance(self):
        batch = self._batch()
        preview = ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=True)
        final = _final_image()

        for item in (preview, final):
            batch.stamp(item)

        assert preview.prompt_index is None
        assert final.prompt_text == 'a1'

    def test_a_derived_image_joins_the_prompt_of_the_image_it_follows(self):
        batch = self._batch()
        images = [_final_image() for _ in range(3)]
        derived = _final_image(derived=True)

        for item in (*images, derived):
            batch.stamp(item)

        assert (derived.prompt_index, derived.prompt_text) == (1, 'b1')
        assert batch.cursor == 3

    def test_a_derived_image_with_a_seed_uses_it_to_find_its_prompt(self):
        batch = self._batch()
        batch.stamp(_final_image())
        derived = _final_image(derived=True, seed=13)

        batch.stamp(derived)

        assert (derived.prompt_index, derived.prompt_text) == (1, 'b2')

    def test_a_derived_image_before_any_final_stays_unstamped(self):
        derived = _final_image(derived=True)

        self._batch().stamp(derived)

        assert derived.prompt_index is None

    def test_videos_are_stamped_too(self, tmp_path):
        batch = self._batch()
        video = VideoGenerationOutput(video_path=tmp_path / 'x.mp4', temporary=False)

        batch.stamp(GalleryGenerationOutput(images=[], videos=[video]))

        assert video.prompt_index == 0

    def test_outputs_past_the_batch_are_not_stamped(self):
        batch = self._batch()
        images = [_final_image() for _ in range(5)]

        for image in images:
            batch.stamp(image)

        assert [i.prompt_index for i in images] == [0, 0, 1, 1, None]


class TestBatchBookkeeping:
    def test_saved_paths_are_recorded_against_the_prompt_that_made_them(self):
        batch = PromptBatch(per_prompt=1)
        first, second = _final_image(prompt_index=0), _final_image(prompt_index=1)
        first._saved_path, second._saved_path = 'outputs/d/g/0.png', 'outputs/d/g/1.png'
        unstamped = _final_image()
        unstamped._saved_path = 'outputs/d/g/2.png'

        batch.record_saved(GalleryGenerationOutput(images=[first, second, unstamped]))

        assert batch.files == {'outputs/d/g/0.png': 0, 'outputs/d/g/1.png': 1}

    def test_every_pipe_receives_the_layout(self):
        batch = PromptBatch(per_prompt=2)
        batch.pairs = [{'positive': 'x', 'negative': ''}] * 6
        pipes = [{'name': 'a', 'config': {'k': 1}}, {'name': 'b'}]

        batch.apply_to_pipes(pipes)

        assert pipes[0]['config'] == {'k': 1, 'prompt_batch': {'per_prompt': 2, 'count': 3}}
        assert pipes[1]['config'] == {'prompt_batch': {'per_prompt': 2, 'count': 3}}

    def test_seed_for_a_prompt_is_its_first_image_seed(self):
        batch = PromptBatch(per_prompt=4, base_seed=100)

        assert [batch.seed_for(i) for i in range(3)] == [100, 104, 108]


class TestOrchestratorWiring:
    @pytest.fixture
    def orchestrator(self):
        from src.features.generation.orchestrator import GenerationOrchestrator
        built = GenerationOrchestrator(
            pipeline_builder=Mock(),
            backend_registry=Mock(),
            connection_hub=Mock(),
            settings=Mock(),
            output_processor=Mock(),
            preset_template_loader=Mock(), kit_expander=inert_kit_expander(),
        )
        built.output_processor.process_output = AsyncMock(return_value={'processed': True})
        record = Mock()
        record.state.value = 'running'
        built.status_tracker = Mock()
        built.status_tracker.get.return_value = record
        return built

    @pytest.mark.asyncio
    async def test_outputs_are_stamped_before_they_are_saved(self, orchestrator):
        batch = PromptBatch(per_prompt=1)
        batch.pairs = [{'positive': 'one', 'negative': ''}, {'positive': 'two', 'negative': ''}]
        orchestrator._prompt_batches['g'] = batch
        images = [_final_image(), _final_image()]
        for index, image in enumerate(images):
            image._saved_path = f'outputs/d/g/{index}.png'
        seen = []
        orchestrator.output_processor.process_output = AsyncMock(
            side_effect=lambda gid, out, uid: seen.extend(i.prompt_text for i in out.images) or {'processed': True}
        )

        with patch('src.features.generation.orchestrator.generation_repo') as repo:
            repo.get_by_id.return_value = Mock(user_id='u')
            await orchestrator._handle_generation_output('g', GalleryGenerationOutput(images=images), 'native', None)

        assert seen == ['one', 'two']
        assert batch.files == {'outputs/d/g/0.png': 0, 'outputs/d/g/1.png': 1}

    @pytest.mark.asyncio
    async def test_generations_without_a_batch_are_not_stamped(self, orchestrator):
        image = _final_image()

        with patch('src.features.generation.orchestrator.generation_repo') as repo:
            repo.get_by_id.return_value = Mock(user_id='u')
            await orchestrator._handle_generation_output('g', image, 'native', None)

        assert image.prompt_index is None

    @pytest.mark.asyncio
    async def test_start_registers_the_batch_with_the_expanded_pairs(self, orchestrator):
        request = _request(['cat', 'dog'], quantity=2, seed=5)
        pipes = [{'name': 'generator', 'config': {}}]
        orchestrator.pipeline_builder.build_pipeline.return_value = Mock(preset_template=None, pipes=pipes)
        backend = Mock(engine='native')
        backend.start_generation = AsyncMock()
        db_generation = Mock(user_id='u', mode='txt2img')
        db_generation.id = 'g'

        with patch('src.features.generation.orchestrator.generation_repo'):
            await orchestrator._start_generation('g', request, backend, db_generation, None)

        batch = orchestrator._prompt_batches['g']
        assert batch.per_prompt == 2
        assert batch.base_seed == 5
        assert [p['positive'] for p in batch.pairs] == ['cat', 'cat', 'dog', 'dog']
        built = orchestrator.pipeline_builder.build_pipeline.call_args.kwargs
        assert len(built['prompts']) == 4
        assert pipes[0]['config']['prompt_batch'] == {'per_prompt': 2, 'count': 2}

    @pytest.mark.asyncio
    async def test_a_plain_start_registers_nothing(self, orchestrator):
        request = _request(['cat'], quantity=2, seed=5, batch=False)
        orchestrator.pipeline_builder.build_pipeline.return_value = Mock(preset_template=None, pipes=[])
        backend = Mock(engine='native')
        backend.start_generation = AsyncMock()
        db_generation = Mock(user_id='u', mode='txt2img')

        with patch('src.features.generation.orchestrator.generation_repo'):
            await orchestrator._start_generation('g', request, backend, db_generation, None)

        assert orchestrator._prompt_batches == {}
