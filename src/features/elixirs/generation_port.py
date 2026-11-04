from typing import Any, Callable, Dict, List, Mapping, Optional

from src.features.elixirs.errors import MediaRefError, WriteRefused
from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.policy import GenerationPolicy
from src.features.generation.state_lookup import (
    GenerationStateView,
    resolve_generation_state,
)
from src.features.prompt_enhancement import operations as writer_operations
from src.features.prompt_enhancement.media import encode_media
from src.features.prompt_enhancement.operations import PromptWriteRefused
from src.features.video_director.binding import bind_director_media
from src.platform.security.user import AccountType

MEDIA_REF_KEYS = {"history": {"kind", "generation_id", "file_id"}, "library": {"kind", "item_id"}}


def _not_found(generation_id: str) -> GenerationNotFoundException:
    return GenerationNotFoundException(f"Generation '{generation_id}' not found")


class GenerationPort:
    def __init__(
        self,
        users: Any,
        orchestrator: Any,
        broadcaster: Any,
        generations: Any,
        generation_files: Any,
        history: Any,
        file_service: Any,
        media_store: Any,
        prompt_writer: Any,
        storage_root: Callable[[], str],
        phrasebook_values: Any = None,
    ) -> None:
        self.users = users
        self.orchestrator = orchestrator
        self.broadcaster = broadcaster
        self.generations = generations
        self.generation_files = generation_files
        self.history = history
        self.file_service = file_service
        self.media_store = media_store
        self.prompt_writer = prompt_writer
        self.storage_root = storage_root
        self.phrasebook_values = phrasebook_values

    def user(self, user_id: str):
        return self.users.get_by_id(user_id)

    def _state(self, user, generation_id: str) -> GenerationStateView:
        state = resolve_generation_state(
            self.orchestrator.status_tracker.get(generation_id), self.generations, generation_id
        )
        if state is None or not GenerationPolicy.can_access(user, state.owner_id):
            raise _not_found(generation_id)
        return state

    async def submit(self, user, request: Mapping[str, Any]) -> Dict[str, Any]:
        if user is None:
            raise PermissionError("A user is required to submit a generation")
        result = await self.orchestrator.start_generation(GenerationRequest.model_validate(dict(request)), user.id)
        status = result.get("status")
        return {
            "generation_id": result["generation_id"],
            "status": status.get("status") if isinstance(status, dict) else status,
            "queue_position": result.get("queue_position"),
        }

    def state(self, user, generation_id: str) -> Dict[str, Any]:
        return self._state(user, generation_id).payload

    def _visible_files(self, user, generation_id: str) -> List[Dict[str, Any]]:
        self._state(user, generation_id)
        files = self.generation_files.get_generation_files(generation_id)
        indexes = {entry.id: position for position, entry in enumerate(files)}
        entries = [dict(entry.to_dict(), index=indexes[entry.id]) for entry in files]
        visible = self.history.query.visible_file_dicts(entries, viewer_id=getattr(user, "id", None))
        if entries and not visible:
            raise _not_found(generation_id)
        for entry in visible:
            entry["index"] = indexes[entry["id"]]
        return visible

    def files(self, user, generation_id: str) -> List[Dict[str, Any]]:
        return [entry for entry in self._visible_files(user, generation_id) if entry.get("is_final") and not entry.get("is_derived")]

    def batch_files(self, user, generation_id: str, prompts: List[str], per_prompt: int) -> List[Dict[str, Any]]:
        origin = self.files(user, generation_id)
        members = [
            member_id
            for member_id in self.generations.batch_member_ids(generation_id, getattr(user, "id", None))
            if member_id != generation_id
        ]
        if not members:
            last = max(0, len(prompts) - 1)
            return [
                {**entry, "generation_id": generation_id, "prompt_index": min(position // max(1, per_prompt), last)}
                for position, entry in enumerate(origin)
            ]
        files = [{**entry, "generation_id": generation_id, "prompt_index": 0} for entry in origin]
        following = 1
        for member_id in members:
            member = self.generations.get_by_id(member_id, user_id=getattr(user, "id", None))
            state = getattr(member, "prompt_state", None)
            authored = state.get("prompt") if isinstance(state, dict) else None
            index = next((position for position in range(following, len(prompts)) if prompts[position] == authored), following)
            following = index + 1
            try:
                entries = self.files(user, member_id)
            except GenerationNotFoundException:
                continue
            files.extend({**entry, "generation_id": member_id, "prompt_index": index} for entry in entries)
        return files

    async def cancel(self, user, generation_id: str) -> bool:
        self._state(user, generation_id)
        cancelled = await self.orchestrator.cancel_generation(generation_id)
        if cancelled:
            await self.broadcaster.broadcast_cancelled(generation_id)
        return cancelled

    def _resolve_history(self, user, ref: Mapping[str, Any]) -> Dict[str, Any]:
        generation_id, file_id = ref.get("generation_id"), ref.get("file_id")
        if not isinstance(generation_id, str) or not generation_id or not isinstance(file_id, str) or not file_id:
            raise MediaRefError("invalid_ref", "A history reference needs generation_id and file_id")
        try:
            files = self._visible_files(user, generation_id)
        except GenerationNotFoundException:
            raise MediaRefError("not_found", "The referenced media is not available") from None
        entry = next((item for item in files if item["id"] == file_id and item.get("file_path")), None)
        if entry is None or not self.file_service.generation_exists(entry["file_path"]):
            raise MediaRefError("not_found", "The referenced media is not available")
        return {
            "value": entry["file_path"],
            "media_type": entry["file_type"],
            "original_filename": entry["file_path"].rsplit("/", 1)[-1],
            "origin": {"generation_id": generation_id, "file_index": entry["index"]},
        }

    def _resolve_library(self, user, ref: Mapping[str, Any]) -> Dict[str, Any]:
        item_id = ref.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise MediaRefError("invalid_ref", "A library reference needs item_id")
        upload = self.media_store.upload_repo.get_by_id(item_id, getattr(user, "id", None))
        if upload is None or not self.media_store.storage_driver.exists(f"uploads/{upload.filename}"):
            raise MediaRefError("not_found", "The referenced media is not available")
        return {
            "value": f"uploads/{upload.filename}",
            "media_type": upload.media_type,
            "original_filename": upload.original_filename,
            "origin": None,
        }

    def resolve(self, user, ref: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(ref, Mapping):
            raise MediaRefError("invalid_ref", "A media reference must be an object")
        kind = ref.get("kind")
        if kind not in MEDIA_REF_KEYS or set(ref) != MEDIA_REF_KEYS[kind]:
            raise MediaRefError("invalid_ref", "Unsupported media reference")
        if kind == "history":
            return self._resolve_history(user, ref)
        return self._resolve_library(user, ref)

    def bind_director(
        self,
        request: Dict[str, Any],
        role: str,
        media: Mapping[str, Any],
        segment_id: Optional[str] = None,
        promote: bool = False,
    ):
        return bind_director_media(request, role, media, segment_id=segment_id, promote=promote)

    async def write_prompt(self, user, request: Mapping[str, Any]) -> Dict[str, Any]:
        is_admin = user is not None and user.account_type == AccountType.ADMIN
        preset_id = request.get("preset_id")
        media = request.get("media") or []
        try:
            if preset_id and not writer_operations.preset_visible(self.prompt_writer, user.id, preset_id, is_admin):
                raise PromptWriteRefused("preset_not_available", "The preset this prompt is for is not available to you.")
            config = writer_operations.writer_config(self.prompt_writer, user.id, request.get("llm_id"), vision=bool(media))
            images = await encode_media(self.storage_root(), media)
            form_state = (
                {"preset": preset_id, "mode": request.get("mode"), "form_data": dict(request.get("form_data") or {})}
                if preset_id else None
            )
            return await writer_operations.write_prompt(
                self.prompt_writer,
                user.id,
                request["instruction"],
                llm_id=config.id,
                form_state=form_state,
                images=images,
                is_admin=is_admin,
            )
        except PromptWriteRefused as refused:
            raise WriteRefused(str(refused)) from None

    def phrasebook_texts(self, user, path: str) -> List[str]:
        if self.phrasebook_values is None or not path:
            return []
        return [value.value for value in self.phrasebook_values.get_by_path(path, user.id) if value.is_active and value.value]

    def writer_status(self, user, llm_id: Optional[str] = None) -> Dict[str, Any]:
        return writer_operations.writer_status(self.prompt_writer, user.id, llm_id)
