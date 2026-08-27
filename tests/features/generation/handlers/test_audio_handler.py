"""Tests for the audio generation output handler.

Everything here drives the real save path: a real `.wav` (see
tests/fixtures/audio_fixtures.py) goes through AudioGenerationOutputHandler
into a real FileStore over a real migrated schema - mirroring
test_mesh_handler.py rather than mocking FileStore/generation_repo, so these
tests exercise the actual streamed save, the actual temp_source_tracker
registration, and the actual duration probe rather than a description of
them.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.generation.handlers.audio_handler import (
    AudioGenerationOutputHandler,
    serialize_audio_output,
)
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import output_type_registry
from src.features.generation.repository import generation_repo
from src.features.generation.temp_source_tracker import temp_source_tracker
from src.pipelines.outputs import AudioGenerationOutput, ProgressGenerationOutput
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid


@pytest.fixture
def settings(test_storage):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(test_storage)
    return settings


@pytest.fixture
def repos_on_test_db(mock_db):
    """Point the repositories at the test database.

    `mock_db` only replaces `src.platform.database.database.db`; the
    repositories bound their own `db` name at import time, so without this
    they would keep writing to the real one.
    """
    with patch('src.features.generation.file_repository.db', mock_db), \
         patch('src.features.generation.repository.db', mock_db):
        yield mock_db


@pytest.fixture
def generation_id(repos_on_test_db):
    """A real generation row (and its owning user) to hang files off."""
    mock_db = repos_on_test_db
    user_id = generate_ulid()
    gen_id = generate_ulid()

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, 'audio_tester', 'audio@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/audio/test', '1.0.0', '{}', user_id, 'running', 0.0),
        )

    return gen_id, user_id


class TestAudioCanHandle:
    def test_can_handle_audio_output(self, generation_id, settings, minimal_wav_file):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        assert handler.can_handle(AudioGenerationOutput(audio_path=minimal_wav_file)) is True

    def test_can_handle_non_audio_output(self, generation_id, settings):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        assert handler.can_handle(ProgressGenerationOutput(state="test")) is False


class TestAudioSavePath:
    """The handler writes a real audio file into storage and records it."""

    def test_final_audio_is_written_and_recorded(
        self, generation_id, settings, test_storage, minimal_wav_file, minimal_wav_bytes
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False, track_type="vocal")
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        assert metadata['saved_path'], "handler reported no saved path"

        on_disk = test_storage / metadata['saved_path']
        assert on_disk.exists(), f"nothing written at {on_disk}"
        assert on_disk.suffix == '.wav'
        # The stored file is the emitted file, byte for byte - proves the
        # streamed disk-to-disk copy, not a truncated/garbled write.
        assert on_disk.read_bytes() == minimal_wav_bytes

        files = generation_repo.get_files(gen_id, is_final=True)
        assert len(files) == 1
        record = files[0]
        assert record.file_type == 'AUDIO'
        assert record.file_path == metadata['saved_path']
        assert record.file_size == len(minimal_wav_bytes)

    def test_duration_is_probed_and_persisted_when_not_set_by_the_pipe(
        self, generation_id, settings, minimal_wav_file, wav_duration_seconds
    ):
        """The field that matters most for a long Stable Audio 3 track."""
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False)
        handler.handle(output)

        record = generation_repo.get_files(gen_id, is_final=True)[0]
        assert record.duration_seconds == pytest.approx(wav_duration_seconds)

    def test_duration_set_by_the_pipe_is_not_overwritten(
        self, generation_id, settings, minimal_wav_file
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False, duration=123.0)
        handler.handle(output)

        record = generation_repo.get_files(gen_id, is_final=True)[0]
        assert record.duration_seconds == 123.0

    def test_temporary_audio_goes_to_tmp_without_a_record(
        self, generation_id, settings, test_storage, minimal_wav_file, minimal_wav_bytes
    ):
        """Fixes the dead preview link: the serializer's temporary branch
        always emits a /api/media/tmp/{filename} URL, so a temporary track
        must actually be copied into tmp/ - not skipped the way the original
        handler skipped every temporary save."""
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=True)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved = Path(metadata['saved_path'])
        assert saved.parts[0] == 'tmp'
        assert (test_storage / saved).read_bytes() == minimal_wav_bytes
        assert metadata['file_record'] is None
        assert generation_repo.get_files(gen_id) == []

    def test_two_final_audio_tracks_do_not_overwrite_each_other(
        self, generation_id, settings, test_storage, minimal_wav_file
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        first = handler.handle(AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False))
        second = handler.handle(AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False))

        assert first['saved_path'] != second['saved_path']
        assert (test_storage / first['saved_path']).exists()
        assert (test_storage / second['saved_path']).exists()

    def test_is_derived_flows_onto_the_record(
        self, generation_id, settings, minimal_wav_file
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False)
        output.derived = True
        handler.handle(output)

        record = generation_repo.get_files(gen_id, is_final=True)[0]
        assert record.is_derived is True

    @pytest.mark.parametrize("extension,expected_mime", [
        ('wav', 'audio/wav'),
        ('mp3', 'audio/mpeg'),
        ('ogg', 'audio/ogg'),
        ('flac', 'audio/flac'),
        ('m4a', 'audio/mp4'),
    ])
    def test_saved_extension_serves_with_the_right_mime_type(
        self, generation_id, settings, test_storage, tmp_path, extension, expected_mime
    ):
        """MIME is derived at serve time from the suffix (`MediaTypeResolver.
        get_media_type`), not from a `files.mime_type` column - `File(...)`
        deliberately does not set that column here (see `_create_file_record`):
        `FileRepository.create`'s INSERT never persists it for any file type,
        matching mesh/video. This drives the real serve path end to end
        instead of asserting on the column that never gets written."""
        from src.features.media.media_types import MediaTypeResolver

        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        source = tmp_path / f"source.{extension}"
        source.write_bytes(b"not really encoded audio, any bytes will do")

        output = AudioGenerationOutput(audio_path=source, temporary=False)
        metadata = handler.handle(output)

        saved = Path(metadata['saved_path'])
        assert saved.suffix == f'.{extension}'
        assert MediaTypeResolver().get_media_type(saved.suffix) == expected_mime

    def test_save_streams_disk_to_disk_rather_than_buffering_in_memory(
        self, generation_id, settings, minimal_wav_file
    ):
        """At up to 380s of 44.1kHz stereo (~67MB), reading the file into a
        `bytes` object to hand to `save_file` would double-buffer the payload.
        Pins that the handler goes through the streamed `save_file_from_path`
        and never calls `save_file` at all."""
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        with patch('src.platform.filesystem.file_store.FileStore.save_file_from_path') as mock_streamed, \
             patch('src.platform.filesystem.file_store.FileStore.save_file') as mock_buffered:
            mock_streamed.return_value = (str(minimal_wav_file), {
                'file_path': 'generations/x/0_mixed.wav',
                'file_type': 'AUDIO',
                'mime_type': 'audio/wav',
                'file_size': 100,
                'is_temporary': False,
            })

            output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False)
            handler.handle(output)

            mock_streamed.assert_called_once()
            assert mock_streamed.call_args.kwargs['source_path'] == str(minimal_wav_file)
            mock_buffered.assert_not_called()

    def test_no_audio_path_is_a_noop(self, generation_id, settings):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=None, temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        assert metadata['saved_path'] is None
        assert generation_repo.get_files(gen_id) == []

    def test_missing_source_file_is_a_reported_failure(
        self, generation_id, settings
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=Path("/nonexistent/audio.wav"), temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is False
        assert 'save_error' in metadata
        assert metadata['saved_path'] is None


class TestAudioTempSourceTracking:
    """The pipe's own temp file must be registered for cleanup, not orphaned."""

    def test_saved_source_is_registered_with_the_tracker(
        self, generation_id, settings
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            from tests.fixtures.audio_fixtures import build_minimal_wav
            f.write(build_minimal_wav())
            temp_source = f.name

        try:
            output = AudioGenerationOutput(audio_path=Path(temp_source), temporary=True)
            handler.handle(output)

            removed = temp_source_tracker.cleanup(gen_id)
            assert removed == 1
            assert not os.path.exists(temp_source)
        finally:
            if os.path.exists(temp_source):
                os.unlink(temp_source)


class TestAudioOutputTypeRegistration:
    def test_spec_is_registered(self):
        spec = output_type_registry.spec_for(AudioGenerationOutput(audio_path=Path('/tmp/x.wav')))

        assert spec is not None
        assert spec.key == 'audio'
        assert spec.resolve_message_type(AudioGenerationOutput(audio_path=Path('/tmp/x.wav'))) == 'workbench_update'
        assert spec.handler_cls is AudioGenerationOutputHandler
        assert spec.serializer is serialize_audio_output


class TestAudioThroughOutputProcessor:
    @pytest.mark.asyncio
    async def test_processor_routes_audio_to_its_handler(
        self, generation_id, settings, test_storage, minimal_wav_file, minimal_wav_bytes
    ):
        from src.features.generation.output_processor import OutputProcessor

        gen_id, user_id = generation_id
        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False)

        metadata = await OutputProcessor(settings).process_output(gen_id, output, user_id)

        assert metadata['handler'] == 'AudioGenerationOutputHandler'
        assert metadata['processed'] is True
        assert (test_storage / metadata['saved_path']).read_bytes() == minimal_wav_bytes

        files = generation_repo.get_files(gen_id, is_final=True)
        assert [f.file_type for f in files] == ['AUDIO']


class TestAudioSerializedEnvelope:
    """The exact payload shape the frontend receives - and the fixed preview link."""

    def test_final_audio_envelope_points_at_the_generation_route(
        self, generation_id, settings, minimal_wav_file
    ):
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=False, seed=4242)
        metadata = handler.handle(output)

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)

        filename = Path(metadata['saved_path']).name
        assert message['type'] == 'workbench_update'
        assert message['file_type'] == 'audio'
        assert message['temporary'] is False
        assert message['path'] == f'/api/media/generations/{gen_id}/{filename}'
        json.dumps(message)  # Must survive the WebSocket hop.

    def test_temporary_audio_envelope_points_at_a_real_tmp_file(
        self, generation_id, settings, test_storage, minimal_wav_file, minimal_wav_bytes
    ):
        """Regression guard for the dead-link bug: the URL the serializer
        emits for a temporary track must resolve to a file that actually
        exists in tmp/, not merely to some plausible-looking path."""
        gen_id, user_id = generation_id
        handler = AudioGenerationOutputHandler(gen_id, user_id, settings)

        output = AudioGenerationOutput(audio_path=minimal_wav_file, temporary=True)
        handler.handle(output)

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)

        assert message['temporary'] is True
        assert message['path'].startswith('/api/media/tmp/')
        served_filename = message['path'].rsplit('/', 1)[-1]
        assert (test_storage / 'tmp' / served_filename).read_bytes() == minimal_wav_bytes

    def test_unsaved_audio_carries_no_path(self):
        output = AudioGenerationOutput(audio_path=None)

        message = GenerationOutputSerializer(generation_id='gen1').serialize_output(output)

        assert message['file_type'] == 'audio'
        assert 'path' not in message
