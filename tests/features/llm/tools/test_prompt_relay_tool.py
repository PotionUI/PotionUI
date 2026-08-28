"""Tests for SetPromptRelayTimelineTool."""

import json
import pytest

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.prompt_relay_tool import SetPromptRelayTimelineTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(user_id: str = "user-1") -> ToolContext:
    return ToolContext(user_id=user_id)


def make_segment(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


_SIMPLE_SEGMENTS = [
    make_segment(0, 2, "A calm beach at sunrise"),
    make_segment(2, 5, "Waves crashing on the shore"),
]


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------

class TestSetPromptRelayTimelineToolSchema:
    def test_name(self):
        assert SetPromptRelayTimelineTool().name == "set_prompt_relay_timeline"

    def test_requires_approval(self):
        assert SetPromptRelayTimelineTool().requires_approval is True

    def test_hint_is_nonempty(self):
        assert len(SetPromptRelayTimelineTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(SetPromptRelayTimelineTool().description) > 0

    def test_parameters_has_segments_required(self):
        schema = SetPromptRelayTimelineTool().parameters
        assert "segments" in schema["properties"]
        assert "segments" in schema["required"]

    def test_parameters_segments_is_array(self):
        schema = SetPromptRelayTimelineTool().parameters
        assert schema["properties"]["segments"]["type"] == "array"

    def test_segment_items_require_start_end_text(self):
        schema = SetPromptRelayTimelineTool().parameters
        items = schema["properties"]["segments"]["items"]
        for field in ("start", "end", "text"):
            assert field in items["properties"]
            assert field in items["required"]

    def test_global_prompt_is_optional(self):
        schema = SetPromptRelayTimelineTool().parameters
        assert "global_prompt" in schema["properties"]
        assert "global_prompt" not in schema.get("required", [])

    def test_duration_and_fps_are_optional(self):
        schema = SetPromptRelayTimelineTool().parameters
        for field in ("duration", "fps"):
            assert field in schema["properties"]
            assert field not in schema.get("required", [])

    def test_to_schema_structure(self):
        schema = SetPromptRelayTimelineTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "set_prompt_relay_timeline"
        assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# execute() – invalid input / hard errors
# ---------------------------------------------------------------------------

class TestExecuteErrors:
    @pytest.mark.asyncio
    async def test_error_when_segments_missing(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_error_when_segments_is_empty_list(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=[])
        assert result.success is False
        assert "non-empty" in result.error.lower() or "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_error_when_segment_missing_start(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[{"end": 2, "text": "hello"}]
        )
        assert result.success is False
        assert "start" in result.error

    @pytest.mark.asyncio
    async def test_error_when_segment_missing_end(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[{"start": 0, "text": "hello"}]
        )
        assert result.success is False
        assert "end" in result.error

    @pytest.mark.asyncio
    async def test_error_when_segment_missing_text(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[{"start": 0, "end": 2}]
        )
        assert result.success is False
        assert "text" in result.error

    @pytest.mark.asyncio
    async def test_error_when_start_equals_end(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(2, 2, "same")]
        )
        assert result.success is False
        assert "start" in result.error or "end" in result.error

    @pytest.mark.asyncio
    async def test_error_when_start_greater_than_end(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(5, 2, "reversed")]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_error_when_text_is_empty_string(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(0, 2, "")]
        )
        assert result.success is False
        assert "text" in result.error.lower()

    @pytest.mark.asyncio
    async def test_error_when_text_is_whitespace_only(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(0, 2, "   ")]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_error_when_start_end_are_non_numeric(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[{"start": "two", "end": "five", "text": "hi"}]
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# execute() – happy path
# ---------------------------------------------------------------------------

class TestExecuteSuccess:
    @pytest.mark.asyncio
    async def test_returns_pending_approval_status(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_payload_contains_required_keys(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        payload = json.loads(result.data)
        for key in ("segments", "segment_count", "duration", "fps", "global_prompt"):
            assert key in payload, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_segment_count_matches_input(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        payload = json.loads(result.data)
        assert payload["segment_count"] == 2
        assert len(payload["segments"]) == 2

    @pytest.mark.asyncio
    async def test_segments_are_sorted_by_start(self):
        ctx = make_context()
        unordered = [
            make_segment(3, 5, "late scene"),
            make_segment(0, 2, "early scene"),
            make_segment(2, 3, "middle scene"),
        ]
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=unordered)
        payload = json.loads(result.data)
        starts = [s["start"] for s in payload["segments"]]
        assert starts == sorted(starts)

    @pytest.mark.asyncio
    async def test_global_prompt_is_preserved(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx,
            segments=_SIMPLE_SEGMENTS,
            global_prompt="cinematic, 4k",
        )
        payload = json.loads(result.data)
        assert payload["global_prompt"] == "cinematic, 4k"

    @pytest.mark.asyncio
    async def test_global_prompt_defaults_to_empty(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        payload = json.loads(result.data)
        assert payload["global_prompt"] == ""

    @pytest.mark.asyncio
    async def test_duration_default_is_5(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        payload = json.loads(result.data)
        assert payload["duration"] == 5

    @pytest.mark.asyncio
    async def test_fps_default_is_24(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS)
        payload = json.loads(result.data)
        assert payload["fps"] == 24

    @pytest.mark.asyncio
    async def test_custom_duration_and_fps(self):
        ctx = make_context()
        segs = [make_segment(0, 10, "long scene")]
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=segs, duration=10, fps=30
        )
        payload = json.loads(result.data)
        assert payload["duration"] == 10
        assert payload["fps"] == 30

    @pytest.mark.asyncio
    async def test_segments_clamped_to_duration(self):
        """End times beyond duration should be clamped down."""
        ctx = make_context()
        segs = [make_segment(0, 99, "scene beyond duration")]
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=segs, duration=5
        )
        payload = json.loads(result.data)
        assert payload["segments"][0]["end"] <= 5

    @pytest.mark.asyncio
    async def test_overlap_produces_warning_not_error(self):
        """Overlapping segments should warn but not fail."""
        ctx = make_context()
        segs = [
            make_segment(0, 3, "first"),
            make_segment(2, 5, "overlapping second"),
        ]
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=segs, duration=5)
        assert result.success is True
        payload = json.loads(result.data)
        assert "warnings" in payload
        assert any("overlap" in w.lower() for w in payload["warnings"])

    @pytest.mark.asyncio
    async def test_gap_produces_warning_not_error(self):
        """Gaps between segments should warn but not fail."""
        ctx = make_context()
        segs = [
            make_segment(0, 1, "first"),
            make_segment(3, 5, "second with gap"),
        ]
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=segs, duration=5)
        assert result.success is True
        payload = json.loads(result.data)
        assert "warnings" in payload
        assert any("gap" in w.lower() for w in payload["warnings"])

    @pytest.mark.asyncio
    async def test_no_warnings_for_contiguous_segments(self):
        """Perfectly contiguous segments should have no warnings."""
        ctx = make_context()
        segs = [
            make_segment(0, 2, "first"),
            make_segment(2, 5, "second"),
        ]
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=segs, duration=5)
        payload = json.loads(result.data)
        assert "warnings" not in payload

    @pytest.mark.asyncio
    async def test_single_segment_accepted(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(0, 5, "one scene")]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["segment_count"] == 1


# ---------------------------------------------------------------------------
# execute() – structured approval preview (.preview)
# ---------------------------------------------------------------------------

class TestApprovalPreview:
    @pytest.mark.asyncio
    async def test_preview_kind_and_summary(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=_SIMPLE_SEGMENTS, duration=5)
        assert result.preview.kind == "timeline"
        assert result.preview.summary == "2 scenes · 5s"

    @pytest.mark.asyncio
    async def test_preview_rows_formatted_and_sorted(self):
        ctx = make_context()
        segs = [
            make_segment(2, 5, "second"),
            make_segment(0, 2, "first"),
        ]
        result = await SetPromptRelayTimelineTool().execute(ctx, segments=segs, duration=5)
        assert result.preview.rows == [
            {"range": "0:00–0:02", "text": "first"},
            {"range": "0:02–0:05", "text": "second"},
        ]

    @pytest.mark.asyncio
    async def test_preview_range_renders_fractional_seconds(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(0, 4.5, "scene")], duration=5,
        )
        assert result.preview.rows == [{"range": "0:00–0:04.5", "text": "scene"}]

    @pytest.mark.asyncio
    async def test_preview_summary_singular_scene(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute(
            ctx, segments=[make_segment(0, 5, "one scene")], duration=5,
        )
        assert result.preview.summary == "1 scene · 5s"


# ---------------------------------------------------------------------------
# execute_confirmed() – action payload shape
# ---------------------------------------------------------------------------

class TestExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_action_is_set_prompt_relay(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "set_prompt_relay"

    @pytest.mark.asyncio
    async def test_payload_has_timeline_key(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS
        )
        payload = json.loads(result.data)
        assert "timeline" in payload

    @pytest.mark.asyncio
    async def test_timeline_contains_required_fields(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS, duration=5, fps=24
        )
        payload = json.loads(result.data)
        timeline = payload["timeline"]
        for key in ("duration", "fps", "segments"):
            assert key in timeline, f"Missing timeline key: {key}"

    @pytest.mark.asyncio
    async def test_segment_items_have_start_end_text_no_id(self):
        """Frontend assigns ids — the tool must NOT include them."""
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS
        )
        payload = json.loads(result.data)
        for seg in payload["timeline"]["segments"]:
            assert "start" in seg
            assert "end" in seg
            assert "text" in seg
            assert "id" not in seg

    @pytest.mark.asyncio
    async def test_global_prompt_preserved_in_confirmed(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx,
            segments=_SIMPLE_SEGMENTS,
            global_prompt="ultra detailed",
        )
        payload = json.loads(result.data)
        assert payload["global_prompt"] == "ultra detailed"

    @pytest.mark.asyncio
    async def test_global_prompt_defaults_to_empty_in_confirmed(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS
        )
        payload = json.loads(result.data)
        assert payload["global_prompt"] == ""

    @pytest.mark.asyncio
    async def test_segments_sorted_in_confirmed(self):
        ctx = make_context()
        unordered = [
            make_segment(4, 5, "last"),
            make_segment(0, 2, "first"),
        ]
        result = await SetPromptRelayTimelineTool().execute_confirmed(ctx, segments=unordered)
        payload = json.loads(result.data)
        starts = [s["start"] for s in payload["timeline"]["segments"]]
        assert starts == sorted(starts)

    @pytest.mark.asyncio
    async def test_duration_and_fps_defaults_in_confirmed(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=_SIMPLE_SEGMENTS
        )
        payload = json.loads(result.data)
        assert payload["timeline"]["duration"] == 5
        assert payload["timeline"]["fps"] == 24

    @pytest.mark.asyncio
    async def test_custom_duration_fps_in_confirmed(self):
        ctx = make_context()
        segs = [make_segment(0, 10, "one")]
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx, segments=segs, duration=10, fps=60
        )
        payload = json.loads(result.data)
        assert payload["timeline"]["duration"] == 10
        assert payload["timeline"]["fps"] == 60

    @pytest.mark.asyncio
    async def test_confirmed_with_empty_segments(self):
        """execute_confirmed with empty segments should still succeed (empty timeline)."""
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(ctx, segments=[])
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "set_prompt_relay"
        assert payload["timeline"]["segments"] == []

    @pytest.mark.asyncio
    async def test_confirmed_skips_unusable_segments(self):
        """Segments with start >= end should be silently dropped in confirmed."""
        ctx = make_context()
        segs = [
            make_segment(0, 2, "valid"),
            {"start": 3, "end": 3, "text": "zero-length"},
        ]
        result = await SetPromptRelayTimelineTool().execute_confirmed(ctx, segments=segs)
        payload = json.loads(result.data)
        assert len(payload["timeline"]["segments"]) == 1
        assert payload["timeline"]["segments"][0]["text"] == "valid"

    @pytest.mark.asyncio
    async def test_full_round_trip_shape(self):
        """Verify the exact payload shape end-to-end."""
        ctx = make_context()
        segs = [
            make_segment(0, 2, "sunrise over mountains"),
            make_segment(2, 5, "clouds rolling in"),
        ]
        result = await SetPromptRelayTimelineTool().execute_confirmed(
            ctx,
            segments=segs,
            global_prompt="cinematic, 4k",
            duration=5,
            fps=24,
        )
        assert result.success is True
        payload = json.loads(result.data)

        assert payload == {
            "action": "set_prompt_relay",
            "global_prompt": "cinematic, 4k",
            "timeline": {
                "duration": 5.0,
                "fps": 24,
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "sunrise over mountains"},
                    {"start": 2.0, "end": 5.0, "text": "clouds rolling in"},
                ],
            },
        }


# ---------------------------------------------------------------------------
# Approval contract
# ---------------------------------------------------------------------------

class TestApprovalContract:
    def test_requires_approval_is_true(self):
        assert SetPromptRelayTimelineTool().requires_approval is True

    @pytest.mark.asyncio
    async def test_execute_confirmed_is_callable(self):
        ctx = make_context()
        result = await SetPromptRelayTimelineTool().execute_confirmed(ctx, segments=[])
        assert isinstance(result, ToolResult)
