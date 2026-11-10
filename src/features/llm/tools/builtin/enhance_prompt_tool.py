"""Prompt enhancement tool: routes conversational enhancement requests into the pipeline."""

import json
from typing import Any, Dict, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.model_info_tool import model_visible
from src.features.llm.tools.builtin.utils import allowed_model_ids, video_director_active
from src.features.presets.operations.access import preset_available_to_user


class EnhancePromptTool(BaseTool):
    """Runs the staged enhancement pipeline and returns a finished rich prompt."""

    modes = ["generation"]
    icon = "sparkles"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        # The result is taught to be applied via the update_segment tag, which
        # has no meaning once the Video Director owns "segment #N" (a shot).
        return not video_director_active(form_state)

    name = "enhance_prompt"
    group = "Prompt writing"
    user_description = "Rewrites your prompt into a stronger version tuned to your model."

    @property
    def description(self) -> str:
        return (
            "Enhance an image generation prompt via a dedicated multi-step creative pipeline "
            "that grounds on the active model's metadata, community examples, and the user's "
            "approved prompts. Returns a finished rich prompt. Pass a brief; in chat it falls "
            "back to the open tab's prompt segments. Without an open Generate tab, pass "
            "model_id (from search_models) and/or preset_id (from list_presets) to ground the "
            "result on that model and preset. Use it when the user wants a richer prompt; "
            "do not rewrite it yourself first. Present the result verbatim in an "
            "update_segment tag."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "brief": {
                    "type": "string",
                    "description": (
                        "Short description of the desired image. If omitted, the user's "
                        "current prompt segments are used as the brief."
                    ),
                },
                "model_id": {
                    "type": "string",
                    "description": "Model to ground on, when no Generate tab is open.",
                },
                "preset_id": {
                    "type": "string",
                    "description": "Preset whose prompting guide to follow, when no Generate tab is open.",
                },
                "n_candidates": {
                    "type": "integer",
                    "description": "Number of enhanced prompts to produce (default 1).",
                    "default": 1,
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_enhancement_manager:
            return ToolResult(success=False, data="", error="Prompt enhancement not available")
        if not context.llm_id:
            return ToolResult(
                success=False, data="",
                error="No default LLM is configured on this server, so prompts cannot be enhanced.",
            )

        brief = (kwargs.get("brief") or "").strip()
        if not brief:
            brief = self._brief_from_segments(context)
        if not brief:
            return ToolResult(
                success=False, data="",
                error="No prompt to enhance. Pass 'brief' with a short description of the image.",
            )

        try:
            n_candidates = max(1, min(int(kwargs.get("n_candidates") or 1), 3))
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'n_candidates' must be an integer from 1 to 3.")
        form_state = context.session_metadata.get("form_state")
        if not isinstance(form_state, dict):
            form_state, error = self._grounding_form_state(context, kwargs.get("model_id"), kwargs.get("preset_id"))
            if error:
                return ToolResult(success=False, data="", error=error)

        from src.features.prompt_enhancement import operations as prompt_enhancement_operations

        result = await prompt_enhancement_operations.enhance(
            context.prompt_enhancement_manager,
            user_id=context.user_id,
            llm_id=context.llm_id,
            brief=brief,
            form_state=form_state,
            n_candidates=n_candidates,
        )

        candidates = [c["text"] for c in result.get("candidates", []) if c.get("text")]
        if not candidates:
            return ToolResult(success=False, data="", error="The enhancement pipeline returned no candidates")

        payload = {
            "enhanced_prompt": candidates[0],
            "instruction": (
                "Present this prompt to the user EXACTLY as-is, wrapped in the "
                "segment-update tag with every attribute quoted, targeting their "
                "positive segment: "
                '<tool_action type="update_segment" segment_index="N" segment_id="ID">'
                "the prompt</tool_action>. This tag is only for update_segment; every "
                "other tool is called as a real tool call. Do not shorten, rephrase, "
                "or summarize the prompt."
            ),
        }
        if not context.chat_session:
            payload.pop("instruction")
        if len(candidates) > 1:
            payload["alternative_prompts"] = candidates[1:]
        return ToolResult(success=True, data=json.dumps(payload))

    @staticmethod
    def _grounding_form_state(
        context: ToolContext, model_id: Optional[str], preset_id: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not model_id and not preset_id:
            return None, None
        form_state: Dict[str, Any] = {"form_data": {}}
        if model_id:
            if not context.model_index_manager or not model_visible(context, model_id, allowed_model_ids(context)):
                return None, f"Model '{model_id}' not found. Call search_models to find model ids."
            form_state["form_data"]["model"] = f"model:{model_id}"
        if preset_id:
            collaborators = context.preset_collaborators
            if collaborators is None:
                return None, "Presets are not available on this server."
            allowed = preset_available_to_user(collaborators.db_repo, context.user_id, preset_id, context.is_admin)
            if not allowed or not collaborators.file_repo.find_preset_by_id(preset_id):
                return None, f"No preset '{preset_id}'. Call list_presets to see the preset ids you can use."
            form_state["preset"] = preset_id
        return form_state, None

    @staticmethod
    def _brief_from_segments(context: ToolContext) -> str:
        segments = context.session_metadata.get("segments") or []
        parts = []
        for seg in segments:
            if seg.get("enabled") is False or seg.get("isDisabled"):
                continue
            if (seg.get("type") or "content") == "negative":
                continue
            text = (seg.get("content") or "").strip()
            if text:
                parts.append(text)
        return " ".join(parts)
