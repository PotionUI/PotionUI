"""Starts a generation from explicit preset/form arguments rather than a live
chat session's form_state (see `run_generation_tool.py` for the chat-form
counterpart). This is the tool an MCP client - which has no Generate form
open - uses to generate: it submits through the same
`generation_orchestrator.start_generation()` the HTTP `/api/generations/start`
route uses, so `bind_form` fills every field the caller didn't set with the
preset's own defaults.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.features.generation.details_url import REQUEST_ORIGIN_KEY, generation_details_url
from src.features.generation.failure import start_failure_reason
from src.features.generation.prompt_batch import MAX_BATCH_IMAGES, PromptBatchError
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import build_generation_preview
from src.features.llm.tools.errors import unexpected
from src.features.llm.tools.media_values import MEDIA_VALUE_FORM, preset_form_media_errors
from src.features.llm.tools.model_values import preset_form_model_errors
from src.features.forms.binding import FormBindingError
from src.features.presets.operations.access import preset_available_to_user
from src.features.presets.regional_prompt import OPTION_KEY, offers_regional_prompt, regional_prompt_modes
from src.pipelines.pipes._shared.regional import normalize_regional_prompt

logger = logging.getLogger(__name__)


class StartGenerationTool(BaseTool):
    """Starts an image/video generation from an explicit preset id and form values."""

    modes = ["generation"]
    icon = "play"
    context_group = "library"

    name = "start_generation"
    mcp = True
    group = "Generation"
    user_description = "Starts a generation from a chosen preset, without needing an open form."
    requires_approval = True

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return not form_state

    @property
    def description(self) -> str:
        return (
            "Start a generation for an explicit preset, independent of any live form state. "
            "Unset fields fall back to the preset's own defaults. Get preset ids from list_presets "
            "and a preset's modes and field names from get_preset_info. Requires user approval; "
            "returns the generation_id once started. For several distinct prompts in one run, pass "
            "'prompts' (a list of {positive, negative?}) instead of 'prompt': they render in one "
            "generation and are much faster than calling this tool once per prompt. 'prompts' and "
            "'prompt' are mutually exclusive; form_overrides.quantity then applies per prompt. Example: "
            '{"preset_id": "<id from list_presets>", "mode": "txt2img", "prompt": "a red fox in snow", '
            '"form_overrides": {"width": 1024, "height": 1024, "steps": 30}}'
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset_id": {"type": "string", "description": "The preset id to generate with."},
                "mode": {"type": "string", "description": "One of the preset's modes (see get_preset_info). Defaults to txt2img when the preset has it, else its first mode."},
                "prompt": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "prompts": {
                    "type": "array",
                    "maxItems": MAX_BATCH_IMAGES,
                    "description": (
                        "Several distinct prompts rendered in one generation, in order. Use instead of "
                        "'prompt'/'negative_prompt'. Each prompt is expanded 'quantity' times; the total "
                        f"image count (prompts x quantity) is capped at {MAX_BATCH_IMAGES}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "positive": {"type": "string"},
                            "negative": {"type": "string"},
                        },
                        "required": ["positive"],
                    },
                },
                "form_overrides": {
                    "type": "object",
                    "description": (
                        "Field overrides on top of the preset's defaults (e.g. {'width': 1024, "
                        "'seed': 12345, 'batch_size': 4}). Field names must match the preset's form "
                        f"fields (see get_preset_info). An image/video/audio/media field takes {MEDIA_VALUE_FORM}. "
                        "If get_preset_info shows a 'regional_prompt' section, set 'regional_prompt' to "
                        "{enabled: true, items: [{x, y, w, h, caption}]} (fractions, top-left origin)."
                    ),
                },
            },
            "required": ["preset_id"],
        }

    @staticmethod
    def _build_preview(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        preview: Dict[str, Any] = {
            "action": "Start generation",
            "preset_id": kwargs.get("preset_id"),
            "mode": kwargs.get("mode") or "txt2img",
        }
        if kwargs.get("prompts"):
            preview["prompts"] = kwargs["prompts"]
        if kwargs.get("prompt"):
            preview["prompt"] = kwargs["prompt"]
        if kwargs.get("negative_prompt"):
            preview["negative_prompt"] = kwargs["negative_prompt"]
        if kwargs.get("form_overrides"):
            preview["form_overrides"] = kwargs["form_overrides"]
        return preview

    @staticmethod
    def _prompt_pairs(kwargs: Dict[str, Any]) -> Tuple[Optional[List[Dict[str, str]]], Optional[str]]:
        prompts = kwargs.get("prompts")
        if prompts is None:
            return None, None
        if kwargs.get("prompt") or kwargs.get("negative_prompt"):
            return None, "Give either 'prompts' or 'prompt'/'negative_prompt', not both."
        if not isinstance(prompts, list) or not prompts:
            return None, "'prompts' must be a non-empty list of {positive, negative} objects."
        if len(prompts) > MAX_BATCH_IMAGES:
            return None, f"'prompts' can hold at most {MAX_BATCH_IMAGES} entries."
        pairs: List[Dict[str, str]] = []
        for index, entry in enumerate(prompts, start=1):
            if not isinstance(entry, dict):
                return None, f"prompts[{index}] must be an object with a 'positive' string."
            positive, negative = entry.get("positive"), entry.get("negative") or ""
            if not isinstance(positive, str) or not positive.strip():
                return None, f"prompts[{index}] needs a non-empty 'positive' string."
            if not isinstance(negative, str):
                return None, f"prompts[{index}].negative must be a string."
            pairs.append({"positive": positive, "negative": negative})
        return pairs, None

    @staticmethod
    def _regional_prompt_error(context: ToolContext, preset_id: str, mode: str, overrides: Dict[str, Any]) -> Optional[str]:
        if OPTION_KEY not in overrides:
            return None
        try:
            preset = context.preset_collaborators.file_repo.find_preset_by_id(preset_id)
        except Exception:
            return None
        preset_vars = getattr(preset, "vars", None)
        if not offers_regional_prompt(preset_vars, mode):
            modes = regional_prompt_modes(preset_vars)
            where = f"It is available in: {', '.join(modes)}." if modes else "This preset does not offer it."
            return f"form_overrides.regional_prompt is not available for mode '{mode}'. {where} Remove it."
        try:
            normalize_regional_prompt(overrides[OPTION_KEY])
        except ValueError as exc:
            return (
                f"form_overrides.regional_prompt is invalid: {exc}. Use "
                "{enabled: true, items: [{x, y, w, h, caption}]} with x, y, w, h as fractions from 0 to 1 "
                "and w, h above 0."
            )
        return None

    @staticmethod
    def _build_approval_preview(kwargs: Dict[str, Any]):
        # No live form here (see the module docstring), so there is no "old"
        # value to diff a form_override against - old_form_data stays None.
        pairs = kwargs.get("prompts")
        if pairs:
            prompt_text = "\n".join(f"{index}. {pair['positive']}" for index, pair in enumerate(pairs, start=1))
            negative_text = "\n".join(
                f"{index}. {pair['negative']}" for index, pair in enumerate(pairs, start=1) if pair.get("negative")
            )
        else:
            prompt_text = kwargs.get("prompt") or ""
            negative_text = kwargs.get("negative_prompt") or ""
        return build_generation_preview(
            preset_id=kwargs.get("preset_id"),
            mode=kwargs.get("mode") or "txt2img",
            prompt_text=prompt_text,
            negative_text=negative_text,
            form_data=kwargs.get("form_overrides") or {},
        )

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Build a generation preview for user approval."""
        preset_id = kwargs.get("preset_id")
        if not preset_id:
            return ToolResult(success=False, data="", error="'preset_id' is required")

        mode, preset_error = self._resolve_preset(context, preset_id, kwargs.get("mode"))
        if preset_error:
            return ToolResult(success=False, data="", error=preset_error)
        pairs, prompts_error = self._prompt_pairs(kwargs)
        if prompts_error:
            return ToolResult(success=False, data="", error=prompts_error)
        kwargs = {**kwargs, "mode": mode, **({"prompts": pairs} if pairs else {})}
        overrides = kwargs.get("form_overrides") or {}
        media_errors = preset_form_media_errors(context.preset_collaborators, context.storage_dir(), preset_id, mode, overrides)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        model_errors = preset_form_model_errors(
            context.preset_collaborators, context.model_index_manager, preset_id, mode, overrides
        )
        if model_errors:
            return ToolResult(success=False, data="", error="; ".join(model_errors))
        regional_error = self._regional_prompt_error(context, preset_id, mode, overrides)
        if regional_error:
            return ToolResult(success=False, data="", error=regional_error)

        return ToolResult(
            success=True,
            data=json.dumps(self._build_preview(kwargs)),
            preview=self._build_approval_preview(kwargs),
        )

    @staticmethod
    def _resolve_preset(context: ToolContext, preset_id: str, mode: Optional[str]) -> Tuple[str, Optional[str]]:
        collaborators = context.preset_collaborators
        if collaborators is None:
            return mode or "txt2img", None
        not_found = f"No preset '{preset_id}'. Call list_presets to see the preset ids you can use."
        try:
            if not preset_available_to_user(collaborators.db_repo, context.user_id, preset_id, context.is_admin):
                return mode or "txt2img", not_found
            found = collaborators.file_repo.find_preset_by_id(preset_id)
        except Exception:
            logger.warning("could not check preset %s before starting", preset_id, exc_info=True)
            return mode or "txt2img", None
        if not found:
            return mode or "txt2img", not_found
        modes = list((getattr(found, "modes", None) or {}).keys())
        if not mode:
            mode = "txt2img" if "txt2img" in modes or not modes else modes[0]
        if modes and mode not in modes:
            return mode, f"Preset '{preset_id}' has no mode '{mode}'. Its modes are: {', '.join(modes)}."
        return mode, None

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Actually start the generation after user approval."""
        if not context.generation_orchestrator:
            return ToolResult(success=False, data="", error="Generation orchestrator not available.")

        preset_id = kwargs.get("preset_id")
        if not preset_id:
            return ToolResult(success=False, data="", error="'preset_id' is required")

        mode, preset_error = self._resolve_preset(context, preset_id, kwargs.get("mode"))
        if preset_error:
            return ToolResult(success=False, data="", error=preset_error)
        pairs, prompts_error = self._prompt_pairs(kwargs)
        if prompts_error:
            return ToolResult(success=False, data="", error=prompts_error)
        form_data = dict(kwargs.get("form_overrides") or {})

        # Re-validated on replay (same reasoning as run_generation): approval
        # is not evidence a path still exists.
        media_errors = preset_form_media_errors(context.preset_collaborators, context.storage_dir(), preset_id, mode, form_data)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        model_errors = preset_form_model_errors(
            context.preset_collaborators, context.model_index_manager, preset_id, mode, form_data
        )
        if model_errors:
            return ToolResult(success=False, data="", error="; ".join(model_errors))
        regional_error = self._regional_prompt_error(context, preset_id, mode, form_data)
        if regional_error:
            return ToolResult(success=False, data="", error=regional_error)

        try:
            from src.features.generation.dto import GenerationRequest, PromptPair

            if pairs:
                prompt_pairs = [PromptPair(positive=pair["positive"], negative=pair["negative"]) for pair in pairs]
            else:
                prompt_pairs = [PromptPair(positive=kwargs.get("prompt") or "", negative=kwargs.get("negative_prompt") or "")]
            request = GenerationRequest(
                preset_id=preset_id,
                mode=mode,
                form_data=form_data,
                prompts=prompt_pairs,
                batch_prompts=bool(pairs) and len(pairs) > 1,
            )

            result = await context.generation_orchestrator.start_generation(
                request=request,
                user_id=context.user_id,
            )

            started_id = result.get("generation_id", "")
            return ToolResult(success=True, data=json.dumps({
                "message": "Generation started successfully",
                "generation_id": started_id,
                "status": result.get("status", {}).get("status", "pending"),
                "details_url": generation_details_url(
                    started_id, context.settings, context.session_metadata.get(REQUEST_ORIGIN_KEY)
                ) if started_id else "",
            }))
        except PromptBatchError as e:
            return ToolResult(success=False, data="", error=str(e))
        except FormBindingError as e:
            return ToolResult(
                success=False,
                data="",
                error=f"The form values were rejected: {e}. Fix those fields and call start_generation again.",
            )
        except Exception as e:
            logger.error(f"Failed to start generation: {e}")
            return ToolResult(success=False, data="", error=unexpected("start_generation", "start the generation", start_failure_reason(e, context.is_admin), True))
