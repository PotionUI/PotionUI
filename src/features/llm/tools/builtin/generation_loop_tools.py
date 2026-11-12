import asyncio
import base64
import binascii
import json
import logging
import time
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from src.features.generation.details_url import REQUEST_ORIGIN_KEY, generation_details_url, resolve_public_base_url
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.history_executor import HistoryExecutorSaturated
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.generation_loop import (
    TERMINAL_STATES,
    RunLookupUnavailable,
    waits_for,
    MAX_CONCURRENT_WAITS,
    find_run,
    status_payload,
)
from src.features.llm.tools.builtin.media_sniff import sniff_media
from src.features.llm.tools.builtin.domain_support import unknown_arguments
from src.features.llm.tools.errors import unexpected
from src.features.media.validators import UPLOAD_PURPOSE_USER
from src.platform.http.base_controller import PassThrough

logger = logging.getLogger(__name__)

WAIT_DEFAULT_SECONDS = 30
WAIT_MAX_SECONDS = 55
WAIT_RECHECK_SECONDS = 5.0
RESULT_IMAGES_DEFAULT = 4
RESULT_IMAGES_MAX = 8
RESULT_IMAGE_BYTES = 1_500_000
RESULT_TOTAL_BYTES = 6_000_000
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
MEDIA_LINK_PATH = "/api/media/files/{file_id}"
IMAGE_SIZES = {"large": ("large", "medium", "small"), "medium": ("medium", "small")}
PARAM_SEED = "seed"
PARAM_PROMPT = "positive_prompt"
PARAM_NEGATIVE = "negative_prompt"


def _not_found(generation_id: str) -> ToolResult:
    return ToolResult(
        success=False,
        data="",
        error=f"No generation '{generation_id}' of yours. Use the generation_id start_generation returned.",
    )


def _unavailable(exc: Exception) -> ToolResult:
    return ToolResult(success=False, data="", error=str(exc))


def _generation_id(kwargs: Dict[str, Any]) -> Optional[str]:
    value = kwargs.get("generation_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


class GenerationLoopTool(BaseTool):
    modes = ["mcp"]
    mcp = True
    group = "Generation"
    context_group = "library"

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        unknown = unknown_arguments(self, kwargs)
        if unknown is not None:
            return unknown
        return await self.run(context, **kwargs)

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        raise NotImplementedError

    def _generation_id_property(self) -> Dict[str, Any]:
        return {"type": "string", "description": "The generation_id start_generation returned."}


class GetGenerationStatusTool(GenerationLoopTool):
    icon = "activity"
    name = "get_generation_status"
    read_only = True
    user_description = "Shows how one of your generations is doing: state, progress and queue position."

    @property
    def description(self) -> str:
        return (
            "Check one of your generations by generation_id. Returns status (pending, running, completed, "
            "failed or cancelled), finished, progress_percent, a plain-words step, queue_position while it "
            "waits (0 is next), and error when it failed. Once finished is true call get_generation_results."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"generation_id": self._generation_id_property()},
            "required": ["generation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id = _generation_id(kwargs)
        if not generation_id:
            return ToolResult(success=False, data="", error="'generation_id' is required")
        try:
            run = await find_run(context, generation_id)
        except RunLookupUnavailable as exc:
            return _unavailable(exc)
        if run is None:
            return _not_found(generation_id)
        return ToolResult(success=True, data=json.dumps(await status_payload(context, generation_id, run)))


class WaitForGenerationTool(GenerationLoopTool):
    icon = "timer"
    name = "wait_for_generation"
    read_only = True
    user_description = "Waits for one of your generations to finish, for a bounded time."

    @property
    def description(self) -> str:
        return (
            "Wait for one of your generations to finish, then return its status like get_generation_status. "
            f"Returns early when it finishes, otherwise after 'timeout_seconds' (default {WAIT_DEFAULT_SECONDS}, "
            f"at most {WAIT_MAX_SECONDS}) with timed_out true: call it again to keep waiting. When finished is "
            "true call get_generation_results."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "generation_id": self._generation_id_property(),
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": WAIT_MAX_SECONDS,
                    "default": WAIT_DEFAULT_SECONDS,
                },
            },
            "required": ["generation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id = _generation_id(kwargs)
        if not generation_id:
            return ToolResult(success=False, data="", error="'generation_id' is required")
        try:
            timeout = int(kwargs.get("timeout_seconds") or WAIT_DEFAULT_SECONDS)
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'timeout_seconds' must be an integer.")
        timeout = min(max(1, timeout), WAIT_MAX_SECONDS)
        deadline = time.monotonic() + timeout
        try:
            registry = waits_for(context.generation_orchestrator.status_tracker)
        except AttributeError:
            return _unavailable(RunLookupUnavailable("Generation is not available on this server."))
        event = registry.enter(context.user_id, generation_id)
        try:
            while True:
                run = await find_run(context, generation_id)
                if run is None:
                    return _not_found(generation_id)
                if str(run.get("status")) in TERMINAL_STATES:
                    break
                remaining = deadline - time.monotonic()
                if event is None or remaining <= 0:
                    break
                await _await_signal(event, min(WAIT_RECHECK_SECONDS, remaining))
        except RunLookupUnavailable as exc:
            return _unavailable(exc)
        finally:
            if event is not None:
                registry.leave(context.user_id, generation_id, event)
        payload = await status_payload(context, generation_id, run)
        payload["timed_out"] = not payload["finished"]
        if event is None and not payload["finished"]:
            payload["note"] = (
                f"Too many waits are open for this account (at most {MAX_CONCURRENT_WAITS}); this is the current "
                "status. Try again shortly."
            )
        return ToolResult(success=True, data=json.dumps(payload))


class CancelGenerationTool(GenerationLoopTool):
    icon = "square"
    name = "cancel_generation"
    user_description = "Cancels one of your queued or running generations."

    @property
    def description(self) -> str:
        return (
            "Cancel one of your generations that is queued or running. A queued one never starts; a running "
            "one stops at its next safe point. A finished generation cannot be cancelled."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"generation_id": self._generation_id_property()},
            "required": ["generation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id = _generation_id(kwargs)
        if not generation_id:
            return ToolResult(success=False, data="", error="'generation_id' is required")
        try:
            run = await find_run(context, generation_id)
        except RunLookupUnavailable as exc:
            return _unavailable(exc)
        if run is None:
            return _not_found(generation_id)
        status = str(run.get("status"))
        if status in TERMINAL_STATES:
            return ToolResult(
                success=False, data="", error=f"Generation '{generation_id}' already {status}; there is nothing to cancel."
            )
        orchestrator = context.generation_orchestrator
        try:
            cancelled = await orchestrator.cancel_generation(generation_id)
        except Exception as exc:
            logger.error("cancel_generation failed for %s: %s", generation_id, exc, exc_info=True)
            return ToolResult(success=False, data="", error=unexpected("cancel_generation", "cancel", exc, context.is_admin))
        if not cancelled:
            return ToolResult(
                success=False,
                data="",
                error=f"Generation '{generation_id}' could not be cancelled right now. Check get_generation_status and try again.",
            )
        broadcaster = getattr(orchestrator, "output_broadcaster", None)
        if broadcaster is not None:
            try:
                await broadcaster.broadcast_cancelled(generation_id)
            except Exception:
                logger.warning("could not announce the cancel of %s", generation_id, exc_info=True)
        record = await orchestrator.get_generation_status(generation_id)
        payload: Dict[str, Any] = {"generation_id": generation_id, "status": "cancelled", "message": "Generation cancelled."}
        notice = getattr(record, "cancel_notice", None)
        if notice:
            payload["cancel_notice"] = notice
        return ToolResult(success=True, data=json.dumps(payload))


async def _await_signal(event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(event.wait(), seconds)
    except asyncio.TimeoutError:
        pass


def _first(values: Any) -> Any:
    if isinstance(values, list):
        return values[0] if values else None
    return values


def _link(context: ToolContext, file_id: str) -> str:
    base = resolve_public_base_url(context.settings, context.session_metadata.get(REQUEST_ORIGIN_KEY))
    return f"{base}{MEDIA_LINK_PATH.format(file_id=file_id)}"


def _fetch_images(store: Any, file_ids: List[str], sizes: Tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
    blocks: Dict[str, Dict[str, Any]] = {}
    spent = 0
    for file_id in file_ids:
        for size in sizes:
            stored = store.served_size(file_id, size)
            if stored is None or stored > RESULT_IMAGE_BYTES or spent + stored > RESULT_TOTAL_BYTES:
                continue
            try:
                result = store.get_file_by_id(file_id, size=size)
            except ValueError:
                break
            content = result.content or b""
            spent += len(content)
            blocks[file_id] = {
                "type": "image",
                "data": base64.b64encode(content).decode("ascii"),
                "mimeType": result.media_type,
            }
            break
    return blocks


class GetGenerationResultsTool(GenerationLoopTool):
    icon = "image"
    name = "get_generation_results"
    read_only = True
    user_description = "Returns the pictures and files of one of your finished generations."

    @property
    def description(self) -> str:
        return (
            "Get the outputs of one of your finished generations. Images come back as image content "
            f"(at most 'max_images', default {RESULT_IMAGES_DEFAULT}, up to {RESULT_IMAGES_MAX}, each kept small); "
            "every other output (more images, videos, audio, meshes) comes back as a resource link. The text "
            "part lists each file and gives the seed and the resolved prompt. Call it after "
            "wait_for_generation or get_generation_status reports finished."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "generation_id": self._generation_id_property(),
                "max_images": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": RESULT_IMAGES_MAX,
                    "default": RESULT_IMAGES_DEFAULT,
                    "description": "How many images to return inline; the rest are links.",
                },
                "image_size": {"type": "string", "enum": list(IMAGE_SIZES), "default": "large"},
            },
            "required": ["generation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id = _generation_id(kwargs)
        if not generation_id:
            return ToolResult(success=False, data="", error="'generation_id' is required")
        try:
            max_images = int(RESULT_IMAGES_DEFAULT if kwargs.get("max_images") is None else kwargs["max_images"])
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'max_images' must be an integer.")
        max_images = min(max(0, max_images), RESULT_IMAGES_MAX)
        image_size = kwargs.get("image_size") or "large"
        if image_size not in IMAGE_SIZES:
            return ToolResult(success=False, data="", error=f"'image_size' must be one of {', '.join(IMAGE_SIZES)}.")
        facade = context.generation_history_facade
        if facade is None:
            return ToolResult(success=False, data="", error="Generation history is not available on this server.")
        try:
            run = await find_run(context, generation_id)
        except RunLookupUnavailable as exc:
            return _unavailable(exc)
        if run is None:
            return _not_found(generation_id)
        status = await status_payload(context, generation_id, run)
        if status["status"] == "failed":
            return ToolResult(success=False, data="", error=status.get("error") or "The generation failed.")
        if not status["finished"]:
            return ToolResult(
                success=False,
                data="",
                error=f"Generation '{generation_id}' is still {status['status']}. Call wait_for_generation first.",
            )
        try:
            detail = await facade.get_by_id_async(generation_id, context.user_id, True, False)
        except GenerationNotFoundException:
            return _not_found(generation_id)
        except HistoryExecutorSaturated:
            return ToolResult(success=False, data="", error="The server is busy reading history. Try again shortly.", retryable=True)

        files = [f for f in detail.get("files") or [] if f.get("id")]
        finals = [f for f in files if f.get("is_final")]
        files = finals or files
        images = [f for f in files if f.get("file_type") == "image"]
        inline_ids = [f["id"] for f in images[:max_images]]
        blocks: Dict[str, Dict[str, Any]] = {}
        if inline_ids and context.media_store is not None:
            blocks = await asyncio.to_thread(_fetch_images, context.media_store, inline_ids, IMAGE_SIZES[image_size])

        entries: List[Dict[str, Any]] = []
        content: List[Dict[str, Any]] = []
        for index, file in enumerate(files):
            file_id = file["id"]
            url = _link(context, file_id)
            delivered = "image" if file_id in blocks else "link"
            entry = {
                "index": index,
                "file_id": file_id,
                "type": file.get("file_type"),
                "mime_type": file.get("mime_type"),
                "width": file.get("width"),
                "height": file.get("height"),
                "duration_seconds": file.get("duration_seconds"),
                "delivered_as": delivered,
            }
            if delivered == "link":
                entry["url"] = url
                content.append({
                    "type": "resource_link",
                    "uri": url,
                    "name": PurePosixPath(file.get("file_path") or file_id).name,
                    **({"mimeType": file["mime_type"]} if file.get("mime_type") else {}),
                })
            else:
                content.append(blocks[file_id])
            entries.append({k: v for k, v in entry.items() if v is not None})

        parameters = detail.get("parameters") or {}
        seeds = parameters.get(PARAM_SEED)
        prompts = parameters.get(PARAM_PROMPT)
        negatives = parameters.get(PARAM_NEGATIVE)
        seed = _first(seeds)
        payload = {
            "generation_id": generation_id,
            "status": status["status"],
            "preset_id": detail.get("preset_id"),
            "mode": detail.get("mode"),
            "seed": seed if seed is not None else detail.get("seed"),
            "prompt": _first(prompts),
            "negative_prompt": _first(negatives),
            "seeds": seeds if isinstance(seeds, list) and len(seeds) > 1 else None,
            "prompts": prompts if isinstance(prompts, list) and len(prompts) > 1 else None,
            "files": entries,
            "details_url": generation_details_url(
                generation_id, context.settings, context.session_metadata.get(REQUEST_ORIGIN_KEY)
            ),
        }
        if not entries:
            payload["message"] = "This generation produced no files."
        return ToolResult(
            success=True,
            data=json.dumps({k: v for k, v in payload.items() if v is not None}),
            mcp_content=content or None,
        )


def _decode_upload(encoded: str) -> bytes:
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    return base64.b64decode("".join(encoded.split()), validate=True)


class UploadMediaTool(GenerationLoopTool):
    icon = "upload"
    name = "upload_media"
    user_description = "Adds a picture, video or audio file from the client to your library."

    @property
    def description(self) -> str:
        return (
            "Upload a file from the client into the user's library so a generation can use it, for example "
            "an image for an image-to-image or reference field. Send the bytes as base64 in 'data_base64' "
            f"(at most {UPLOAD_MAX_BYTES // (1024 * 1024)} MB of file). Images (PNG, JPEG, WebP, GIF), video "
            "(MP4, WebM, MOV) and audio (WAV, MP3, OGG, FLAC, M4A) are accepted; the content is checked, not "
            "the name. Returns 'path': pass it verbatim as the value of a media field in start_generation's "
            "form_overrides."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "A name for the file, e.g. 'reference.png'."},
                "data_base64": {"type": "string", "description": "The file's bytes, base64 encoded."},
                "mime_type": {"type": "string", "description": "Optional. Checked against the content when given."},
            },
            "required": ["filename", "data_base64"],
        }

    @staticmethod
    def _prepare(encoded: str, filename: str, declared: Optional[str]) -> Tuple[Optional[bytes], str, str, Optional[str]]:
        try:
            data = _decode_upload(encoded)
        except (binascii.Error, ValueError):
            return None, "", "", "'data_base64' is not valid base64."
        if not data:
            return None, "", "", "The file is empty."
        if len(data) > UPLOAD_MAX_BYTES:
            return None, "", "", f"The file is larger than {UPLOAD_MAX_BYTES // (1024 * 1024)} MB."
        sniffed = sniff_media(data)
        if sniffed is None:
            return None, "", "", (
                "The content is not a supported picture, video or audio file "
                "(PNG, JPEG, WebP, GIF, MP4, WebM, MOV, WAV, MP3, OGG, FLAC, M4A)."
            )
        mime, extension = sniffed
        if declared and declared.split("/")[0].lower() != mime.split("/")[0]:
            return None, "", "", f"'mime_type' says {declared} but the content is {mime}."
        stem = PurePosixPath(filename.replace("\\", "/")).stem or "upload"
        return data, f"{stem}{extension}", mime, None

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        filename, encoded = kwargs.get("filename"), kwargs.get("data_base64")
        if not isinstance(filename, str) or not filename.strip():
            return ToolResult(success=False, data="", error="'filename' is required")
        if not isinstance(encoded, str) or not encoded:
            return ToolResult(success=False, data="", error="'data_base64' is required")
        if len(encoded) > UPLOAD_MAX_BYTES * 4 // 3 + 1024:
            return ToolResult(
                success=False, data="", error=f"The file is larger than {UPLOAD_MAX_BYTES // (1024 * 1024)} MB."
            )
        store = context.media_store
        if store is None:
            return ToolResult(success=False, data="", error="Uploads are not available on this server.")
        declared = kwargs.get("mime_type") if isinstance(kwargs.get("mime_type"), str) else None
        data, stored_name, mime, problem = await asyncio.to_thread(self._prepare, encoded, filename, declared)
        if problem:
            return ToolResult(success=False, data="", error=problem)
        try:
            await asyncio.to_thread(store.check_upload_size, context.user_id, len(data), UPLOAD_PURPOSE_USER, mime)
            result = await store.upload_media(
                file_data=data,
                filename=stored_name,
                content_type=mime,
                user_id=context.user_id,
                purpose=UPLOAD_PURPOSE_USER,
            )
        except (ValueError, PassThrough) as exc:
            return ToolResult(success=False, data="", error=str(exc))
        except Exception as exc:
            logger.error("upload_media failed: %s", exc, exc_info=True)
            return ToolResult(success=False, data="", error=unexpected("upload_media", "upload", exc, context.is_admin))
        payload = {
            "path": f"uploads/{result.filename}",
            "id": result.id,
            "file_name": stored_name,
            "media_type": mime.split("/")[0],
            "mime_type": mime,
            "size": result.size,
            "width": result.width,
            "height": result.height,
            "duration_seconds": result.duration_seconds,
            "fps": result.fps,
            "message": "Uploaded to your library. Pass 'path' as the media field value in start_generation's form_overrides.",
        }
        return ToolResult(success=True, data=json.dumps({k: v for k, v in payload.items() if v is not None}))


GENERATION_LOOP_TOOLS = (
    GetGenerationStatusTool,
    WaitForGenerationTool,
    CancelGenerationTool,
    GetGenerationResultsTool,
    UploadMediaTool,
)
