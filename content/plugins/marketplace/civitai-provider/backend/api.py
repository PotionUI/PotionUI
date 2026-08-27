import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from src.plugin_api import GenerationNotFoundException, User, get_container, get_current_active_user

from .a1111 import build_a1111_parameters, inject_a1111_parameters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/civitai-provider", tags=["CivitAI Provider"])


@router.get("/export-png")
async def export_png(
    generation_id: str,
    index: int = 0,
    current_user: User = Depends(get_current_active_user),
):
    """Download one generated image as a PNG carrying an A1111-style
    `parameters` chunk, so dropping it onto Civitai auto-fills the upload
    form. Ownership is enforced end to end: every lookup below is scoped to
    `current_user.id`, and any failure - not found, not owned, not an image,
    missing on disk - collapses to a 404 rather than leaking which case it was.
    """
    container = get_container()
    history = container.generation_history_facade

    try:
        generation = history.get_by_id(generation_id, current_user.id, include_files=True)
    except GenerationNotFoundException:
        raise HTTPException(status_code=404, detail="Generation not found")

    files = generation.get("files") or []
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="Generation not found")

    file_record = files[index]
    file_type = (file_record.get("file_type") or "").upper()
    if file_type != "IMAGE":
        raise HTTPException(status_code=400, detail="Only images can be exported for Civitai")

    filename = os.path.basename(file_record.get("file_path") or "")
    if not filename:
        raise HTTPException(status_code=404, detail="Generation not found")

    try:
        params_result = history.query.get_params(generation_id, index, current_user.id)
        parameters = dict(params_result.get("parameters") or {})
        if not parameters:
            # Derived files (e.g. the inline-enhance pass) sit at indices past
            # the base batch and have no parameter rows of their own - fall
            # back to the source image's params (index - quantity).
            #
            # `quantity` is a submitted FORM field, not a top-level column on
            # the generation - history_query.get_by_id() never sets a
            # top-level "quantity" key (see Generation.to_dict()), so it must
            # be read out of form_data. Reading generation.get("quantity")
            # here always returns None/0 and this whole fallback branch is
            # dead code in production.
            form_data = generation.get("form_data") or {}
            quantity = form_data.get("quantity") or 0
            if quantity and index >= quantity:
                params_result = history.query.get_params(
                    generation_id, index - quantity, current_user.id
                )
                parameters = dict(params_result.get("parameters") or {})
    except GenerationNotFoundException:
        raise HTTPException(status_code=404, detail="Generation not found")
    except Exception:
        logger.exception(
            "Failed to resolve export parameters for generation %s index %s",
            generation_id, index,
        )
        raise

    try:
        media_result = container.media_store.get_generation_media(
            generation_id, filename, user_id=current_user.id
        )
        with open(media_result.file_path, "rb") as f:
            original_png = f.read()
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Generation not found")

    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(original_png)) as img:
            # The file's real pixels are the ground truth for Size - the
            # recorded resolution param is the FORM value, which is wrong for
            # derived files (enhance upscales) and absent for some presets.
            parameters["resolution"] = f"{img.width}x{img.height}"
    except Exception:
        pass

    parameters_text = build_a1111_parameters(
        parameters, params_result.get("models", [])
    )
    exported_png = inject_a1111_parameters(original_png, parameters_text)

    return Response(
        content=exported_png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
