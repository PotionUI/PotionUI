"""GenerationOrchestrator._finish_generation scheduling a mesh thumbnail
render for a completed generation's MESH files.

Fixture pattern mirrors test_orchestrator_temp_cleanup.py: every collaborator
is mocked, orchestrator constructed directly rather than through the
container.
"""

import asyncio
import time
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_pipeline_builder():
    from src.features.generation.pipeline_builder import PipelineBuilder
    return Mock(spec=PipelineBuilder)


@pytest.fixture
def mock_backend_registry():
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    registry.get_backend = Mock(return_value=backend)
    return registry


@pytest.fixture
def mock_connection_manager():
    from src.platform.websocket.connection_hub import ConnectionHub
    return Mock(spec=ConnectionHub)


@pytest.fixture
def mock_settings():
    from src.platform.settings.settings import Settings
    return Mock(spec=Settings)


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    return Mock(spec=OutputProcessor)


@pytest.fixture
def mock_preset_template_loader():
    return Mock()


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='user_123'))
        mock_repo.get_files = Mock(return_value=[])
        yield mock_repo


class FakeStorageDriver:
    def __init__(self):
        self.written = {}

    def put_bytes(self, key, data):
        self.written[key] = data


class FakeFileStore:
    def __init__(self):
        self.storage_driver = FakeStorageDriver()

    def get_full_path(self, relative_path):
        return f"/storage/{relative_path}"


class FakeMediaIndexRepository:
    def __init__(self):
        self.thumbnails = {}

    def set_thumbnails(self, file_id, small, medium, large):
        self.thumbnails[file_id] = (small, medium, large)


@pytest.fixture
def fake_media_indexer():
    indexer = Mock()
    indexer.on_generation_complete = Mock()
    indexer.file_service = FakeFileStore()
    indexer.repository = FakeMediaIndexRepository()
    return indexer


@pytest.fixture
def orchestrator(
    mock_pipeline_builder, mock_backend_registry, mock_connection_manager,
    mock_settings, mock_output_processor, mock_preset_template_loader,
    fake_media_indexer,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
        media_indexer=fake_media_indexer,
    )


def _record(state):
    from src.features.generation.status_tracker import GenerationRecord
    return GenerationRecord(
        id='gen-1', preset_id='native/Hunyuan3D/mesh', backend_id='local_backend_1',
        state=state, created_at=time.time() - 5.0, started_at=time.time() - 5.0,
    )


def _mesh_file(file_id, thumbnail_medium=None, file_path=None):
    from src.features.generation.records import File
    return File(
        file_path=file_path or f"generations/g/{file_id}.glb",
        file_type='MESH',
        user_id='user_123',
        id=file_id,
        thumbnail_medium=thumbnail_medium,
    )


async def _finish_and_settle(orchestrator, record):
    """Awaits `_finish_generation` and then every mesh-thumbnail task it
    scheduled - `asyncio.create_task` only guarantees the task starts, not
    that it has run by the time `_finish_generation` returns."""
    await orchestrator._finish_generation('gen-1', record, output_callback=None)
    tasks = list(orchestrator._mesh_thumbnail_tasks)
    if tasks:
        await asyncio.gather(*tasks)


class TestMeshThumbnailAutoTrigger:
    @pytest.mark.asyncio
    async def test_completed_mesh_generation_renders_and_writes_thumbnail(
        self, orchestrator, mock_generation_repo, fake_media_indexer,
    ):
        from src.features.generation.status_tracker import GenerationState

        mock_generation_repo.get_files = Mock(return_value=[_mesh_file('m1')])
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        with patch(
            'src.platform.runtime.native.mesh_preview.render_mesh_preview',
            return_value=b'fake-png-bytes',
        ) as mock_render:
            await _finish_and_settle(orchestrator, record)

        mock_render.assert_called_once_with('/storage/generations/g/m1.glb')
        assert fake_media_indexer.repository.thumbnails['m1'] == (
            'thumbnails/m1_medium.png', 'thumbnails/m1_medium.png', 'thumbnails/m1_medium.png',
        )
        assert fake_media_indexer.file_service.storage_driver.written[
            'generations/g/thumbnails/m1_medium.png'
        ] == b'fake-png-bytes'

    @pytest.mark.asyncio
    async def test_render_failure_logs_warning_and_does_not_raise(
        self, orchestrator, mock_generation_repo, fake_media_indexer, caplog,
    ):
        from src.features.generation.status_tracker import GenerationState
        from src.platform.runtime.native.mesh_preview import MeshPreviewError

        mock_generation_repo.get_files = Mock(return_value=[_mesh_file('m1')])
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        with patch(
            'src.platform.runtime.native.mesh_preview.render_mesh_preview',
            side_effect=MeshPreviewError('not a real glTF-binary file'),
        ):
            with caplog.at_level('WARNING'):
                await _finish_and_settle(orchestrator, record)

        assert 'm1' not in fake_media_indexer.repository.thumbnails
        assert any('does not parse as a renderable' in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_already_thumbnailed_mesh_is_never_re_rendered(
        self, orchestrator, mock_generation_repo, fake_media_indexer,
    ):
        from src.features.generation.status_tracker import GenerationState

        mock_generation_repo.get_files = Mock(
            return_value=[_mesh_file('m1', thumbnail_medium='thumbnails/m1_medium.png')]
        )
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        with patch(
            'src.platform.runtime.native.mesh_preview.render_mesh_preview'
        ) as mock_render:
            await _finish_and_settle(orchestrator, record)

        mock_render.assert_not_called()
        assert orchestrator._mesh_thumbnail_tasks == set()

    @pytest.mark.asyncio
    async def test_no_mesh_files_schedules_nothing(
        self, orchestrator, mock_generation_repo, fake_media_indexer,
    ):
        from src.features.generation.status_tracker import GenerationState

        mock_generation_repo.get_files = Mock(return_value=[])
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(
            return_value=_record(GenerationState.COMPLETED)
        )

        await _finish_and_settle(orchestrator, record)

        assert orchestrator._mesh_thumbnail_tasks == set()
