"""Serving and path containment for generated `.glb` meshes.

These drive the real objects end to end - a real mesh written through
MeshGenerationOutputHandler/FileStore, then read back through a real
MediaStore over a real FilePathResolver. Only the settings manager and the
plugin registry are stubbed, because neither has anything to say about paths.

The containment cases are the point: they plant a file *outside* the storage
directory and check that the traversal is refused rather than that the file
happens to be missing. A rejection for the wrong reason would prove nothing.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.generation.file_repository import FileRepository
from src.features.generation.handlers.mesh_handler import MeshGenerationOutputHandler
from src.features.generation.repository import GenerationRepository
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.store import MediaStore
from src.features.media.media_types import MediaTypeResolver
from src.features.media.upload_repository import UploadRepository
from src.pipelines.outputs import MeshGenerationOutput
from src.platform.filesystem.file_store import FileStore
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

GLB_MEDIA_TYPE = 'model/gltf-binary'


class _NoopHookContext:
    data: dict = {}


class _NoopPluginRegistry:
    """A plugin registry that neither blocks nor rewrites anything."""

    def execute_hook(self, hook, initial_data=None):
        context = _NoopHookContext()
        context.data = dict(initial_data or {})
        return context, []


@pytest.fixture
def repos_on_test_db(mock_db):
    """Point the repositories at the test database (they resolve `db` at call time)."""
    with patch('src.platform.database.database.db', mock_db):
        yield mock_db


@pytest.fixture
def settings(test_storage):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(test_storage)
    return settings


@pytest.fixture
def media_store(settings, test_storage):
    """A MediaStore wired with real path resolution and real repositories."""
    return MediaStore(
        file_resolver=FilePathResolver(settings),
        image_processor=ImageProcessor(),
        media_type_resolver=MediaTypeResolver(),
        file_repository=FileRepository(),
        generation_repository=GenerationRepository(),
        settings=settings,
        file_service=FileStore(str(test_storage)),
        plugin_registry=_NoopPluginRegistry(),
        upload_repository=UploadRepository(),
    )


@pytest.fixture
def saved_mesh(repos_on_test_db, settings, minimal_glb_file):
    """A real .glb written through the real save path, with its generation row.

    Returns (generation_id, filename, bytes).
    """
    mock_db = repos_on_test_db
    user_id = generate_ulid()
    gen_id = generate_ulid()

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, 'mesh_server_tester', 'mesh-serve@example.com', 'hashed'),
        )
        cursor.execute(
            """
            INSERT INTO generations (id, preset_id, preset_version, form_data, user_id, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (gen_id, 'workbench/mesh/test', '1.0.0', '{}', user_id, 'completed', 1.0),
        )

    handler = MeshGenerationOutputHandler(gen_id, user_id, settings)
    metadata = handler.handle(MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=False))
    assert metadata['processed'] is True

    return gen_id, Path(metadata['saved_path']).name, minimal_glb_file.read_bytes()


class TestMeshServing:
    """A stored mesh comes back through the generation media route."""

    def test_final_mesh_is_served_with_the_gltf_media_type(self, media_store, saved_mesh):
        gen_id, filename, expected_bytes = saved_mesh

        result = media_store.get_generation_media(gen_id, filename)

        assert result.media_type == GLB_MEDIA_TYPE
        assert Path(result.file_path).read_bytes() == expected_bytes
        assert result.headers['Content-Length'] == str(len(expected_bytes))

    def test_temporary_mesh_is_served_from_the_tmp_route(
        self, repos_on_test_db, media_store, settings, minimal_glb_file
    ):
        handler = MeshGenerationOutputHandler(generate_ulid(), None, settings)
        metadata = handler.handle(MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=True))
        filename = Path(metadata['saved_path']).name

        result = media_store.get_temp_media(filename)

        assert result.media_type == GLB_MEDIA_TYPE
        assert Path(result.file_path).read_bytes() == minimal_glb_file.read_bytes()

    def test_thumbnail_request_is_not_available(self, media_store, saved_mesh):
        """No renderer runs at save time, so a mesh has no thumbnail to serve.

        Same answer a video gives before its async thumbnails land, and the
        reason the gallery has to render a typed mesh card rather than an
        <img>.
        """
        gen_id, filename, _bytes = saved_mesh

        with pytest.raises(ValueError) as excinfo:
            media_store.get_generation_media(gen_id, filename, size='small')

        assert 'thumbnail' in str(excinfo.value).lower()

    def test_unknown_mesh_filename_is_not_found(self, media_store, saved_mesh):
        gen_id, _filename, _bytes = saved_mesh

        with pytest.raises(ValueError):
            media_store.get_generation_media(gen_id, 'nope.glb')


class TestMeshPathContainment:
    """Traversal out of the storage directory is refused.

    Containment is not reimplemented for meshes: the tmp route resolves through
    `FilePathResolver.resolve_temp_file`, whose `validate_path_security` check
    is what these exercise, and the generation route never joins a URL onto a
    directory at all - it looks the file up by its `files` row.
    """

    @pytest.fixture
    def planted_secret(self, tmp_path, test_storage, minimal_glb_bytes):
        """A readable .glb sitting outside the storage directory.

        `test_storage` is `tmp_path/test_storage`, so this is exactly two
        levels up from `storage/tmp/` - reachable by traversal if, and only if,
        nothing checks.
        """
        secret = tmp_path / "secret.glb"
        secret.write_bytes(minimal_glb_bytes + b'SECRET')
        assert secret.exists() and secret.is_file()
        assert not str(secret).startswith(str(test_storage))
        return secret

    @pytest.mark.parametrize("attempt", [
        "../../secret.glb",
        "../../../secret.glb",
        "./../../secret.glb",
        "subdir/../../../secret.glb",
    ])
    def test_traversal_out_of_tmp_is_refused(self, media_store, planted_secret, attempt):
        with pytest.raises(ValueError) as excinfo:
            media_store.get_temp_media(attempt)

        # Refused for escaping, not for being absent - the file is right there.
        assert planted_secret.exists()
        assert "traversal" in str(excinfo.value).lower()

    def test_absolute_path_outside_storage_is_refused(self, media_store, planted_secret):
        with pytest.raises(ValueError):
            media_store.get_temp_media(str(planted_secret))

        assert planted_secret.exists()

    def test_traversal_never_returns_the_planted_bytes(self, media_store, planted_secret):
        """The escape is refused, not merely relabelled."""
        served = None
        try:
            served = media_store.get_temp_media("../../secret.glb")
        except ValueError:
            pass

        assert served is None, "traversal was served"

    def test_a_legitimate_tmp_mesh_still_resolves(
        self, repos_on_test_db, media_store, settings, minimal_glb_file
    ):
        """The containment check rejects escapes, not everything."""
        handler = MeshGenerationOutputHandler(generate_ulid(), None, settings)
        metadata = handler.handle(MeshGenerationOutput(mesh_path=minimal_glb_file, temporary=True))

        result = media_store.get_temp_media(Path(metadata['saved_path']).name)

        assert Path(result.file_path).exists()
