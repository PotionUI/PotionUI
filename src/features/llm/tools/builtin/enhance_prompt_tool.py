"""Prompt enhancement tool: routes conversational enhancement requests into the pipeline."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import video_director_active

logger = logging.getLogger(__name__)


class EnhancePromptTool(BaseTool):
    """Runs the staged enhancement pipeline and returns a finished rich prompt."""

    modes = ["generation"]
    icon = "sparkles"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        # The result is taught to be applied via the update_segment tool, which
        # has no meaning once the Video Director owns "segment #N" (a shot).
        return not video_director_active(form_state)

    @property
    def name(self) -> str:
        return "enhance_prompt"

    @property
    def group(self) -> str:
        return "Prompt writing"

    @property
    def user_description(self) -> str:
        return "Rewrites your prompt into a stronger version tuned to your model."

    @property
    def hint(self) -> str:
        return (
            "When the user wants their prompt made richer — 'improve it', 'make it better', "
            "'expand this', 'help me' — or the prompt is thin or generic. This runs a full "
            "creative pipeline (model grounding, community examples, ideation, writing); do NOT "
            "rewrite the prompt yourself first. Present the returned prompt EXACTLY as-is by "
            "calling the update_segment tool with it."
        )

    @property
    def description(self) -> str:
        return (
            "Enhance an image generation prompt via a dedicated multi-step creative pipeline "
            "that grounds on the active model's metadata, community examples, and the user's "
            "approved prompts. Returns a finished rich prompt. Optionally pass a brief; "
            "otherwise the current prompt segments are used."
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
            return ToolResult(success=False, data="", error="No LLM configuration in context")

        brief = (kwargs.get("brief") or "").strip()
        if not brief:
            brief = self._brief_from_segments(context)
        if not brief:
            return ToolResult(
                success=False, data="",
                error="No prompt to enhance. Ask the user to describe the image or fill a prompt segment.",
            )

        n_candidates = kwargs.get("n_candidates") or 1
        form_state = context.session_metadata.get("form_state")

        try:
            result = await context.prompt_enhancement_manager.enhance(
                user_id=context.user_id,
                llm_id=context.llm_id,
                brief=brief,
                form_state=form_state,
                n_candidates=max(1, min(int(n_candidates), 3)),
            )
        except Exception as e:
            logger.error(f"enhance_prompt failed: {e}")
            return ToolResult(success=False, data="", error=f"Enhancement failed: {e}")

        candidates = [c["text"] for c in result.get("candidates", []) if c.get("text")]
        if not candidates:
            return ToolResult(success=False, data="", error="The enhancement pipeline returned no candidates")

        payload = {
            "enhanced_prompt": candidates[0],
            "instruction": (
                "Present this prompt to the user EXACTLY as-is by calling the "
                "update_segment tool, targeting their positive segment. Do not print "
                "it in your reply text and do not shorten, rephrase, or summarize it."
            ),
        }
        if len(candidates) > 1:
            payload["alternative_prompts"] = candidates[1:]
        return ToolResult(success=True, data=json.dumps(payload))

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
