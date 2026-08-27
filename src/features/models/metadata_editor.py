"""Edits to a single indexed model: its tags, description, triggers, and removal."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.validation import coerce_attribute_value
from src.features.models.exceptions import (
    ModelNotFoundException,
    ModelIndexingException,
    InvalidTagException,
    InvalidModelMetadataException,
)
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.repository import ModelRepository
from src.features.tags.repository import TagRepository
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


class ModelMetadataEditor:
    """Mutating operations on one model's own metadata, each gated by plugin hooks."""

    def __init__(
        self,
        model_repository: ModelRepository,
        tag_repository: TagRepository,
        plugin_registry: PluginRegistry,
        settings: Settings,
        storage_driver: Optional[FileStorageDriver] = None,
        attribute_definition_repository: Optional[AttributeDefinitionRepository] = None,
    ):
        self.model_repo = model_repository
        self.tag_repo = tag_repository
        self.plugins = plugin_registry
        self.settings = settings
        self.attribute_definitions = attribute_definition_repository or AttributeDefinitionRepository()
        # Optional so tests that build this editor directly (without a
        # container) keep working: `_build_image_thumbnails` falls back to a
        # local driver rooted at the settings storage directory when unset.
        self.storage_driver = storage_driver

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        """Remove a model from the index (leaves the file on disk).

        Fires model_index.before_delete (can block) and after_delete. Raises
        ModelNotFoundException / ModelIndexingException.
        """
        model = self.model_repo.get_by_id(model_id, include_providers=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_delete,
            {"model_id": model_id, "filename": model.filename}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Delete blocked by plugin")
            raise ModelIndexingException(reason)

        success = self.model_repo.delete(model_id)

        if success:
            execute_hook(
                self.plugins,
                MODEL_INDEX_HOOKS.after_delete,
                {"model_id": model_id, "filename": model.filename}
            )
            return {
                "message": f"Model {model.filename} removed from index",
                "model_id": model_id
            }
        else:
            raise ModelIndexingException("Failed to delete model from index")

    def update_model_tags(self, model_id: str, tag_ids: List[str]) -> Dict[str, Any]:
        """Set a model's tags, verifying each is a MODEL-type tag.

        Fires model_index.before_update_tags (can block) and after_update_tags.
        Raises ModelNotFoundException / InvalidTagException.
        """
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        for tag_id in tag_ids:
            tag = self.tag_repo.get_tag_by_id(tag_id)
            if not tag or tag.type != 'MODEL':
                raise InvalidTagException(f"Invalid tag ID: {tag_id}")

        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_update_tags,
            {"model_id": model_id, "tag_ids": tag_ids}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Tag update blocked by plugin")
            raise InvalidTagException(reason)

        success = self.tag_repo.set_model_tags(model_id, tag_ids)

        if success:
            execute_hook(
                self.plugins,
                MODEL_INDEX_HOOKS.after_update_tags,
                {"model_id": model_id, "tag_ids": tag_ids}
            )

            updated_model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=True)
            return {
                "message": "Model tags updated successfully",
                "model": updated_model.to_dict(include_providers=False, include_tags=True)
            }
        else:
            raise InvalidTagException("Failed to update model tags")

    def update_model_description(self, model_id: str, description: str) -> Dict[str, Any]:
        """Set a model's description. Raises ModelNotFoundException on failure."""
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        success = self.model_repo.update_description(model_id, description)

        if success:
            updated_model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=True)
            return {
                "message": "Model description updated successfully",
                "model": updated_model.to_dict(include_providers=False, include_tags=True)
            }
        else:
            raise ModelNotFoundException("Failed to update model description")

    def update_model_prompting_guidance(self, model_id: str, prompting_guidance: str) -> Dict[str, Any]:
        """Set a model's admin-authored prompting guidance. Raises ModelNotFoundException on failure."""
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        success = self.model_repo.update_prompting_guidance(model_id, prompting_guidance)

        if success:
            updated_model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=True)
            return {
                "message": "Model prompting guidance updated successfully",
                "model": updated_model.to_dict(include_providers=False, include_tags=True)
            }
        else:
            raise ModelNotFoundException("Failed to update model prompting guidance")

    def update_model_metadata(self, model_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Replace a model's shared attribute values (`models.model_metadata`).

        Validates `values` against the attribute definitions declared for
        `model.model_type` (`AttributeDefinitionRepository.for_model_type`):
        every key must name a declared definition, and its value is coerced per
        the definition's `field_type` and checked against its `config`. Rejects
        rather than clamps - an out-of-range value is a caller bug, not
        something to silently correct. Raises ModelNotFoundException /
        InvalidModelMetadataException.
        """
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        definitions = {d.key: d for d in self.attribute_definitions.for_model_type(model.model_type)}

        coerced: Dict[str, Any] = {}
        for key, raw_value in values.items():
            definition = definitions.get(key)
            if definition is None:
                raise InvalidModelMetadataException(
                    f"'{key}' is not a declared attribute for model type '{model.model_type}'"
                )
            coerced[key] = coerce_attribute_value(definition, raw_value)

        success = self.model_repo.update_model_metadata(model_id, coerced)
        if not success:
            raise ModelNotFoundException("Failed to update model metadata")

        updated_model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=True)
        return {
            "message": "Model metadata updated successfully",
            "model": updated_model.to_dict(include_providers=False, include_tags=True)
        }

    def update_model_preview(
        self,
        model_id: str,
        preview_input: Optional[Dict[str, Any]],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set (or clear, with None) a model's admin-set preview media.

        `preview_input` is `{source_path, type, name?}` where `source_path` is a
        storage-relative path to a file already uploaded through the media feature
        (or picked from generation history). The file is registered as a `files`
        row and the preview references it via the auth-exempt
        `/api/media/files/<id>` route, so it renders in plain `<img>`/`<video>`
        tags (the `/api/media/uploads/` route is bearer-gated and 401s there).
        Any previous preview's file row is dropped. Persisted as JSON
        (`{file_id, url, type, name}`). Raises ModelNotFoundException if the model
        is missing, ModelIndexingException on an invalid/unsafe source path.
        """
        from src.features.generation.file_repository import file_repo
        from src.features.generation.records import File

        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        old_file_id = (model.preview_media or {}).get('file_id')

        stored: Optional[Dict[str, Any]] = None
        if preview_input is not None:
            stored = self._create_preview_file_row(preview_input, user_id)

        success = self.model_repo.update_preview_media(
            model_id, json.dumps(stored) if stored is not None else None
        )
        if not success:
            raise ModelNotFoundException("Failed to update model preview")

        # Drop the previous preview's file row now the column no longer points at it.
        new_file_id = stored['file_id'] if stored else None
        if old_file_id and old_file_id != new_file_id:
            file_repo.delete(old_file_id)

        updated_model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=True)
        return {
            "message": "Model preview updated successfully",
            "model": updated_model.to_dict(include_providers=False, include_tags=True)
        }

    def _create_preview_file_row(
        self, preview_input: Dict[str, Any], user_id: Optional[str]
    ) -> Dict[str, Any]:
        """Register an uploaded preview source as a `files` row.

        `preview_input` is `{source_path, type, name?}`. Returns the denormalized
        shape persisted for a preview: `{file_id, url, type, name}`. Shared by the
        single-preview endpoint and the preview-list endpoints so both
        validate and thumbnail identically. Raises ModelIndexingException on an
        invalid/unsafe source path.
        """
        from src.features.generation.file_repository import file_repo
        from src.features.generation.records import File

        source_path = (preview_input.get('source_path') or '').strip()
        media_type = preview_input.get('type')
        if not source_path or media_type not in ('image', 'video', 'audio'):
            raise ModelIndexingException("A preview needs a source_path and an image/video/audio type")

        full_path = self._resolve_within_storage(source_path, user_id)

        # Give an image preview the same small/medium/large thumbnails every
        # other model image has, so cards and pickers fetch a resized variant
        # instead of falling back to the full-resolution original on `?size=`.
        # Built before create() so they persist on the row in one insert.
        thumbs, (width, height) = (
            self._build_image_thumbnails(full_path, source_path, user_id)
            if media_type == 'image' else ({}, (None, None))
        )

        created = file_repo.create(File(
            file_path=source_path,
            file_type=media_type.upper(),
            user_id=user_id,
            file_size=full_path.stat().st_size,
            thumbnail_small=thumbs.get('small'),
            thumbnail_medium=thumbs.get('medium'),
            thumbnail_large=thumbs.get('large'),
            width=width,
            height=height,
        ))

        return {
            'file_id': created.id,
            'url': f"/api/media/files/{created.id}",
            'type': media_type,
            'name': preview_input.get('name'),
        }

    # --- Preview media list (multiple admin-set previews) ---
    #
    # `models.preview_media` (the single-preview column, migration 085) stays the
    # untouched source of truth for the *legacy* single-set/clear endpoint above.
    # These list operations read/write a parallel `model_preview_media` table
    # (migration 094) and, whenever position 0 changes, mirror it into
    # `models.preview_media` so every existing display site keeps working with no
    # changes. A model that only ever used the legacy endpoint has no rows in the
    # list table yet; `_ensure_previews_seeded` lazily backfills one row (position
    # 0) from the column the first time the list is read or mutated.

    def _ensure_previews_seeded(self, model_id: str) -> List[Dict[str, Any]]:
        rows = self.model_repo.list_preview_media(model_id)
        if rows:
            return rows

        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        legacy = model.preview_media if model else None
        if not legacy or not legacy.get('url') or not legacy.get('type'):
            return []

        self.model_repo.insert_preview_media_row(
            model_id,
            legacy.get('file_id'),
            legacy['url'],
            legacy['type'],
            legacy.get('name'),
            position=0,
        )
        return self.model_repo.list_preview_media(model_id)

    def _mirror_primary_to_column(self, model_id: str, rows: List[Dict[str, Any]]) -> None:
        """Keep `models.preview_media` in sync with position 0 of the list."""
        primary = rows[0] if rows else None
        stored = None
        if primary:
            stored = {
                'file_id': primary.get('file_id'),
                'url': primary['url'],
                'type': primary['type'],
                'name': primary.get('name'),
            }
        self.model_repo.update_preview_media(model_id, json.dumps(stored) if stored else None)

    def list_model_previews(self, model_id: str) -> List[Dict[str, Any]]:
        """List a model's previews, ordered (position 0 = primary)."""
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")
        return self._ensure_previews_seeded(model_id)

    def add_model_preview(
        self, model_id: str, preview_input: Dict[str, Any], user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Append one preview to the model's list. Raises ModelNotFoundException /
        ModelIndexingException as `update_model_preview` does."""
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        existing = self._ensure_previews_seeded(model_id)
        stored = self._create_preview_file_row(preview_input, user_id)
        new_id = self.model_repo.insert_preview_media_row(
            model_id, stored['file_id'], stored['url'], stored['type'], stored['name'],
            position=len(existing),
        )

        rows = self.model_repo.list_preview_media(model_id)
        self._mirror_primary_to_column(model_id, rows)
        return {"message": "Preview added", "id": new_id, "previews": rows}

    def delete_model_preview(self, model_id: str, preview_id: str) -> Dict[str, Any]:
        """Remove one preview from the model's list and its backing `files` row."""
        from src.features.generation.file_repository import file_repo

        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        self._ensure_previews_seeded(model_id)
        row = self.model_repo.get_preview_media_row(preview_id)
        if not row or row['model_id'] != model_id:
            raise ModelNotFoundException(f"Preview '{preview_id}' not found on model '{model_id}'")

        self.model_repo.delete_preview_media_row(preview_id)
        if row.get('file_id'):
            file_repo.delete(row['file_id'])

        # Compact positions (0..n-1) so "position 0 is primary" always holds.
        remaining = self.model_repo.list_preview_media(model_id)
        self.model_repo.set_preview_media_positions(model_id, [r['id'] for r in remaining])
        rows = self.model_repo.list_preview_media(model_id)
        self._mirror_primary_to_column(model_id, rows)
        return {"message": "Preview removed", "previews": rows}

    def reorder_model_previews(self, model_id: str, ordered_ids: List[str]) -> Dict[str, Any]:
        """Reorder a model's previews. `ordered_ids` must be exactly the model's
        existing preview ids, in the desired order. Raises ModelIndexingException
        if the given ids don't match the model's current preview set."""
        model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        existing = self._ensure_previews_seeded(model_id)
        existing_ids = {row['id'] for row in existing}
        if set(ordered_ids) != existing_ids or len(ordered_ids) != len(existing_ids):
            raise ModelIndexingException("Reorder must include exactly the model's current previews")

        self.model_repo.set_preview_media_positions(model_id, ordered_ids)
        rows = self.model_repo.list_preview_media(model_id)
        self._mirror_primary_to_column(model_id, rows)
        return {"message": "Previews reordered", "previews": rows}

    def _build_image_thumbnails(self, full_path: Path, source_key: str, user_id: Optional[str]):
        """Write small/medium/large thumbnails next to an image preview.

        Returns `({size: relative_path}, (width, height))`. Best-effort: on failure
        returns `({}, (None, None))` and the serve route falls back to the original.
        Written through `self.storage_driver` (the shared, container-injected
        driver - falls back to a local driver rooted at the settings storage
        directory when this editor was built without one, e.g. in tests) under
        `base_key = dirname(source_key)` - `source_key` is the same
        storage-relative path persisted as `files.file_path`, so this is exactly
        the `base_key = Path(file_path).parent` join media's `get_file_by_id`
        uses to resolve a `?size=` request.
        """
        from pathlib import PurePosixPath
        import uuid as _uuid
        from PIL import Image
        from src.features.generation.handlers import generate_thumbnails
        from src.platform.filesystem.storage_driver import LocalFileStorageDriver

        driver = self.storage_driver
        if driver is None:
            driver = LocalFileStorageDriver(self.settings.get_file_storage_directory(user_id))

        try:
            with Image.open(full_path) as image:
                image.load()
                dims = image.size
                base_key = str(PurePosixPath(source_key).parent)
                thumbs = generate_thumbnails(image, driver, base_key, _uuid.uuid4().hex)
            return thumbs or {}, dims
        except Exception as e:
            logger.error(f"Could not generate preview thumbnails for {full_path.name}: {e}")
            return {}, (None, None)

    def _resolve_within_storage(self, relative_path: str, user_id: Optional[str]) -> Path:
        """Resolve a storage-relative path, refusing anything that escapes storage.

        The preview is served auth-exempt via /api/media/files/<id>, so a source
        path outside the storage root would be a directory-traversal read for any
        anonymous caller. Contain it here, before a `files` row is ever created.
        """
        storage_dir = Path(self.settings.get_file_storage_directory(user_id)).resolve()
        candidate = (storage_dir / relative_path).resolve()
        if not candidate.is_relative_to(storage_dir):
            raise ModelIndexingException("Preview source path escapes the storage directory")
        if not candidate.is_file():
            raise ModelIndexingException("Preview source file not found")
        return candidate

