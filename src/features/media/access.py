import re
from typing import Any, Optional

from src.features.content_safety.constants import STATE_FLAGGED, STATE_SAFE
from src.features.generation.policy import GenerationPolicy
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key

_TEMP_NAME = re.compile(r"^tmp_[a-z]+_(?P<generation_id>[^_/\\]+)_[0-9A-Za-z]{26}\.[0-9A-Za-z]+$")


class MediaAccess:
    def __init__(
        self,
        generation_repository: Any,
        file_repository: Any,
        upload_repository: Any,
        model_repository: Any,
        content_safety: Any = None,
    ):
        self.generations = generation_repository
        self.files = file_repository
        self.uploads = upload_repository
        self.models = model_repository
        self.content_safety = content_safety

    def is_restricted(self, viewer: Any) -> bool:
        return self.content_safety is not None and bool(self.content_safety.is_restricted(viewer.id))

    def _rating_state(self, key: str) -> Optional[str]:
        return self.content_safety.ledger.states([key]).get(key)

    def generation_allowed(self, viewer: Any, generation_id: Optional[str]) -> bool:
        if viewer is None or not generation_id:
            return False
        generation = self.generations.get_by_id(generation_id)
        if generation is None or not GenerationPolicy.can_access(viewer, generation.user_id):
            return False
        if not self.is_restricted(viewer) or generation.is_active():
            return True
        return generation_id in self.content_safety.viewable_generation_ids(viewer.id, [generation_id])

    def temp_allowed(self, viewer: Any, filename: str) -> bool:
        if viewer is None:
            return False
        match = _TEMP_NAME.match(filename or "")
        if match is None:
            return GenerationPolicy.is_admin(viewer) and not self.is_restricted(viewer)
        return self.generation_allowed(viewer, match.group("generation_id"))

    def upload_allowed(self, viewer: Any, filename: str) -> bool:
        if viewer is None or not filename:
            return False
        try:
            key = uploads_key(filename)
        except StorageKeyError:
            return False
        upload = self.uploads.get_by_filename_unscoped(filename)
        if upload is None or not GenerationPolicy.can_access(viewer, upload.user_id):
            return False
        if not self.is_restricted(viewer):
            return True
        return self._rating_state(key) != STATE_FLAGGED

    def file_allowed(self, viewer: Any, file_id: str) -> bool:
        if viewer is None or not file_id:
            return False
        record = self.files.get_by_id(file_id)
        if record is None:
            return False
        generation_ids = self.files.generation_ids_for_file(file_id)
        if generation_ids:
            return any(self.generation_allowed(viewer, gid) for gid in generation_ids)
        restricted = self.is_restricted(viewer)
        model_ids = self.models.model_ids_for_file(file_id)
        if model_ids:
            if restricted:
                return False
            if GenerationPolicy.is_admin(viewer):
                return True
            return any(self.models.is_model_assigned_to_user(mid, viewer.id) for mid in model_ids)
        if not GenerationPolicy.can_access(viewer, getattr(record, "user_id", None)):
            return False
        return not restricted or self._rating_state(record.file_path) == STATE_SAFE
