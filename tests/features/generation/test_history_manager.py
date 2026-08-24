"""Tests for GenerationHistoryManager."""

import io
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime
from PIL import Image

from src.features.generation.history_manager import GenerationHistoryManager
from src.features.generation.exceptions import (
    GenerationNotFoundException,
    GenerationDeleteFailedException,
    UploadFailedException,
    InvalidTagException,
    InvalidDateFilterException,
)
from src.features.generation.hooks import GENERATION_HOOKS
from src.platform.plugins.hooks import execute_hook


class TestGenerationHistoryManagerInit:
    """Tests for GenerationHistoryManager initialization."""

    def test_init_with_all_dependencies(self):
        """Should initialize with all required dependencies."""
        mock_repo = Mock()
        mock_file_service = Mock()
        mock_plugins = Mock()

        manager = GenerationHistoryManager(
            generation_repo=mock_repo,
            file_service=mock_file_service,
            plugin_registry=mock_plugins
        )

        assert manager.generation_repo is mock_repo
        assert manager.file_service is mock_file_service
        assert manager.plugins is mock_plugins


class TestValidationHelpers:
    """Tests for validation helper methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_get_generation_or_raise_found(self):
        """Should return generation if found."""
        mock_generation = Mock()
        self.mock_repo.get_by_id.return_value = mock_generation

        result = self.manager._query._get_generation_or_raise("gen-123", "user-123")

        assert result is mock_generation
        self.mock_repo.get_by_id.assert_called_once_with(
            "gen-123", user_id="user-123", include_files=False
        )

    def test_get_generation_or_raise_not_found(self):
        """Should raise GenerationNotFoundException if not found."""
        self.mock_repo.get_by_id.return_value = None

        with pytest.raises(GenerationNotFoundException) as exc_info:
            self.manager._query._get_generation_or_raise("gen-123", "user-123")

        assert "gen-123" in str(exc_info.value)

    def test_get_generation_or_raise_with_files(self):
        """Should pass include_files parameter."""
        mock_generation = Mock()
        self.mock_repo.get_by_id.return_value = mock_generation

        self.manager._query._get_generation_or_raise("gen-123", "user-123", include_files=True)

        self.mock_repo.get_by_id.assert_called_once_with(
            "gen-123", user_id="user-123", include_files=True
        )

    @patch('src.features.tags.repository.tag_repo')
    def test_validate_tag_ids_valid(self, mock_tag_repo):
        """Should not raise for valid tags."""
        mock_tag = Mock()
        mock_tag.type = 'GENERATION'
        mock_tag.user_id = "user-123"
        mock_tag_repo.get_tag_by_id.return_value = mock_tag

        # Should not raise
        self.manager._query._validate_tag_ids(["tag-1", "tag-2"], "user-123")

    @patch('src.features.tags.repository.tag_repo')
    def test_validate_tag_ids_invalid_type(self, mock_tag_repo):
        """Should raise InvalidTagException for wrong tag type."""
        mock_tag = Mock()
        mock_tag.type = 'MODEL'  # Wrong type
        mock_tag.user_id = "user-123"
        mock_tag_repo.get_tag_by_id.return_value = mock_tag

        with pytest.raises(InvalidTagException) as exc_info:
            self.manager._query._validate_tag_ids(["tag-1"], "user-123")

        assert "tag-1" in str(exc_info.value)

    @patch('src.features.tags.repository.tag_repo')
    def test_validate_tag_ids_wrong_owner(self, mock_tag_repo):
        """Should raise InvalidTagException for wrong owner."""
        mock_tag = Mock()
        mock_tag.type = 'GENERATION'
        mock_tag.user_id = "different-user"
        mock_tag_repo.get_tag_by_id.return_value = mock_tag

        with pytest.raises(InvalidTagException):
            self.manager._query._validate_tag_ids(["tag-1"], "user-123")

    @patch('src.features.tags.repository.tag_repo')
    def test_validate_tag_ids_not_found(self, mock_tag_repo):
        """Should raise InvalidTagException if tag not found."""
        mock_tag_repo.get_tag_by_id.return_value = None

        with pytest.raises(InvalidTagException):
            self.manager._query._validate_tag_ids(["tag-1"], "user-123")


class TestValidateDateFilters:
    """Tests for date filter validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_validate_date_filters_valid_date_only(self):
        """Should accept valid date-only format."""
        # Should not raise
        self.manager._query.validate_date_filters(
            "2024-01-01", "2024-12-31", None, None
        )

    def test_validate_date_filters_valid_datetime(self):
        """Should accept valid datetime format."""
        # Should not raise
        self.manager._query.validate_date_filters(
            "2024-01-01 10:00:00", "2024-12-31 23:59:59", None, None
        )

    def test_validate_date_filters_invalid_format(self):
        """Should raise for invalid date format."""
        with pytest.raises(InvalidDateFilterException) as exc_info:
            self.manager._query.validate_date_filters(
                "invalid-date", None, None, None
            )

        assert "created_from" in str(exc_info.value)

    def test_validate_date_filters_from_after_to(self):
        """Should raise if from date is after to date."""
        with pytest.raises(InvalidDateFilterException) as exc_info:
            self.manager._query.validate_date_filters(
                "2024-12-31", "2024-01-01", None, None
            )

        assert "must be before" in str(exc_info.value)

    def test_validate_date_filters_none_values(self):
        """Should accept None values."""
        # Should not raise
        self.manager._query.validate_date_filters(None, None, None, None)


class TestGetHistory:
    """Tests for get_history method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_get_history_success(self):
        """Should return history data."""
        mock_gen = Mock()
        mock_gen.to_dict.return_value = {"id": "gen-1"}
        mock_gen.preset_id = "01KX46YCC5RB5EGYY38SBMVKR5"
        self.mock_repo.get_all.return_value = [mock_gen]
        self.mock_repo.count_by_status.return_value = 1

        result = self.manager.get_history(user_id="user-123", limit=50, offset=0)

        assert "generations" in result
        assert result["total"] == 1
        # No resolver wired here, so the id stands in for the name.
        assert result["generations"][0]["preset_name"] == "01KX46YCC5RB5EGYY38SBMVKR5"

    def test_get_history_null_preset_id(self):
        """Should use 'Uploaded' for null preset_id."""
        mock_gen = Mock()
        mock_gen.to_dict.return_value = {"id": "gen-1"}
        mock_gen.preset_id = None
        self.mock_repo.get_all.return_value = [mock_gen]
        self.mock_repo.count_by_status.return_value = 1

        result = self.manager.get_history(user_id="user-123")

        assert result["generations"][0]["preset_name"] == "Uploaded"

    def test_get_history_with_filters(self):
        """Should pass filters to repository."""
        self.mock_repo.get_all.return_value = []
        self.mock_repo.count_by_status.return_value = 0

        self.manager.get_history(
            user_id="user-123",
            status="completed",
            created_from="2024-01-01",
            tag_ids=["tag-1"]
        )

        self.mock_repo.get_all.assert_called_once()
        call_kwargs = self.mock_repo.get_all.call_args[1]
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["created_from"] == "2024-01-01"
        assert call_kwargs["tag_ids"] == ["tag-1"]


class TestGetById:
    """Tests for get_by_id method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    @patch('src.features.tags.repository.tag_repo')
    @patch('src.features.generation.segment_repository.generation_segment_repo')
    @patch('src.features.generation.parameter_repository.generation_parameter_repo')
    @patch('src.features.generation.model_repository.generation_model_repo')
    def test_get_by_id_success(self, mock_model_repo, mock_param_repo, mock_segment_repo, mock_tag_repo):
        """Should return generation with parameters, models, segments and tags."""
        mock_gen = Mock()
        mock_gen.to_dict.return_value = {"id": "gen-123"}
        self.mock_repo.get_by_id.return_value = mock_gen

        mock_param = Mock()
        mock_param.parameter_name = "steps"
        mock_param.to_dict.return_value = {"parameter_value": 30}
        mock_param_repo.get_by_generation.return_value = [mock_param]

        mock_model = Mock()
        mock_model.to_dict.return_value = {"name": "model1"}
        mock_model_repo.get_by_generation.return_value = [mock_model]

        mock_segment = Mock()
        mock_segment.to_dict.return_value = {"text": "a segment"}
        mock_segment_repo.get_by_generation.return_value = [mock_segment]

        # A real DTO, not a Mock: tag_repo returns pydantic Tags (see
        # test_get_tags_success for why a Mock would hide a serialization bug).
        from src.features.tags.dto import Tag, TagType
        real_tag = Tag(id="tag-1", name="fav", type=TagType.GENERATION, user_id="user-123")
        mock_tag_repo.get_generation_tags.return_value = [real_tag]

        result = self.manager.get_by_id("gen-123", "user-123")

        assert result["id"] == "gen-123"
        assert "parameters" in result
        assert "models" in result
        assert result["segments"] == [{"text": "a segment"}]
        assert result["tags"] == [real_tag.model_dump(mode="json")]

    def test_get_by_id_not_found(self):
        """Should raise GenerationNotFoundException if not found."""
        self.mock_repo.get_by_id.return_value = None

        with pytest.raises(GenerationNotFoundException):
            self.manager.get_by_id("gen-123", "user-123")


class TestDelete:
    """Tests for delete method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_file_service.delete_generation_outputs.return_value = (0, 0)
        self.mock_plugins = Mock()

        # Set up default hook execution (no blocking)
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_delete_success(self):
        """Should delete generation successfully."""
        mock_gen = Mock()
        mock_gen.preset_id = "preset-123"
        self.mock_repo.get_by_id.return_value = mock_gen
        self.mock_repo.get_files.return_value = []
        self.mock_repo.delete.return_value = True

        result = self.manager.delete("gen-123", "user-123")

        assert "files_deleted_fs" in result
        assert "files_deleted_db" in result
        self.mock_repo.delete.assert_called_once_with("gen-123")

    def test_delete_not_found(self):
        """Should raise GenerationNotFoundException if not found."""
        self.mock_repo.get_by_id.return_value = None

        with pytest.raises(GenerationNotFoundException):
            self.manager.delete("gen-123", "user-123")

    def test_delete_blocked_by_hook(self):
        """Should raise if hook blocks deletion."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen

        mock_context = Mock()
        mock_context.data = {
            "blocked": True,
            "block_reason": "Deletion blocked by plugin"
        }
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(GenerationDeleteFailedException) as exc_info:
            self.manager.delete("gen-123", "user-123")

        assert "blocked" in str(exc_info.value).lower()

    def test_delete_executes_hooks(self):
        """Should execute before and after hooks."""
        mock_gen = Mock()
        mock_gen.preset_id = "preset-123"
        self.mock_repo.get_by_id.return_value = mock_gen
        self.mock_repo.get_files.return_value = []
        self.mock_repo.delete.return_value = True

        self.manager.delete("gen-123", "user-123")

        # Verify hooks were called
        calls = self.mock_plugins.execute_hook.call_args_list
        hook_names = [str(call) for call in calls]
        assert any(GENERATION_HOOKS.before_delete in name for name in hook_names)
        assert any(GENERATION_HOOKS.after_delete in name for name in hook_names)

    def test_delete_db_failure(self):
        """Should raise if database delete fails."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen
        self.mock_repo.get_files.return_value = []
        self.mock_repo.delete.return_value = False

        with pytest.raises(GenerationDeleteFailedException):
            self.manager.delete("gen-123", "user-123")


class TestBulkDelete:
    """Tests for bulk_delete method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_file_service.delete_generation_outputs.return_value = (0, 0)
        self.mock_plugins = Mock()

        # Set up default hook execution (no blocking)
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_bulk_delete_empty_list(self):
        """Should return zero counts for empty list."""
        result = self.manager.bulk_delete([], "user-123")

        assert result["deleted_count"] == 0
        assert result["failed_count"] == 0

    def test_bulk_delete_success(self):
        """Should delete multiple generations."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen
        self.mock_repo.get_files.return_value = []
        self.mock_repo.delete.return_value = True

        result = self.manager.bulk_delete(["gen-1", "gen-2"], "user-123")

        assert result["deleted_count"] == 2
        assert result["failed_count"] == 0

    def test_bulk_delete_partial_failure(self):
        """Should handle partial failures."""
        # First generation exists, second doesn't
        self.mock_repo.get_by_id.side_effect = [Mock(), None]
        self.mock_repo.get_files.return_value = []
        self.mock_repo.delete.return_value = True

        result = self.manager.bulk_delete(["gen-1", "gen-2"], "user-123")

        assert result["deleted_count"] == 1
        assert result["failed_count"] == 1
        assert "gen-2" in result["failed_ids"]

    def test_bulk_delete_blocked_by_hook(self):
        """Should raise if hook blocks bulk deletion."""
        mock_context = Mock()
        mock_context.data = {
            "blocked": True,
            "block_reason": "Bulk deletion blocked"
        }
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(GenerationDeleteFailedException):
            self.manager.bulk_delete(["gen-1"], "user-123")


class TestUploadGenerations:
    """Tests for upload_generations method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        # Set up default hook execution (no blocking)
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    @pytest.mark.asyncio
    async def test_upload_empty_files(self):
        """Should raise for empty file list."""
        with pytest.raises(UploadFailedException) as exc_info:
            await self.manager.upload_generations([], [], "user-123")

        assert "No files provided" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upload_blocked_by_hook(self):
        """Should raise if hook blocks upload."""
        mock_context = Mock()
        mock_context.data = {
            "blocked": True,
            "block_reason": "Upload blocked"
        }
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        mock_file = Mock()

        with pytest.raises(UploadFailedException):
            await self.manager.upload_generations([mock_file], [], "user-123")

    @pytest.mark.asyncio
    @patch('src.features.generation.history_archive.os.makedirs')
    @patch('src.features.generation.history_archive.generate_ulid')
    async def test_upload_no_valid_images(self, mock_ulid, mock_makedirs):
        """Should raise if no valid images uploaded."""
        mock_ulid.return_value = "gen-123"
        self.mock_file_service.get_full_path.return_value = "/tmp/test"

        mock_file = Mock()
        mock_file.content_type = "text/plain"  # Not an image
        mock_file.filename = "test.txt"

        # Set up repository methods
        self.mock_repo.create.return_value = Mock()
        self.mock_repo.delete.return_value = True

        with pytest.raises(UploadFailedException) as exc_info:
            await self.manager.upload_generations([mock_file], [], "user-123")

        assert "No valid files" in str(exc_info.value)


class TestUploadGenerationsFileTypes:
    """Real-filesystem upload tests distinguishing MESH from IMAGE handling."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.mock_repo.create.return_value = Mock()

        created_file = Mock()
        created_file.to_dict.return_value = {}
        self.mock_repo.add_file.return_value = created_file

    def _make_manager(self, tmp_path):
        mock_file_service = Mock()
        mock_file_service.get_full_path.side_effect = lambda rel: str(tmp_path / rel)
        return GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=mock_file_service,
            plugin_registry=self.mock_plugins
        )

    @pytest.mark.asyncio
    async def test_upload_glb_is_stored_as_mesh(self, tmp_path):
        """A .glb upload must produce a MESH File record with no thumbnails or dimensions."""
        manager = self._make_manager(tmp_path)

        mock_file = MagicMock()
        mock_file.filename = "model.glb"
        mock_file.content_type = "model/gltf-binary"
        mock_file.read = AsyncMock(return_value=b"not-really-a-glb-but-any-bytes-will-do")

        result = await manager.upload_generations([mock_file], [], "user-123")

        assert result["generation_id"]
        added_record = self.mock_repo.add_file.call_args[0][1]
        assert added_record.file_type == "MESH"
        assert added_record.mime_type == "model/gltf-binary"
        assert added_record.width is None
        assert added_record.height is None
        assert added_record.thumbnail_small is None
        assert added_record.thumbnail_medium is None
        assert added_record.thumbnail_large is None

    @pytest.mark.asyncio
    async def test_upload_image_still_gets_thumbnails_and_dimensions(self, tmp_path):
        """Regression guard: image uploads keep producing dimensions and thumbnails."""
        manager = self._make_manager(tmp_path)

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), color="red").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        mock_file = MagicMock()
        mock_file.filename = "photo.png"
        mock_file.content_type = "image/png"
        mock_file.read = AsyncMock(return_value=png_bytes)

        await manager.upload_generations([mock_file], [], "user-123")

        added_record = self.mock_repo.add_file.call_args[0][1]
        assert added_record.file_type == "IMAGE"
        assert added_record.mime_type == "image/png"
        assert added_record.width == 32
        assert added_record.height == 32
        assert added_record.thumbnail_small is not None

    @pytest.mark.asyncio
    async def test_upload_wav_is_stored_as_audio(self, tmp_path):
        """A .wav upload must produce an AUDIO File record with no thumbnails
        or dimensions - mirrors test_upload_glb_is_stored_as_mesh, pinning
        the same `is_mesh -> elif is_audio -> elif is_video -> else` branch
        for the audio arm (previously missing: a .wav upload fell through to
        'IMAGE' and `Image.open` was attempted on it)."""
        manager = self._make_manager(tmp_path)

        mock_file = MagicMock()
        mock_file.filename = "track.wav"
        mock_file.content_type = "audio/wav"
        mock_file.read = AsyncMock(return_value=b"not-really-a-wav-but-any-bytes-will-do")

        result = await manager.upload_generations([mock_file], [], "user-123")

        assert result["generation_id"]
        added_record = self.mock_repo.add_file.call_args[0][1]
        assert added_record.file_type == "AUDIO"
        assert added_record.mime_type == "audio/wav"
        assert added_record.width is None
        assert added_record.height is None
        assert added_record.thumbnail_small is None
        assert added_record.thumbnail_medium is None
        assert added_record.thumbnail_large is None


class _NoopUploadPluginRegistry:
    """A plugin registry that neither blocks nor rewrites anything."""

    def execute_hook(self, hook, initial_data=None):
        context = Mock()
        context.data = dict(initial_data or {})
        return context, []


class TestUploadGenerationsRealPersistence:
    """`test_upload_glb_is_stored_as_mesh` above only proves the manager
    *builds* a File record with file_type='MESH' - `self.mock_repo` is a bare
    Mock, so `add_file.call_args` is just the argument the manager handed it,
    not anything read back from a database. This drives the real
    GenerationRepository/FileRepository over a real migrated schema instead,
    so it actually proves persistence rather than construction.
    """

    @pytest.fixture
    def repos_on_test_db(self, mock_db):
        """Point the repositories at the test database (they bind `db` at import)."""
        with patch('src.features.generation.file_repository.db', mock_db), \
             patch('src.features.generation.repository.db', mock_db):
            yield mock_db

    @pytest.fixture
    def manager(self, repos_on_test_db, test_storage):
        from src.features.generation.repository import GenerationRepository
        from src.platform.filesystem.file_store import FileStore

        return GenerationHistoryManager(
            generation_repo=GenerationRepository(),
            file_service=FileStore(str(test_storage)),
            plugin_registry=_NoopUploadPluginRegistry(),
        )

    @pytest.mark.asyncio
    async def test_upload_glb_persists_as_mesh_in_the_real_database(self, manager, repos_on_test_db):
        from src.features.generation.repository import GenerationRepository
        from src.platform.util.ids import generate_ulid

        user_id = generate_ulid()
        with repos_on_test_db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
                (user_id, 'mesh_upload_tester', 'mesh-upload@example.com', 'hashed'),
            )

        mock_file = MagicMock()
        mock_file.filename = "model.glb"
        mock_file.content_type = "model/gltf-binary"
        mock_file.read = AsyncMock(return_value=b"not-really-a-glb-but-any-bytes-will-do")

        result = await manager.upload_generations([mock_file], [], user_id)

        gen_id = result["generation_id"]
        files = GenerationRepository().get_files(gen_id)
        assert len(files) == 1
        assert files[0].file_type == "MESH"
        assert files[0].width is None
        assert files[0].height is None
        assert files[0].thumbnail_small is None

        # `test_upload_glb_is_stored_as_mesh` (Mock-repo version above) only
        # proved the manager *builds* a File with mime_type set - it never
        # exercised FileRepository.create()'s INSERT. This is the real
        # round-trip check.
        assert files[0].mime_type == "model/gltf-binary"


class TestTagOperations:
    """Tests for tag-related methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        # Set up default hook execution (no blocking)
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    @patch('src.features.tags.repository.tag_repo')
    def test_get_tags_success(self, mock_tag_repo):
        """Should return tags for generation."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen

        # A real DTO, not a Mock: tag_repo returns pydantic Tags, and a Mock
        # auto-satisfies any serialization method the code happens to call -
        # which is exactly how the .to_dict() regression got through (see
        # test_update_tags_success below, fixed the same way in 566259a2).
        from src.features.tags.dto import Tag, TagType
        real_tag = Tag(id="tag-1", name="Test Tag", type=TagType.GENERATION, user_id="user-123")
        mock_tag_repo.get_generation_tags.return_value = [real_tag]

        result = self.manager.get_tags("gen-123", "user-123")

        assert result == [real_tag.model_dump(mode="json")]

    def test_get_tags_not_found(self):
        """Should raise GenerationNotFoundException if not found."""
        self.mock_repo.get_by_id.return_value = None

        with pytest.raises(GenerationNotFoundException):
            self.manager.get_tags("gen-123", "user-123")

    @patch('src.features.tags.repository.tag_repo')
    def test_update_tags_success(self, mock_tag_repo):
        """Should update tags successfully."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen

        # A real DTO, not a Mock: the repository returns pydantic Tags, and a
        # Mock auto-satisfies any serialization method the code happens to
        # call - which is exactly how the .to_dict() regression got through.
        from src.features.tags.dto import Tag, TagType
        real_tag = Tag(id="tag-1", name="fav", type=TagType.GENERATION, user_id="user-123")
        mock_tag_repo.get_tag_by_id.return_value = real_tag
        mock_tag_repo.set_generation_tags.return_value = True
        mock_tag_repo.get_generation_tags.return_value = [real_tag]

        result = self.manager.update_tags("gen-123", ["tag-1"], "user-123")

        assert result == [real_tag.model_dump(mode="json")]
        mock_tag_repo.set_generation_tags.assert_called_once()

    @patch('src.features.tags.repository.tag_repo')
    def test_update_tags_blocked_by_hook(self, mock_tag_repo):
        """Should raise if hook blocks tag update."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen

        # Valid tag for validation
        mock_tag = Mock()
        mock_tag.type = 'GENERATION'
        mock_tag.user_id = "user-123"
        mock_tag_repo.get_tag_by_id.return_value = mock_tag

        # Block the update
        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Blocked"}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(InvalidTagException):
            self.manager.update_tags("gen-123", ["tag-1"], "user-123")

    @patch('src.features.tags.repository.tag_repo')
    def test_remove_tag_success(self, mock_tag_repo):
        """Should remove tag successfully."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen
        mock_tag_repo.remove_tag_from_generation.return_value = True

        result = self.manager.remove_tag("gen-123", "tag-1", "user-123")

        assert result is True
        mock_tag_repo.remove_tag_from_generation.assert_called_once_with("gen-123", "tag-1")

    @patch('src.features.tags.repository.tag_repo')
    def test_remove_tag_not_found(self, mock_tag_repo):
        """Should return False if tag not found."""
        mock_gen = Mock()
        self.mock_repo.get_by_id.return_value = mock_gen
        mock_tag_repo.remove_tag_from_generation.return_value = False

        result = self.manager.remove_tag("gen-123", "tag-1", "user-123")

        assert result is False


class TestHookExecution:
    """Tests for hook execution behavior."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_file_service = Mock()
        self.mock_plugins = Mock()

        self.manager = GenerationHistoryManager(
            generation_repo=self.mock_repo,
            file_service=self.mock_file_service,
            plugin_registry=self.mock_plugins
        )

    def test_execute_hook_returns_context_data(self):
        """Should return context data from hook execution."""
        mock_context = Mock()
        mock_context.data = {"key": "value", "blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(
            self.manager.plugins,
            GENERATION_HOOKS.before_delete,
            {"generation_id": "gen-123"}
        )

        assert data["key"] == "value"
        assert blocked is False

    def test_execute_hook_detects_blocked(self):
        """Should detect when hook sets blocked flag."""
        mock_context = Mock()
        mock_context.data = {"blocked": True}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(
            self.manager.plugins,
            GENERATION_HOOKS.before_delete,
            {"generation_id": "gen-123"}
        )

        assert blocked is True

    def test_execute_hook_passes_initial_data(self):
        """Should pass initial data to hook."""
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        execute_hook(
            self.manager.plugins,
            GENERATION_HOOKS.before_delete,
            {"generation_id": "gen-123", "user_id": "user-123"}
        )

        self.mock_plugins.execute_hook.assert_called_once()
        call_kwargs = self.mock_plugins.execute_hook.call_args[1]
        assert call_kwargs["initial_data"]["generation_id"] == "gen-123"
        assert call_kwargs["initial_data"]["user_id"] == "user-123"


class TestGenerationModelsAreUserFacing:
    """A generation's history names the models it used, not where they live on disk."""

    def test_models_are_serialized_without_operational_fields(self):
        """`to_dict()` defaults to the admin payload, so generation history has to opt
        out explicitly — otherwise sha256, file_path and file_size ride along into the
        user-facing generation details modal."""
        manager = GenerationHistoryManager(
            generation_repo=Mock(), file_service=Mock(), plugin_registry=Mock()
        )
        generation = Mock()
        generation.user_id = 'u1'
        generation.to_dict.return_value = {'id': 'g1', 'user_id': 'u1'}
        manager.generation_repo.get_by_id.return_value = generation

        model = Mock()
        model.to_dict.return_value = {'id': 'm1', 'name': 'detail'}

        with patch('src.features.generation.parameter_repository.generation_parameter_repo') as params, \
             patch('src.features.generation.model_repository.generation_model_repo') as models, \
             patch('src.features.generation.segment_repository.generation_segment_repo') as segs:
            params.get_by_generation.return_value = []
            models.get_by_generation.return_value = [model]
            segs.get_by_generation.return_value = []

            manager.get_by_id('g1', 'u1', include_files=False)

        assert model.to_dict.call_args.kwargs.get('admin') is False


class TestHasProfileFlag:
    """`has_profile` on the generation detail: a cheap file-existence check that
    the admin profile viewer keys off, and which must never break get_by_id."""

    def _make_query(self, base_storage_dir):
        from src.features.generation.history_query import GenerationHistoryQuery
        file_service = Mock()
        file_service.base_storage_dir = base_storage_dir
        return GenerationHistoryQuery(Mock(), file_service)

    def test_true_when_profile_file_present(self, tmp_path):
        from src.features.generation import profile_paths
        pdir = profile_paths.profile_dir(tmp_path, 'gen-1')
        pdir.mkdir(parents=True)
        (pdir / 'profile.jsonl').write_text('{}\n')

        query = self._make_query(tmp_path)
        assert query._has_profile('gen-1') is True

    def test_false_when_no_profile_file(self, tmp_path):
        query = self._make_query(tmp_path)
        assert query._has_profile('gen-1') is False

    def test_false_without_file_service(self):
        from src.features.generation.history_query import GenerationHistoryQuery
        query = GenerationHistoryQuery(Mock())  # no file_service
        assert query._has_profile('gen-1') is False

    def test_false_when_path_unresolvable(self):
        # base_storage_dir is a bare Mock (Path(Mock()) raises) -- must swallow.
        query = self._make_query(Mock())
        assert query._has_profile('gen-1') is False

    def test_get_by_id_includes_has_profile(self, tmp_path):
        from src.features.generation import profile_paths
        pdir = profile_paths.profile_dir(tmp_path, 'g1')
        pdir.mkdir(parents=True)
        (pdir / 'profile.jsonl').write_text('{}\n')

        manager = GenerationHistoryManager(
            generation_repo=Mock(), file_service=Mock(), plugin_registry=Mock()
        )
        manager.file_service.base_storage_dir = tmp_path
        manager._query.file_service = manager.file_service
        generation = Mock()
        generation.user_id = 'u1'
        generation.to_dict.return_value = {'id': 'g1', 'user_id': 'u1'}
        manager.generation_repo.get_by_id.return_value = generation

        with patch('src.features.generation.parameter_repository.generation_parameter_repo') as params, \
             patch('src.features.generation.model_repository.generation_model_repo') as models, \
             patch('src.features.generation.segment_repository.generation_segment_repo') as segs, \
             patch('src.features.tags.repository.tag_repo') as tags:
            params.get_by_generation.return_value = []
            models.get_by_generation.return_value = []
            segs.get_by_generation.return_value = []
            tags.get_generation_tags.return_value = []

            result = manager.get_by_id('g1', 'u1', include_files=False)

        assert result['has_profile'] is True
