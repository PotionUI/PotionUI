from tests.fixtures.kits import inert_kit_expander
import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.generation.prompt_batch import PromptBatchError


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def mock_settings():
    from src.platform.settings.settings import Settings
    manager = Mock(spec=Settings)
    manager.get_file_storage_directory = Mock(return_value='/storage')
    return manager


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    processor = Mock(spec=OutputProcessor)
    processor.process_output = AsyncMock(return_value={'processed': True})
    return processor


@pytest.fixture
def mock_preset_template_loader():
    loader = Mock()
    preset = Mock()
    preset.engine = 'native'
    preset.tags = []
    preset.vars = {}
    loader.load_preset_by_id = Mock(return_value=preset)
    return loader


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader, kit_expander=inert_kit_expander(),
    )


def _batch_request(count, quantity=1, batch=True):
    return GenerationRequest(
        preset_id='test_preset_123',
        prompts=[PromptPair(positive=f'prompt {i}', negative='') for i in range(count)],
        form_data={'quantity': quantity, 'seed': 1},
        batch_prompts=batch,
    )


class TestBatchAdmission:
    @pytest.mark.asyncio
    async def test_a_backend_without_batch_support_is_refused_before_persistence(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.supports_prompt_batch = False

        with pytest.raises(PromptBatchError, match='one prompt per generation'):
            await orchestrator.start_generation(_batch_request(3), 'user_123')

        mock_generation_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_batch_over_the_image_cap_is_refused_before_persistence(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.supports_prompt_batch = True

        with pytest.raises(PromptBatchError, match='at most 20'):
            await orchestrator.start_generation(_batch_request(6, quantity=4), 'user_123')

        mock_generation_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_supported_batch_is_persisted_with_the_per_prompt_quantity(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.supports_prompt_batch = True

        persisted = []
        mock_generation_repo.create.side_effect = lambda record: persisted.append(record.form_data['quantity'])

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_batch_1'):
            result = await orchestrator.start_generation(_batch_request(3, quantity=2), 'user_123')

        assert result['generation_id'] == 'gen_batch_1'
        assert persisted == [2]
        assert orchestrator.pipeline_builder.build_pipeline.call_args.kwargs['form_data']['quantity'] == 6

    @pytest.mark.asyncio
    async def test_extra_prompts_without_the_flag_skip_the_backend_check(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.supports_prompt_batch = False

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_batch_2'):
            result = await orchestrator.start_generation(_batch_request(3, batch=False), 'user_123')

        assert result['generation_id'] == 'gen_batch_2'


class TestBatchFinish:
    @pytest.fixture
    def finishing(self, orchestrator):
        from src.features.generation.status_tracker import GenerationState

        record = Mock(state=GenerationState.COMPLETED, started_at=0, created_at=0, backend_id=None, preset_id='p')
        orchestrator.status_tracker = Mock()
        orchestrator.status_tracker.get.return_value = record
        orchestrator.status_tracker.transition_async = AsyncMock(return_value=record)
        orchestrator.media_indexer = Mock()
        orchestrator._schedule_mesh_thumbnails = Mock()
        orchestrator._schedule_media_tagging = Mock()
        orchestrator._notifier = Mock()
        orchestrator._prompt_batch_splitter = Mock()
        orchestrator._prompt_batch_splitter.split.return_value = ['split-1', 'split-2']
        return orchestrator

    @staticmethod
    def _batch():
        from src.features.generation.prompt_batch import PromptBatch

        batch = PromptBatch(per_prompt=1, base_seed=1)
        batch.pairs = [{'positive': 'a', 'negative': ''}] * 3
        return batch

    @pytest.mark.asyncio
    async def test_the_batch_is_split_when_the_run_finishes(self, finishing, mock_generation_repo):
        batch = self._batch()
        finishing._prompt_batches['g'] = batch

        await finishing._handle_generation_completion('g', None)

        finishing._prompt_batch_splitter.split.assert_called_once_with('g', batch)
        assert 'g' not in finishing._prompt_batches

    @pytest.mark.asyncio
    async def test_split_rows_are_indexed_and_tagged_like_the_original(self, finishing, mock_generation_repo):
        finishing._prompt_batches['g'] = self._batch()

        await finishing._handle_generation_completion('g', None)

        indexed = [call.args[0] for call in finishing.media_indexer.on_generation_complete.call_args_list]
        assert indexed == ['g', 'split-1', 'split-2']
        tagged = [call.args[0] for call in finishing._schedule_media_tagging.call_args_list]
        assert tagged == ['g', 'split-1', 'split-2']

    @pytest.mark.asyncio
    async def test_a_plain_run_is_never_split(self, finishing, mock_generation_repo):
        await finishing._handle_generation_completion('g', None)

        finishing._prompt_batch_splitter.split.assert_not_called()
        assert [c.args[0] for c in finishing.media_indexer.on_generation_complete.call_args_list] == ['g']

    @pytest.mark.asyncio
    async def test_a_failing_split_never_breaks_completion(self, finishing, mock_generation_repo):
        finishing._prompt_batches['g'] = self._batch()
        finishing._prompt_batch_splitter.split.side_effect = RuntimeError('db locked')

        await finishing._handle_generation_completion('g', None)

        finishing._notifier.notify_completion.assert_called_once()
        assert [c.args[0] for c in finishing.media_indexer.on_generation_complete.call_args_list] == ['g']

    @pytest.mark.asyncio
    async def test_a_failing_split_is_surfaced_on_the_generation(self, finishing, mock_generation_repo):
        from src.features.generation.prompt_batch import PROMPT_BATCH_SPLIT_NOTICE

        finishing._prompt_batches['g'] = self._batch()
        finishing._prompt_batch_splitter.split.side_effect = RuntimeError('db locked')

        await finishing._handle_generation_completion('g', None)

        finishing.status_tracker.set_message.assert_called_once_with('g', PROMPT_BATCH_SPLIT_NOTICE)

    @pytest.mark.asyncio
    async def test_a_clean_split_leaves_the_message_alone(self, finishing, mock_generation_repo):
        finishing._prompt_batches['g'] = self._batch()

        await finishing._handle_generation_completion('g', None)

        finishing.status_tracker.set_message.assert_not_called()


class TestBatchCountsOnce:
    @pytest.mark.asyncio
    async def test_a_batch_is_admitted_as_one_generation_and_the_split_does_not_admit_again(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.supports_prompt_batch = True
        orchestrator.limit_guard = Mock()
        orchestrator.limit_guard.admit.return_value = ['event']

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_once'):
            await orchestrator.start_generation(_batch_request(5, quantity=3), 'user_123')

        orchestrator.limit_guard.admit.assert_called_once()
        assert orchestrator.limit_guard.admit.call_args.args[0].ref_id == 'gen_once'

        from src.features.generation.prompt_batch import PromptBatch
        orchestrator._prompt_batch_splitter = Mock()
        orchestrator._prompt_batch_splitter.split.return_value = ['a', 'b']
        batch = PromptBatch(per_prompt=3, base_seed=1)
        await orchestrator._split_prompt_batch('gen_once', batch)

        orchestrator.limit_guard.admit.assert_called_once()
