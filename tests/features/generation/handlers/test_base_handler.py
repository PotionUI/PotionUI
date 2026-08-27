"""`_resolve_storage_dir` lives on `BaseGenerationOutputHandler` and is
inherited by every concrete output handler - it used to be copy-pasted into
each one, each with its own module-level "warned already" flag. Consolidating
onto the base makes that flag handler-global: a warning fired by one output
type (e.g. image) now silences the same warning for another (e.g. audio)
within the same process."""

from unittest.mock import Mock

import pytest

from src.features.generation.handlers import base_handler
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler
from src.platform.settings.settings import Settings


class _ConcreteHandler(BaseGenerationOutputHandler):
    def can_handle(self, output):
        return True

    def handle(self, output):
        return {}


@pytest.fixture(autouse=True)
def _reset_warn_once_flag():
    base_handler._warned_storage_dir_fallback = False
    yield
    base_handler._warned_storage_dir_fallback = False


class TestResolveStorageDir:
    def test_falls_back_to_storage_with_no_settings(self):
        handler = _ConcreteHandler("gen-id")
        assert handler._resolve_storage_dir() == "storage"

    def test_uses_the_configured_directory(self):
        settings = Mock(spec=Settings)
        settings.get_file_storage_directory.return_value = "/configured/root"
        handler = _ConcreteHandler("gen-id", settings=settings)
        assert handler._resolve_storage_dir() == "/configured/root"

    def test_falls_back_to_storage_when_settings_raise(self):
        settings = Mock(spec=Settings)
        settings.get_file_storage_directory.side_effect = RuntimeError("boom")
        handler = _ConcreteHandler("gen-id", settings=settings)
        assert handler._resolve_storage_dir() == "storage"

    def test_warn_once_flag_is_shared_across_handler_types(self, caplog):
        """The flag is now a single module-level global on base_handler, not
        one copy per handler subclass - a warning from the image handler
        suppresses the same warning from the audio handler."""
        settings = Mock(spec=Settings)
        settings.get_file_storage_directory.side_effect = RuntimeError("boom")

        image_handler = ImageGenerationOutputHandler("gen-id", settings=settings)
        audio_handler = AudioGenerationOutputHandler("gen-id", settings=settings)

        with caplog.at_level("WARNING"):
            image_handler._resolve_storage_dir()
            audio_handler._resolve_storage_dir()

        warnings = [r for r in caplog.records if "Failed to get file storage directory" in r.message]
        assert len(warnings) == 1
