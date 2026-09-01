"""Tests for the mesh generation output handler.

Everything here drives the real save path: a real `.glb` (see
tests/fixtures/mesh_fixtures.py) goes through MeshGenerationOutputHandler into
a real FileStore over a real migrated schema. Nothing constructs the stored
file itself and asserts on it - that would only prove the test can write a
file.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.generation.handlers.mesh_handler import (
    MeshGenerationOutputHandler,
    serialize_mesh_output,
)
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import output_type_registry
from src.features.generation.repository import generation_repo
from src.pipelines.outputs import GalleryGenerationOutput, MeshGenerationOutput
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid
from tests.fixtures.mesh_fixtures import (
    TRIANGLE_FACE_COUNT,
    TRIANGLE_VERTEX_COUNT,
    build_minimal_glb,
)


@pytest.fixture
def settings(test_storage):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(test_storage)
    return settings


@pytest.fixture
def repos_on_test_db(mock_db):
    """Point the repositories at the test database.

    Repositories resolve `db` at call time from
    `src.platform.database.database`, which `mock_db` already redirects.
    """
    with patch('src.platform.database.database.db', mock_db):
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
            (user_id, 'mesh_tester', 'mesh@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/mesh/test', '1.0.0', '{}', user_id, 'running', 0.0),
        )

    return gen_id, user_id


class TestMeshSavePath:
    """The handler writes a real .glb into storage and records it."""

    def test_final_mesh_is_written_and_recorded(
        self, generation_id, settings, test_storage, minimal_glb_file, minimal_glb_bytes
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        assert metadata['saved_path'], "handler reported no saved path"

        on_disk = test_storage / metadata['saved_path']
        assert on_disk.exists(), f"nothing written at {on_disk}"
        assert on_disk.suffix == '.glb'
        # The stored file is the emitted file, byte for byte.
        assert on_disk.read_bytes() == minimal_glb_bytes

        # The record the gallery and the serving route both read from.
        files = generation_repo.get_files(gen_id, is_final=True)
        assert len(files) == 1
        record = files[0]
        assert record.file_type == 'MESH'
        assert record.file_path == metadata['saved_path']
        assert record.file_size == len(minimal_glb_bytes)
        # No renderer runs at save time, so there is no thumbnail to point at.
        assert record.thumbnail_small is None
        assert record.thumbnail_medium is None
        assert record.thumbnail_large is None

    def test_geometry_counts_come_from_the_file(
        self, generation_id, settings, minimal_glb_file
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False)
        handler.handle(output)

        assert output.vertex_count == TRIANGLE_VERTEX_COUNT
        assert output.face_count == TRIANGLE_FACE_COUNT

    def test_handle_never_renders_a_thumbnail_synchronously(
        self, generation_id, settings, minimal_glb_file
    ):
        """The generation must not wait on a mesh render (mesh_handler.py's
        stated constraint) - that happens later, off this call, at generation
        completion (`GenerationOrchestrator._schedule_mesh_thumbnails`)."""
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False)
        with patch(
            'src.platform.runtime.native.mesh_preview.render_mesh_preview'
        ) as mock_render:
            metadata = handler.handle(output)

        assert metadata['processed'] is True
        mock_render.assert_not_called()

    def test_counts_set_by_the_pipe_are_not_overwritten(
        self, generation_id, settings, minimal_glb_file
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(
            mesh_path=minimal_glb_file, temporary=False, vertex_count=99, face_count=33
        )
        handler.handle(output)

        assert output.vertex_count == 99
        assert output.face_count == 33

    def test_temporary_mesh_goes_to_tmp_without_a_record(
        self, generation_id, settings, test_storage, minimal_glb_file, minimal_glb_bytes
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=True)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        saved = Path(metadata['saved_path'])
        assert saved.parts[0] == 'tmp'
        assert (test_storage / saved).read_bytes() == minimal_glb_bytes
        assert metadata['file_record'] is None
        assert generation_repo.get_files(gen_id) == []

    def test_two_final_meshes_do_not_overwrite_each_other(
        self, generation_id, settings, test_storage, minimal_glb_file
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        first = handler.handle(MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False))
        second = handler.handle(MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False))

        assert first['saved_path'] != second['saved_path']
        assert (test_storage / first['saved_path']).exists()
        assert (test_storage / second['saved_path']).exists()


class TestMeshValidation:
    """A file that is not a .glb never reaches storage."""

    @pytest.mark.parametrize("payload,reason", [
        (b'not a mesh at all, just some bytes', "not a glb at all"),
        # Well-formed in every respect except the magic, so only the magic
        # check can reject it - otherwise this case passes on a later check
        # and the magic check itself is never pinned by anything.
        (b'BLOB' + build_minimal_glb()[4:], "bad magic"),
        (build_minimal_glb(version=1), "unsupported version"),
        (build_minimal_glb(declared_length=999999), "length mismatch"),
        (b'glTF' + b'\x02\x00\x00\x00' + b'\x0c\x00\x00\x00', "no chunks"),
    ])
    def test_invalid_mesh_is_rejected(
        self, generation_id, settings, test_storage, tmp_path, payload, reason
    ):
        gen_id, user_id = generation_id
        bad_path = tmp_path / f"bad_{abs(hash(reason))}.glb"
        bad_path.write_bytes(payload)

        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
        metadata = handler.handle(MeshGenerationOutput(mesh_path=bad_path, temporary=False))

        assert metadata['processed'] is False, f"invalid mesh accepted ({reason})"
        assert 'save_error' in metadata
        assert metadata['saved_path'] is None
        assert generation_repo.get_files(gen_id) == []
        # Nothing landed in the generations tree either.
        assert not list((test_storage / 'generations').rglob('*.glb'))

    def test_unregistered_extension_is_rejected(
        self, generation_id, settings, test_storage, tmp_path
    ):
        """An extension no mesh format is registered for never reaches storage.

        `.ply` is deliberately not registered (only `.glb` is, for now). The
        payload is a genuinely valid `.glb` byte-for-byte - this pins that the
        gate is the file's *extension* against the registry, not merely
        content that fails to parse; a dispatcher that always ran the glb
        probe regardless of extension would accept this file.
        """
        gen_id, user_id = generation_id
        bad_path = tmp_path / "model.ply"
        bad_path.write_bytes(build_minimal_glb())

        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
        metadata = handler.handle(MeshGenerationOutput(mesh_path=bad_path, temporary=False))

        assert metadata['processed'] is False
        assert 'save_error' in metadata
        assert metadata['saved_path'] is None
        assert generation_repo.get_files(gen_id) == []
        assert not list((test_storage / 'generations').rglob('*.ply'))

    def test_json_chunk_that_is_not_json_is_rejected(
        self, generation_id, settings, tmp_path
    ):
        import struct

        gen_id, user_id = generation_id
        garbage = b'{{{{not json'
        garbage += b' ' * ((4 - len(garbage) % 4) % 4)
        body = struct.pack('<II', len(garbage), 0x4E4F534A) + garbage
        payload = b'glTF' + struct.pack('<II', 2, 12 + len(body)) + body

        bad_path = tmp_path / "bad_json.glb"
        bad_path.write_bytes(payload)

        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
        metadata = handler.handle(MeshGenerationOutput(mesh_path=bad_path, temporary=False))

        assert metadata['processed'] is False
        assert generation_repo.get_files(gen_id) == []


class TestMeshFormatReflectsRealFilename:
    """`mesh_format` in the envelope is the file's real extension.

    `.ply` is registered here only for the duration of the test - it proves
    `mesh_format` is derived per-file rather than always reporting 'glb',
    without needing a second production probe to exist yet.
    """

    @pytest.fixture
    def fake_ply_format(self):
        from src.platform.filesystem.mesh_formats import MeshFormat, mesh_format_registry

        mesh_format_registry.register(
            MeshFormat(extension='.ply', mime_type='application/x-ply', probe=lambda path: (5, 2))
        )
        try:
            yield
        finally:
            del mesh_format_registry._by_extension['.ply']

    def test_saved_ply_reports_ply_not_glb(
        self, fake_ply_format, generation_id, settings, test_storage, tmp_path
    ):
        gen_id, user_id = generation_id
        ply_path = tmp_path / "source.ply"
        ply_path.write_bytes(b"pretend ply bytes")

        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
        output = MeshGenerationOutput(mesh_path=ply_path, temporary=False)
        metadata = handler.handle(output)

        assert metadata['processed'] is True
        assert Path(metadata['saved_path']).suffix == '.ply'

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)
        assert message['mesh_format'] == 'ply'

    def test_glb_still_reports_glb(
        self, fake_ply_format, generation_id, settings, minimal_glb_file
    ):
        """Registering a second format must not disturb the first one's label."""
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False)
        handler.handle(output)

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)
        assert message['mesh_format'] == 'glb'

    def test_gallery_mesh_format_reflects_real_filename(
        self, fake_ply_format, generation_id, settings, test_storage, tmp_path
    ):
        from src.features.generation.handlers.gallery_handler import (
            GalleryGenerationOutputHandler,
            serialize_gallery_output,
        )
        from src.features.generation.output_types import SerializeContext

        gen_id, user_id = generation_id
        ply_path = tmp_path / "source.ply"
        ply_path.write_bytes(b"pretend ply bytes")

        gallery = GalleryGenerationOutput(
            images=[],
            meshes=[MeshGenerationOutput(mesh_path=ply_path, temporary=False)],
        )
        GalleryGenerationOutputHandler(gen_id, user_id, settings).handle(gallery)

        payload = serialize_gallery_output(gallery, SerializeContext(generation_id=gen_id))
        assert payload['meshes'][0]['mesh_format'] == 'ply'


class TestMeshOutputTypeRegistration:
    """The registry entry the WebSocket layer resolves through."""

    def test_spec_is_registered(self):
        spec = output_type_registry.spec_for(MeshGenerationOutput(mesh_path=Path('/tmp/x.glb')))

        assert spec is not None
        assert spec.key == 'mesh'
        assert spec.resolve_message_type(MeshGenerationOutput(mesh_path=Path('/tmp/x.glb'))) == 'workbench_update'
        assert spec.handler_cls is MeshGenerationOutputHandler
        assert spec.serializer is serialize_mesh_output


class TestMeshThroughOutputProcessor:
    """The dispatch path production actually uses, not the handler directly."""

    @pytest.mark.asyncio
    async def test_processor_routes_a_mesh_to_its_handler(
        self, generation_id, settings, test_storage, minimal_glb_file, minimal_glb_bytes
    ):
        from src.features.generation.output_processor import OutputProcessor

        gen_id, user_id = generation_id
        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False)

        metadata = await OutputProcessor(settings).process_output(gen_id, output, user_id)

        assert metadata['handler'] == 'MeshGenerationOutputHandler'
        assert metadata['processed'] is True
        assert (test_storage / metadata['saved_path']).read_bytes() == minimal_glb_bytes

        files = generation_repo.get_files(gen_id, is_final=True)
        assert [f.file_type for f in files] == ['MESH']


class TestMeshSerializedEnvelope:
    """The exact payload shape the frontend receives."""

    def test_final_mesh_envelope(
        self, generation_id, settings, minimal_glb_file
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False, seed=4242)
        output.pipe_id = 3
        output.pipe_name = 'generator_mesh'
        metadata = handler.handle(output)

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)

        filename = Path(metadata['saved_path']).name
        assert message == {
            'type': 'workbench_update',
            'generation_id': gen_id,
            'pipe_id': 3,
            'pipe_name': 'generator_mesh',
            'output_type': 'mesh',
            'file_type': 'mesh',
            'mesh_format': 'glb',
            'temporary': False,
            'derived': False,
            'seed': 4242,
            'vertex_count': TRIANGLE_VERTEX_COUNT,
            'face_count': TRIANGLE_FACE_COUNT,
            'path': f'/api/media/generations/{gen_id}/{filename}',
            'mesh_name': filename,
        }
        # Must survive the WebSocket hop.
        json.dumps(message)

    def test_temporary_mesh_envelope_points_at_the_tmp_route(
        self, generation_id, settings, minimal_glb_file
    ):
        gen_id, user_id = generation_id
        handler = MeshGenerationOutputHandler(gen_id, user_id, settings)

        output = MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=True)
        metadata = handler.handle(output)

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(output)

        filename = Path(metadata['saved_path']).name
        assert message['temporary'] is True
        assert message['path'] == f'/api/media/tmp/{filename}'
        assert message['mesh_name'] == filename

    def test_unsaved_mesh_carries_no_path(self):
        output = MeshGenerationOutput(mesh_path=Path('/tmp/never_saved.glb'))

        message = GenerationOutputSerializer(generation_id='gen1').serialize_output(output)

        assert message['file_type'] == 'mesh'
        assert 'path' not in message


class TestMeshInGallery:
    """A mesh reaches the gallery through GalleryGenerationOutput."""

    def test_gallery_saves_meshes_and_serializes_urls(
        self, generation_id, settings, test_storage, minimal_glb_file
    ):
        from src.features.generation.handlers.gallery_handler import (
            GalleryGenerationOutputHandler,
            serialize_gallery_output,
        )
        from src.features.generation.output_types import SerializeContext

        gen_id, user_id = generation_id
        gallery = GalleryGenerationOutput(
            images=[],
            meshes=[
                MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False, seed=7),
                MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False, derived=True),
            ],
        )

        metadata = GalleryGenerationOutputHandler(gen_id, user_id, settings).handle(gallery)

        assert metadata['processed'] is True
        assert metadata['mesh_count'] == 2
        assert len(metadata['processed_meshes']) == 2

        files = generation_repo.get_files(gen_id, is_final=True)
        assert [f.file_type for f in files] == ['MESH', 'MESH']
        assert len({f.file_path for f in files}) == 2
        for record in files:
            assert (test_storage / record.file_path).exists()

        payload = serialize_gallery_output(gallery, SerializeContext(generation_id=gen_id))
        assert len(payload['meshes']) == 2
        assert len(payload['mesh_urls_list']) == 2
        assert all(m['file_type'] == 'mesh' for m in payload['meshes'])
        assert all(
            m['path'].startswith(f'/api/media/generations/{gen_id}/')
            for m in payload['mesh_urls_list']
        )
        assert payload['meshes'][0]['seed'] == 7
        assert payload['meshes'][1]['derived'] is True

    @pytest.mark.asyncio
    async def test_plugin_shaped_terminal_pipe_persists_a_mesh(
        self, generation_id, settings, test_storage, minimal_glb_file, minimal_glb_bytes
    ):
        """The whole path a plugin's terminal pipe actually takes.

        The core `gallery` pipe declares only IMAGE/VIDEO inputs, so a mesh
        pipeline builds its own GalleryGenerationOutput and emits it. That
        makes this - not the `gallery` pipe - the only thing that reaches the
        mesh branches of the gallery handler and serializer in production, so
        it is imported through `src.plugin_api` (the sole surface a plugin may
        use) and dispatched through OutputProcessor rather than by calling the
        handler directly.
        """
        from src.features.generation.output_processor import OutputProcessor
        from src.plugin_api.pipes import (
            GalleryGenerationOutput as PluginGallery,
            MeshGenerationOutput as PluginMesh,
        )

        gen_id, user_id = generation_id
        emitted = []

        def generation_outputs(output):
            emitted.append(output)

        # Exactly the shape documented in docs/plugin-api.md.
        generation_outputs(PluginGallery(
            images=[],
            meshes=[PluginMesh(mesh_path=minimal_glb_file, temporary=False, seed=99)],
        ))

        processor = OutputProcessor(settings)
        for output in emitted:
            metadata = await processor.process_output(gen_id, output, user_id)

        assert metadata['handler'] == 'GalleryGenerationOutputHandler'
        assert metadata['processed'] is True
        assert metadata['mesh_count'] == 1

        files = generation_repo.get_files(gen_id, is_final=True)
        assert [f.file_type for f in files] == ['MESH']
        assert (test_storage / files[0].file_path).read_bytes() == minimal_glb_bytes

        message = GenerationOutputSerializer(generation_id=gen_id).serialize_output(emitted[0])
        assert message['type'] == 'gallery_update'
        assert len(message['mesh_urls_list']) == 1
        assert message['mesh_urls_list'][0]['path'] == (
            f"/api/media/generations/{gen_id}/{Path(files[0].file_path).name}"
        )
        assert message['mesh_urls_list'][0]['seed'] == 99

    def test_gallery_without_meshes_is_unchanged(self):
        from src.features.generation.handlers.gallery_handler import serialize_gallery_output
        from src.features.generation.output_types import SerializeContext

        payload = serialize_gallery_output(
            GalleryGenerationOutput(images=[]), SerializeContext(generation_id='gen1')
        )

        assert payload['meshes'] == []
        assert payload['mesh_urls_list'] == []
