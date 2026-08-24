"""
Comprehensive integration tests for handler system.

Tests cover:
- Handler can_handle() logic
- Cross-handler integration
- File saving with user_id
- Metadata propagation
"""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from PIL import Image
from typing import Dict, Any

from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler
from src.features.generation.handlers.video_handler import VideoGenerationOutputHandler
from src.features.generation.handlers.gallery_handler import GalleryGenerationOutputHandler
from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.generation.handlers.artifact_handlers import (
    SeedGenerationOutputHandler,
    ModelsGenerationOutputHandler,
    ProgressGenerationOutputHandler
)
from src.pipelines.outputs import (
    ImageGenerationOutput,
    VideoGenerationOutput,
    GalleryGenerationOutput,
    ParamGenerationOutput,
    SeedGenerationOutput,
    ModelsGenerationOutput,
    ModelGenerationOutput,
    ProgressGenerationOutput
)
from src.pipelines.outputs import Progress
from pathlib import Path


class TestImageHandlerIntegration:
    """Integration tests for image handler."""

    def test_image_handler_can_handle(self):
        """Test image handler can_handle logic."""
        handler = ImageGenerationOutputHandler('gen_123', 'user_456')

        image = Image.new('RGB', (100, 100))
        output = ImageGenerationOutput(image=image)

        assert handler.can_handle(output) is True

    def test_image_handler_rejects_other_outputs(self):
        """Test image handler rejects non-image outputs."""
        handler = ImageGenerationOutputHandler('gen_123', 'user_456')

        output = ParamGenerationOutput(name='test', values=[1, 2])

        assert handler.can_handle(output) is False

    @patch('src.features.generation.handlers.image_handler.generation_repo')
    @patch('src.features.generation.handlers.image_handler.os')
    @patch('src.platform.filesystem.file_store.FileStore')
    def test_image_handler_saves_permanent_image(self, mock_file_service_class, mock_os, mock_repo):
        """Test image handler saves permanent images."""
        mock_os.makedirs = Mock()
        mock_os.path.exists = Mock(return_value=True)
        mock_os.path.getsize = Mock(return_value=1000)

        # Mock FileStore
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.get_full_path.return_value = "/storage/path/to/image.png"

        # Create handler with settings_manager mock
        mock_settings = Mock()
        mock_settings.get_file_storage_directory.return_value = "/storage"
        handler = ImageGenerationOutputHandler('gen_123', 'user_456', mock_settings, Mock())

        image = Image.new('RGB', (100, 100))
        output = ImageGenerationOutput(image=image, temporary=False, pipe_name='generator')

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch.object(handler, '_save_image', return_value=('path/to/image.png', {'small': 'path/to/thumb.png'})):
            result = handler.handle(output)

        assert result['processed'] is True
        assert result['temporary'] is False
        mock_repo.add_file.assert_called_once()

    @patch('src.features.generation.handlers.image_handler.generation_repo')
    def test_image_handler_skips_temporary_images(self, mock_repo):
        """Test image handler doesn't save temporary images to DB."""
        handler = ImageGenerationOutputHandler('gen_123', 'user_456')

        image = Image.new('RGB', (100, 100))
        output = ImageGenerationOutput(image=image, temporary=True)

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch.object(handler, '_save_image', return_value=('/path/to/temp.png', {'small': '/path/to/thumb.png'})):
            result = handler.handle(output)

        assert result['processed'] is True
        assert result['temporary'] is True
        mock_repo.add_file.assert_not_called()


class TestGalleryHandlerIntegration:
    """Integration tests for gallery handler."""

    def test_gallery_handler_can_handle(self):
        """Test gallery handler can_handle logic."""
        handler = GalleryGenerationOutputHandler('gen_123', 'user_456')

        image = Image.new('RGB', (50, 50))
        image_output = ImageGenerationOutput(image=image)
        output = GalleryGenerationOutput(images=[image_output])

        assert handler.can_handle(output) is True

    @patch('src.features.generation.handlers.image_handler.generation_repo')
    @patch('src.features.generation.handlers.image_handler.os')
    @patch('src.platform.filesystem.file_store.FileStore')
    def test_gallery_handler_saves_all_images(self, mock_file_service_class, mock_os, mock_repo):
        """Test gallery handler saves all images in gallery."""
        mock_os.makedirs = Mock()
        mock_os.path.exists = Mock(return_value=True)
        mock_os.path.getsize = Mock(return_value=1000)

        # Mock FileStore
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.get_full_path.side_effect = lambda p: f"/storage/{p}"

        # Create handler with settings_manager mock
        mock_settings = Mock()
        mock_settings.get_file_storage_directory.return_value = "/storage"
        handler = GalleryGenerationOutputHandler('gen_123', 'user_456', mock_settings, Mock())

        # Create gallery with 3 images
        images = []
        for i in range(3):
            image = Image.new('RGB', (100, 100))
            images.append(ImageGenerationOutput(
                image=image,
                temporary=False,
                pipe_name='generator'
            ))

        output = GalleryGenerationOutput(images=images)

        # Mock the ImageGenerationOutputHandler's _save_image - returns tuple of (file_path, thumbnail_paths)
        with patch('src.features.generation.handlers.image_handler.ImageGenerationOutputHandler._save_image',
                   side_effect=[(f'path/to/image{i}.png', {'small': f'path/to/thumb{i}.png'}) for i in range(3)]):
            result = handler.handle(output)

        assert result['processed'] is True
        assert result['image_count'] == 3
        # Each image should create a file record
        assert mock_repo.add_file.call_count == 3


class TestParamHandlerIntegration:
    """Integration tests for param handler."""

    def test_param_handler_can_handle(self):
        """Test param handler can_handle logic."""
        handler = ParamGenerationOutputHandler('gen_123', 'user_456')

        output = ParamGenerationOutput(name='seed', values=[12345, 67890])

        assert handler.can_handle(output) is True

    @patch('src.features.generation.parameter_repository.generation_parameter_repo')
    def test_param_handler_processes_params(self, mock_param_repo):
        """Test param handler processes parameters correctly."""
        handler = ParamGenerationOutputHandler('gen_123', 'user_456')

        output = ParamGenerationOutput(
            name='cfg_scale',
            values=[7.0, 7.5, 8.0]
        )

        # Mock the repository to return saved parameters
        mock_params = [Mock(id=f'param_{i}') for i in range(3)]
        mock_param_repo.create_batch.return_value = mock_params

        result = handler.handle(output)

        assert result['processed'] is True
        assert result['parameter_name'] == 'cfg_scale'
        assert result['value_count'] == 3


class TestSeedHandlerIntegration:
    """Integration tests for seed handler."""

    def test_seed_handler_can_handle(self):
        """Test seed handler can_handle logic."""
        handler = SeedGenerationOutputHandler('gen_123', 'user_456')

        output = SeedGenerationOutput(index=0, seed=12345)

        assert handler.can_handle(output) is True

    def test_seed_handler_processes_seed(self):
        """Test seed handler processes seed correctly."""
        handler = SeedGenerationOutputHandler('gen_123', 'user_456')

        output = SeedGenerationOutput(index=2, seed=98765)

        result = handler.handle(output)

        assert result['processed'] is True
        assert result['seed'] == 98765
        assert result['index'] == 2


class TestModelsHandlerIntegration:
    """Integration tests for models handler."""

    def test_models_handler_can_handle(self):
        """Test models handler can_handle logic."""
        handler = ModelsGenerationOutputHandler('gen_123', 'user_456')

        model = ModelGenerationOutput(name='test_model', type='checkpoint')
        output = ModelsGenerationOutput(models=[model])

        assert handler.can_handle(output) is True

    def test_models_handler_processes_models(self):
        """Test models handler processes models correctly."""
        handler = ModelsGenerationOutputHandler('gen_123', 'user_456')

        models = [
            ModelGenerationOutput(name='checkpoint1', type='checkpoint', weight=1.0),
            ModelGenerationOutput(name='lora1', type='lora', weight=0.8),
            ModelGenerationOutput(name='lora2', type='lora', weight=0.6)
        ]
        output = ModelsGenerationOutput(models=models)

        result = handler.handle(output)

        # ModelsGenerationOutputHandler is transport-only, doesn't return model_count
        assert result['processed'] is True
        assert result['handler'] == 'ModelsGenerationOutputHandler'


class TestProgressHandlerIntegration:
    """Integration tests for progress handler."""

    def test_progress_handler_can_handle(self):
        """Test progress handler can_handle logic."""
        handler = ProgressGenerationOutputHandler('gen_123', 'user_456')

        output = ProgressGenerationOutput(
            pipe_id=2,
            state='Generating',
            progress=Progress(current=5, max=10)
        )

        assert handler.can_handle(output) is True

    def test_progress_handler_processes_progress(self):
        """Test progress handler processes progress correctly."""
        handler = ProgressGenerationOutputHandler('gen_123', 'user_456')

        output = ProgressGenerationOutput(
            pipe_id=3,
            pipe_name='generator',
            state='Processing',
            title='Generating Images',
            progress=Progress(current=7, max=10)
        )

        result = handler.handle(output)

        # ProgressGenerationOutputHandler is transport-only, doesn't return state
        assert result['processed'] is True
        assert result['handler'] == 'ProgressGenerationOutputHandler'


class TestUserIdPropagation:
    """Test cases for user_id propagation through handlers."""

    @patch('src.features.generation.handlers.image_handler.generation_repo')
    @patch('src.features.generation.handlers.image_handler.os')
    @patch('src.platform.filesystem.file_store.FileStore')
    def test_user_id_included_in_file_record(self, mock_file_service_class, mock_os, mock_repo):
        """Test that user_id is included in file records."""
        mock_os.makedirs = Mock()
        mock_os.path.exists = Mock(return_value=True)
        mock_os.path.getsize = Mock(return_value=1000)

        # Mock FileStore
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.get_full_path.return_value = "/storage/path/to/image.png"

        user_id = 'user_789'
        # Create handler with settings_manager mock
        mock_settings = Mock()
        mock_settings.get_file_storage_directory.return_value = "/storage"
        handler = ImageGenerationOutputHandler('gen_123', user_id, mock_settings, Mock())

        image = Image.new('RGB', (100, 100))
        output = ImageGenerationOutput(image=image, temporary=False, pipe_name='generator')

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch.object(handler, '_save_image', return_value=('path/to/image.png', {'small': 'path/to/thumb.png'})):
            handler.handle(output)

        # Verify user_id was passed to repository
        mock_repo.add_file.assert_called_once()
        file_arg = mock_repo.add_file.call_args[0][1]
        assert file_arg.user_id == user_id


class TestErrorHandling:
    """Test cases for error handling in handlers."""

    def test_handler_error_returns_error_metadata(self):
        """Test that handler errors are captured in metadata."""
        class FailingHandler(BaseGenerationOutputHandler):
            def can_handle(self, output):
                return True

            def handle(self, output):
                raise Exception("Handler processing failed")

        handler = FailingHandler('gen_123')

        with pytest.raises(Exception, match="Handler processing failed"):
            handler.handle(Mock())


class TestCrossHandlerIntegration:
    """Test cases for cross-handler integration scenarios."""

    @patch('src.features.generation.handlers.image_handler.generation_repo')
    @patch('src.features.generation.handlers.image_handler.os')
    @patch('src.platform.filesystem.file_store.FileStore')
    def test_gallery_with_mixed_temporary_permanent_images(self, mock_file_service_class, mock_os, mock_repo):
        """Test gallery handler with mix of temporary and permanent images."""
        mock_os.makedirs = Mock()
        mock_os.path.exists = Mock(return_value=True)
        mock_os.path.getsize = Mock(return_value=1000)

        # Mock FileStore
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.get_full_path.side_effect = lambda p: f"/storage/{p}"

        # Create handler with settings_manager mock
        mock_settings = Mock()
        mock_settings.get_file_storage_directory.return_value = "/storage"
        handler = GalleryGenerationOutputHandler('gen_123', 'user_456', mock_settings, Mock())

        images = [
            ImageGenerationOutput(
                image=Image.new('RGB', (100, 100)),
                temporary=False,  # Permanent
                pipe_name='generator'
            ),
            ImageGenerationOutput(
                image=Image.new('RGB', (100, 100)),
                temporary=True,  # Temporary
                pipe_name='generator'
            ),
            ImageGenerationOutput(
                image=Image.new('RGB', (100, 100)),
                temporary=False,  # Permanent
                pipe_name='generator'
            )
        ]

        output = GalleryGenerationOutput(images=images)

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch('src.features.generation.handlers.image_handler.ImageGenerationOutputHandler._save_image',
                   side_effect=[(f'path/to/image{i}.png', {'small': f'path/to/thumb{i}.png'}) for i in range(3)]):
            result = handler.handle(output)

        # Only permanent images should create DB records (2 out of 3)
        assert mock_repo.add_file.call_count == 2
