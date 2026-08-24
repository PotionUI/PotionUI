"""Tool for composing and applying a Prompt Relay video timeline."""

import json
import logging
from typing import Any, Dict, List

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_DURATION = 5
_DEFAULT_FPS = 24


def _normalize_segments(
    segments: List[Dict[str, Any]],
    duration: float,
) -> List[Dict[str, Any]]:
    """Sort segments by start time and clamp them within [0, duration]."""
    normalized = []
    for seg in segments:
        start = max(0.0, min(float(seg["start"]), duration))
        end = max(0.0, min(float(seg["end"]), duration))
        normalized.append({"start": start, "end": end, "text": seg["text"].strip()})
    normalized.sort(key=lambda s: s["start"])
    return normalized


def _validate_segments(
    segments: List[Dict[str, Any]],
    duration: float,
) -> tuple[bool, str, List[str]]:
    """
    Validate the segments list.

    Returns (ok, error_message, warnings).
    Hard errors return ok=False.  Soft issues go into warnings.
    """
    if not segments:
        return False, "segments must be a non-empty array.", []

    warnings: List[str] = []

    for i, seg in enumerate(segments):
        label = f"segments[{i}]"

        # Required keys present
        for key in ("start", "end", "text"):
            if key not in seg:
                return False, f"{label} is missing required field '{key}'.", []

        # start / end must be numeric
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (TypeError, ValueError):
            return (
                False,
                f"{label} 'start' and 'end' must be numbers.",
                [],
            )

        # start must be strictly less than end
        if start >= end:
            return (
                False,
                f"{label} start ({start}) must be less than end ({end}).",
                [],
            )

        # text must be non-empty
        if not str(seg.get("text", "")).strip():
            return False, f"{label} 'text' must be a non-empty string.", []

    # Soft checks on the sorted list
    sorted_segs = sorted(segments, key=lambda s: float(s["start"]))

    for i in range(len(sorted_segs) - 1):
        cur_end = float(sorted_segs[i]["end"])
        nxt_start = float(sorted_segs[i + 1]["start"])

        if nxt_start < cur_end:
            overlap = cur_end - nxt_start
            warnings.append(
                f"segments[{i}] and segments[{i+1}] overlap by {overlap:.2f}s."
            )
        elif nxt_start > cur_end:
            gap = nxt_start - cur_end
            warnings.append(
                f"Gap of {gap:.2f}s between segments[{i}] (ends {cur_end}) "
                f"and segments[{i+1}] (starts {nxt_start})."
            )

    return True, "", warnings


class SetPromptRelayTimelineTool(BaseTool):
    """Composes and applies a Prompt Relay timeline to the user's video editor."""

    modes = ["generation"]
    icon = "film"

    @property
    def name(self) -> str:
        return "set_prompt_relay_timeline"

    @property
    def group(self) -> str:
        return "Prompt writing"

    @property
    def user_description(self) -> str:
        return "Puts a timed scene-by-scene video prompt timeline into your editor."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "When the user wants to create a video as a sequence of timed scenes, "
            "a 'prompt relay', or shots that change over time — you should compose "
            "the timeline yourself: write each segment's prompt and choose the start "
            "and end times that make creative sense. Then call set_prompt_relay_timeline "
            "to put the timeline into the editor. Do the creative work (scene writing, "
            "pacing, timing) before calling the tool — don't ask the user to fill in "
            "the details."
        )

    @property
    def description(self) -> str:
        return (
            "Apply a Prompt Relay timeline to the user's editor: a global prompt plus "
            "an ordered list of segments, each with a start time, end time (seconds) "
            "and a prompt that controls that slice of the video. The user approves "
            "before it is applied."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "global_prompt": {
                    "type": "string",
                    "description": (
                        "Optional prompt that conditions the entire video, applied on "
                        "top of every segment's individual prompt."
                    ),
                },
                "duration": {
                    "type": "number",
                    "description": "Total video length in seconds. Defaults to 5.",
                    "default": _DEFAULT_DURATION,
                },
                "fps": {
                    "type": "integer",
                    "description": "Frames per second. Defaults to 24.",
                    "default": _DEFAULT_FPS,
                },
                "segments": {
                    "type": "array",
                    "description": (
                        "Ordered list of timed scene prompts. Each segment controls "
                        "the video from 'start' to 'end' seconds."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "number",
                                "description": "Segment start time in seconds.",
                            },
                            "end": {
                                "type": "number",
                                "description": "Segment end time in seconds.",
                            },
                            "text": {
                                "type": "string",
                                "description": "The prompt text for this segment.",
                            },
                        },
                        "required": ["start", "end", "text"],
                    },
                },
            },
            "required": ["segments"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Validate and preview the proposed timeline — does not apply it yet."""
        segments = kwargs.get("segments")
        if segments is None:
            return ToolResult(
                success=False,
                data="",
                error="'segments' is required and must be a non-empty array.",
            )

        duration = float(kwargs.get("duration") or _DEFAULT_DURATION)
        fps = int(kwargs.get("fps") or _DEFAULT_FPS)
        global_prompt = str(kwargs.get("global_prompt") or "")

        ok, error_msg, warnings = _validate_segments(segments, duration)
        if not ok:
            return ToolResult(success=False, data="", error=error_msg)

        sorted_segs = _normalize_segments(segments, duration)

        result: Dict[str, Any] = {
            "status": "pending_approval",
            "global_prompt": global_prompt,
            "duration": duration,
            "fps": fps,
            "segments": sorted_segs,
            "segment_count": len(sorted_segs),
        }
        if warnings:
            result["warnings"] = warnings

        return ToolResult(success=True, data=json.dumps(result))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """After user approval, return the action payload for the frontend to apply."""
        segments = kwargs.get("segments") or []
        duration = float(kwargs.get("duration") or _DEFAULT_DURATION)
        fps = int(kwargs.get("fps") or _DEFAULT_FPS)
        global_prompt = str(kwargs.get("global_prompt") or "")

        sorted_segs = _normalize_segments(
            [s for s in segments if _segment_is_usable(s)],
            duration,
        )

        return ToolResult(
            success=True,
            data=json.dumps({
                "action": "set_prompt_relay",
                "global_prompt": global_prompt,
                "timeline": {
                    "duration": duration,
                    "fps": fps,
                    "segments": sorted_segs,
                },
            }),
        )


def _segment_is_usable(seg: Dict[str, Any]) -> bool:
    """Return True only if a segment has the minimum required fields."""
    try:
        return (
            "start" in seg
            and "end" in seg
            and "text" in seg
            and float(seg["start"]) < float(seg["end"])
            and str(seg.get("text", "")).strip() != ""
        )
    except (TypeError, ValueError):
        return False
