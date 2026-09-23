import logging
import os
from tempfile import SpooledTemporaryFile
from typing import Any, Dict, List, NoReturn, Optional, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from src.plugin_api import (
    GenerationNotFoundException,
    ProviderConnectionError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    User,
    ensure_providers_discovered,
    get_container,
    get_current_active_user,
    get_current_admin_user,
    get_model_provider_info,
    import_prompts_for_user,
    list_user_ids,
)

from .a1111 import build_a1111_parameters, inject_a1111_parameters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/civitai-provider", tags=["CivitAI Provider"])

_CIVITAI_PROVIDER_ID = "civitai"
_PROMPT_SOURCE_PROVIDER = "civitai-provider"

_SORT_OPTIONS = [
    "Most Reactions", "Most Comments", "Most Collected", "Newest", "Oldest", "Random", "Recently Added",
]
_PERIOD_OPTIONS = ["AllTime", "Year", "Month", "Week", "Day"]
_NSFW_LEVELS = ["None", "Soft", "Mature", "X"]
_LIMIT_MAX = 200
_PROMPT_FETCH_DEFAULTS = {
    "sort": "Most Reactions", "period": "AllTime", "nsfw": "None",
    "limit": 50, "include_showcase": True, "include_negative": True,
}
_MAX_PAGES_PER_MODEL = 10
_EXPORT_ZIP_SPOOL_MAX_BYTES = 25 * 1024 * 1024
_EXPORT_ZIP_STREAM_CHUNK_BYTES = 1024 * 1024
_EXPORT_ID_SHORT_LEN = 8


class PromptsFetchRequest(BaseModel):
    model_ids: List[str] = []
    user_ids: Optional[List[str]] = None
    sort: str = "Most Reactions"
    period: str = "AllTime"
    nsfw: Optional[str] = None
    limit: int = 50
    include_showcase: bool = True
    include_negative: bool = True


class ExportZipRequest(BaseModel):
    generation_ids: List[str] = []


def _invalid_request(message: str) -> NoReturn:
    raise HTTPException(status_code=400, detail={"success": False, "error": "invalid_request", "message": message})


def _normalize_prompt_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _prepare_import_entries(
    deduped_items: List[Dict[str, Any]], *, model_id: str, model_name: Optional[str], include_negative: bool,
) -> List[Dict[str, Any]]:
    entries = []
    for item in deduped_items:
        stats = item.get("stats") or {}
        width, height = item.get("width"), item.get("height")
        entries.append({
            "prompt": item.get("prompt"),
            "negative_prompt": item.get("negative_prompt") if include_negative else None,
            "source_id": item.get("source_id"),
            "source_url": item.get("source_url"),
            "model_id": model_id,
            "model_name": item.get("model_name") or model_name,
            "base_model": item.get("base_model"),
            "cfg_scale": item.get("cfg_scale"),
            "steps": item.get("steps"),
            "sampler": item.get("sampler"),
            "width": width,
            "height": height,
            "nsfw": bool(item.get("nsfw", False)),
            "tags": item.get("tags") or [],
            "metadata": {
                "sampler": item.get("sampler"),
                "steps": item.get("steps"),
                "cfg_scale": item.get("cfg_scale"),
                "seed": item.get("seed"),
                "size": f"{width}x{height}" if width and height else None,
                "clip_skip": item.get("clip_skip"),
                "likes": stats.get("like_count"),
                "username": item.get("username"),
            },
        })
    return entries


class _ExportSkip(Exception):
    def __init__(self, reason: str, *, not_found: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.not_found = not_found


def _resolve_export_png(
    container: Any,
    generation: Dict[str, Any],
    generation_id: str,
    index: int,
    user_id: str,
) -> Tuple[bytes, str]:
    files = generation.get("files") or []
    if index < 0 or index >= len(files):
        raise _ExportSkip("file index out of range", not_found=True)

    file_record = files[index]
    file_type = (file_record.get("file_type") or "").upper()
    if file_type != "IMAGE":
        raise _ExportSkip(f"not an image ({file_type.lower() or 'unknown type'})")

    filename = os.path.basename(file_record.get("file_path") or "")
    if not filename:
        raise _ExportSkip("missing file path", not_found=True)

    history = container.generation_history_facade
    try:
        params_result = history.query.get_params(generation_id, index, user_id)
        parameters = dict(params_result.get("parameters") or {})
        if not parameters:
            form_data = generation.get("form_data") or {}
            quantity = form_data.get("quantity") or 0
            if quantity and index >= quantity:
                params_result = history.query.get_params(
                    generation_id, index - quantity, user_id
                )
                parameters = dict(params_result.get("parameters") or {})
    except GenerationNotFoundException:
        raise _ExportSkip("generation not found", not_found=True)
    except Exception:
        logger.exception(
            "Failed to resolve export parameters for generation %s index %s",
            generation_id, index,
        )
        raise

    try:
        media_result = container.media_store.get_generation_media(
            generation_id, filename, user_id=user_id
        )
        with open(media_result.file_path, "rb") as f:
            original_bytes = f.read()
    except (ValueError, OSError):
        raise _ExportSkip("file missing on disk", not_found=True)

    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(original_bytes)) as img:
            parameters["resolution"] = f"{img.width}x{img.height}"
    except Exception:
        pass

    parameters_text = build_a1111_parameters(parameters, params_result.get("models", []))
    exported_png = inject_a1111_parameters(original_bytes, parameters_text)
    return exported_png, filename


@router.get("/export-png")
async def export_png(
    generation_id: str,
    index: int = 0,
    current_user: User = Depends(get_current_active_user),
):
    container = get_container()
    history = container.generation_history_facade

    try:
        generation = history.get_by_id(generation_id, current_user.id, include_files=True)
    except GenerationNotFoundException:
        raise HTTPException(status_code=404, detail="Generation not found")

    try:
        exported_png, filename = _resolve_export_png(
            container, generation, generation_id, index, current_user.id
        )
    except _ExportSkip as skip:
        if skip.not_found:
            raise HTTPException(status_code=404, detail="Generation not found")
        raise HTTPException(status_code=400, detail="Only images can be exported for Civitai")

    return Response(
        content=exported_png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _short_generation_id(generation_id: str) -> str:
    return (generation_id or "generation")[:_EXPORT_ID_SHORT_LEN]


def _close_spooled_file(spooled_file: SpooledTemporaryFile) -> None:
    try:
        spooled_file.close()
    except Exception:
        logger.exception("Failed to close spooled civitai zip export file")


class _SpooledZipResponse(StreamingResponse):
    def __init__(self, spooled_file: SpooledTemporaryFile, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._spooled_file = spooled_file

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            _close_spooled_file(self._spooled_file)


@router.post("/export-zip")
async def export_zip(
    request: ExportZipRequest,
    current_user: User = Depends(get_current_active_user),
):
    if not request.generation_ids:
        _invalid_request("generation_ids must not be empty")

    container = get_container()
    history = container.generation_history_facade

    report_lines: List[str] = []
    exported = 0

    spooled_file = SpooledTemporaryFile(max_size=_EXPORT_ZIP_SPOOL_MAX_BYTES)
    try:
        with ZipFile(spooled_file, mode="w", compression=ZIP_DEFLATED) as archive:
            for generation_id in request.generation_ids:
                try:
                    generation = history.get_by_id(generation_id, current_user.id, include_files=True)
                except GenerationNotFoundException:
                    report_lines.append(f"{generation_id}: not found")
                    continue

                files = generation.get("files") or []
                if not files:
                    report_lines.append(f"{generation_id}: no files")
                    continue

                short_id = _short_generation_id(generation_id)
                for index in range(len(files)):
                    try:
                        exported_png, _ = _resolve_export_png(
                            container, generation, generation_id, index, current_user.id
                        )
                    except _ExportSkip as skip:
                        report_lines.append(f"{generation_id} [{index}]: {skip.reason}")
                        continue
                    except Exception:
                        logger.exception(
                            "Failed to export generation %s index %s for civitai zip",
                            generation_id, index,
                        )
                        report_lines.append(f"{generation_id} [{index}]: internal error")
                        continue

                    archive.writestr(f"{short_id}_{index}.png", exported_png)
                    exported += 1

            report_body = f"{exported} image(s) exported.\n"
            if report_lines:
                report_body += "\n" + "\n".join(report_lines) + "\n"
            archive.writestr("export-report.txt", report_body)
    except Exception:
        _close_spooled_file(spooled_file)
        raise

    spooled_file.seek(0)
    filename = f"civitai-export-{exported}.zip"

    def iter_chunks():
        while True:
            chunk = spooled_file.read(_EXPORT_ZIP_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    return _SpooledZipResponse(
        spooled_file,
        iter_chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/prompts/options")
async def get_prompt_fetch_options(current_user: User = Depends(get_current_admin_user)):
    return {
        "success": True,
        "data": {
            "sorts": _SORT_OPTIONS,
            "periods": _PERIOD_OPTIONS,
            "nsfw": _NSFW_LEVELS,
            "limit_max": _LIMIT_MAX,
            "defaults": _PROMPT_FETCH_DEFAULTS,
        },
    }


@router.post("/prompts/fetch")
async def fetch_prompts(
    request: PromptsFetchRequest,
    current_user: User = Depends(get_current_admin_user),
):
    """Pull community prompts for each of `request.model_ids` from CivitAI and
    save them into the prompt library for `request.user_ids` (every user when
    omitted). Runs to completion per model - a model with no CivitAI link, or
    that fails on the network, is reported in its own `error` field rather
    than aborting the other models in the batch.
    """
    if not request.model_ids:
        _invalid_request("model_ids must not be empty")
    if request.user_ids is not None and not request.user_ids:
        _invalid_request("user_ids must not be empty when provided")
    if request.sort not in _SORT_OPTIONS:
        _invalid_request(f"Unknown sort: {request.sort}")
    if request.period not in _PERIOD_OPTIONS:
        _invalid_request(f"Unknown period: {request.period}")
    if request.nsfw is not None and request.nsfw not in _NSFW_LEVELS:
        _invalid_request(f"Unknown nsfw level: {request.nsfw}")
    if not (1 <= request.limit <= _LIMIT_MAX):
        _invalid_request(f"limit must be between 1 and {_LIMIT_MAX}")

    user_ids = request.user_ids if request.user_ids is not None else list_user_ids()

    registry = await ensure_providers_discovered()
    provider = registry.get_provider(_CIVITAI_PROVIDER_ID)
    if provider is None:
        raise HTTPException(
            status_code=502,
            detail={"success": False, "error": "civitai_unavailable", "message": "CivitAI provider is not available"},
        )

    models_result: List[Dict[str, Any]] = []
    network_failures = 0

    for model_id in request.model_ids:
        entry: Dict[str, Any] = {
            "model_id": model_id, "model_name": None, "version_id": None,
            "images_seen": 0, "with_prompt": 0, "created": 0,
            "skipped_duplicates": 0, "per_user": {}, "error": None,
        }

        link = get_model_provider_info(model_id, provider=_CIVITAI_PROVIDER_ID)
        version_id = (link or {}).get("provider_version_id")
        provider_model_id = (link or {}).get("provider_model_id")
        entry["model_name"] = (link or {}).get("model_name")
        entry["version_id"] = version_id

        if not link or (not version_id and not provider_model_id):
            entry["error"] = "model_not_linked"
            models_result.append(entry)
            continue

        try:
            items: List[Dict[str, Any]] = []
            if request.include_showcase and version_id:
                items.extend(await provider.fetch_showcase_prompts(version_id))

            remaining = request.limit - len(items)
            if remaining > 0:
                items.extend(await provider.fetch_image_prompts(
                    model_id=None if version_id else provider_model_id,
                    model_version_id=version_id,
                    sort=request.sort, period=request.period, nsfw=request.nsfw,
                    limit=remaining, fetch_all=True, max_pages=_MAX_PAGES_PER_MODEL,
                ))
        except ProviderNotFoundError:
            entry["error"] = "model_not_linked"
            models_result.append(entry)
            continue
        except (ProviderConnectionError, ProviderRateLimitError):
            network_failures += 1
            entry["error"] = "civitai_unavailable"
            models_result.append(entry)
            continue

        entry["images_seen"] = len(items)

        seen_texts = set()
        deduped: List[Dict[str, Any]] = []
        for item in items:
            prompt_text = (item.get("prompt") or "").strip()
            if not prompt_text:
                continue
            key = _normalize_prompt_text(prompt_text)
            if key in seen_texts:
                continue
            seen_texts.add(key)
            deduped.append(item)
            if len(deduped) >= request.limit:
                break

        entry["with_prompt"] = len(deduped)

        prepared_entries = _prepare_import_entries(
            deduped, model_id=model_id, model_name=entry["model_name"], include_negative=request.include_negative,
        )

        for user_id in user_ids:
            outcome = await import_prompts_for_user(
                user_id, prepared_entries, source_provider=_PROMPT_SOURCE_PROVIDER,
            )
            entry["per_user"][user_id] = outcome
            entry["created"] += outcome["created"]
            entry["skipped_duplicates"] += outcome["skipped_duplicates"]

        models_result.append(entry)

    if network_failures and network_failures == len(request.model_ids):
        raise HTTPException(
            status_code=502,
            detail={"success": False, "error": "civitai_unavailable", "message": "CivitAI is unavailable"},
        )

    return {"success": True, "data": {"models": models_result, "users": user_ids}}
