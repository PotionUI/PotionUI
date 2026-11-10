import json
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.errors import teach
from src.features.presets.regional_prompt import offers_regional_prompt, regional_prompt_modes
from src.pipelines.pipes._shared.regional import normalize_regional_prompt


def _percent(value: float) -> int:
    return int(round(value * 100))


def _find_preset(context: ToolContext, preset_id: str) -> Any:
    collaborators = context.preset_collaborators
    if not collaborators or not preset_id:
        return None
    try:
        return collaborators.file_repo.find_preset_by_id(preset_id)
    except Exception:
        return None


def _offer_error(context: ToolContext) -> Optional[str]:
    form_state = context.session_metadata.get("form_state") or {}
    preset_id, mode = form_state.get("preset"), form_state.get("mode")
    if not preset_id or not mode:
        return "No form is open, so there is no preset and mode to place regions on."
    preset = _find_preset(context, preset_id)
    preset_vars = getattr(preset, "vars", None)
    if offers_regional_prompt(preset_vars, mode):
        return None
    modes = regional_prompt_modes(preset_vars)
    if modes:
        return teach(
            f"Mode '{mode}' of this preset does not offer regional prompting",
            f"It is available in: {', '.join(modes)}",
            "Ask the user to switch mode, or describe the layout in the prompt instead",
        )
    return teach(
        "The current preset does not offer regional prompting",
        "Only presets that declare it can place regions",
        "Describe the layout in the prompt instead",
    )


def _build_document(kwargs: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    regions = kwargs.get("regions")
    if not isinstance(regions, list):
        return None, "'regions' is required and must be an array of {x, y, w, h, description}."
    enabled = kwargs.get("enabled", True)
    if not isinstance(enabled, bool):
        return None, "'enabled' must be true or false."
    items: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            return None, f"regions[{index}] must be an object with x, y, w, h and description."
        description = region.get("description")
        if not isinstance(description, str) or not description.strip():
            return None, f"regions[{index}] needs a non-empty 'description'."
        items.append({
            "x": region.get("x"), "y": region.get("y"), "w": region.get("w"), "h": region.get("h"),
            "caption": description.strip(),
        })
    try:
        document = normalize_regional_prompt({"enabled": enabled, "items": items})
    except ValueError as exc:
        return None, teach(
            f"Invalid region: {exc}",
            "x, y, w and h are numbers from 0 to 1 measured from the top-left corner, with w and h above 0",
        )
    return {
        "enabled": document.enabled,
        "items": [
            {"x": item.x, "y": item.y, "w": item.w, "h": item.h, "caption": item.caption}
            for item in document.items
        ],
    }, None


def _build_preview(document: Dict[str, Any]) -> ToolApprovalPreview:
    items = document["items"]
    word = "region" if len(items) == 1 else "regions"
    rows = [
        {
            "position": f"{_percent(item['x'])}%, {_percent(item['y'])}%",
            "size": f"{_percent(item['w'])}% × {_percent(item['h'])}%",
            "text": item["caption"],
        }
        for item in items
    ]
    return ToolApprovalPreview(
        action="Set regional prompt",
        target=f"{len(items)} {word}",
        kind="regions",
        summary=f"{len(items)} {word}" + ("" if document["enabled"] else " · off"),
        rows=rows,
    )


class SetRegionalPromptTool(BaseTool):
    modes = ["generation"]
    icon = "square-dashed"

    name = "set_regional_prompt"
    group = "Prompt writing"
    user_description = "Places described regions on the image canvas for exact placement of objects and text."
    requires_approval = True

    @property
    def description(self) -> str:
        return (
            "Set the regional prompt on the user's Generate form: boxes on the image, each with its own "
            "description, so objects and text land where you place them. Only works when the current "
            "preset and mode offer regional prompting (get_preset_info shows a 'regional_prompt' section). "
            "Boxes are fractions of the image from 0 to 1 measured from the top-left corner. Words in "
            "double quotes inside a description are rendered as exact text in that box. The main prompt "
            "keeps describing the whole image; each description covers only its box. Replaces all current "
            "regions. Pass enabled=false to keep the regions but switch back to the plain text prompt. "
            "The user approves before it is applied."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "regions": {
                    "type": "array",
                    "description": "The full list of regions; overlapping boxes are allowed.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number", "minimum": 0, "maximum": 1, "description": "Left edge as a fraction of the width."},
                            "y": {"type": "number", "minimum": 0, "maximum": 1, "description": "Top edge as a fraction of the height."},
                            "w": {"type": "number", "minimum": 0, "maximum": 1, "description": "Width as a fraction of the width."},
                            "h": {"type": "number", "minimum": 0, "maximum": 1, "description": "Height as a fraction of the height."},
                            "description": {"type": "string", "description": "What this part of the image looks like. Quote exact text to render."},
                        },
                        "required": ["x", "y", "w", "h", "description"],
                    },
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Defaults to true. False keeps the regions but switches back to the text prompt.",
                    "default": True,
                },
            },
            "required": ["regions"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        offer_error = _offer_error(context)
        if offer_error:
            return ToolResult(success=False, data="", error=offer_error)
        document, error = _build_document(kwargs)
        if error:
            return ToolResult(success=False, data="", error=error)
        return ToolResult(
            success=True,
            data=json.dumps({"status": "pending_approval", "regional_prompt": document}),
            preview=_build_preview(document),
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        offer_error = _offer_error(context)
        if offer_error:
            return ToolResult(success=False, data="", error=offer_error)
        document, error = _build_document(kwargs)
        if error:
            return ToolResult(success=False, data="", error=error)
        return ToolResult(
            success=True,
            data=json.dumps({"action": "set_regional_prompt", "regional_prompt": document}),
        )
