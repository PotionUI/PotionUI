"""Tests for the PhrasebookPreviewGenerator class."""
import os
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, AsyncMock

from src.features.phrasebook.preview_generator import PhrasebookPreviewGenerator
from src.features.phrasebook.dto import (
    PhrasebookCategory,
    PhrasebookValue,
    PhrasebookStateFilter,
)
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.settings.settings import Settings


class TestPhrasebookPreviewGenerator:
    """Tests for PhrasebookPreviewGenerator."""

    @pytest.fixture
    def mock_category_repository(self):
        """Create a mock PhrasebookCategoryRepository."""
        return Mock(spec=PhrasebookCategoryRepository)

    @pytest.fixture
    def mock_value_repository(self):
        """Create a mock PhrasebookValueRepository."""
        return Mock(spec=PhrasebookValueRepository)

    @pytest.fixture
    def mock_settings(self):
        """Create a mock Settings."""
        manager = Mock(spec=Settings)
        manager.get_setting.return_value = "/tmp/test_storage"
        return manager

    @pytest.fixture
    def generator(
        self,
        mock_category_repository,
        mock_value_repository,
        mock_settings
    ):
        """Create an PhrasebookPreviewGenerator with mocks."""
        return PhrasebookPreviewGenerator(
            category_repository=mock_category_repository,
            value_repository=mock_value_repository,
            settings=mock_settings
        )

    @pytest.fixture
    def sample_category(self):
        """Create a sample category."""
        return PhrasebookCategory(
            id="cat-123",
            name="Test Category",
            path="test.category",
            parent_id=None,
            description="Test description",
            is_active=True,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_value(self):
        """Create a sample value."""
        return PhrasebookValue(
            id="val-123",
            category_id="cat-123",
            label="Test Value",
            value="test value content",
            sort_order=0,
            is_active=True,
            preview_file_id=None,
            preview_generation_id=None,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    # ========== Template Validation Tests ==========

    def test_validate_prompt_template_valid(self, generator):
        """Test validating a valid template."""
        result = generator.validate_prompt_template("A photo of << value >>")

        assert result is True

    def test_validate_prompt_template_valid_no_spaces(self, generator):
        """Test validating a template without spaces in placeholder."""
        result = generator.validate_prompt_template("A photo of <<value>>")

        assert result is True

    def test_validate_prompt_template_valid_extra_spaces(self, generator):
        """Test validating a template with extra spaces in placeholder."""
        result = generator.validate_prompt_template("A photo of <<   value   >>")

        assert result is True

    def test_validate_prompt_template_missing_placeholder(self, generator):
        """Test validating a template without the value placeholder."""
        with pytest.raises(ValueError) as exc_info:
            generator.validate_prompt_template("A photo of something")

        assert "<< value >>" in str(exc_info.value)

    def test_validate_prompt_template_rejects_legacy_placeholder(self, generator):
        """The old {{ value }} token is not accepted - no dual-syntax support."""
        with pytest.raises(ValueError) as exc_info:
            generator.validate_prompt_template("A photo of {{ value }}")

        assert "<< value >>" in str(exc_info.value)

    def test_validate_prompt_template_complex_valid(self, generator):
        """Test validating a complex valid template."""
        template = """
        A professional photo of << value >>,
        high quality, detailed
        """
        result = generator.validate_prompt_template(template)

        assert result is True

    # ========== Prompt Rendering Tests ==========

    def test_render_prompt_simple(self, generator):
        """Test rendering a simple prompt."""
        result = generator.render_prompt("A photo of << value >>", "sunset")

        assert result == "A photo of sunset"

    def test_render_prompt_multiple_occurrences(self, generator):
        """Test rendering a prompt with multiple value placeholders."""
        result = generator.render_prompt(
            "<< value >> is beautiful, especially << value >>",
            "sunset"
        )

        assert result == "sunset is beautiful, especially sunset"

    def test_render_prompt_no_spaces(self, generator):
        """Test rendering a prompt without spaces in placeholder."""
        result = generator.render_prompt("A photo of <<value>>", "mountain")

        assert result == "A photo of mountain"

    def test_render_prompt_preserves_whitespace(self, generator):
        """Test that rendering preserves whitespace."""
        result = generator.render_prompt(
            "  A photo of << value >>  ",
            "ocean"
        )

        assert result == "  A photo of ocean  "

    def test_render_prompt_with_special_characters(self, generator):
        """Test rendering with special characters in value."""
        result = generator.render_prompt(
            "A photo of << value >>",
            "sunrise & sunset, \"beautiful\""
        )

        assert result == "A photo of sunrise & sunset, \"beautiful\""

    def test_render_prompt_does_not_evaluate_value_content(self, generator):
        """Plain substitution: a value that looks like a template expression is
        left literal, never evaluated (there is no template engine anymore)."""
        result = generator.render_prompt(
            "A photo of << value >>",
            "<< value.__class__ >>"
        )

        assert result == "A photo of << value.__class__ >>"

    # ========== Storage Path Tests ==========

    def test_get_preview_storage_path(self, generator, mock_settings):
        """Test getting preview storage path."""
        result = generator.get_preview_storage_path("cat-123")

        assert result == "/tmp/test_storage/phrasebook/cat-123"
        mock_settings.get_setting.assert_called_once_with(
            "file_storage_directory", "storage"
        )

    def test_get_preview_storage_path_uses_default(self, generator, mock_settings):
        """Test that storage path uses default when setting not found."""
        mock_settings.get_setting.return_value = "storage"

        result = generator.get_preview_storage_path("cat-456")

        assert result == "storage/phrasebook/cat-456"

    # ========== Build Generation Request Tests ==========

    def test_build_generation_request_basic(self, generator):
        """Test building a basic generation request."""
        session_data = {
            'form_data': {'width': 512, 'height': 512},
            'mode': 'txt2img'
        }

        result = generator.build_generation_request(
            session_data=session_data,
            preset_id="preset-123",
            rendered_prompt="A photo of sunset"
        )

        assert result['preset_id'] == "preset-123"
        assert result['prompts'][0]['positive'] == "A photo of sunset"
        assert result['mode'] == 'txt2img'
        assert result['form_data']['width'] == 512
        assert 'seed' in result
        assert isinstance(result['seed'], int)

    def test_build_generation_request_with_negative_override(self, generator):
        """Test building request with negative prompt override."""
        session_data = {
            'form_data': {'width': 512},
            'negative_prompt': 'session negative'
        }

        result = generator.build_generation_request(
            session_data=session_data,
            preset_id="preset-123",
            rendered_prompt="A photo of sunset",
            negative_prompt="override negative"
        )

        assert result['prompts'][0]['negative'] == "override negative"

    def test_build_generation_request_uses_session_negative(self, generator):
        """Test building request uses session negative prompt when no override."""
        session_data = {
            'form_data': {'width': 512},
            'negative_prompt': 'session negative'
        }

        result = generator.build_generation_request(
            session_data=session_data,
            preset_id="preset-123",
            rendered_prompt="A photo of sunset"
        )

        assert result['prompts'][0]['negative'] == "session negative"

    def test_build_generation_request_with_fixed_seed(self, generator):
        """Test building request with fixed seed."""
        session_data = {'form_data': {}}

        result = generator.build_generation_request(
            session_data=session_data,
            preset_id="preset-123",
            rendered_prompt="A photo of sunset",
            seed=12345
        )

        assert result['seed'] == 12345

    def test_build_generation_request_random_seed(self, generator):
        """Test building request with random seed."""
        session_data = {'form_data': {}}

        result1 = generator.build_generation_request(
            session_data=session_data,
            preset_id="preset-123",
            rendered_prompt="A photo of sunset",
            seed=None
        )

        # Seed should be a random integer
        assert isinstance(result1['seed'], int)
        assert 0 <= result1['seed'] <= 2147483647

    # ========== Delete Preview Image Tests ==========

    def test_delete_preview_image_success(self, generator, mock_value_repository, sample_value):
        """Test successfully deleting a preview image."""
        # Value with preview file ID (not a path)
        with_preview = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=sample_value.updated_at
        )
        mock_value_repository.get_by_id.return_value = with_preview
        mock_value_repository.update_preview_file.return_value = True

        # Mock file record
        mock_file_record = Mock()
        mock_file_record.file_path = "generations/gen-123/preview_val-123.png"

        mock_storage_driver = Mock()
        generator.storage_driver = mock_storage_driver

        with patch('src.features.generation.file_repository.file_repo') as mock_file_repo:
            mock_file_repo.get_by_id.return_value = mock_file_record
            result = generator.delete_preview_image("val-123", "cat-123", "user-123")

        assert result is True
        mock_file_repo.get_by_id.assert_called_once_with("file-123")
        mock_storage_driver.delete.assert_called_once_with("generations/gen-123/preview_val-123.png")
        mock_file_repo.delete.assert_called_once_with("file-123")
        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", None, None
        )

    def test_delete_preview_image_no_preview(self, generator, mock_value_repository, sample_value):
        """Test deleting preview when value has no preview."""
        mock_value_repository.get_by_id.return_value = sample_value

        result = generator.delete_preview_image("val-123", "cat-123", "user-123")

        assert result is False

    def test_delete_preview_image_value_not_found(self, generator, mock_value_repository):
        """Test deleting preview when value doesn't exist."""
        mock_value_repository.get_by_id.return_value = None

        result = generator.delete_preview_image("nonexistent", "cat-123", "user-123")

        assert result is False

    def test_delete_preview_image_file_not_exists(self, generator, mock_value_repository, sample_value):
        """Test deleting preview when file doesn't exist."""
        # Value with preview file ID but file doesn't exist on disk
        with_preview = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=sample_value.updated_at
        )
        mock_value_repository.get_by_id.return_value = with_preview
        mock_value_repository.update_preview_file.return_value = True

        # Mock file record
        mock_file_record = Mock()
        mock_file_record.file_path = "generations/gen-123/preview_val-123.png"

        # Mock FileStore
        mock_file_service = Mock()
        mock_file_service.get_full_path.return_value = "/tmp/test_storage/generations/gen-123/preview_val-123.png"

        with patch('src.features.generation.file_repository.file_repo') as mock_file_repo, \
             patch('src.platform.filesystem.file_store.FileStore', return_value=mock_file_service), \
             patch('os.path.exists', return_value=False):
            mock_file_repo.get_by_id.return_value = mock_file_record
            result = generator.delete_preview_image("val-123", "cat-123", "user-123")

        # Should still succeed (clear the DB reference even if file doesn't exist)
        assert result is True
        mock_file_repo.delete.assert_called_once_with("file-123")
        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", None, None
        )


class TestPhrasebookPreviewGeneratorAsync:
    """Async tests for PhrasebookPreviewGenerator."""

    @pytest.fixture
    def mock_category_repository(self):
        """Create a mock PhrasebookCategoryRepository."""
        return Mock(spec=PhrasebookCategoryRepository)

    @pytest.fixture
    def mock_value_repository(self):
        """Create a mock PhrasebookValueRepository."""
        return Mock(spec=PhrasebookValueRepository)

    @pytest.fixture
    def mock_settings(self):
        """Create a mock Settings."""
        manager = Mock(spec=Settings)
        manager.get_setting.return_value = "/tmp/test_storage"
        return manager

    @pytest.fixture
    def generator(
        self,
        mock_category_repository,
        mock_value_repository,
        mock_settings
    ):
        """Create an PhrasebookPreviewGenerator with mocks."""
        return PhrasebookPreviewGenerator(
            category_repository=mock_category_repository,
            value_repository=mock_value_repository,
            settings=mock_settings
        )

    @pytest.fixture
    def sample_category(self):
        """Create a sample category."""
        return PhrasebookCategory(
            id="cat-123",
            name="Test Category",
            path="test.category",
            parent_id=None,
            description="Test description",
            is_active=True,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.fixture
    def sample_value(self):
        """Create a sample value."""
        return PhrasebookValue(
            id="val-123",
            category_id="cat-123",
            label="Test Value",
            value="test value content",
            sort_order=0,
            is_active=True,
            preview_file_id=None,
            preview_generation_id=None,
            user_id="user-123",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

    @pytest.mark.asyncio
    async def test_generate_previews_invalid_template(self, generator):
        """Test generate_previews with invalid template."""
        mock_orchestrator = AsyncMock()

        with pytest.raises(ValueError) as exc_info:
            await generator.generate_previews(
                category_id="cat-123",
                session_id="session-123",
                prompt_template="No placeholder here",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator
            )

        assert "<< value >>" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_previews_category_not_found(
        self, generator, mock_category_repository
    ):
        """Test generate_previews when category not found."""
        mock_category_repository.get_by_id.return_value = None
        mock_orchestrator = AsyncMock()

        with pytest.raises(ValueError) as exc_info:
            await generator.generate_previews(
                category_id="nonexistent",
                session_id="session-123",
                prompt_template="A photo of << value >>",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator
            )

        assert "Category not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_previews_session_not_found(
        self, generator, mock_category_repository, sample_category
    ):
        """Test generate_previews when session not found."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_orchestrator = AsyncMock()

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo:
            mock_session_repo.get_by_id.return_value = None

            with pytest.raises(ValueError) as exc_info:
                await generator.generate_previews(
                    category_id="cat-123",
                    session_id="nonexistent",
                    prompt_template="A photo of << value >>",
                    mode="txt2img",
                    user_id="user-123",
                    generation_orchestrator=mock_orchestrator
                )

        assert "Session not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_previews_no_values(
        self, generator, mock_category_repository, mock_value_repository, sample_category
    ):
        """Test generate_previews when no values to generate."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_value_repository.get_by_category.return_value = []
        mock_orchestrator = AsyncMock()

        mock_session = Mock()
        mock_session.preset_id = "preset-123"
        # Session data is mode-based: session.data[mode] = { formData: {...}, ... }
        mock_session.data = {
            'txt2img': {
                'formData': {},
                'negativePrompt': ''
            }
        }

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo, \
             patch('os.makedirs'):
            mock_session_repo.get_by_id.return_value = mock_session

            result = await generator.generate_previews(
                category_id="cat-123",
                session_id="session-123",
                prompt_template="A photo of << value >>",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator
            )

        assert result['total'] == 0
        assert result['started'] == 0
        assert result['completed'] == 0
        assert result['failed'] == 0
        assert result['generations'] == []

    @pytest.mark.asyncio
    async def test_generate_previews_success(
        self,
        generator,
        mock_category_repository,
        mock_value_repository,
        sample_category,
        sample_value
    ):
        """Test successful preview generation."""
        import asyncio
        mock_category_repository.get_by_id.return_value = sample_category
        mock_value_repository.get_by_category.return_value = [sample_value]

        # Create a mock orchestrator that calls the callback with None to signal completion
        async def mock_start_generation(request, user_id, output_callback=None):
            # Call the callback with None to signal completion (as the real orchestrator does)
            if output_callback:
                await output_callback('gen-123', None)
            return {'generation_id': 'gen-123'}

        mock_orchestrator = AsyncMock()
        mock_orchestrator.start_generation = mock_start_generation

        mock_session = Mock()
        mock_session.preset_id = "preset-123"
        # Session data is mode-based: session.data[mode] = { formData: {...}, ... }
        mock_session.data = {
            'txt2img': {
                'formData': {'width': 512},
                'negativePrompt': 'blurry'
            }
        }

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo, \
             patch('os.makedirs'):
            mock_session_repo.get_by_id.return_value = mock_session

            result = await generator.generate_previews(
                category_id="cat-123",
                session_id="session-123",
                prompt_template="A photo of << value >>",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator
            )

        assert result['total'] == 1
        assert result['started'] == 1
        assert result['completed'] == 1
        assert result['failed'] == 0
        assert len(result['generations']) == 1
        assert result['generations'][0]['value_id'] == sample_value.id
        assert result['generations'][0]['generation_id'] == 'gen-123'
        assert result['generations'][0]['rendered_prompt'] == "A photo of test value content"

    @pytest.mark.asyncio
    async def test_generate_previews_with_specific_value_ids(
        self,
        generator,
        mock_category_repository,
        mock_value_repository,
        sample_category
    ):
        """Test preview generation for specific value IDs."""
        mock_category_repository.get_by_id.return_value = sample_category

        value1 = PhrasebookValue(
            id="val-1", category_id="cat-123", label="Value 1",
            value="content 1", sort_order=0, is_active=True,
            user_id="user-123", created_at=datetime.utcnow(), updated_at=datetime.utcnow()
        )
        value2 = PhrasebookValue(
            id="val-2", category_id="cat-123", label="Value 2",
            value="content 2", sort_order=1, is_active=True,
            user_id="user-123", created_at=datetime.utcnow(), updated_at=datetime.utcnow()
        )
        value3 = PhrasebookValue(
            id="val-3", category_id="cat-123", label="Value 3",
            value="content 3", sort_order=2, is_active=True,
            user_id="user-123", created_at=datetime.utcnow(), updated_at=datetime.utcnow()
        )

        mock_value_repository.get_by_category.return_value = [value1, value2, value3]

        # Track generation IDs assigned
        gen_counter = [0]

        async def mock_start_generation(request, user_id, output_callback=None):
            gen_counter[0] += 1
            gen_id = f'gen-{gen_counter[0]}'
            # Call the callback with None to signal completion
            if output_callback:
                await output_callback(gen_id, None)
            return {'generation_id': gen_id}

        mock_orchestrator = AsyncMock()
        mock_orchestrator.start_generation = mock_start_generation

        mock_session = Mock()
        mock_session.preset_id = "preset-123"
        # Session data is mode-based: session.data[mode] = { formData: {...}, ... }
        mock_session.data = {
            'txt2img': {
                'formData': {},
                'negativePrompt': ''
            }
        }

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo, \
             patch('os.makedirs'):
            mock_session_repo.get_by_id.return_value = mock_session

            # Only generate for value 1 and 3
            result = await generator.generate_previews(
                category_id="cat-123",
                session_id="session-123",
                prompt_template="A photo of << value >>",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator,
                value_ids=["val-1", "val-3"]
            )

        assert result['total'] == 2
        assert result['started'] == 2
        assert result['completed'] == 2
        generated_ids = [g['value_id'] for g in result['generations']]
        assert "val-1" in generated_ids
        assert "val-3" in generated_ids
        assert "val-2" not in generated_ids

    @pytest.mark.asyncio
    async def test_generate_previews_handles_generation_failure(
        self,
        generator,
        mock_category_repository,
        mock_value_repository,
        sample_category,
        sample_value
    ):
        """Test that generation failures are handled gracefully."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_value_repository.get_by_category.return_value = [sample_value]

        mock_orchestrator = AsyncMock()
        mock_orchestrator.start_generation.side_effect = Exception("Generation failed")

        mock_session = Mock()
        mock_session.preset_id = "preset-123"
        # Session data is mode-based: session.data[mode] = { formData: {...}, ... }
        mock_session.data = {
            'txt2img': {
                'formData': {},
                'negativePrompt': ''
            }
        }

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo, \
             patch('os.makedirs'):
            mock_session_repo.get_by_id.return_value = mock_session

            result = await generator.generate_previews(
                category_id="cat-123",
                session_id="session-123",
                prompt_template="A photo of << value >>",
                mode="txt2img",
                user_id="user-123",
                generation_orchestrator=mock_orchestrator
            )

        assert result['total'] == 1
        assert result['started'] == 0
        assert result['completed'] == 0
        assert result['failed'] == 1
        assert result['generations'] == []

    @pytest.mark.asyncio
    async def test_generate_previews_mode_not_found(
        self, generator, mock_category_repository, mock_value_repository, sample_category
    ):
        """Test generate_previews when mode not found in session data."""
        mock_category_repository.get_by_id.return_value = sample_category
        mock_value_repository.get_by_category.return_value = []
        mock_orchestrator = AsyncMock()

        mock_session = Mock()
        mock_session.preset_id = "preset-123"
        # Session data only has txt2img, not img2img
        mock_session.data = {
            'txt2img': {
                'formData': {},
                'negativePrompt': ''
            }
        }

        with patch('src.features.phrasebook.preview_generator.session_repo') as mock_session_repo:
            mock_session_repo.get_by_id.return_value = mock_session

            with pytest.raises(ValueError) as exc_info:
                await generator.generate_previews(
                    category_id="cat-123",
                    session_id="session-123",
                    prompt_template="A photo of << value >>",
                    mode="img2img",  # This mode is not in session data
                    user_id="user-123",
                    generation_orchestrator=mock_orchestrator
                )

        assert "Mode 'img2img' not found" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
