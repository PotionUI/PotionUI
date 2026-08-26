"""Publish / delete an inspiration.

`publish` takes a snapshot of a generation's output: it copies the chosen
files into `storage/inspirations/<id>/` and embeds the generation's
`form_data` (allowlist-filtered) plus a curated params preview, so the
resulting row never has to read through to the source generation again.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.features.inspirations.collaborators import InspirationCollaborators
from src.features.inspirations.records import Inspiration
from src.features.inspirations.storage import inspiration_media_key
from src.features.inspirations.technique import derive_technique
from src.features.presets.form_overrides import mode_field_inventory
from src.platform.filesystem.storage_driver import StorageKeyError
from src.platform.util.ids import generate_ulid

if TYPE_CHECKING:
    from src.features.generation.records import Generation
    from src.features.presets.templates import FieldTemplate

logger = logging.getLogger(__name__)

# Generation `files.file_type` -> the inspirations `media[].type` vocabulary.
# Mirrors `src.features.library.operations.mutations`'s `_FILE_TYPE_TO_MEDIA_TYPE`.
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


def publish(
    collaborators: InspirationCollaborators,
    user_id: str,
    generation_id: str,
    filenames: List[str],
    title: str,
    description: Optional[str] = None,
) -> Inspiration:
    """Publish a snapshot of the caller's generation output.

    Copies the chosen files into `storage/inspirations/<id>/` and embeds the
    generation's `form_data` + a curated params preview - the resulting row
    never reads through to the source generation again.

    Raises:
        ValueError: If the title is empty, no filenames are given, the
            generation is not found/owned, or a filename is not one of its
            final output files.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("Title is required")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Title must be {MAX_TITLE_LENGTH} characters or fewer")

    filenames = [f for f in (filenames or []) if f]
    if not filenames:
        raise ValueError("At least one filename is required")

    generation = collaborators.generation_repository.get_by_id(generation_id, user_id=user_id)
    if not generation:
        raise ValueError("Generation not found")

    files = collaborators.generation_repository.get_files(generation_id, is_final=True)
    files_by_name = {Path(f.file_path).name: f for f in files}
    missing = [fn for fn in filenames if fn not in files_by_name]
    if missing:
        raise ValueError(f"File(s) not found in generation output: {', '.join(missing)}")

    inspiration_id = generate_ulid()
    storage_root = Path(collaborators.file_store.base_storage_dir)
    media_entries = []
    for filename in filenames:
        file_record = files_by_name[filename]
        media_type = _FILE_TYPE_TO_MEDIA_TYPE.get((file_record.file_type or "").upper())
        if not media_type:
            raise ValueError(f"Cannot publish a {file_record.file_type} file")

        source = Path(collaborators.file_store.get_full_path(file_record.file_path))
        if not collaborators.file_resolver.validate_path_security(source, storage_root):
            logger.warning(f"Refused inspiration publish of out-of-tree file path: {file_record.file_path}")
            raise ValueError("File not found")
        if not source.exists():
            raise ValueError("File not found")

        written = collaborators.storage_driver.put_file(inspiration_media_key(inspiration_id, filename), source)
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

    preset_name = (
        collaborators.preset_name_resolver.resolve(generation.preset_id) if generation.preset_id else None
    )
    preset_template = (
        collaborators.preset_template_loader.load_preset_by_id(generation.preset_id)
        if generation.preset_id else None
    )
    filtered_form_data, omitted_fields, has_image_input, has_video_input = _filter_shareable_form_data(
        collaborators, generation, preset_template
    )
    technique = derive_technique(
        mode=generation.mode,
        category=preset_template.category if preset_template else None,
        has_image_input=has_image_input,
        has_video_input=has_video_input,
    )
    params_snapshot = {
        "form_data": filtered_form_data,
        "preview": _build_params_preview(collaborators, generation_id, preset_name),
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
    created = collaborators.repository.create(inspiration)
    logger.info(f"Published inspiration {created.id} from generation {generation_id}")
    return created


def _filter_shareable_form_data(
    collaborators: InspirationCollaborators, generation: "Generation", preset_template
) -> tuple:
    """Allowlist a generation's `form_data` down to publicly-shareable fields
    only - the never-leak-by-default posture (see
    `src.platform.plugins.field_types.FieldTypeDefinition.shareable`).

    Returns `(filtered_form_data, omitted_field_names, has_image_input,
    has_video_input)`. A key is included only when the preset's form (for the
    generation's mode) declares it with a `shareable=True` field type - an
    unknown key, a key whose field type isn't registered as shareable, or a
    preset/form the loader can't resolve (`preset_template` is `None`) are
    all treated the same way: omitted. `has_image_input`/`has_video_input`
    are read from the field's declared type regardless of its shareable
    classification, since a media field is a real input-shape signal for
    `derive_technique` even though it never appears in the filtered data.
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

        if field_type is not None and collaborators.field_type_registry.get(field_type).shareable:
            filtered[name] = value
        else:
            omitted.append(name)

    return filtered, omitted, has_image_input, has_video_input


def _build_params_preview(
    collaborators: InspirationCollaborators, generation_id: str, preset_name: Optional[str]
) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    if preset_name:
        preview.append({"name": "preset", "value": preset_name})

    params = collaborators.generation_parameter_repository.get_by_generation(generation_id)
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


def delete(collaborators: InspirationCollaborators, inspiration_id: str, user_id: str, is_admin: bool = False) -> None:
    """Delete an inspiration - its row and its copied files.

    Raises:
        ValueError: If not found, or found but owned by someone else and the
            caller is not an admin.
    """
    insp = collaborators.repository.get_by_id(inspiration_id)
    if not insp:
        raise ValueError("Inspiration not found")
    if insp.user_id != user_id and not is_admin:
        raise ValueError("Inspiration not found")

    for entry in insp.media:
        try:
            collaborators.storage_driver.delete(inspiration_media_key(inspiration_id, entry["filename"]))
        except StorageKeyError:
            logger.warning(f"Skipped deleting invalid inspiration media key: {entry.get('filename')!r}")

    collaborators.repository.delete(inspiration_id)
    logger.info(f"Deleted inspiration {inspiration_id}")
