"""
Mesh generation output handler for the application layer.

Processes MeshGenerationOutput: validates the emitted file against its
registered mesh format (`src.platform.filesystem.mesh_formats`), copies it
into storage through FileStore, and records a `files` row typed 'MESH' for
final meshes.

Modelled on the video handler - the payload is a file already on disk, so the
same streamed disk-to-disk save applies. It differs in two ways:

- The file is validated before it is stored. Every registered mesh format
  (`src.platform.filesystem.mesh_formats`) is a container with a checkable
  header, and storing arbitrary bytes under that extension would push the
  failure all the way out to a viewer in the browser. An extension that is
  not registered at all is rejected the same way - through `save_error` -
  rather than silently stored under a format nothing understands.
- No thumbnail is generated HERE - a generation is never held open for a
  render. The orchestrator schedules `render_and_store_mesh_thumbnail`
  (src/features/media_index/mesh_thumbnails.py, a pure-torch rasterizer, no
  GL) as a background task after completion; until it lands, the gallery
  renders a typed mesh card from `file_type` plus the geometry counts on the
  WebSocket payload.
"""

import logging
import os
from typing import Dict, Any, Optional, Tuple

from src.pipelines.outputs import MeshGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.records import File
from src.features.generation.repository import generation_repo
from src.features.generation.temp_source_tracker import temp_source_tracker
from src.platform.filesystem.mesh_formats import InvalidMeshError, mesh_format_registry

logger = logging.getLogger(__name__)

MESH_FILE_TYPE = 'MESH'


class MeshGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for MeshGenerationOutput - validates and stores registered mesh formats."""

    def can_handle(self, output) -> bool:
        """Check if this handler can process MeshGenerationOutput."""
        return isinstance(output, MeshGenerationOutput)

    def handle(self, output: MeshGenerationOutput) -> Dict[str, Any]:
        """
        Process MeshGenerationOutput - validate, then save to disk (tmp if temporary).

        Args:
            output: MeshGenerationOutput to process

        Returns:
            Dictionary with processing metadata including file path
        """
        metadata = {
            'handler': 'MeshGenerationOutputHandler',
            'processed': True,
            'temporary': output.temporary,
            'saved_path': None,
            'file_record': None
        }

        try:
            if not output.mesh_path:
                return metadata

            try:
                vertex_count, face_count = self._probe_source(output)
            except InvalidMeshError as e:
                # A malformed mesh (or an extension no format is registered
                # for) is a real failure, not a silent skip: the orchestrator
                # reads 'processed'/'save_error' to decide whether the
                # generation actually produced anything.
                logger.error(f"Refusing to store invalid mesh: {e}")
                metadata['processed'] = False
                metadata['save_error'] = f"Invalid mesh file: {e}"
                return metadata

            output.vertex_count = vertex_count
            output.face_count = face_count

            saved_path = self._save_mesh_file(output)
            if saved_path:
                metadata['saved_path'] = saved_path
                output._saved_path = saved_path  # Store path in output for serializer access

                if not output.temporary:
                    file_record = self._create_file_record(output, saved_path)
                    if file_record:
                        metadata['file_record'] = {
                            'id': file_record.id,
                            'filename': os.path.basename(file_record.file_path),
                            'file_path': file_record.file_path,
                            'file_size': file_record.file_size
                        }
            elif not output.temporary:
                logger.warning("Failed to save mesh")
                metadata['processed'] = False
                metadata['save_error'] = "Failed to save mesh"

        except Exception as e:
            logger.error(f"Error handling MeshGenerationOutput: {str(e)}")
            metadata['processed'] = False
            metadata['error'] = str(e)

        return metadata

    def _probe_source(self, output: MeshGenerationOutput) -> Tuple[Optional[int], Optional[int]]:
        """Validate the emitted file and resolve its geometry counts.

        Dispatches by the file's actual extension against
        `mesh_format_registry` rather than assuming glTF-binary, so an
        extension no format is registered for is refused here - the same
        `InvalidMeshError` path a malformed file within a known format takes.

        Counts a pipe set itself win - it knows what it generated, and the
        probe reads the authored primitives rather than the instantiated
        scene. Validation still runs either way.
        """
        extension = os.path.splitext(str(output.mesh_path))[1]
        mesh_format = mesh_format_registry.get(extension)
        if mesh_format is None:
            raise InvalidMeshError(
                f"no mesh format registered for extension {extension!r}: {output.mesh_path}"
            )

        probed_vertex_count, probed_face_count = mesh_format.probe(str(output.mesh_path))
        vertex_count = output.vertex_count if output.vertex_count is not None else probed_vertex_count
        face_count = output.face_count if output.face_count is not None else probed_face_count
        return vertex_count, face_count

    def _save_mesh_file(self, output: MeshGenerationOutput) -> Optional[str]:
        """Save the mesh using FileStore with consistent naming and structure."""
        try:
            from src.platform.filesystem.file_store import FileStore

            file_service = FileStore(self._resolve_storage_dir(), storage_driver=self._resolve_storage_driver())

            extension = os.path.splitext(str(output.mesh_path))[1].lstrip('.')

            if output.temporary:
                storage_type = 'tmp'
                is_temporary = True
                prefix = f"tmp_mesh_{self.generation_id}" if self.generation_id else "tmp_mesh"
            else:
                storage_type = 'generations'
                is_temporary = False
                self.image_counter += 1
                prefix = str(self.image_counter)

            full_path, file_metadata = file_service.save_file_from_path(
                generation_id=self.generation_id if not output.temporary else None,
                source_path=str(output.mesh_path),
                extension=extension,
                prefix=prefix,
                storage_type=storage_type,
                is_temporary=is_temporary
            )

            if full_path and file_metadata:
                # Same preview -> final re-read as video: the pipe's own
                # temporary source is read again later, so hand it to the
                # tracker rather than unlinking it here. Registered only after
                # a successful copy.
                temp_source_tracker.register(self.generation_id, str(output.mesh_path))
                logger.debug(
                    f"Mesh saved successfully to {'tmp' if output.temporary else 'generations'}: "
                    f"{file_metadata['file_path']}"
                )
                return file_metadata['file_path']  # Return relative path

            logger.error("Failed to save mesh file")
            return None

        except Exception as e:
            logger.error(f"Error saving mesh file: {str(e)}")
            return None

    def _create_file_record(self, output: MeshGenerationOutput, saved_path: str) -> Optional[File]:
        """Create database record for the saved mesh file."""
        try:
            file_size = self._resolve_storage_driver().size(saved_path) or 0

            # width/height/duration/fps stay None: none of them describe a
            # mesh, and vertex/face counts have no column of their own. No
            # mime_type either - FileRepository.create does not persist that
            # column for any file type, and the serve path derives the type
            # from the suffix.
            file_record = File(
                file_path=saved_path,
                file_type=MESH_FILE_TYPE,
                user_id=self.user_id,
                file_size=file_size,
                pipe_name=getattr(output, 'pipe_name', None),
                is_final=not output.temporary,
                is_derived=bool(getattr(output, 'derived', False)),
            )

            return generation_repo.add_file(self.generation_id, file_record)

        except Exception as e:
            logger.error(f"Error creating mesh file record: {str(e)}")
            return None


def mesh_api_path(output: MeshGenerationOutput, generation_id: str) -> Optional[str]:
    """The media-route URL a saved mesh is served from, or None if unsaved.

    Final meshes resolve through the generation route, which looks the file up
    by its `files` row rather than by joining the URL onto a directory;
    temporary ones resolve through the tmp route, whose resolver applies the
    shared containment check (`FilePathResolver.resolve_temp_file`).
    """
    saved_path = getattr(output, '_saved_path', None)
    if not saved_path:
        return None

    filename = os.path.basename(str(saved_path))
    if getattr(output, 'temporary', True):
        return f"/api/media/tmp/{filename}"
    return f"/api/media/generations/{generation_id}/{filename}"


def mesh_format_of(output: MeshGenerationOutput) -> Optional[str]:
    """The real extension (no dot, e.g. 'glb') of the mesh a `MeshGenerationOutput`
    carries - the saved copy if there is one, else the source path.

    Never a constant: a second registered format must show up here without
    touching this function.
    """
    source = getattr(output, '_saved_path', None) or getattr(output, 'mesh_path', None)
    if not source:
        return None
    extension = os.path.splitext(str(source))[1]
    return extension.lstrip('.').lower() or None


def serialize_mesh_output(output: MeshGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize MeshGenerationOutput for workbench_update messages."""
    result = {
        'file_type': 'mesh',  # What the frontend dispatches on
        'mesh_format': mesh_format_of(output),
        'temporary': getattr(output, 'temporary', True),
        'derived': bool(getattr(output, 'derived', False)),
        'seed': getattr(output, 'seed', None),
        'vertex_count': getattr(output, 'vertex_count', None),
        'face_count': getattr(output, 'face_count', None),
    }

    try:
        path = mesh_api_path(output, ctx.generation_id)
        if path:
            result['path'] = path
            result['mesh_name'] = os.path.basename(path)
    except Exception as e:
        logger.error(f"Failed to serialize mesh path: {str(e)}")

    return result


output_type_registry.register(OutputTypeSpec(
    output_cls=MeshGenerationOutput,
    key='mesh',
    message_type='workbench_update',
    serializer=serialize_mesh_output,
    handler_cls=MeshGenerationOutputHandler,
))
