import os
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock
from PIL import Image
from datetime import datetime

from src.features.generation.handlers import (
    BaseGenerationOutputHandler, ImageGenerationOutputHandler,
    VideoGenerationOutputHandler, GalleryGenerationOutputHandler,
    ParamGenerationOutputHandler,
    CompareImagesGenerationOutputHandler, ProgressGenerationOutputHandler,
    TimerGenerationOutputHandler, ModelsGenerationOutputHandler,
    SeedGenerationOutputHandler, AudioGenerationOutputHandler,
    MeshGenerationOutputHandler,
    ComfyUIWorkflowGenerationOutputHandler, WarmStartGenerationOutputHandler,
    RenderedPromptGenerationOutputHandler, DiffTextGenerationOutputHandler
)
from src.features.generation.output_types import output_type_registry
from src.pipelines.outputs import (
    GenerationOutput, ImageGenerationOutput, VideoGenerationOutput,
    GalleryGenerationOutput, CompareImagesGenerationOutput,
    ProgressGenerationOutput, TimerGenerationOutput,
    ModelsGenerationOutput, ModelGenerationOutput,
    ParamGenerationOutput, SeedGenerationOutput, AudioGenerationOutput
)
from src.pipelines.outputs import Icon, Progress


class TestBaseGenerationOutputHandler:
    def test_abstract_methods_not_implemented(self):
        # BaseGenerationOutputHandler is abstract, so we can't instantiate it directly
        # This test verifies that trying to do so raises a TypeError
        with pytest.raises(TypeError):
            BaseGenerationOutputHandler("test_gen")


class TestImageGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.user_id = "user_456"
        # Create handler with mock settings
        self.mock_settings = Mock()
        self.mock_settings.get_file_storage_directory.return_value = "/storage"
        self.mock_storage_driver = Mock()
        self.handler = ImageGenerationOutputHandler(
            self.generation_id, self.user_id, self.mock_settings, self.mock_storage_driver
        )

        # Create a test image
        self.test_image = Image.new('RGB', (100, 100), color='red')

    def test_can_handle_image_output(self):
        image_output = ImageGenerationOutput(image=self.test_image)
        assert self.handler.can_handle(image_output) is True

    def test_can_handle_non_image_output(self):
        progress_output = ProgressGenerationOutput(state="test")
        assert self.handler.can_handle(progress_output) is False

    def test_handle_temporary_image(self):
        output = ImageGenerationOutput(image=self.test_image, temporary=True)

        with patch.object(self.handler, '_save_image') as mock_save:
            result = self.handler.handle(output)

            # Should not attempt to save temporary images
            mock_save.assert_not_called()

            assert result['handler'] == 'ImageGenerationOutputHandler'
            assert result['processed'] is True
            assert result['temporary'] is True

    def test_handle_permanent_image_success(self):
        output = ImageGenerationOutput(image=self.test_image, temporary=False)
        saved_path = "/test/path/image.png"
        thumbnail_paths = {'small': '/test/path/thumb.png'}
        mock_file_record = Mock()
        mock_file_record.id = "file_123"

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch.object(self.handler, '_save_image', return_value=(saved_path, thumbnail_paths)) as mock_save, \
             patch.object(self.handler, '_save_file_record', return_value=mock_file_record) as mock_save_record:

            result = self.handler.handle(output)

            mock_save.assert_called_once_with(self.test_image)
            # _save_file_record now takes 3 parameters: file_path, output, thumbnail_paths
            mock_save_record.assert_called_once_with(saved_path, output, thumbnail_paths)

            assert result['handler'] == 'ImageGenerationOutputHandler'
            assert result['processed'] is True
            assert result['temporary'] is False
            assert result['saved_path'] == saved_path
            assert result['file_id'] == "file_123"
            assert hasattr(output, '_saved_path')
            assert output._saved_path == saved_path

    def test_handle_permanent_image_save_failure(self):
        output = ImageGenerationOutput(image=self.test_image, temporary=False)

        with patch.object(self.handler, '_save_image', return_value=None) as mock_save:
            result = self.handler.handle(output)

            mock_save.assert_called_once_with(self.test_image)

            assert result['handler'] == 'ImageGenerationOutputHandler'
            assert result['processed'] is True
            assert result['temporary'] is False
            assert 'saved_path' not in result
            assert result['save_error'] == "Failed to save image"

    def test_handle_database_error(self):
        output = ImageGenerationOutput(image=self.test_image, temporary=False)
        saved_path = "/test/path/image.png"
        thumbnail_paths = {'small': '/test/path/thumb.png'}

        # _save_image returns tuple of (file_path, thumbnail_paths)
        with patch.object(self.handler, '_save_image', return_value=(saved_path, thumbnail_paths)), \
             patch.object(self.handler, '_save_file_record', side_effect=Exception("DB Error")):

            result = self.handler.handle(output)

            assert result['handler'] == 'ImageGenerationOutputHandler'
            assert result['processed'] is True
            assert result['saved_path'] == saved_path
            assert result['db_error'] == "DB Error"

    def test_handle_exception(self):
        output = ImageGenerationOutput(image=self.test_image, temporary=False)

        with patch.object(self.handler, '_save_image', side_effect=Exception("Save error")):
            result = self.handler.handle(output)

            assert result['handler'] == 'ImageGenerationOutputHandler'
            assert result['processed'] is False
            assert result['error'] == "Save error"

    def test_save_image_success(self):
        # The _save_image method now uses FileStore, which handles all the file operations
        # FileStore is imported inside the method, so we patch it at import time
        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class, \
             patch.object(self.test_image, 'save') as mock_image_save:

            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            # The actual method is save_file, not save_image
            mock_file_service.save_file.return_value = (
                "generations/2024-01-15/test_gen_123/0.png",
                {'file_path': 'generations/2024-01-15/test_gen_123/0.png'}
            )
            mock_file_service.get_generation_directory.return_value = "generations/2024-01-15/test_gen_123"

            result = self.handler._save_image(self.test_image)

            # Should call FileStore.save_file
            mock_file_service.save_file.assert_called_once()

            # Should return tuple of (path, thumbnail_paths)
            assert result is not None
            assert result[0] == "generations/2024-01-15/test_gen_123/0.png"
            assert self.handler.image_counter == 1

    def test_save_image_directory_creation(self):
        # The _save_image method now uses FileStore, which handles directory creation
        # FileStore is imported inside the method, so we patch it at import time
        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class, \
             patch.object(self.test_image, 'save') as mock_image_save:

            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            # The actual method is save_file, not save_image
            mock_file_service.save_file.return_value = (
                "generations/2024-01-15/test_gen_123/0.png",
                {'file_path': 'generations/2024-01-15/test_gen_123/0.png'}
            )
            mock_file_service.get_generation_directory.return_value = "generations/2024-01-15/test_gen_123"

            result = self.handler._save_image(self.test_image)

            # FileStore handles directory creation internally
            mock_file_service.save_file.assert_called_once()
            assert result is not None

    def test_save_image_error_handling(self):
        # Test error handling when FileStore fails
        # FileStore is imported inside the method, so we patch it at import time
        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class:
            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            mock_file_service.save_image.side_effect = OSError("Permission denied")

            result = self.handler._save_image(self.test_image)

            assert result is None

    def test_save_file_record_success(self):
        file_path = "generations/2024-01-15/test_gen_123/image.png"
        thumbnail_paths = {'small': 'generations/2024-01-15/test_gen_123/image_thumb_small.png'}
        output = ImageGenerationOutput(image=self.test_image, temporary=False)
        output.pipe_name = "test_pipe"

        mock_file_record = Mock()
        mock_file_record.id = "file_123"

        # _save_file_record now uses FileStore (imported inside the method)
        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class, \
             patch('src.features.generation.handlers.image_handler.generation_repo') as mock_repo, \
             patch('src.features.generation.handlers.image_handler.generate_ulid', return_value="ulid_123"):

            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            self.mock_storage_driver.size.return_value = 1024

            mock_repo.add_file.return_value = mock_file_record

            result = self.handler._save_file_record(file_path, output, thumbnail_paths)

            assert result == mock_file_record
            mock_repo.add_file.assert_called_once()

            # Check the File object passed to add_file
            call_args = mock_repo.add_file.call_args
            assert call_args[0][0] == self.generation_id  # generation_id
            file_obj = call_args[0][1]  # File object
            assert file_obj.id == "ulid_123"
            assert file_obj.file_path == file_path
            # file_type is 'IMAGE' (uppercase) from FileType enum
            assert file_obj.file_type == 'IMAGE'
            assert file_obj.user_id == self.user_id
            assert file_obj.file_size == 1024
            assert file_obj.pipe_name == "test_pipe"
            assert file_obj.is_final is True
            assert file_obj.is_derived is False

    def test_save_file_record_marks_derived(self):
        file_path = "generations/2024-01-15/test_gen_123/image.png"
        output = ImageGenerationOutput(image=self.test_image, temporary=False, derived=True)

        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class, \
             patch('src.features.generation.handlers.image_handler.generation_repo') as mock_repo, \
             patch('src.features.generation.handlers.image_handler.generate_ulid', return_value="ulid_123"):

            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            self.mock_storage_driver.size.return_value = 1024

            mock_repo.add_file.return_value = Mock()

            self.handler._save_file_record(file_path, output, {})

            file_obj = mock_repo.add_file.call_args[0][1]
            assert file_obj.is_derived is True
            assert file_obj.is_final is True

    def test_save_file_record_file_not_exists(self):
        file_path = "generations/2024-01-15/test_gen_123/image.png"
        thumbnail_paths = {'small': 'generations/2024-01-15/test_gen_123/image_thumb_small.png'}
        output = ImageGenerationOutput(image=self.test_image, temporary=False)

        mock_file_record = Mock()

        # FileStore is imported inside the method, so we patch it at import time
        with patch('src.platform.filesystem.file_store.FileStore') as mock_file_service_class, \
             patch('src.features.generation.handlers.image_handler.generation_repo') as mock_repo, \
             patch('src.features.generation.handlers.image_handler.generate_ulid', return_value="ulid_123"):

            mock_file_service = Mock()
            mock_file_service_class.return_value = mock_file_service
            self.mock_storage_driver.size.return_value = None

            mock_repo.add_file.return_value = mock_file_record

            result = self.handler._save_file_record(file_path, output, thumbnail_paths)

            # Should still create record but with None file_size
            call_args = mock_repo.add_file.call_args
            file_obj = call_args[0][1]
            assert file_obj.file_size is None

    def test_save_file_record_error(self):
        file_path = "/test/path/image.png"
        output = ImageGenerationOutput(image=self.test_image, temporary=False)

        with patch('src.features.generation.handlers.image_handler.generation_repo') as mock_repo:
            mock_repo.add_file.side_effect = Exception("Database error")

            result = self.handler._save_file_record(file_path, output)

            assert result is None


class TestVideoGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.user_id = "user_456"
        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = "/test/storage"
        self.mock_storage_driver = Mock()
        self.handler = VideoGenerationOutputHandler(
            self.generation_id, self.user_id, self.settings, self.mock_storage_driver
        )

        # Create a temporary video file for testing
        self.temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.temp_video.write(b"fake video data")
        self.temp_video.close()
        self.temp_video_path = self.temp_video.name

    def teardown_method(self):
        # Clean up temp file
        if os.path.exists(self.temp_video_path):
            os.unlink(self.temp_video_path)

    def test_can_handle_video_output(self):
        video_output = VideoGenerationOutput(video_path=self.temp_video_path)
        assert self.handler.can_handle(video_output) is True

    def test_can_handle_non_video_output(self):
        image_output = ImageGenerationOutput(image=Image.new('RGB', (100, 100)))
        assert self.handler.can_handle(image_output) is False

    @patch('src.platform.filesystem.file_store.FileStore')
    def test_handle_temporary_video(self, mock_file_service_class):
        """Test that temporary videos are saved to tmp directory."""
        # Setup mock
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (
            "/test/storage/tmp/tmp_video_test_gen_123_xxx.mp4",
            {
                'file_path': "tmp/tmp_video_test_gen_123_xxx.mp4",
                'file_type': 'VIDEO',
                'mime_type': 'video/mp4',
                'file_size': 15,
                'is_temporary': True
            }
        )

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=True)

        result = self.handler.handle(output)

        # Verify save_file_from_path was called with tmp storage type, streaming
        # from the source path rather than a bytes blob
        mock_file_service.save_file_from_path.assert_called_once()
        mock_file_service.save_file.assert_not_called()
        call_args = mock_file_service.save_file_from_path.call_args
        assert call_args.kwargs['source_path'] == self.temp_video_path
        assert call_args.kwargs['storage_type'] == 'tmp'
        assert call_args.kwargs['is_temporary'] is True
        assert call_args.kwargs['generation_id'] is None  # No generation_id for tmp files
        assert 'tmp_video_test_gen_123' in call_args.kwargs['prefix']

        # Check result
        assert result['handler'] == 'VideoGenerationOutputHandler'
        assert result['processed'] is True
        assert result['temporary'] is True
        assert result['saved_path'] == "tmp/tmp_video_test_gen_123_xxx.mp4"
        assert result['file_record'] is None  # No DB record for temporary

    @patch('src.platform.filesystem.file_store.FileStore')
    @patch('src.features.generation.handlers.video_handler.generation_repo')
    def test_handle_permanent_video(self, mock_repo, mock_file_service_class):
        """Test that permanent videos are saved to generations directory."""
        # Setup mocks
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (
            "/test/storage/generations/2024-01-01/test_gen_123/1.mp4",
            {
                'file_path': "generations/2024-01-01/test_gen_123/1.mp4",
                'file_type': 'VIDEO',
                'mime_type': 'video/mp4',
                'file_size': 15,
                'is_temporary': False
            }
        )

        mock_file_record = Mock()
        mock_file_record.id = "file_456"
        mock_file_record.file_path = "generations/2024-01-01/test_gen_123/1.mp4"
        mock_file_record.file_size = 15
        mock_repo.add_file.return_value = mock_file_record

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=False)

        with patch.object(self.handler, '_get_video_dimensions', return_value=(1920, 1080)):
            result = self.handler.handle(output)

        # Verify save_file_from_path was called with generations storage type
        mock_file_service.save_file_from_path.assert_called_once()
        call_args = mock_file_service.save_file_from_path.call_args
        assert call_args.kwargs['source_path'] == self.temp_video_path
        assert call_args.kwargs['storage_type'] == 'generations'
        assert call_args.kwargs['is_temporary'] is False
        assert call_args.kwargs['generation_id'] == self.generation_id
        assert call_args.kwargs['prefix'] == '1'  # Counter incremented

        # Check result
        assert result['handler'] == 'VideoGenerationOutputHandler'
        assert result['processed'] is True
        assert result['temporary'] is False
        assert result['saved_path'] == "generations/2024-01-01/test_gen_123/1.mp4"
        assert result['file_record'] is not None
        assert result['file_record']['id'] == "file_456"

    @patch('src.platform.filesystem.file_store.FileStore')
    def test_handle_video_save_failure(self, mock_file_service_class):
        """Test handling when video save fails."""
        # Setup mock to return failure
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (None, None)

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=False)

        result = self.handler.handle(output)

        assert result['handler'] == 'VideoGenerationOutputHandler'
        # A failed FINAL save must be visible as a failure - the orchestrator
        # relies on 'processed'/'save_error' to decide whether the
        # generation actually completed (see _final_save_error).
        assert result['processed'] is False
        assert result['save_error'] == "Failed to save video"
        assert result['saved_path'] is None
        assert result['file_record'] is None

    @patch('src.platform.filesystem.file_store.FileStore')
    def test_handle_temporary_video_save_failure_does_not_fail_processing(self, mock_file_service_class):
        """A failed save of a TEMPORARY (intermediate) video is not fatal -
        only a final, non-temporary save failure must fail the generation."""
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (None, None)

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=True)

        result = self.handler.handle(output)

        assert result['processed'] is True
        assert 'save_error' not in result
        assert result['saved_path'] is None

    @patch('src.platform.filesystem.file_store.FileStore')
    @patch('src.features.generation.handlers.video_handler.temp_source_tracker')
    def test_handle_registers_temp_source_for_temporary_video(self, mock_tracker, mock_file_service_class):
        """The raw source path a pipe wrote via NamedTemporaryFile is
        registered for later cleanup, not deleted here -- the same path is
        read again by the terminal gallery pipe's non-temporary save."""
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (
            "/test/storage/tmp/tmp_video_test_gen_123_xxx.mp4",
            {'file_path': "tmp/tmp_video_test_gen_123_xxx.mp4", 'file_type': 'VIDEO'}
        )

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=True)
        self.handler.handle(output)

        mock_tracker.register.assert_called_once_with(self.generation_id, self.temp_video_path)
        assert os.path.exists(self.temp_video_path)  # not deleted by the handler itself

    @patch('src.platform.filesystem.file_store.FileStore')
    @patch('src.features.generation.handlers.video_handler.generation_repo')
    @patch('src.features.generation.handlers.video_handler.temp_source_tracker')
    def test_handle_registers_temp_source_for_permanent_video(self, mock_tracker, mock_repo, mock_file_service_class):
        mock_file_service = Mock()
        mock_file_service_class.return_value = mock_file_service
        mock_file_service.save_file_from_path.return_value = (
            "/test/storage/generations/2024-01-01/test_gen_123/1.mp4",
            {'file_path': "generations/2024-01-01/test_gen_123/1.mp4", 'file_type': 'VIDEO'}
        )
        mock_file_record = Mock()
        mock_file_record.id = "file_456"
        mock_file_record.file_path = "generations/2024-01-01/test_gen_123/1.mp4"
        mock_file_record.file_size = 15
        mock_repo.add_file.return_value = mock_file_record

        output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=False)
        with patch.object(self.handler, '_get_video_dimensions', return_value=(1920, 1080)):
            self.handler.handle(output)

        mock_tracker.register.assert_called_once_with(self.generation_id, self.temp_video_path)

    @patch('src.features.generation.handlers.video_handler.temp_source_tracker')
    def test_handle_does_not_register_when_copy_fails(self, mock_tracker):
        """Nothing was ever successfully copied from the source path, so
        nothing is registered -- avoids tracking (and later unlinking) a path
        the handler never touched. Uses a real FileStore (unmocked) so the
        streaming copy genuinely fails against a missing source."""
        from src.platform.filesystem.file_store import FileStore

        with tempfile.TemporaryDirectory() as storage_dir:
            self.settings.get_file_storage_directory.return_value = storage_dir
            with patch('src.platform.filesystem.file_store.FileStore', return_value=FileStore(storage_dir)):
                output = VideoGenerationOutput(video_path="/nonexistent/path/video.mp4", temporary=True)
                self.handler.handle(output)

        mock_tracker.register.assert_not_called()

    def test_save_video_file_streams_without_reading_whole_file_into_memory(self):
        """The video source is copied disk-to-disk, not read
        fully into a `bytes` object before being handed to FileStore."""
        with tempfile.TemporaryDirectory() as storage_dir:
            self.settings.get_file_storage_directory.return_value = storage_dir
            # Exercise a real local driver rooted at storage_dir instead of
            # the shared setup_method Mock, so the streamed copy actually
            # lands on disk for the assertions below to find.
            self.handler.storage_driver = None

            output = VideoGenerationOutput(video_path=self.temp_video_path, temporary=True)
            saved_path = self.handler._save_video_file(output)

            assert saved_path is not None
            full_path = os.path.join(storage_dir, saved_path)
            assert os.path.exists(full_path)
            with open(full_path, 'rb') as f:
                assert f.read() == b"fake video data"

        # The handler itself never calls the whole-file-read pattern -- the
        # source is opened only by the streaming copy inside FileStore.
        import inspect
        source = inspect.getsource(self.handler._save_video_file)
        assert ".read()" not in source


class TestGalleryGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.user_id = "user_456"
        self.handler = GalleryGenerationOutputHandler(self.generation_id, self.user_id)

        # Create test images
        self.image1 = Image.new('RGB', (100, 100), color='red')
        self.image2 = Image.new('RGB', (100, 100), color='blue')

    def test_can_handle_gallery_output(self):
        gallery_output = GalleryGenerationOutput(images=[])
        assert self.handler.can_handle(gallery_output) is True

    def test_can_handle_non_gallery_output(self):
        image_output = ImageGenerationOutput(image=self.image1)
        assert self.handler.can_handle(image_output) is False

    def test_handle_empty_gallery(self):
        output = GalleryGenerationOutput(images=[])

        result = self.handler.handle(output)

        assert result['handler'] == 'GalleryGenerationOutputHandler'
        assert result['processed'] is True
        assert result['image_count'] == 0
        assert result['processed_images'] == []

    def test_handle_gallery_with_images(self):
        image_outputs = [
            ImageGenerationOutput(image=self.image1, temporary=False),
            ImageGenerationOutput(image=self.image2, temporary=True)
        ]
        output = GalleryGenerationOutput(images=image_outputs)

        # Need to patch where it's imported in gallery_handler module
        with patch('src.features.generation.handlers.gallery_handler.ImageGenerationOutputHandler') as mock_handler_class:
            mock_handler = Mock()
            mock_handler.image_counter = 0
            mock_handler_class.return_value = mock_handler

            # Mock handle method to return different results for each image - returns dict not Mock
            mock_handler.handle.side_effect = [
                {'processed': True, 'handler': 'ImageGenerationOutputHandler', 'saved_path': '/path/image1.png'},
                {'processed': True, 'handler': 'ImageGenerationOutputHandler', 'temporary': True}
            ]

            result = self.handler.handle(output)

            assert result['handler'] == 'GalleryGenerationOutputHandler'
            assert result['processed'] is True
            assert result['image_count'] == 2
            assert len(result['processed_images']) == 2

            # Check that gallery index was added to each image result
            assert result['processed_images'][0]['gallery_index'] == 0
            assert result['processed_images'][1]['gallery_index'] == 1

            # Verify handler was called for each image
            assert mock_handler.handle.call_count == 2

    def test_handle_gallery_counter_management(self):
        image_outputs = [
            ImageGenerationOutput(image=self.image1, temporary=False),
            ImageGenerationOutput(image=self.image2, temporary=False)
        ]
        output = GalleryGenerationOutput(images=image_outputs)

        # The counter now seeds from the generation's persisted file count
        with patch(
            'src.features.generation.file_repository.file_repo.get_generation_files',
            return_value=[Mock()] * 5,
        ), patch('src.features.generation.handlers.ImageGenerationOutputHandler') as mock_handler_class:
            mock_handler = Mock()
            mock_handler_class.return_value = mock_handler

            # Simulate image handler incrementing its counter
            def handle_side_effect(image_output):
                mock_handler.image_counter += 1
                return {'processed': True}

            mock_handler.handle.side_effect = handle_side_effect
            mock_handler.image_counter = 5  # Initial value

            result = self.handler.handle(output)

            # Check that main handler counter was updated
            assert self.handler.image_counter == 7  # 5 + 2 images

    def test_handle_exception(self):
        image_outputs = [ImageGenerationOutput(image=self.image1)]
        output = GalleryGenerationOutput(images=image_outputs)

        # Patch the gallery handler module's import, not the image_handler module
        with patch('src.features.generation.handlers.gallery_handler.ImageGenerationOutputHandler', side_effect=Exception("Handler error")):
            result = self.handler.handle(output)

            assert result['handler'] == 'GalleryGenerationOutputHandler'
            assert result['processed'] is False
            assert result['error'] == "Handler error"

    def test_handle_gallery_with_audio_only(self):
        """Test gallery with only audio files."""
        from pathlib import Path

        audio_outputs = [
            AudioGenerationOutput(audio_path=Path("/tmp/vocal.wav"), track_type="vocal", temporary=False),
            AudioGenerationOutput(audio_path=Path("/tmp/instrumental.wav"), track_type="instrumental", temporary=False),
            AudioGenerationOutput(audio_path=Path("/tmp/mixed.wav"), track_type="mixed", temporary=False)
        ]
        output = GalleryGenerationOutput(images=[], audios=audio_outputs)

        with patch('src.features.generation.handlers.gallery_handler.AudioGenerationOutputHandler') as mock_handler_class:
            mock_handler = Mock()
            mock_handler.image_counter = 0
            mock_handler_class.return_value = mock_handler

            mock_handler.handle.side_effect = [
                {'processed': True, 'handler': 'AudioGenerationOutputHandler', 'track_type': 'vocal'},
                {'processed': True, 'handler': 'AudioGenerationOutputHandler', 'track_type': 'instrumental'},
                {'processed': True, 'handler': 'AudioGenerationOutputHandler', 'track_type': 'mixed'}
            ]

            result = self.handler.handle(output)

            assert result['handler'] == 'GalleryGenerationOutputHandler'
            assert result['processed'] is True
            assert result['image_count'] == 0
            assert result['audio_count'] == 3
            assert len(result['processed_audios']) == 3
            assert mock_handler.handle.call_count == 3

    def test_handle_gallery_with_mixed_media(self):
        """Test gallery with images, videos, and audio."""
        from pathlib import Path

        image_outputs = [ImageGenerationOutput(image=self.image1, temporary=False)]
        video_outputs = [VideoGenerationOutput(video_path=Path("/tmp/video.mp4"), temporary=False)]
        audio_outputs = [AudioGenerationOutput(audio_path=Path("/tmp/audio.wav"), track_type="mixed", temporary=False)]

        output = GalleryGenerationOutput(
            images=image_outputs,
            videos=video_outputs,
            audios=audio_outputs
        )

        with patch('src.features.generation.handlers.gallery_handler.ImageGenerationOutputHandler') as mock_image_handler_class, \
             patch('src.features.generation.handlers.gallery_handler.VideoGenerationOutputHandler') as mock_video_handler_class, \
             patch('src.features.generation.handlers.gallery_handler.AudioGenerationOutputHandler') as mock_audio_handler_class:

            # Setup mock handlers
            mock_image_handler = Mock()
            mock_image_handler.image_counter = 0
            mock_image_handler_class.return_value = mock_image_handler
            mock_image_handler.handle.return_value = {'processed': True, 'handler': 'ImageGenerationOutputHandler'}

            mock_video_handler = Mock()
            mock_video_handler.image_counter = 1
            mock_video_handler_class.return_value = mock_video_handler
            mock_video_handler.handle.return_value = {'processed': True, 'handler': 'VideoGenerationOutputHandler'}

            mock_audio_handler = Mock()
            mock_audio_handler.image_counter = 2
            mock_audio_handler_class.return_value = mock_audio_handler
            mock_audio_handler.handle.return_value = {'processed': True, 'handler': 'AudioGenerationOutputHandler'}

            result = self.handler.handle(output)

            assert result['handler'] == 'GalleryGenerationOutputHandler'
            assert result['processed'] is True
            assert result['image_count'] == 1
            assert result['video_count'] == 1
            assert result['audio_count'] == 1
            assert len(result['processed_images']) == 1
            assert len(result['processed_videos']) == 1
            assert len(result['processed_audios']) == 1

    def test_handle_audio_counter_management(self):
        """Test that counter is managed correctly across audio files."""
        from pathlib import Path

        audio_outputs = [
            AudioGenerationOutput(audio_path=Path("/tmp/audio1.wav"), track_type="vocal", temporary=False),
            AudioGenerationOutput(audio_path=Path("/tmp/audio2.wav"), track_type="instrumental", temporary=False)
        ]
        output = GalleryGenerationOutput(images=[], audios=audio_outputs)

        # The counter now seeds from the generation's persisted file count
        with patch(
            'src.features.generation.file_repository.file_repo.get_generation_files',
            return_value=[Mock()] * 10,
        ), patch('src.features.generation.handlers.gallery_handler.AudioGenerationOutputHandler') as mock_handler_class:
            mock_handler = Mock()
            mock_handler_class.return_value = mock_handler

            # Simulate audio handler incrementing its counter
            def handle_side_effect(audio_output):
                mock_handler.image_counter += 1
                return {'processed': True}

            mock_handler.handle.side_effect = handle_side_effect
            mock_handler.image_counter = 10  # Initial value

            result = self.handler.handle(output)

            # Check that main handler counter was updated
            assert self.handler.image_counter == 12  # 10 + 2 audio files

    def test_handle_empty_gallery_all_media_types(self):
        """Test empty gallery with all media type fields."""
        output = GalleryGenerationOutput(images=[], videos=[], audios=[])

        result = self.handler.handle(output)

        assert result['handler'] == 'GalleryGenerationOutputHandler'
        assert result['processed'] is True
        assert result['image_count'] == 0
        assert result['video_count'] == 0
        assert result['audio_count'] == 0
        assert result['processed_images'] == []
        assert result['processed_videos'] == []
        assert result['processed_audios'] == []


class TestCompareImagesGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.handler = CompareImagesGenerationOutputHandler(self.generation_id)

        self.image1 = Image.new('RGB', (100, 100), color='red')
        self.image2 = Image.new('RGB', (100, 100), color='blue')

    def test_can_handle_compare_output(self):
        output = CompareImagesGenerationOutput(
            index=0,
            compare=("label1", self.image1),
            to=("label2", self.image2)
        )
        assert self.handler.can_handle(output) is True

    def test_can_handle_non_compare_output(self):
        output = ProgressGenerationOutput(state="test")
        assert self.handler.can_handle(output) is False

    def test_handle_compare_output(self):
        output = CompareImagesGenerationOutput(
            index=0,
            compare=("label1", self.image1),
            to=("label2", self.image2)
        )

        result = self.handler.handle(output)

        assert result['handler'] == 'CompareImagesGenerationOutputHandler'
        assert result['processed'] is True



class TestProgressGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.handler = ProgressGenerationOutputHandler(self.generation_id)

    def test_can_handle_progress_output(self):
        output = ProgressGenerationOutput(state="Processing")
        assert self.handler.can_handle(output) is True

    def test_can_handle_non_progress_output(self):
        output = TimerGenerationOutput(name="test_timer", value=1.5)
        assert self.handler.can_handle(output) is False

    def test_handle_progress_output(self):
        output = ProgressGenerationOutput(
            state="Processing step 1",
            icon=Icon("gear", "spin"),
            progress=Progress(current=5, max=10)
        )

        result = self.handler.handle(output)

        assert result['handler'] == 'ProgressGenerationOutputHandler'
        assert result['processed'] is True



class TestTimerGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.handler = TimerGenerationOutputHandler(self.generation_id)

    def test_can_handle_timer_output(self):
        output = TimerGenerationOutput(name="test_timer", value=1.5)
        assert self.handler.can_handle(output) is True

    def test_can_handle_non_timer_output(self):
        output = ProgressGenerationOutput(state="test")
        assert self.handler.can_handle(output) is False

    def test_handle_timer_output(self):
        output = TimerGenerationOutput(
            name="generation.total",
            value=2.5,
            unit="s"
        )

        result = self.handler.handle(output)

        assert result['handler'] == 'TimerGenerationOutputHandler'
        assert result['processed'] is True



class TestModelsGenerationOutputHandler:
    def setup_method(self):
        self.generation_id = "test_gen_123"
        self.handler = ModelsGenerationOutputHandler(self.generation_id)

    def test_can_handle_models_output(self):
        model = ModelGenerationOutput(name="test_model", type="checkpoint")
        output = ModelsGenerationOutput(models=[model])
        assert self.handler.can_handle(output) is True

    def test_can_handle_non_models_output(self):
        output = ProgressGenerationOutput(state="test")
        assert self.handler.can_handle(output) is False

    def test_handle_models_output(self):
        models = [
            ModelGenerationOutput(name="model1", type="checkpoint", weight=1.0),
            ModelGenerationOutput(name="model2", type="lora", weight=0.8)
        ]
        output = ModelsGenerationOutput(models=models)

        result = self.handler.handle(output)

        assert result['handler'] == 'ModelsGenerationOutputHandler'
        assert result['processed'] is True



class TestOutputTypeRegistryHandlerResolution:
    """Tests that the shared output_type_registry resolves handler_cls correctly.

    This replaces the old GenerationOutputHandlerRegistry linear can_handle scan:
    each output type now declares its handler_cls directly in its OutputTypeSpec.
    """

    def test_spec_found_for_image_output(self):
        image = Image.new('RGB', (100, 100), color='red')
        output = ImageGenerationOutput(image=image)

        spec = output_type_registry.spec_for(output)

        assert spec is not None
        assert spec.handler_cls is ImageGenerationOutputHandler

        handler = spec.handler_cls("test_gen", "user_123", None)
        assert isinstance(handler, ImageGenerationOutputHandler)
        assert handler.generation_id == "test_gen"
        assert handler.user_id == "user_123"

    def test_spec_resolves_distinct_handlers_per_type(self):
        image = Image.new('RGB', (100, 100), color='red')
        image_output = ImageGenerationOutput(image=image)
        image_spec = output_type_registry.spec_for(image_output)
        assert image_spec.handler_cls is ImageGenerationOutputHandler

        progress_output = ProgressGenerationOutput(state="test")
        progress_spec = output_type_registry.spec_for(progress_output)
        assert progress_spec.handler_cls is ProgressGenerationOutputHandler

    def test_process_output_with_handler(self):
        output = ProgressGenerationOutput(state="test")

        spec = output_type_registry.spec_for(output)
        handler = spec.handler_cls("test_gen", "user_123", None)
        result = handler.handle(output)

        assert result['handler'] == 'ProgressGenerationOutputHandler'
        assert result['processed'] is True

    def test_spec_not_found_for_unregistered_type(self):
        class UnregisteredOutput(GenerationOutput):
            pass

        spec = output_type_registry.spec_for(UnregisteredOutput())

        assert spec is None


class TestDefaultOutputTypeRegistry:
    def test_default_registry_has_all_handlers(self):
        """Test that all built-in output types with handlers are registered."""
        expected_handlers = {
            ImageGenerationOutputHandler,
            VideoGenerationOutputHandler,
            AudioGenerationOutputHandler,
            MeshGenerationOutputHandler,
            GalleryGenerationOutputHandler,
            CompareImagesGenerationOutputHandler,
            ProgressGenerationOutputHandler,
            TimerGenerationOutputHandler,
            ModelsGenerationOutputHandler,
            ParamGenerationOutputHandler,
            SeedGenerationOutputHandler,
            RenderedPromptGenerationOutputHandler,
            WarmStartGenerationOutputHandler,
            ComfyUIWorkflowGenerationOutputHandler,
            DiffTextGenerationOutputHandler
        }

        registered_handlers = {
            spec.handler_cls for spec in output_type_registry.all() if spec.handler_cls is not None
        }

        assert registered_handlers == expected_handlers

    def test_default_registry_processes_all_output_types(self):
        """Test that every registered output type can be processed via its handler_cls."""
        test_image = Image.new('RGB', (100, 100), color='red')

        test_outputs = [
            ImageGenerationOutput(image=test_image),
            GalleryGenerationOutput(images=[]),
            CompareImagesGenerationOutput(index=0, compare=("a", test_image), to=("b", test_image)),
            ProgressGenerationOutput(state="test"),
            TimerGenerationOutput(name="test", value=1.0),
            ModelsGenerationOutput(models=[])
        ]

        for output in test_outputs:
            spec = output_type_registry.spec_for(output)
            assert spec is not None and spec.handler_cls is not None, \
                f"No handler found for {type(output).__name__}"

            handler = spec.handler_cls("test_gen", None, None)
            result = handler.handle(output)
            assert result['processed'] is True, f"Failed to process {type(output).__name__}"


class TestFileCounterSeeding:
    """Fresh handlers must continue a generation's file numbering, never
    restart it — two gallery pipes in one pipeline (inline enhance) used to
    overwrite each other's 0.png originals and thumbnails."""

    def _gallery_output(self, count):
        img = Image.new('RGB', (8, 8), color='red')
        return GalleryGenerationOutput(
            images=[ImageGenerationOutput(image=img, temporary=False) for _ in range(count)],
            videos=[],
        )

    def _run_gallery(self, existing_records, output):
        handler = GalleryGenerationOutputHandler('gen_seed_test', 'user_1', Mock())
        seen_counters = []

        def fake_save(self_inner, image):
            seen_counters.append(self_inner.image_counter)
            path = f"{self_inner.image_counter}.png"
            self_inner.image_counter += 1
            return (path, {})

        with patch(
            'src.features.generation.file_repository.file_repo.get_generation_files',
            return_value=existing_records,
        ), patch.object(
            ImageGenerationOutputHandler, '_save_image', autospec=True, side_effect=fake_save
        ), patch.object(
            ImageGenerationOutputHandler, '_save_file_record', return_value=Mock(id='f1')
        ):
            handler.handle(output)
        return seen_counters, handler.image_counter

    def test_first_gallery_starts_at_zero(self):
        counters, final = self._run_gallery([], self._gallery_output(2))
        assert counters == [0, 1]
        assert final == 2

    def test_second_gallery_continues_after_persisted_files(self):
        counters, final = self._run_gallery([Mock(), Mock()], self._gallery_output(2))
        assert counters == [2, 3]
        assert final == 4

    def test_standalone_image_output_seeds_itself(self):
        handler = ImageGenerationOutputHandler('gen_seed_test', 'user_1', Mock())
        output = ImageGenerationOutput(image=Image.new('RGB', (8, 8)), temporary=False)
        with patch(
            'src.features.generation.file_repository.file_repo.get_generation_files',
            return_value=[Mock()],
        ), patch.object(handler, '_save_image', return_value=('1.png', {})), patch.object(
            handler, '_save_file_record', return_value=Mock(id='f1')
        ):
            handler.handle(output)
        assert handler.image_counter == 1
