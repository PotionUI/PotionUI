from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.edits import rules
from src.features.edits.records import Edit
from src.features.edits.repository import EditRepository
from src.features.media.upload_repository import UploadRepository
from src.features.tab_drafts.validation import MAX_DOC_BYTES, doc_size
from src.platform.database.rows import now_iso
from src.platform.http.errors import ConflictError, DomainError, InvalidInputError, NotFoundError
from src.platform.util.ids import generate_ulid


def _not_found() -> NotFoundError:
    return NotFoundError("edit_not_found", "Edit not found")


def _check_state_size(state: Dict[str, Any]) -> None:
    size = doc_size(state)
    if size > MAX_DOC_BYTES:
        raise DomainError(
            "edit_state_too_large", f"The edit state is {size} bytes; the limit is {MAX_DOC_BYTES}.", 413,
            limit=MAX_DOC_BYTES,
        )


class EditManager:

    def __init__(
        self, repository: EditRepository, uploads: UploadRepository, clock: Callable[[], str] = now_iso
    ):
        self.repository = repository
        self.uploads = uploads
        self.clock = clock

    def _check_thumbnail(self, user_id: str, upload_id: Optional[str]) -> None:
        if upload_id is not None and self.uploads.get_by_id(upload_id, user_id) is None:
            raise InvalidInputError("invalid_edit_thumbnail", "Thumbnail not found")

    def list(
        self, user_id: str, tool: Optional[str], search: Optional[str], limit: int, offset: int
    ) -> Tuple[List[Edit], int]:
        if tool:
            rules.check_tool(tool)
        return self.repository.list(user_id, tool, (search or "").strip() or None, limit, offset)

    def get(self, user_id: str, edit_id: str) -> Edit:
        edit = self.repository.get(user_id, edit_id)
        if edit is None:
            raise _not_found()
        return edit

    def create(
        self,
        user_id: str,
        tool: str,
        name: Optional[str],
        source_kind: str,
        source_id: str,
        state: Dict[str, Any],
        thumbnail_upload_id: Optional[str] = None,
    ) -> Edit:
        rules.check_tool(tool)
        rules.check_source(source_kind, source_id)
        clean = rules.clean_name(name)
        _check_state_size(state)
        self._check_thumbnail(user_id, thumbnail_upload_id)
        edit = Edit(
            id=rules.ID_PREFIX + generate_ulid(), user_id=user_id, tool=tool, name=clean,
            source_kind=source_kind, source_id=source_id, state=state,
            thumbnail_upload_id=thumbnail_upload_id,
        )
        return self.repository.create(edit, self.clock())

    def update_state(
        self,
        user_id: str,
        edit_id: str,
        revision: int,
        state: Dict[str, Any],
        set_thumbnail: bool = False,
        thumbnail_upload_id: Optional[str] = None,
    ) -> Edit:
        _check_state_size(state)
        if set_thumbnail:
            self._check_thumbnail(user_id, thumbnail_upload_id)
        applied, current = self.repository.update_state(
            user_id, edit_id, revision, state, set_thumbnail, thumbnail_upload_id, self.clock()
        )
        if current is None:
            raise _not_found()
        if not applied:
            raise ConflictError(
                "edit_revision_conflict", "The edit changed since this revision was loaded", revision=current
            )
        return self.get(user_id, edit_id)

    def rename(self, user_id: str, edit_id: str, name: Optional[str]) -> Edit:
        clean = rules.clean_name(name)
        if not self.repository.rename(user_id, edit_id, clean, self.clock()):
            raise _not_found()
        return self.get(user_id, edit_id)

    def duplicate(self, user_id: str, edit_id: str) -> Edit:
        source = self.get(user_id, edit_id)
        copy = Edit(
            id=rules.ID_PREFIX + generate_ulid(), user_id=user_id, tool=source.tool,
            name=rules.copy_name(source.name), source_kind=source.source_kind, source_id=source.source_id,
            state=source.state, thumbnail_upload_id=source.thumbnail_upload_id,
        )
        return self.repository.create(copy, self.clock())

    def delete(self, user_id: str, edit_id: str) -> None:
        if not self.repository.delete(user_id, edit_id):
            raise _not_found()
