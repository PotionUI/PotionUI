"""Form context tools for reading live editor state from session metadata."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.llm.tools.errors import unexpected
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import video_director_active
from src.features.presets import operations
from src.features.llm.tools.builtin.prompt_variables_tool import NO_TAB_OPEN_MESSAGE, active_draft_variables
from src.platform.resources.prompt_variables import render_prompt_variable_summary

logger = logging.getLogger(__name__)

_MEDIA_FIELD_TYPES = ("image", "video", "audio", "media")


def _compact_media_item(item: Any) -> Any:
    """A media-loader item trimmed to what a tool caller needs to ADDRESS it
    (a `form_media` reference: field + path/label) -- `path`, and the
    label/name/type a model would read to pick the right one -- dropping the
    verbose `url`/`relative_path`/`metadata` (width, height, duration, size)
    a form value otherwise carries."""
    if not isinstance(item, dict):
        return item
    compact: Dict[str, Any] = {"path": item.get("path") or item.get("relative_path")}
    for key in ("label", "name", "type"):
        if item.get(key):
            compact[key] = item[key]
    return compact


def _compact_media_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_media_item(v) for v in value]
    return _compact_media_item(value)


_SEGMENT_UPDATE_INSTRUCTION = (
    'To propose changes to segments, include '
    '<tool_action type="update_segment" segment_index="N" segment_id="ID">'
    'new content</tool_action> blocks in your response text. '
    'Existing `#...` tokens in segment content are phrasebook chips — keep them unless '
    'you are intentionally changing them. You may embed new markers obtained from '
    'get_phrasebook_values into your proposed content.'
)


class GetCurrentSegmentsTool(BaseTool):
    """Reads the prompt segments currently loaded in the user's editor."""

    modes = ["generation"]
    icon = "text-cursor-input"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        # With the Video Director active, "segment #N" means a shot
        # (get_video_director), not a prompt segment -- offering both meanings
        # at once is how the model ends up editing the global prompt instead
        # of a shot.
        return not video_director_active(form_state)

    name = "get_current_segments"
    read_only = True
    group = "Form & segments"
    user_description = "Reads the prompt segments currently in your editor."

    @property
    def description(self) -> str:
        return (
            "Read the user's currently loaded prompt segments from their editor. "
            "Segments are the individual building blocks that make up the active prompt. "
            "Each segment has an index, id, content, optional name, type, and enabled state. "
            "A segment applied from a Segment Template also carries a `template` object "
            "(`{id, name, slot, position}`) — `id`/`name` identify the template, `slot` is the "
            "name of the template slot it was applied from, and `position` is that slot's index. "
            "Use it to tell the user which template (and which slot in it) a segment came from, "
            "and cross-reference get_segment_templates to inspect the template itself. Segments "
            "without a `template` field were not applied from a template. "
            "Use this tool to understand what the user has already written before suggesting "
            "edits, additions, or improvements to their prompt. "
            "The per-turn PROMPT STATE system block is only a truncated view of these segments — "
            "call this tool to read their full, untruncated content. "
            "Use each segment's index and id in update_segment tags."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt_index": {
                    "type": "integer",
                    "description": "Index of another prompt in the batch to read; omit for the active prompt.",
                },
            },
            "required": [],
        }

    @staticmethod
    def _prompt_segments(session_metadata: Dict[str, Any], prompt_index: Any) -> Optional[list]:
        batch = session_metadata.get("prompt_batch")
        prompts = batch.get("prompts") if isinstance(batch, dict) else None
        if not isinstance(prompts, list):
            return None
        for entry in prompts:
            if isinstance(entry, dict) and entry.get("index") == prompt_index:
                return entry.get("segments") or []
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        prompt_index = kwargs.get("prompt_index")
        segments_raw = context.session_metadata.get("segments")
        if prompt_index is not None:
            segments_raw = self._prompt_segments(context.session_metadata, prompt_index)
            if segments_raw is None:
                return ToolResult(
                    success=False,
                    data="",
                    error=f"No prompt at index {prompt_index}.",
                )

        if segments_raw is None:
            return ToolResult(
                success=False,
                data="",
                error=(
                    "No segment data available. The user may not have any segments loaded, "
                    "or tools context may not include segment data."
                ),
            )

        result_segments = []
        for idx, seg in enumerate(segments_raw):
            entry = {
                "index": idx,
                "id": seg.get("id", ""),
                "content": seg.get("content", ""),
                "name": seg.get("name", ""),
                "type": seg.get("type", ""),
                "enabled": seg.get("enabled", not seg.get("isDisabled", False)),
            }
            template = seg.get("template")
            if template:
                entry["template"] = template
            result_segments.append(entry)

        return ToolResult(
            success=True,
            data=json.dumps({
                "segments": result_segments,
                "count": len(result_segments),
                **({"prompt_index": prompt_index} if prompt_index is not None else {}),
                "instruction": _SEGMENT_UPDATE_INSTRUCTION
                + (f' Add prompt="{prompt_index}" to each tag to edit this prompt.' if prompt_index is not None else ""),
            }),
        )


class GetFormStateTool(BaseTool):
    """Reads the user's current form state merged with schema metadata."""

    modes = ["generation"]
    icon = "clipboard-list"

    name = "get_form_state"
    read_only = True
    group = "Form & segments"
    user_description = "Reads the current values of your generation form."

    @property
    def description(self) -> str:
        return (
            "Read the user's current form state, including which preset and mode are active, "
            "and the current values of all form fields merged with their schema metadata "
            "(label, type, model_type, etc.). Each field entry shows both its schema info "
            "and current value. For model fields (type 'model'), the value contains a modelPath "
            "that can be used with get_model_info to get model details like description. "
            "If the prompt uses ${name} variables, a 'prompt_variables' list summarizes each "
            "(type, whether it shuffles, is pinned, or re-rolls per image, and for a choice its "
            "option count with the first few options; for a number its range and step)."
            "{{#if get_prompt_variables}} Use get_prompt_variables for the full options."
            "{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        form_state = context.session_metadata.get("form_state")

        if form_state is None:
            return ToolResult(
                success=False,
                data="",
                error="No form state available.",
            )

        try:
            preset_id = form_state.get("preset")
            mode = form_state.get("mode")
            form_data = form_state.get("form_data", {})

            result: Dict[str, Any] = {
                "preset": preset_id,
                "mode": mode,
            }

            # Prompt variables (${name} placeholders) as compact plain-language
            # lines, e.g. "mood: one of noir, sunlit — shuffles each generation".
            variable_lines = (
                render_prompt_variable_summary(active_draft_variables(context), form_state.get("variable_rolls"))
                if form_state.get("tab_id")
                else [NO_TAB_OPEN_MESSAGE]
            )
            if variable_lines:
                result["prompt_variables"] = variable_lines

            # Try to get form schema to merge with values
            schema_props = {}
            if preset_id and context.preset_collaborators:
                try:
                    schema_data = operations.get_form_schema(
                        context.preset_collaborators, preset_id, mode=mode
                    )
                    schema_props = (
                        schema_data.get("form_schema", {}).get("properties", {})
                    )
                except Exception as e:
                    logger.debug(f"Could not load form schema: {e}")

            # Merge: for each field in schema, attach current value from form_data
            merged_fields = {}
            for field_name, field_schema in schema_props.items():
                entry: Dict[str, Any] = {
                    "label": field_schema.get("title", field_name),
                    "type": field_schema.get("type", ""),
                }
                # Add description if available
                if field_schema.get("description"):
                    entry["description"] = field_schema["description"]
                # Add default if available
                if "default" in field_schema:
                    entry["default"] = field_schema["default"]
                # Add slider/number range info
                if field_schema.get("minimum") is not None:
                    entry["minimum"] = field_schema["minimum"]
                if field_schema.get("maximum") is not None:
                    entry["maximum"] = field_schema["maximum"]
                if field_schema.get("step") is not None:
                    entry["step"] = field_schema["step"]
                config = field_schema.get("configuration")
                if config and isinstance(config, dict):
                    model_type = config.get("model_type")
                    if model_type:
                        entry["model_type"] = model_type
                    options = config.get("options")
                    if options and isinstance(options, list):
                        if len(options) <= 50:
                            entry["options"] = [
                                {"label": o.get("label", o.get("value", "")), "value": o.get("value", "")}
                                if isinstance(o, dict) else {"label": str(o), "value": str(o)}
                                for o in options
                            ]
                        else:
                            entry["options_count"] = len(options)
                ai_hint = field_schema.get("ai_hint")
                if ai_hint:
                    entry["ai_hint"] = ai_hint
                if field_schema.get("enum"):
                    enum_vals = field_schema["enum"]
                    if len(enum_vals) <= 50:
                        entry["options"] = [{"label": str(v), "value": v} for v in enum_vals]
                    else:
                        entry["options_count"] = len(enum_vals)
                if field_name in form_data:
                    raw_value = form_data[field_name]
                    entry["value"] = (
                        _compact_media_value(raw_value)
                        if field_schema.get("type") in _MEDIA_FIELD_TYPES
                        else raw_value
                    )
                merged_fields[field_name] = entry

            # Also include form_data fields not in schema (shouldn't happen, but safe)
            for field_name, value in form_data.items():
                if field_name not in merged_fields:
                    merged_fields[field_name] = {"value": value}

            regional = form_data.get("regional_prompt")
            if isinstance(regional, dict) and regional.get("items"):
                result["regional_prompt"] = regional
                merged_fields.pop("regional_prompt", None)

            if merged_fields:
                result["fields"] = merged_fields
            elif form_data:
                # Fallback if schema wasn't available
                result["form_data"] = form_data

            return ToolResult(success=True, data=json.dumps(result))
        except Exception as e:
            logger.error(f"Error reading form state from session metadata: {e}")
            return ToolResult(success=False, data="", error=unexpected(self.name, "run", e, context.is_admin))
