import logging
import os
from tempfile import SpooledTemporaryFile
from typing import Any, Dict, List, NoReturn, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from src.plugin_api import (
    GenerationNotFoundException,
    User,
    get_container,
    get_current_active_user,
)

from .a1111 import build_a1111_parameters, inject_a1111_parameters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/a1111-metadata-export", tags=["A1111 Metadata Export"])

_EXPORT_ZIP_SPOOL_MAX_BYTES = 25 * 1024 * 1024
_EXPORT_ZIP_STREAM_CHUNK_BYTES = 1024 * 1024
_EXPORT_ID_SHORT_LEN = 8


class ExportZipRequest(BaseModel):
    generation_ids: List[str] = []


def _invalid_request(message: str) -> NoReturn:
    raise HTTPException(status_code=400, detail={"success": False, "error": "invalid_request", "message": message})


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
        raise HTTPException(status_code=400, detail="Only images can be exported")

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
        logger.exception("Failed to close spooled a1111 zip export file")


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
                            "Failed to export generation %s index %s for a1111 zip",
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
    filename = f"a1111-export-{exported}.zip"

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
