"""Inspirations domain manager - mutations only (house rule: reads go
route -> repository directly).

Framework-agnostic: raises ValueError, which the controller maps to a uniform
404 (an inspiration/collection/comment that is not yours, or does not exist,
is reported identically - the `delete_upload` precedent in
`src.features.media.routes`).
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.features.inspirations.records import Inspiration, InspirationComment, InspirationCollection
from src.features.inspirations.repository import InspirationRepository
from src.features.inspirations.storage import inspiration_media_key
from src.features.inspirations.technique import derive_technique
from src.features.media.records import Upload
from src.features.presets.form_overrides import mode_field_inventory
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key
from src.platform.util.ids import generate_ulid

if TYPE_CHECKING:
    from src.features.generation.file_repository import FileRepository
    from src.features.generation.parameter_repository import GenerationParameterRepository
    from src.features.generation.records import Generation
    from src.features.generation.repository import GenerationRepository
    from src.features.media.file_resolver import FilePathResolver
    from src.features.media.upload_repository import UploadRepository
    from src.features.notifications.manager import NotificationManager
    from src.features.presets.loader import PresetTemplateLoader
    from src.features.presets.name_resolver import PresetNameResolver
    from src.features.presets.templates import FieldTemplate
    from src.platform.filesystem import FileStore
    from src.platform.filesystem.storage_driver import FileStorageDriver
    from src.platform.plugins.field_types import FieldTypeRegistry

logger = logging.getLogger(__name__)

# Generation `files.file_type` -> the inspirations `media[].type` vocabulary.
# Mirrors `LibraryManager`'s `_FILE_TYPE_TO_MEDIA_TYPE`.
_FILE_TYPE_TO_MEDIA_TYPE = {
    "IMAGE": "image",
    "VIDEO": "video",
    "AUDIO": "audio",
}

# The small, curated subset of a generation's parameters worth showing on a
# feed card - the "just preset + a few basics" idea from
# `src.features.generation.display_parameters`, without depending on that
# module's generation-history-specific allowlist.
_PREVIEW_PARAMETER_NAMES = ("seed", "resolution", "resolution_target", "cfg", "cfg_scale", "steps")

# Declared field types that signal an image-shaped or video-shaped input was
# submitted, for `derive_technique`'s has_image_input/has_video_input - `media`
# (the generic multi-kind loader) counts toward the image bucket since a
# generic media upload is closer to "an image was provided" than "no input"
# for classification purposes; a preset that actually wants video-in-video
# should declare `video`, not `media`.
_IMAGE_INPUT_FIELD_TYPES = frozenset({"image", "media"})
_VIDEO_INPUT_FIELD_TYPES = frozenset({"video"})

MAX_TITLE_LENGTH = 200
MAX_COMMENT_LENGTH = 2000


class InspirationManager:

    def __init__(
        self,
        inspiration_repository: InspirationRepository,
        generation_repository: "GenerationRepository",
        generation_parameter_repository: "GenerationParameterRepository",
        preset_name_resolver: "PresetNameResolver",
        preset_template_loader: "PresetTemplateLoader",
        field_type_registry: "FieldTypeRegistry",
        file_store: "FileStore",
        file_resolver: "FilePathResolver",
        storage_driver: "FileStorageDriver",
        upload_repository: "UploadRepository",
        notification_manager: "NotificationManager",
    ):
        self.repository = inspiration_repository
        self.generation_repo = generation_repository
        self.generation_parameter_repo = generation_parameter_repository
        self.preset_name_resolver = preset_name_resolver
        self.preset_template_loader = preset_template_loader
        self.field_type_registry = field_type_registry
        self.file_store = file_store
        self.file_resolver = file_resolver
        self.storage_driver = storage_driver
        self.upload_repository = upload_repository
        self.notification_manager = notification_manager

    # ========== Publish / delete ==========

    def publish(
        self,
        user_id: str,
        generation_id: str,
        filenames: List[str],
        title: str,
        description: Optional[str] = None,
    ) -> Inspiration:
        """Publish a snapshot of the caller's generation output.

        Copies the chosen files into `storage/inspirations/<id>/` and embeds
        the generation's `form_data` + a curated params preview - the
        resulting row never reads through to the source generation again.

        Raises:
            ValueError: If the title is empty, no filenames are given, the
                generation is not found/owned, or a filename is not one of
                its final output files.
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("Title is required")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"Title must be {MAX_TITLE_LENGTH} characters or fewer")

        filenames = [f for f in (filenames or []) if f]
        if not filenames:
            raise ValueError("At least one filename is required")

        generation = self.generation_repo.get_by_id(generation_id, user_id=user_id)
        if not generation:
            raise ValueError("Generation not found")

        files = self.generation_repo.get_files(generation_id, is_final=True)
        files_by_name = {Path(f.file_path).name: f for f in files}
        missing = [fn for fn in filenames if fn not in files_by_name]
        if missing:
            raise ValueError(f"File(s) not found in generation output: {', '.join(missing)}")

        inspiration_id = generate_ulid()
        storage_root = Path(self.file_store.base_storage_dir)
        media_entries = []
        for filename in filenames:
            file_record = files_by_name[filename]
            media_type = _FILE_TYPE_TO_MEDIA_TYPE.get((file_record.file_type or "").upper())
            if not media_type:
                raise ValueError(f"Cannot publish a {file_record.file_type} file")

            source = Path(self.file_store.get_full_path(file_record.file_path))
            if not self.file_resolver.validate_path_security(source, storage_root):
                logger.warning(f"Refused inspiration publish of out-of-tree file path: {file_record.file_path}")
                raise ValueError("File not found")
            if not source.exists():
                raise ValueError("File not found")

            written = self.storage_driver.put_file(inspiration_media_key(inspiration_id, filename), source)
            media_entries.append({
                "filename": filename,
                "type": media_type,
                "width": file_record.width,
                "height": file_record.height,
                "duration_seconds": file_record.duration_seconds,
                "fps": file_record.fps,
                "mime_type": file_record.mime_type,
                "file_size": file_record.file_size or written,
            })

        preset_name = self.preset_name_resolver.resolve(generation.preset_id) if generation.preset_id else None
        preset_template = (
            self.preset_template_loader.load_preset_by_id(generation.preset_id)
            if generation.preset_id else None
        )
        filtered_form_data, omitted_fields, has_image_input, has_video_input = self._filter_shareable_form_data(
            generation, preset_template
        )
        technique = derive_technique(
            mode=generation.mode,
            category=preset_template.category if preset_template else None,
            has_image_input=has_image_input,
            has_video_input=has_video_input,
        )
        params_snapshot = {
            "form_data": filtered_form_data,
            "preview": self._build_params_preview(generation_id, preset_name),
            "omitted_fields": omitted_fields,
            "mode": generation.mode,
        }

        inspiration = Inspiration(
            id=inspiration_id,
            user_id=user_id,
            title=title,
            description=(description or "").strip() or None,
            media=media_entries,
            params_snapshot=params_snapshot,
            preset_id=generation.preset_id,
            preset_name=preset_name,
            technique=technique,
            source_generation_id=generation_id,
        )
        created = self.repository.create(inspiration)
        logger.info(f"Published inspiration {created.id} from generation {generation_id}")
        return created

    def _filter_shareable_form_data(
        self, generation: "Generation", preset_template
    ) -> tuple:
        """Allowlist a generation's `form_data` down to publicly-shareable
        fields only - the never-leak-by-default posture (see
        `src.platform.plugins.field_types.FieldTypeDefinition.shareable`).

        Returns `(filtered_form_data, omitted_field_names, has_image_input,
        has_video_input)`. A key is included only when the preset's form
        (for the generation's mode) declares it with a `shareable=True` field
        type - an unknown key, a key whose field type isn't registered as
        shareable, or a preset/form the loader can't resolve (`preset_template`
        is `None`) are all treated the same way: omitted. `has_image_input`/
        `has_video_input` are read from the field's declared type regardless
        of its shareable classification, since a media field is a real
        input-shape signal for `derive_technique` even though it never
        appears in the filtered data.
        """
        form_data = generation.form_data or {}
        field_index: Dict[str, "FieldTemplate"] = {}
        if preset_template is not None:
            field_index = mode_field_inventory(preset_template, generation.mode)

        filtered: Dict[str, Any] = {}
        omitted: List[str] = []
        has_image_input = False
        has_video_input = False

        for name, value in form_data.items():
            field = field_index.get(name)
            field_type = field.type if field else None

            if field_type in _IMAGE_INPUT_FIELD_TYPES and value:
                has_image_input = True
            if field_type in _VIDEO_INPUT_FIELD_TYPES and value:
                has_video_input = True

            if field_type is not None and self.field_type_registry.get(field_type).shareable:
                filtered[name] = value
            else:
                omitted.append(name)

        return filtered, omitted, has_image_input, has_video_input

    def _build_params_preview(self, generation_id: str, preset_name: Optional[str]) -> List[Dict[str, Any]]:
        preview: List[Dict[str, Any]] = []
        if preset_name:
            preview.append({"name": "preset", "value": preset_name})

        params = self.generation_parameter_repo.get_by_generation(generation_id)
        for name in _PREVIEW_PARAMETER_NAMES:
            match = next((p for p in params if p.parameter_name == name and p.parameter_index == 0), None)
            if not match:
                continue
            try:
                value = json.loads(match.parameter_value)
            except (TypeError, ValueError):
                value = match.parameter_value
            preview.append({"name": name, "value": value})

        return preview

    def delete(self, inspiration_id: str, user_id: str, is_admin: bool = False) -> None:
        """Delete an inspiration - its row and its copied files.

        Raises:
            ValueError: If not found, or found but owned by someone else and
                the caller is not an admin.
        """
        insp = self.repository.get_by_id(inspiration_id)
        if not insp:
            raise ValueError("Inspiration not found")
        if insp.user_id != user_id and not is_admin:
            raise ValueError("Inspiration not found")

        for entry in insp.media:
            try:
                self.storage_driver.delete(inspiration_media_key(inspiration_id, entry["filename"]))
            except StorageKeyError:
                logger.warning(f"Skipped deleting invalid inspiration media key: {entry.get('filename')!r}")

        self.repository.delete(inspiration_id)
        logger.info(f"Deleted inspiration {inspiration_id}")

    # ========== Comments ==========

    def add_comment(self, inspiration_id: str, user_id: str, body: str) -> InspirationComment:
        """Raises ValueError if the inspiration is not found or the body is empty/too long."""
        insp = self.repository.get_by_id(inspiration_id)
        if not insp:
            raise ValueError("Inspiration not found")

        body = (body or "").strip()
        if not body:
            raise ValueError("Comment body is required")
        if len(body) > MAX_COMMENT_LENGTH:
            raise ValueError(f"Comment must be {MAX_COMMENT_LENGTH} characters or fewer")

        comment = self.repository.create_comment(inspiration_id, user_id, body)

        if insp.user_id != user_id:
            self.notification_manager.notify(
                level="info",
                title="New comment on your inspiration",
                message=f'{comment.author_username or "Someone"} commented on "{insp.title}"',
                category="inspirations",
                user_id=insp.user_id,
                source="core",
                type="inspiration.comment",
                metadata={"inspiration_id": inspiration_id, "comment_id": comment.id},
            )

        return comment

    def delete_comment(self, comment_id: str, user_id: str, is_admin: bool = False) -> None:
        comment = self.repository.get_comment(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        if comment.user_id != user_id and not is_admin:
            raise ValueError("Comment not found")
        self.repository.delete_comment(comment_id)

    # ========== Saves ==========

    def save_to_library(self, inspiration_id: str, user_id: str) -> int:
        """Copy this inspiration's media into the caller's library and mark it saved.

        Mirrors `LibraryManager.copy_generation_file`'s storage/DB writes, one
        file at a time, sourcing bytes from the inspiration's own copies
        rather than a generation.

        Raises:
            ValueError: If the inspiration is not found, or none of its
                media files could be copied.
        """
        insp = self.repository.get_by_id(inspiration_id)
        if not insp:
            raise ValueError("Inspiration not found")

        storage_root = Path(self.file_store.base_storage_dir)
        copied = 0
        for entry in insp.media:
            source = Path(self.file_store.get_full_path(inspiration_media_key(inspiration_id, entry["filename"])))
            if not self.file_resolver.validate_path_security(source, storage_root):
                logger.warning(f"Refused library copy of out-of-tree inspiration file: {entry.get('filename')!r}")
                continue
            if not source.exists():
                continue

            filename = f"{uuid.uuid4()}{source.suffix}"
            written = self.storage_driver.put_file(uploads_key(filename), source)

            self.upload_repository.create(Upload(
                user_id=user_id,
                filename=filename,
                original_filename=entry.get("filename") or filename,
                media_type=entry.get("type"),
                mime_type=entry.get("mime_type"),
                width=entry.get("width"),
                height=entry.get("height"),
                duration_seconds=entry.get("duration_seconds"),
                fps=entry.get("fps"),
                file_size=entry.get("file_size") or written,
            ))
            copied += 1

        if copied == 0 and insp.media:
            raise ValueError("Could not copy any files into your library")

        self.repository.create_save(user_id, inspiration_id)
        return self.repository.count_saves(inspiration_id)

    def unsave(self, inspiration_id: str, user_id: str) -> int:
        """Remove the save marker - the library copies made by `save_to_library` stay."""
        self.repository.delete_save(user_id, inspiration_id)
        return self.repository.count_saves(inspiration_id)

    # ========== Collections ==========

    def create_collection(
        self, user_id: str, name: str, parent_id: Optional[str] = None
    ) -> InspirationCollection:
        name = (name or "").strip()
        if not name:
            raise ValueError("Collection name is required")
        if parent_id and not self.repository.get_collection(parent_id, user_id):
            raise ValueError("Parent collection not found or access denied")
        return self.repository.create_collection(user_id, name, parent_id)

    def update_collection(
        self,
        collection_id: str,
        user_id: str,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        parent_id_set: bool = False,
    ) -> InspirationCollection:
        """Rename and/or reparent a collection owned by the user.

        `parent_id_set` distinguishes "the request omitted parent_id" (leave
        it alone) from "the request set it to null" (move to root) - both
        look like `parent_id=None` otherwise.

        Raises:
            ValueError: If the collection/new parent is not found/owned, or
                the move would create a cycle.
        """
        if not self.repository.get_collection(collection_id, user_id):
            raise ValueError("Collection not found or access denied")

        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Collection name is required")
            self.repository.rename_collection(collection_id, user_id, name)

        if parent_id_set:
            if parent_id and not self.repository.get_collection(parent_id, user_id):
                raise ValueError("Parent collection not found or access denied")
            if self.repository.creates_cycle(collection_id, parent_id):
                raise ValueError("Cannot move a collection into itself or one of its subfolders")
            self.repository.move_collection(collection_id, user_id, parent_id)

        updated = self.repository.get_collection(collection_id, user_id)
        if updated is None:
            raise ValueError("Collection not found or access denied")
        return updated

    def delete_collection(self, collection_id: str, user_id: str) -> None:
        if not self.repository.delete_collection(collection_id, user_id):
            raise ValueError("Collection not found or access denied")

    def add_item(self, collection_id: str, user_id: str, inspiration_id: str) -> None:
        if not self.repository.get_collection(collection_id, user_id):
            raise ValueError("Collection not found or access denied")
        if not self.repository.get_by_id(inspiration_id):
            raise ValueError("Inspiration not found")
        self.repository.add_item(collection_id, inspiration_id)

    def remove_item(self, collection_id: str, user_id: str, inspiration_id: str) -> None:
        if not self.repository.get_collection(collection_id, user_id):
            raise ValueError("Collection not found or access denied")
        self.repository.remove_item(collection_id, inspiration_id)
