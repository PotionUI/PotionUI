"""Tool for extracting active model details from the current form state."""

import json
import logging
from typing import Any, Dict, List

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import (
    build_model_field_metadata,
    resolve_active_models,
)

logger = logging.getLogger(__name__)

# One-sentence role explanations so the LLM knows what each model KIND does
# without fetching descriptions. Keyed by the model record's model_type.
MODEL_TYPE_HINTS = {
    "lora": "A LoRA: a lightweight style/subject add-on applied on top of the main model; weave its trigger words into prompts.",
    "checkpoint": "The main generation model that produces the image or video.",
    "diffusion_model": "The main generation model that produces the image or video.",
    "vae": "The VAE converts between pixels and the model's internal latent space; it is not prompted.",
    "upscaler": "An upscaler model that increases output resolution; it is not prompted.",
    "upscalers": "An upscaler model that increases output resolution; it is not prompted.",
    "clip": "The text encoder that interprets prompts; not prompted directly.",
    "text_encoder": "The text encoder that interprets prompts; not prompted directly.",
    "controlnet": "A ControlNet that guides composition from a reference input.",
    "embedding": "A textual embedding usable as a word inside prompts.",
    "mediapipe": "A detection model used to locate faces or hands for enhancement; it is not prompted.",
    "detection_bbox": "A detection model used to locate faces or hands for enhancement; it is not prompted.",
}


class GetActiveModelsTool(BaseTool):
    """Extracts model details for all models currently selected in the form."""

    modes = ["generation"]
    icon = "boxes"

    @property
    def name(self) -> str:
        return "get_active_models"

    @property
    def group(self) -> str:
        return "Models & presets"

    @property
    def user_description(self) -> str:
        return "Reads details of the models currently selected in your form."

    @property
    def hint(self) -> str:
        return (
            "ALWAYS call this before improving prompts, suggesting tags, or giving generation advice — "
            "each active model may carry admin-authored prompting_guidance plus trigger_words that you MUST "
            "consult and weave into any prompt you write or rewrite for it. Also call when the user asks "
            "about their models, LoRAs, VAE, or checkpoint."
        )

    @property
    def description(self) -> str:
        return (
            "Get details (description, tags, provider info) about the models "
            "currently selected in the user's generation form. Scans form data "
            "for model selections (checkpoint, LoRA, VAE, etc.), looks each one up "
            "in the model index, and returns metadata. Use this to understand what "
            "models the user is working with."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.model_index_manager:
            return ToolResult(success=False, data="", error="Model index manager not available")

        form_state = context.session_metadata.get("form_state")
        if not form_state:
            return ToolResult(success=False, data="", error="No form state available")

        form_data = form_state.get("form_data")
        if not form_data:
            return ToolResult(success=False, data="", error="No form data available")

        try:
            # Get field metadata from schema (labels, model_type) if available
            field_meta = build_model_field_metadata(context.preset_manager, form_state)

            # Deep data — description, tags, provider — is fetchable per model via
            # get_model_info; this survey stays compact so the first tool call does
            # not drown the LLM. The DB record is the authoritative type; the
            # form-schema metadata is the fallback.
            active_models: List[Dict[str, Any]] = []
            for field_name, model_path, weight_value, model_info in resolve_active_models(
                form_data, context.model_index_manager
            ):
                meta = field_meta.get(field_name, {})
                model_type = model_info.get("type") or meta.get("model_type") or "unknown"
                name = model_info.get("filename") or model_path.rsplit("/", 1)[-1]
                entry: Dict[str, Any] = {
                    "field": field_name,
                    "id": model_info.get("id", ""),
                    "name": name,
                    "type": model_type,
                }
                type_hint = MODEL_TYPE_HINTS.get(model_type)
                if type_hint:
                    entry["type_hint"] = type_hint
                if weight_value is not None:
                    entry["weight"] = weight_value
                if model_info.get("trigger_words"):
                    entry["trigger_words"] = model_info["trigger_words"]
                if model_info.get("prompting_guidance"):
                    entry["prompting_guidance"] = model_info["prompting_guidance"]
                if meta.get("ai_hint"):
                    entry["ai_hint"] = meta["ai_hint"]

                active_models.append(entry)

            return ToolResult(
                success=True,
                data=json.dumps({"models": active_models, "count": len(active_models)}),
            )
        except Exception as e:
            logger.error(f"Error getting active models: {e}")
            return ToolResult(success=False, data="", error=str(e))
