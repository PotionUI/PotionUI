"""Write side of generation history.

Everything that changes a generation or touches the filesystem: ratings and
favorites, deletion (single, bulk, by-tag), tag edits, uploads and zip export,
plus the file-IO helpers those need. Reads and ownership checks are borrowed
from GenerationHistoryQuery so there is one implementation of each.
"""

import io
import json
import logging
import os
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from PIL import Image

from src.features.media.media_types import MediaTypeResolver, sniff_media_extension
from src.features.generation.exceptions import (
    GenerationDeleteFailedException,
    UploadFailedException,
    InvalidTagException,
    GenerationBundleImportError,
)
from src.features.generation.handlers import generate_thumbnails
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

# Bumped only on a breaking change to the exported envelope. `import_bundle`
# refuses anything it doesn't recognise rather than guessing (mirrors the
# automation module's export/import envelope contract).
GENERATION_BUNDLE_SCHEMA = "potionui.generation"
GENERATION_BUNDLE_SCHEMA_VERSION = 1

# Bounds on an untrusted uploaded bundle. Only `generation.json` is ever
# read - every other zip entry (the reference output files) is ignored, so
# these bound the manifest and the directory listing, not a full extraction.
_MAX_BUNDLE_UPLOAD_BYTES = 200 * 1024 * 1024
_MAX_BUNDLE_ZIP_ENTRIES = 2000
_MAX_BUNDLE_MANIFEST_BYTES = 5 * 1024 * 1024


class GenerationHistoryArchive:
    """Write-side of the generation history: mutations, uploads and file IO."""

    def __init__(
        self,
        generation_repo: GenerationRepository,
        file_service,
        plugin_registry: PluginRegistry,
        query: GenerationHistoryQuery,
    ):
        """Initialize GenerationHistoryArchive.

        Args:
            generation_repo: Repository for generation data access
            file_service: Service for file operations (FileStore)
            plugin_registry: Plugin registry for hook execution
            query: Read-side, reused for ownership checks and tag validation
        """
        self.generation_repo = generation_repo
        self.file_service = file_service
        self.plugins = plugin_registry
        self._query = query

    def _delete_generation_files(self, generation_id: str, user_id: str) -> Tuple[int, int]:
        """Delete generation files through `self.file_service`.

        `delete_generation_outputs` has no directory to scan - it deletes
        exactly the keys it's given, so every file plus its thumbnails (not
        tracked as their own `files` rows) has to be enumerated here.

        Args:
            generation_id: The generation ID
            user_id: The user ID for ownership verification

        Returns:
            Tuple of (files_deleted_fs, files_failed_fs)
        """
        db_files = self.generation_repo.get_files(generation_id, user_id=user_id)

        relative_paths = []
        for file_record in db_files:
            relative_paths.append(file_record.file_path)
            base_key = Path(file_record.file_path).parent
            for thumbnail in (file_record.thumbnail_small, file_record.thumbnail_medium, file_record.thumbnail_large):
                if thumbnail:
                    relative_paths.append((base_key / thumbnail).as_posix())

        return self.file_service.delete_generation_outputs(relative_paths)

    @staticmethod
    def _is_known_extension(media_resolver: MediaTypeResolver, file_ext: str) -> bool:
        """Whether any registry claims `file_ext` - unknown means "sniff it"."""
        if not file_ext:
            return False
        return (
            media_resolver.is_image(file_ext)
            or media_resolver.is_video(file_ext)
            or media_resolver.is_audio(file_ext)
            or media_resolver.is_mesh(file_ext)
        )

    def _process_uploaded_file(
        self,
        upload_file,
        content: bytes,
        generation_dir_relative: str,
        idx: int,
        user_id: str
    ) -> Optional[File]:
        """Process a single uploaded file - written through
        `self.file_service.storage_driver`, never straight to local disk.

        Args:
            upload_file: The uploaded file object
            content: File content bytes
            generation_dir_relative: The key-space directory this generation's
                files live under, e.g. `generations/<date>/<id>`
            idx: File index
            user_id: The user ID

        Returns:
            File record if successful, None otherwise
        """
        # Determine file extension. A name that carries no extension, or one no
        # registry recognises, is decided by the CONTENT: the old '.png'
        # fallback typed every unnamed video, audio track and mesh as an image,
        # and the wrong file_type follows the row forever (wrong player, wrong
        # thumbnailer, wrong serve MIME). '.png' remains the last resort for
        # bytes nothing recognises.
        media_resolver = MediaTypeResolver()
        file_ext = Path(upload_file.filename).suffix if upload_file.filename else ''
        if not self._is_known_extension(media_resolver, file_ext):
            file_ext = sniff_media_extension(content) or file_ext or '.png'

        # Save original file
        file_name = f"{idx}{file_ext}"
        file_relative_path = f"{generation_dir_relative}/{file_name}"
        storage_driver = self.file_service.storage_driver
        storage_driver.put_bytes(file_relative_path, content)

        is_video = media_resolver.is_video(file_ext)
        is_mesh = media_resolver.is_mesh(file_ext)
        is_audio = media_resolver.is_audio(file_ext)
        if is_mesh:
            file_type = 'MESH'
        elif is_audio:
            file_type = 'AUDIO'
        elif is_video:
            file_type = 'VIDEO'
        else:
            file_type = 'IMAGE'
        mime_type = media_resolver.get_media_type(file_ext)

        # Get dimensions and generate thumbnails (images only)
        width, height = None, None
        thumbnail_paths = {}

        if not is_video and not is_mesh and not is_audio:
            try:
                with Image.open(io.BytesIO(content)) as img:
                    width, height = img.size
                    thumbnail_paths = generate_thumbnails(img, storage_driver, generation_dir_relative, idx)
            except Exception as e:
                logger.error(f"Failed to process image {upload_file.filename}: {str(e)}")

        # Create File record
        return File(
            file_path=file_relative_path,
            file_type=file_type,
            mime_type=mime_type,
            user_id=user_id,
            file_size=len(content),
            is_final=True,
            pipe_name=None,
            thumbnail_small=thumbnail_paths.get('small'),
            thumbnail_medium=thumbnail_paths.get('medium'),
            thumbnail_large=thumbnail_paths.get('large'),
            width=width,
            height=height
        )

    def set_rating(self, generation_id: str, rating: int, user_id: str) -> int:
        """Set the star rating (0-5) for a generation.

        Raises:
            GenerationNotFoundException: If generation not found / not owned
            ValueError: If rating is out of range
        """
        if rating < 0 or rating > 5:
            raise ValueError("Rating must be between 0 and 5")
        # Verify ownership
        self._query._get_generation_or_raise(generation_id, user_id)
        self.generation_repo.update_rating(generation_id, rating, user_id=user_id)
        return rating

    def set_favorite(self, generation_id: str, is_favorite: bool, user_id: str) -> bool:
        """Toggle the favorite flag for a generation.

        Raises:
            GenerationNotFoundException: If generation not found / not owned
        """
        # Verify ownership
        self._query._get_generation_or_raise(generation_id, user_id)
        self.generation_repo.set_favorite(generation_id, is_favorite, user_id=user_id)
        return is_favorite

    def delete(self, generation_id: str, user_id: str) -> Dict[str, Any]:
        """Delete a generation and its files.

        Executes hooks:
        - generation.before_delete: Can block deletion
        - generation.after_delete: Notification of successful deletion

        Args:
            generation_id: The generation ID
            user_id: The user ID for ownership verification

        Returns:
            Dict with deletion stats (files_deleted_fs, files_deleted_db)

        Raises:
            GenerationNotFoundException: If generation not found
            GenerationDeleteFailedException: If deletion fails or is blocked
        """
        # Verify ownership
        generation = self._query._get_generation_or_raise(generation_id, user_id, include_files=True)

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            GENERATION_HOOKS.before_delete,
            {
                "generation_id": generation_id,
                "user_id": user_id,
                "preset_id": generation.preset_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Deletion blocked by plugin")
            logger.warning(f"Generation deletion blocked: {reason}")
            raise GenerationDeleteFailedException(reason)

        # Get file count from database
        db_files = self.generation_repo.get_files(generation_id, user_id=user_id)
        files_deleted_db = len(db_files)

        # Delete files from filesystem
        files_deleted_fs, files_failed_fs = self._delete_generation_files(generation_id, user_id)

        # Delete generation from database (cascades to delete generation_files associations)
        success = self.generation_repo.delete(generation_id)

        if not success:
            raise GenerationDeleteFailedException("Failed to delete generation from database")

        # Execute after_delete hook
        execute_hook(self.plugins,
            GENERATION_HOOKS.after_delete,
            {
                "generation_id": generation_id,
                "user_id": user_id,
                "files_deleted_fs": files_deleted_fs,
                "files_deleted_db": files_deleted_db
            }
        )

        logger.info(f"Generation deleted: {generation_id}")

        return {
            "files_deleted_fs": files_deleted_fs,
            "files_deleted_db": files_deleted_db,
            "files_failed_fs": files_failed_fs
        }

    def bulk_delete_by_tags(self, tag_ids: List[str], user_id: str) -> Dict[str, Any]:
        """Delete all generations that have ALL specified tags.

        Composes get_generations_by_tags() with bulk_delete().

        Args:
            tag_ids: List of tag IDs (AND logic)
            user_id: The user ID for ownership verification

        Returns:
            Dict with deletion stats
        """
        if not tag_ids:
            return {
                "deleted_count": 0,
                "failed_count": 0,
                "failed_ids": [],
                "total_files_deleted": 0
            }

        self._query._validate_tag_ids(tag_ids, user_id)

        from src.features.tags.repository import tag_repo
        generation_ids = tag_repo.get_generations_by_tags(tag_ids, user_id)

        if not generation_ids:
            return {
                "deleted_count": 0,
                "failed_count": 0,
                "failed_ids": [],
                "total_files_deleted": 0
            }

        return self.bulk_delete(generation_ids, user_id)

    def bulk_delete(self, generation_ids: List[str], user_id: str) -> Dict[str, Any]:
        """Delete multiple generations and their files.

        Executes hooks:
        - generation.before_bulk_delete: Can block entire operation
        - generation.after_bulk_delete: Notification of completion

        Args:
            generation_ids: List of generation IDs to delete
            user_id: The user ID for ownership verification

        Returns:
            Dict with deletion stats

        Raises:
            GenerationDeleteFailedException: If bulk delete is blocked
        """
        if not generation_ids:
            return {
                "deleted_count": 0,
                "failed_count": 0,
                "failed_ids": [],
                "total_files_deleted": 0
            }

        # Execute before_bulk_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            GENERATION_HOOKS.before_bulk_delete,
            {
                "generation_ids": generation_ids,
                "user_id": user_id,
                "count": len(generation_ids)
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Bulk deletion blocked by plugin")
            logger.warning(f"Bulk generation deletion blocked: {reason}")
            raise GenerationDeleteFailedException(reason)

        deleted_count = 0
        failed_count = 0
        total_files_deleted_db = 0
        total_files_deleted_fs = 0
        total_files_failed_fs = 0
        failed_ids = []

        for generation_id in generation_ids:
            try:
                # Get generation to check ownership and get files
                generation = self.generation_repo.get_by_id(
                    generation_id, user_id=user_id, include_files=True
                )

                if not generation:
                    logger.warning(f"Generation '{generation_id}' not found or not owned by user")
                    failed_count += 1
                    failed_ids.append(generation_id)
                    continue

                # Get file records from database
                db_files = self.generation_repo.get_files(generation_id, user_id=user_id)
                total_files_deleted_db += len(db_files)

                # Delete files from filesystem
                files_deleted_fs, files_failed_fs = self._delete_generation_files(generation_id, user_id)
                total_files_deleted_fs += files_deleted_fs
                total_files_failed_fs += files_failed_fs

                # Delete generation from database
                success = self.generation_repo.delete(generation_id)

                if success:
                    deleted_count += 1
                else:
                    failed_count += 1
                    failed_ids.append(generation_id)

            except Exception as e:
                logger.error(f"Failed to delete generation {generation_id}: {str(e)}")
                failed_count += 1
                failed_ids.append(generation_id)

        # Execute after_bulk_delete hook
        execute_hook(self.plugins,
            GENERATION_HOOKS.after_bulk_delete,
            {
                "generation_ids": generation_ids,
                "user_id": user_id,
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total_files_deleted": total_files_deleted_fs
            }
        )

        logger.info(f"Bulk delete completed: {deleted_count} deleted, {failed_count} failed")

        return {
            "deleted_count": deleted_count,
            "failed_count": failed_count,
            "failed_ids": failed_ids,
            "total_files_deleted": total_files_deleted_fs,
            "total_files_deleted_db": total_files_deleted_db,
            "total_files_failed_fs": total_files_failed_fs
        }

    def _strip_image_metadata(self, full_path: str) -> Optional[bytes]:
        """Re-encode an image without EXIF / embedded workflow metadata.

        Reads the image, copies only the pixel data into a fresh image (which
        drops EXIF, PNG text chunks, ICC profiles, etc.) and re-saves it in the
        original format.

        Args:
            full_path: Absolute path to the image file

        Returns:
            Clean image bytes, or None if the image could not be processed.
        """
        try:
            with Image.open(full_path) as img:
                fmt = img.format  # capture before any conversion
                clean = Image.new(img.mode, img.size)
                clean.putdata(list(img.getdata()))
                if img.mode == 'P' and img.palette is not None:
                    # Preserve palette for palettized images
                    clean.putpalette(img.palette)

                buf = io.BytesIO()
                clean.save(buf, format=fmt)
                return buf.getvalue()
        except Exception as e:
            logger.warning(f"Failed to strip metadata from {full_path}: {str(e)}")
            return None

    def export_zip(
        self,
        generation_ids: List[str],
        user_id: str,
        strip_metadata: bool = False
    ) -> Tuple[bytes, str]:
        """Export the final image/video files of multiple generations as a zip.

        For each generation the ownership is verified and its final files are
        collected. Entries are named ``{generation_id}/{filename}`` to avoid
        collisions across generations. Missing files on disk are skipped with a
        warning. When ``strip_metadata`` is True, IMAGE files are re-encoded
        without EXIF / embedded workflow metadata; VIDEO files are always copied
        as-is.

        Args:
            generation_ids: Generation IDs to export
            user_id: The user ID for ownership verification
            strip_metadata: Whether to strip metadata from images

        Returns:
            Tuple of (zip_bytes, suggested_filename)

        Raises:
            GenerationNotFoundException: If any generation is not found / not owned
        """
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for generation_id in generation_ids:
                # Verify ownership (raises GenerationNotFoundException if missing)
                self._query._get_generation_or_raise(generation_id, user_id)

                files = self.generation_repo.get_files(
                    generation_id, user_id=user_id, is_final=True
                )

                for file_record in files:
                    if not self.file_service.generation_exists(file_record.file_path):
                        logger.warning(
                            f"Skipping missing file for export: {file_record.file_path} "
                            f"(generation {generation_id})"
                        )
                        continue

                    filename = os.path.basename(file_record.file_path)
                    arcname = f"{generation_id}/{filename}"
                    suffix = os.path.splitext(file_record.file_path)[1]

                    with self.file_service.local_copy_of(file_record.file_path, suffix) as local_path:
                        if strip_metadata and file_record.file_type == 'IMAGE':
                            clean_bytes = self._strip_image_metadata(str(local_path))
                            if clean_bytes is not None:
                                zf.writestr(arcname, clean_bytes)
                            else:
                                # Fall back to raw bytes if re-encoding failed
                                zf.write(local_path, arcname)
                        else:
                            # Videos and non-stripped images: copy bytes as-is
                            zf.write(local_path, arcname)

        return zip_buffer.getvalue(), "potionui-export.zip"

    def _build_bundle_envelope(self, generation: Generation, user_id: str) -> Dict[str, Any]:
        """Portable envelope for one generation - schema/kind/schema_version, a
        `generation` payload another instance can reuse to reproduce the run, the
        models it used, and a listing of its final output files.

        The submitted `form_data.seed` may be `-1` (randomize) - the concrete roll
        only exists as a per-output `GenerationParameter` row (`parameter_name ==
        "seed"`, saved by `ParamGenerationOutputHandler`). `form_data.seed` is
        overwritten here with the first output's resolved seed so a re-run of the
        exported form_data reproduces that output; every output's resolved
        parameters (including any per-output seed divergence in a batch) still
        ride along in `generation.parameters`.
        """
        from src.features.generation.parameter_repository import generation_parameter_repo
        from src.features.generation.model_repository import generation_model_repo
        from src.features.generation.segment_repository import generation_segment_repo

        param_rows = generation_parameter_repo.get_by_generation(generation.id)
        params_by_name: Dict[str, List[Any]] = {}
        for row in param_rows:
            # Rows arrive ordered by (parameter_name, parameter_index) - see
            # GenerationParameterRepository.get_by_generation - so appending
            # preserves index order per name without re-sorting on parameter_index.
            params_by_name.setdefault(row.parameter_name, []).append(row.to_dict()['parameter_value'])

        final_files = self.generation_repo.get_files(generation.id, user_id=user_id, is_final=True)
        output_count = len(final_files) or max((len(v) for v in params_by_name.values()), default=0)

        parameters = [
            {name: values[idx] for name, values in params_by_name.items() if idx < len(values)}
            for idx in range(output_count)
        ]

        form_data = deepcopy(generation.form_data) if isinstance(generation.form_data, dict) else {}
        if parameters and 'seed' in parameters[0]:
            form_data['seed'] = parameters[0]['seed']

        models = generation_model_repo.get_by_generation(generation.id)
        models_payload = [
            {
                'model_type': model.model_type,
                'filename': model.filename,
                'name': model.display_name,
                'sha256': model.sha256,
                'triggers': (model.model_metadata or {}).get('triggers', []),
            }
            for model in models
        ]

        segments = generation_segment_repo.get_by_generation(generation.id)
        segments_payload = [segment.to_dict() for segment in segments] or None

        preset_name = None
        if generation.preset_id:
            preset_name = self._query._preset_name_map().get(generation.preset_id, generation.preset_id)

        outputs_payload = [
            {
                'filename': os.path.basename(f.file_path),
                'file_type': f.file_type,
                'width': f.width,
                'height': f.height,
                'duration_seconds': f.duration_seconds,
                'fps': f.fps,
            }
            for f in final_files
        ]

        return {
            "schema": GENERATION_BUNDLE_SCHEMA,
            "schema_version": GENERATION_BUNDLE_SCHEMA_VERSION,
            "kind": "generation",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "generation": {
                "preset_id": generation.preset_id,
                "preset_version": generation.preset_version,
                "preset_name": preset_name,
                "mode": generation.mode,
                "form_name": generation.form_name,
                "form_data": form_data,
                "prompt_state": generation.prompt_state,
                "parameters": parameters,
                "segments": segments_payload,
            },
            "models": models_payload,
            "outputs": outputs_payload,
        }

    def export_bundle(self, generation_id: str, user_id: str) -> Tuple[bytes, str]:
        """Export one generation as a portable bundle: `generation.json` (the
        envelope another PotionUI instance imports to reproduce the output) plus
        the generation's final output files under `outputs/`, for reference.

        Args:
            generation_id: The generation ID
            user_id: The user ID for ownership verification

        Returns:
            Tuple of (zip_bytes, suggested_filename)

        Raises:
            GenerationNotFoundException: If the generation is not found / not owned
        """
        generation = self._query._get_generation_or_raise(generation_id, user_id, include_files=True)
        envelope = self._build_bundle_envelope(generation, user_id)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("generation.json", json.dumps(envelope, indent=2))

            final_files = self.generation_repo.get_files(generation_id, user_id=user_id, is_final=True)
            for file_record in final_files:
                if not self.file_service.generation_exists(file_record.file_path):
                    logger.warning(
                        f"Skipping missing file for bundle export: {file_record.file_path} "
                        f"(generation {generation_id})"
                    )
                    continue
                filename = os.path.basename(file_record.file_path)
                suffix = os.path.splitext(file_record.file_path)[1]
                with self.file_service.local_copy_of(file_record.file_path, suffix) as local_path:
                    zf.write(local_path, f"outputs/{filename}")

        return zip_buffer.getvalue(), f"potionui-generation-{generation_id}.zip"

    def _parse_bundle_document(self, content: bytes) -> Any:
        """Decode an uploaded bundle into its JSON document.

        Accepts either a bare `generation.json` or a zip produced by
        `export_bundle`. For a zip, only the `generation.json` entry is ever
        read - every other entry (the reference output files) is ignored
        outright, and nothing is ever extracted to disk.
        """
        if content[:2] == b'PK':
            try:
                zf = zipfile.ZipFile(io.BytesIO(content))
            except zipfile.BadZipFile as exc:
                raise GenerationBundleImportError(
                    "Uploaded file is not a valid zip or JSON bundle"
                ) from exc

            infos = zf.infolist()
            if len(infos) > _MAX_BUNDLE_ZIP_ENTRIES:
                raise GenerationBundleImportError("Bundle contains too many entries")

            manifest_info = next((i for i in infos if i.filename == "generation.json"), None)
            if manifest_info is None:
                raise GenerationBundleImportError("Bundle is missing generation.json")
            if manifest_info.file_size > _MAX_BUNDLE_MANIFEST_BYTES:
                raise GenerationBundleImportError("generation.json in bundle is too large")

            raw = zf.read(manifest_info)
        else:
            raw = content

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenerationBundleImportError("generation.json is not valid JSON") from exc

    @staticmethod
    def _validate_bundle_envelope(document: Any) -> Dict[str, Any]:
        """Structural check for a candidate bundle document. Returns the
        `generation` payload on success; raises `GenerationBundleImportError`
        on the first violated check."""
        if not isinstance(document, dict):
            raise GenerationBundleImportError("Bundle document must be a JSON object")
        if document.get("schema") != GENERATION_BUNDLE_SCHEMA or document.get("kind") != "generation":
            raise GenerationBundleImportError("Not a PotionUI generation export")
        if document.get("schema_version") != GENERATION_BUNDLE_SCHEMA_VERSION:
            raise GenerationBundleImportError(
                f"Unsupported export schema_version: {document.get('schema_version')!r} "
                f"(this build reads version {GENERATION_BUNDLE_SCHEMA_VERSION})"
            )

        generation = document.get("generation")
        if not isinstance(generation, dict) or not isinstance(generation.get("form_data"), dict):
            raise GenerationBundleImportError("Bundle is missing generation.form_data")
        if not isinstance(generation.get("mode"), str) or not generation["mode"]:
            raise GenerationBundleImportError("Bundle is missing generation.mode")

        models = document.get("models")
        if models is not None and not isinstance(models, list):
            raise GenerationBundleImportError("Bundle 'models' must be a list")

        return generation

    def _check_bundle_environment(
        self, generation: Dict[str, Any], models: List[Dict[str, Any]]
    ) -> Tuple[bool, List[str]]:
        """Environment checks -> warnings, never a hard failure: a preset not
        installed here, or a model missing / digest-mismatched locally. Matched
        by (model_type, filename) - the same cross-instance model identity used
        everywhere else (see docs/models.md). `backend_id` is deliberately not
        part of the bundle at all - it is instance-specific and never checked.
        """
        warnings: List[str] = []

        preset_id = generation.get("preset_id")
        preset_available = False
        if preset_id:
            name_map: Dict[str, str] = {}
            resolver = self._query.preset_name_resolver
            if resolver is not None:
                try:
                    name_map = resolver.name_map()
                except Exception:
                    logger.exception("preset name resolution failed while checking bundle environment")
            preset_available = preset_id in name_map
            if not preset_available:
                preset_label = generation.get("preset_name") or preset_id
                warnings.append(f"Preset '{preset_label}' is not installed on this instance")

        from src.features.models.repository import model_repo

        for model in models:
            if not isinstance(model, dict):
                continue
            filename = model.get("filename")
            if not filename:
                continue
            model_type = model.get("model_type")
            display_name = model.get("name")
            label = f"{display_name} ({filename})" if display_name and display_name != filename else filename
            candidates = [m for m in model_repo.get_by_filename(filename) if m.model_type == model_type]

            if not candidates:
                warnings.append(f"Model '{label}' ({model_type}) was not found locally")
                continue

            expected_sha256 = model.get("sha256")
            local_sha256 = candidates[0].sha256
            if expected_sha256 and local_sha256 and expected_sha256 != local_sha256:
                warnings.append(
                    f"Model '{label}' is present locally but its digest does not match the exported copy"
                )

        return preset_available, warnings

    def import_bundle(self, content: bytes) -> Dict[str, Any]:
        """Parse an uploaded generation bundle into a reuse payload.

        Never creates a generation record - the caller (a "reuse this
        generation" flow) opens a pre-filled generate tab from the returned
        `reuse` data. Structural problems raise `GenerationBundleImportError`
        (mapped to a 4xx by the controller); environment problems (preset or
        models not installed here) come back as `warnings` instead of blocking.

        Args:
            content: The raw uploaded bytes - either a bundle zip or a bare
                generation.json

        Returns:
            Dict with `reuse`, `preset_available`, `warnings`

        Raises:
            GenerationBundleImportError: If the document is malformed, oversized,
                or not a PotionUI generation export
        """
        if len(content) > _MAX_BUNDLE_UPLOAD_BYTES:
            raise GenerationBundleImportError("Bundle exceeds the maximum upload size")

        document = self._parse_bundle_document(content)
        generation = self._validate_bundle_envelope(document)
        models = document.get("models") or []

        preset_available, warnings = self._check_bundle_environment(generation, models)

        reuse = {
            "preset_id": generation.get("preset_id"),
            "mode": generation.get("mode"),
            "form_name": generation.get("form_name"),
            "form_data": generation.get("form_data"),
            "prompt_state": generation.get("prompt_state"),
        }

        return {
            "reuse": reuse,
            "preset_available": preset_available,
            "warnings": warnings,
        }

    async def upload_generations(
        self,
        files: List,
        tag_ids: List[str],
        user_id: str
    ) -> Dict[str, Any]:
        """Upload files as completed generations.

        Executes hooks:
        - generation.before_upload: Can modify data or block
        - generation.after_upload: Notification of successful upload

        Args:
            files: List of uploaded file objects
            tag_ids: List of tag IDs to apply
            user_id: The user ID

        Returns:
            Dict with generation_id and uploaded files

        Raises:
            UploadFailedException: If upload fails or is blocked
            InvalidTagException: If any tag is invalid
        """
        if not files:
            raise UploadFailedException("No files provided")

        # Execute before_upload hook
        hook_data, blocked = execute_hook(self.plugins,
            GENERATION_HOOKS.before_upload,
            {
                "user_id": user_id,
                "file_count": len(files),
                "tag_ids": tag_ids
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Upload blocked by plugin")
            logger.warning(f"Generation upload blocked: {reason}")
            raise UploadFailedException(reason)

        # Generate unique generation ID
        generation_id = generate_ulid()

        # Create Generation record FIRST (required for foreign key constraint)
        generation = Generation(
            id=generation_id,
            preset_id=None,  # NULL for uploaded generations
            form_data={},
            user_id=user_id,
            status='completed',
            progress=1.0,
            completed_at=datetime.now(timezone.utc)
        )
        self.generation_repo.create(generation)

        # The key-space directory this generation's files live under
        # (generations/YYYY-MM-DD/generation_id) - a driver key, not a local path.
        today = datetime.now().strftime('%Y-%m-%d')
        generation_dir_relative = f"generations/{today}/{generation_id}"

        # Process each file
        uploaded_files = []
        for idx, upload_file in enumerate(files):
            # Validate file type
            media_resolver = MediaTypeResolver()
            if not upload_file.content_type or not media_resolver.is_valid_media_type(upload_file.content_type):
                logger.warning(f"Skipping unsupported file: {upload_file.filename} (type: {upload_file.content_type})")
                continue

            # Read file content
            content = await upload_file.read()

            # Process the file
            file_record = self._process_uploaded_file(
                upload_file, content, generation_dir_relative, idx, user_id
            )

            if file_record:
                # Save file to database and associate with generation
                created_file = self.generation_repo.add_file(generation_id, file_record)
                uploaded_files.append(created_file.to_dict())

        if not uploaded_files:
            # Every candidate had an unsupported content_type, so
            # _process_uploaded_file was never called and nothing was written.
            self.generation_repo.delete(generation_id)
            raise UploadFailedException("No valid files were uploaded")

        # Apply tags if provided
        if tag_ids:
            from src.features.tags.repository import tag_repo

            # Validate tags belong to user and are GENERATION type
            for tag_id in tag_ids:
                tag = tag_repo.get_tag_by_id(tag_id)
                if not tag or tag.type != 'GENERATION' or tag.user_id != user_id:
                    logger.warning(f"Skipping invalid tag: {tag_id}")
                    continue

            # Apply tags
            tag_repo.set_generation_tags(generation_id, tag_ids)

        # Execute after_upload hook
        execute_hook(self.plugins,
            GENERATION_HOOKS.after_upload,
            {
                "generation_id": generation_id,
                "user_id": user_id,
                "file_count": len(uploaded_files),
                "tag_ids": tag_ids
            }
        )

        logger.info(f"Generation uploaded: {generation_id} with {len(uploaded_files)} files")

        return {
            "generation_id": generation_id,
            "files": uploaded_files
        }

    def update_tags(
        self,
        generation_id: str,
        tag_ids: List[str],
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Replace all tags for a generation.

        Executes hooks:
        - generation.before_update_tags: Can block update
        - generation.after_update_tags: Notification of update

        Args:
            generation_id: The generation ID
            tag_ids: List of tag IDs to set
            user_id: The user ID for ownership verification

        Returns:
            List of updated tag dicts

        Raises:
            GenerationNotFoundException: If generation not found
            InvalidTagException: If any tag is invalid
        """
        # Verify ownership
        self._query._get_generation_or_raise(generation_id, user_id)

        # Validate all tags
        self._query._validate_tag_ids(tag_ids, user_id)

        # Execute before_update_tags hook
        hook_data, blocked = execute_hook(self.plugins,
            GENERATION_HOOKS.before_update_tags,
            {
                "generation_id": generation_id,
                "user_id": user_id,
                "tag_ids": tag_ids
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Tag update blocked by plugin")
            logger.warning(f"Generation tag update blocked: {reason}")
            raise InvalidTagException(reason)

        from src.features.tags.repository import tag_repo
        success = tag_repo.set_generation_tags(generation_id, tag_ids)

        if not success:
            raise InvalidTagException("Failed to update generation tags")

        tags = tag_repo.get_generation_tags(generation_id)
        tag_dicts = [tag.model_dump(mode="json") for tag in tags]

        # Execute after_update_tags hook
        execute_hook(self.plugins,
            GENERATION_HOOKS.after_update_tags,
            {
                "generation_id": generation_id,
                "user_id": user_id,
                "tag_ids": tag_ids
            }
        )

        logger.info(f"Generation tags updated: {generation_id}")

        return tag_dicts

    def remove_tag(self, generation_id: str, tag_id: str, user_id: str) -> bool:
        """Remove a single tag from a generation.

        Args:
            generation_id: The generation ID
            tag_id: The tag ID to remove
            user_id: The user ID for ownership verification

        Returns:
            True if successful

        Raises:
            GenerationNotFoundException: If generation not found
        """
        # Verify ownership
        self._query._get_generation_or_raise(generation_id, user_id)

        from src.features.tags.repository import tag_repo
        success = tag_repo.remove_tag_from_generation(generation_id, tag_id)

        if success:
            logger.info(f"Tag {tag_id} removed from generation {generation_id}")

        return success
