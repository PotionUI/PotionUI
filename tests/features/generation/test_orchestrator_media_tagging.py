import asyncio
import time
from unittest.mock import Mock, patch

from tests.features.media_index.test_indexer import IndexerTestBase


def _record(state):
    from src.features.generation.status_tracker import GenerationRecord
    return GenerationRecord(
        id='gen-1', preset_id='native/SDXL/txt2img', backend_id='local_backend_1',
        state=state, created_at=time.time() - 5.0, started_at=time.time() - 5.0,
    )


class TestMediaTaggingAutoTrigger(IndexerTestBase):
    def setUp(self):
        super().setUp()
        self.media_indexer = self._indexer()
        self.orchestrator = self._build_orchestrator()

    def _build_orchestrator(self):
        from src.features.generation.orchestrator import GenerationOrchestrator
        from src.features.generation.pipeline_builder import PipelineBuilder
        from src.features.backends.backend_registry import BackendRegistry
        from src.platform.websocket.connection_hub import ConnectionHub
        from src.platform.settings.settings import Settings
        from src.features.generation.output_processor import OutputProcessor

        backend_registry = Mock(spec=BackendRegistry)
        backend = Mock()
        backend.backend_id = 'local_backend_1'
        backend.engine = 'native'
        backend_registry.get_backend = Mock(return_value=backend)

        return GenerationOrchestrator(
            pipeline_builder=Mock(spec=PipelineBuilder),
            backend_registry=backend_registry,
            connection_hub=Mock(spec=ConnectionHub),
            settings=Mock(spec=Settings),
            output_processor=Mock(spec=OutputProcessor),
            preset_template_loader=Mock(),
            media_indexer=self.media_indexer,
        )

    async def _finish_and_settle(self, record):
        with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
            mock_repo.get_by_id = Mock(return_value=Mock(user_id=self.user_id))
            mock_repo.get_files = Mock(return_value=[])
            await self.orchestrator._finish_generation('gen-1', record, output_callback=None)
        tasks = list(self.orchestrator._media_tag_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    def test_completed_generation_gets_system_tags_when_tagger_enabled(self):
        from src.features.generation.status_tracker import GenerationState

        self.create_test_generation('gen-1', self.user_id)
        self._make_file('f1', 'gen-1', is_final=True)

        record = _record(GenerationState.RUNNING)
        self.orchestrator.status_tracker.get = Mock(return_value=record)
        self.orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        asyncio.run(self._finish_and_settle(record))

        tags = self.repo.get_for_files(['f1'])
        assert [t['tag'] for t in tags['f1']['system_tags']] == ['1girl']
        assert self.tagger.calls == ['/storage/generations/g/f1.png']
        assert self._queue_row('f1')['status'] == 'done'

    def test_backlog_ahead_of_this_generation_does_not_block_its_own_tags(self):
        from src.features.generation.status_tracker import GenerationState

        self.create_test_generation('gen-old', self.user_id)
        backlog_ids = [f'backlog{i}' for i in range(5)]
        for file_id in backlog_ids:
            self._make_file(file_id, 'gen-old', is_final=True)
        self.repo.enqueue_files(backlog_ids, 'tags')

        self.create_test_generation('gen-1', self.user_id)
        self._make_file('f1', 'gen-1', is_final=True)

        record = _record(GenerationState.RUNNING)
        self.orchestrator.status_tracker.get = Mock(return_value=record)
        self.orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        asyncio.run(self._finish_and_settle(record))

        assert self._queue_row('f1')['status'] == 'done'
        for i in range(5):
            assert self._queue_row(f'backlog{i}')['status'] == 'pending'

    def test_failing_or_cancelled_generation_schedules_no_drain(self):
        from src.features.generation.status_tracker import GenerationState

        self.create_test_generation('gen-1', self.user_id)
        self._make_file('f1', 'gen-1', is_final=True)

        record = _record(GenerationState.FAILED)
        self.orchestrator.status_tracker.get = Mock(return_value=record)

        asyncio.run(self._finish_and_settle(record))

        assert self._queue_row('f1') is None
        assert self.tagger.calls == []
