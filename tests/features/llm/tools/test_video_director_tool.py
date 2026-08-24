"""Tests for GetVideoDirectorTool and UpdateVideoDirectorTool."""

import json
import pytest
from typing import Any, Optional

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.video_director_tool import (
    GetVideoDirectorTool,
    UpdateVideoDirectorTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(session_metadata: dict = None) -> ToolContext:
    return ToolContext(user_id="user-1", session_metadata=session_metadata or {})


def make_timeline_capabilities(max_duration: Optional[float] = None, max_keyframes: Optional[int] = None) -> dict:
    director_mode: dict = {"tips": []}
    if max_duration is not None:
        director_mode["max_duration"] = max_duration
    if max_keyframes is not None:
        director_mode["max_keyframes"] = max_keyframes
    return {
        "modes": {
            "t2v": {},
            "i2v": {},
            "flf": {},
            "director": director_mode,
        },
        "limits": {"default_fps": 24, "default_duration": 5, "max_duration": 30},
        "segment_routing": False,
    }


def make_chain_capabilities(max_segments: Optional[int] = None, keyframes: Optional[str] = None) -> dict:
    director_mode: dict = {"tips": []}
    if max_segments is not None:
        director_mode["max_segments"] = max_segments
    if keyframes is not None:
        director_mode["keyframes"] = keyframes
    return {
        "modes": {
            "t2v": {},
            "i2v": {},
            "flf": {},
            "director": director_mode,
        },
        "limits": {"default_fps": 16, "default_duration": 5},
        "segment_routing": True,
    }


def make_timeline_doc(mode: str = "t2v", **overrides) -> dict:
    doc = {
        "mode": mode,
        "global_prompt": "a storm over the ocean",
        "negative_prompt": "blurry",
        "simple": {"duration": 5, "fps": 24, "start_image": None, "first_frame": None, "last_frame": None},
        "timeline": {"duration": 10, "fps": 24, "segments": [], "keyframes": [], "audio": [], "ic_lora": []},
        "chain": {"fps": 16, "segments": [], "continuation": {"overlap_frames": 4, "stitch": True}},
    }
    doc.update(overrides)
    return doc


def make_form_state(
    doc: dict, capabilities: dict, active: bool = True, form_data: dict = None, mode: str = "director",
) -> dict:
    return {
        "preset": "preset-wan",
        "mode": mode,
        "form_data": form_data or {},
        "video_director": {"active": active, "doc": doc, "capabilities": capabilities},
    }


# ---------------------------------------------------------------------------
# GetVideoDirectorTool
# ---------------------------------------------------------------------------

class TestGetVideoDirectorToolSchema:
    def test_name(self):
        assert GetVideoDirectorTool().name == "get_video_director"

    def test_hint_is_nonempty(self):
        assert len(GetVideoDirectorTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(GetVideoDirectorTool().description) > 0

    def test_requires_no_approval(self):
        assert GetVideoDirectorTool().requires_approval is False

    def test_to_schema_structure(self):
        schema = GetVideoDirectorTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "get_video_director"


class TestGetVideoDirectorToolInactive:
    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_no_form_state(self):
        ctx = make_context()
        result = await GetVideoDirectorTool().execute(ctx)
        assert result.success is False
        assert "video director" in result.error.lower()
        assert "get_form_state" in result.error or "update_form_settings" in result.error

    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_video_director_absent(self):
        ctx = make_context(session_metadata={"form_state": {"preset": "p", "mode": "m", "form_data": {}}})
        result = await GetVideoDirectorTool().execute(ctx)
        assert result.success is False
        assert "no video director document active" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_inactive(self):
        doc = make_timeline_doc()
        caps = make_timeline_capabilities()
        form_state = make_form_state(doc, caps, active=False)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await GetVideoDirectorTool().execute(ctx)
        assert result.success is False
        assert "no video director document active" in result.error.lower()


class TestGetVideoDirectorToolT2v:
    @pytest.mark.asyncio
    async def test_returns_doc_and_capability_summary(self):
        doc = make_timeline_doc(mode="t2v")
        caps = make_timeline_capabilities()
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)

        assert payload["mode"] == "t2v"
        assert payload["style"] == "timeline"
        assert payload["global_prompt"] == "a storm over the ocean"
        assert payload["negative_prompt"] == "blurry"
        assert len(payload["segments"]) == 1
        assert payload["segments"][0]["prompt"] == "a storm over the ocean"
        assert payload["media"] == []
        assert "capabilities" in payload
        assert payload["capabilities"]["allowed_modes"] == ["t2v", "i2v", "flf", "director"]
        assert "how_to_edit" in payload
        assert "update_video_director" in payload["how_to_edit"]
        assert "update_director_segment" in payload["how_to_edit"]

    @pytest.mark.asyncio
    async def test_settings_reflect_simple_composition(self):
        doc = make_timeline_doc(mode="t2v")
        caps = make_timeline_capabilities()
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)
        payload = json.loads(result.data)
        assert payload["settings"]["fps"] == 24
        assert payload["settings"]["duration"] == 5


class TestGetVideoDirectorToolI2v:
    @pytest.mark.asyncio
    async def test_media_includes_start_image(self):
        doc = make_timeline_doc(mode="i2v")
        doc["simple"]["start_image"] = {"path": "/media/start.png"}
        caps = make_timeline_capabilities()
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)
        payload = json.loads(result.data)
        assert len(payload["media"]) == 1
        assert payload["media"][0]["role"] == "first"
        assert payload["media"][0]["path"] == "/media/start.png"


class TestGetVideoDirectorToolChainStyle:
    @pytest.mark.asyncio
    async def test_chain_segments_use_frames(self):
        doc = make_timeline_doc(mode="director")
        doc["chain"]["segments"] = [
            {"id": "chain-0", "prompt": "storm rolls in", "duration": 3, "loras": None,
             "keyframe": None, "keyframe_strength": 1, "sub_type_override": None},
        ]
        caps = make_chain_capabilities()
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)
        payload = json.loads(result.data)
        assert payload["style"] == "chain"
        assert payload["segments"][0]["id"] == "chain-0"
        assert payload["segments"][0]["frames"] == round(3 * 16)
        assert payload["segments"][0]["start"] is None


class TestGetVideoDirectorToolTimelineDirector:
    @pytest.mark.asyncio
    async def test_timeline_segments_use_start_end(self):
        doc = make_timeline_doc(mode="director")
        doc["timeline"]["segments"] = [
            {"id": "tl-0", "start": 0, "end": 5, "text": "opening shot", "prompt_segments": []},
        ]
        caps = make_timeline_capabilities()
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)
        payload = json.loads(result.data)
        assert payload["style"] == "timeline"
        assert payload["segments"][0]["start"] == 0
        assert payload["segments"][0]["end"] == 5
        assert payload["segments"][0]["frames"] is None


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - schema
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolSchema:
    def test_name(self):
        assert UpdateVideoDirectorTool().name == "update_video_director"

    def test_requires_approval(self):
        assert UpdateVideoDirectorTool().requires_approval is True

    def test_parameters_requires_operations(self):
        schema = UpdateVideoDirectorTool().parameters
        assert "operations" in schema["properties"]
        assert "operations" in schema["required"]

    def test_reason_is_optional(self):
        schema = UpdateVideoDirectorTool().parameters
        assert "reason" in schema["properties"]
        assert "reason" not in schema.get("required", [])

    def test_hint_redirects_prompt_variant_requests_to_the_tag(self):
        hint = UpdateVideoDirectorTool().hint
        assert "do NOT call this tool" in hint
        assert '<tool_action type="update_director_segment" segment_index="N" segment_id="ID">' in hint


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - inactive / no-op errors
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolErrors:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_operations(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_timeline_doc(), make_timeline_capabilities())})
        result = await UpdateVideoDirectorTool().execute(ctx, operations=[])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_error_when_video_director_inactive(self):
        form_state = make_form_state(make_timeline_doc(), make_timeline_capabilities(), active=False)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_mode", "mode": "i2v"}]
        )
        assert result.success is False
        assert "video director" in result.error.lower()


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - per-mode media validation
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolMediaValidation:
    @pytest.mark.asyncio
    async def test_t2v_rejects_media(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "path": "/media/x.png"}}],
        )
        assert result.success is False
        assert "t2v" in result.error.lower()

    @pytest.mark.asyncio
    async def test_i2v_accepts_single_first_media(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "path": "/media/start.png"}}],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["operation_count"] == 1

    @pytest.mark.asyncio
    async def test_i2v_rejects_wrong_role(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "last", "path": "/media/x.png"}}],
        )
        assert result.success is False
        assert "i2v" in result.error.lower()

    @pytest.mark.asyncio
    async def test_flf_accepts_pair_via_end_state_validation(self):
        """Neither media op alone satisfies flf; together with set_mode they do."""
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "set_mode", "mode": "flf"},
                {"op": "upsert_media", "media": {"role": "first", "path": "/media/first.png"}},
                {"op": "upsert_media", "media": {"role": "last", "path": "/media/last.png"}},
            ],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["summary"][0] == "Set mode: flf"
        assert payload["operation_count"] == 3

    @pytest.mark.asyncio
    async def test_flf_rejects_incomplete_pair(self):
        doc = make_timeline_doc(mode="flf")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "path": "/media/first.png"}}],
        )
        assert result.success is False
        assert "flf" in result.error.lower()


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - upsert_media form_media addressing (Stage B)
# ---------------------------------------------------------------------------

def make_hero_form_data(**extra_gallery_items) -> dict:
    return {
        "reference_image": {
            "path": "uploads/hero.png",
            "relative_path": "uploads/hero.png",
            "name": "hero.png",
            "type": "image",
            "label": "Hero",
        },
        "gallery": [
            {"path": "uploads/a.png", "name": "a.png", "type": "image", "label": "First"},
            {"path": "uploads/b.png", "name": "b.png", "type": "image"},
        ],
    }


class TestUpdateVideoDirectorToolFormMediaAddressing:
    @pytest.mark.asyncio
    async def test_resolves_single_field_by_label_case_insensitive_trimmed(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image", "label": "  hero  "}},
            }],
        )
        assert result.success is True
        payload = json.loads(result.data)
        media = payload["operations"][0]["media"]
        assert media["path"] == "uploads/hero.png"
        assert media["form_ref"] == {"field": "reference_image", "path": "uploads/hero.png"}
        assert "from form field" in payload["summary"][0].lower()

    @pytest.mark.asyncio
    async def test_resolves_multiple_field_item_by_label(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "keyframe", "at": 1.0, "form_media": {"field": "gallery", "label": "First"}},
            }],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["path"] == "uploads/a.png"
        assert media["form_ref"] == {"field": "gallery", "path": "uploads/a.png"}

    @pytest.mark.asyncio
    async def test_resolves_multiple_field_item_by_fallback_name_when_no_label(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "keyframe", "at": 1.0, "form_media": {"field": "gallery", "label": "b.png"}},
            }],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["path"] == "uploads/b.png"

    @pytest.mark.asyncio
    async def test_resolves_by_path(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image", "path": "uploads/hero.png"}},
            }],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["form_ref"] == {"field": "reference_image", "path": "uploads/hero.png"}

    @pytest.mark.asyncio
    async def test_unmatched_label_lists_available_labels(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image", "label": "Nope"}},
            }],
        )
        assert result.success is False
        assert "Nope" in result.error
        assert "Hero" in result.error

    @pytest.mark.asyncio
    async def test_ambiguous_label_across_multiple_items_is_an_error(self):
        doc = make_timeline_doc(mode="director")
        form_data = {
            "gallery": [
                {"path": "uploads/a.png", "name": "a.png", "type": "image", "label": "Dup"},
                {"path": "uploads/b.png", "name": "b.png", "type": "image", "label": "Dup"},
            ]
        }
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=form_data)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "keyframe", "at": 1.0, "form_media": {"field": "gallery", "label": "Dup"}},
            }],
        )
        assert result.success is False
        assert "ambiguous" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_field_on_form_is_an_error(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data={})
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image", "label": "Hero"}},
            }],
        )
        assert result.success is False
        assert "reference_image" in result.error

    @pytest.mark.asyncio
    async def test_both_path_and_form_media_is_an_error(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {
                    "role": "first",
                    "path": "/media/x.png",
                    "form_media": {"field": "reference_image", "label": "Hero"},
                },
            }],
        )
        assert result.success is False
        assert "both" in result.error.lower()

    @pytest.mark.asyncio
    async def test_neither_path_nor_form_media_is_an_error(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first"}}],
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_form_media_needs_exactly_one_of_label_or_path(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image"}},
            }],
        )
        assert result.success is False
        assert "exactly one" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_confirmed_also_resolves_form_media(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute_confirmed(
            ctx,
            operations=[{
                "op": "upsert_media",
                "media": {"role": "first", "form_media": {"field": "reference_image", "label": "Hero"}},
            }],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["path"] == "uploads/hero.png"
        assert media["form_ref"] == {"field": "reference_image", "path": "uploads/hero.png"}

    @pytest.mark.asyncio
    async def test_get_video_director_resolves_a_stored_form_ref_against_live_form_data(self):
        """A document already carrying a `form_ref` (e.g. picked through the
        frontend's "From form" UI, not via this tool) reads back with its
        path resolved live against form_data -- the read model mirrors what
        the frontend would submit, not a frozen/absent value."""
        doc = make_timeline_doc(
            mode="i2v",
            simple={
                "duration": 5, "fps": 24,
                "start_image": {"form_ref": {"field": "reference_image", "path": "uploads/hero.png"}},
                "first_frame": None, "last_frame": None,
            },
        )
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)

        assert result.success is True
        media = json.loads(result.data)["media"]
        assert media == [{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1.0, "path": "uploads/hero.png"}]

    @pytest.mark.asyncio
    async def test_get_video_director_omits_a_broken_form_ref(self):
        """A `form_ref` pointing at an item no longer on the form (removed or
        reordered out) resolves to nothing -- same as normalize.py's own
        `_resolve_media_ref` would reject it, this reads as no media rather
        than a stale/wrong path."""
        doc = make_timeline_doc(
            mode="i2v",
            simple={
                "duration": 5, "fps": 24,
                "start_image": {"form_ref": {"field": "reference_image", "path": "uploads/gone.png"}},
                "first_frame": None, "last_frame": None,
            },
        )
        form_state = make_form_state(doc, make_timeline_capabilities(), form_data=make_hero_form_data())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await GetVideoDirectorTool().execute(ctx)

        assert result.success is True
        assert json.loads(result.data)["media"] == []


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - style-gated segment fields
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolSegmentStyleValidation:
    @pytest.mark.asyncio
    async def test_frames_rejected_in_timeline_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "a shot", "frames": 30}}],
        )
        assert result.success is False
        assert "frames" in result.error.lower()
        assert "chain" in result.error.lower()

    @pytest.mark.asyncio
    async def test_start_end_rejected_in_chain_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "a shot", "start": 0, "end": 5}}],
        )
        assert result.success is False
        assert "timeline" in result.error.lower()

    @pytest.mark.asyncio
    async def test_frames_accepted_in_chain_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "storm rolls in", "frames": 81}}],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["summary"][0] == 'Add segment "storm rolls in" (frames 81)'

    @pytest.mark.asyncio
    async def test_start_end_accepted_in_timeline_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "opening shot", "start": 0, "end": 5}}],
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_start_must_be_less_than_end(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "bad shot", "start": 5, "end": 1}}],
        )
        assert result.success is False


class TestUpdateVideoDirectorToolKeyframeRoleValidation:
    @pytest.mark.asyncio
    async def test_keyframe_rejected_in_chain_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "keyframe", "at": 1.0, "path": "/media/kf.png"}}],
        )
        assert result.success is False
        assert "keyframe" in result.error.lower()
        assert "timeline" in result.error.lower()

    @pytest.mark.asyncio
    async def test_keyframe_accepted_in_timeline_style(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "keyframe", "at": 1.0, "path": "/media/kf.png"}}],
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - duration cap
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolDurationCap:
    @pytest.mark.asyncio
    async def test_duration_over_cap_rejected(self):
        doc = make_timeline_doc(mode="t2v")
        caps = make_timeline_capabilities(max_duration=10)
        # t2v draws its cap from limits.max_duration (30 by default helper);
        # override directly on t2v mode caps for a tight cap.
        caps["modes"]["t2v"]["max_duration"] = 10
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_settings", "settings": {"duration": 999}}],
        )
        assert result.success is False
        assert "duration" in result.error.lower()

    @pytest.mark.asyncio
    async def test_duration_within_cap_accepted(self):
        doc = make_timeline_doc(mode="t2v")
        caps = make_timeline_capabilities(max_duration=10)
        caps["modes"]["t2v"]["max_duration"] = 10
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_settings", "settings": {"duration": 8}}],
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_duration_is_rejected_outright_in_chain_style(self):
        """A chain's total is the sum of its shots -- the editor has nowhere to
        put settings.duration, so accepting it would silently do nothing."""
        doc = make_timeline_doc(mode="director")
        caps = make_chain_capabilities()
        caps["modes"]["director"]["max_duration"] = 5
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_settings", "settings": {"duration": 999}}],
        )
        assert result.success is False
        assert "not settable in chain style" in result.error
        assert "upsert_segment" in result.error

    @pytest.mark.asyncio
    async def test_chain_total_ignores_the_mode_duration_cap(self):
        """The per-shot cap is what bounds a chain; the total is deliberately
        unbounded (six 5s shots stay valid under a max_duration of 5)."""
        doc = make_timeline_doc(mode="director")
        caps = make_chain_capabilities()
        caps["modes"]["director"]["max_duration"] = 5
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_segment", "segment": {"prompt": f"shot {i}", "duration": 5}}
                for i in range(6)
            ],
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - id assignment
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolIdAssignment:
    @pytest.mark.asyncio
    async def test_new_segment_gets_assigned_id(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "new shot", "frames": 40}}],
        )
        payload = json.loads(result.data)
        seg = payload["operations"][0]["segment"]
        assert seg["id"]
        assert isinstance(seg["id"], str)

    @pytest.mark.asyncio
    async def test_new_media_gets_assigned_id(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "path": "/media/start.png"}}],
        )
        payload = json.loads(result.data)
        media = payload["operations"][0]["media"]
        assert media["id"]
        assert isinstance(media["id"], str)

    @pytest.mark.asyncio
    async def test_explicit_segment_id_is_preserved(self):
        doc = make_timeline_doc(mode="director")
        doc["chain"]["segments"] = [
            {"id": "chain-0", "prompt": "existing", "duration": 3, "loras": None,
             "keyframe": None, "keyframe_strength": 1, "sub_type_override": None},
        ]
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "chain-0", "prompt": "updated"}}],
        )
        payload = json.loads(result.data)
        seg = payload["operations"][0]["segment"]
        assert seg["id"] == "chain-0"
        assert payload["summary"][0].startswith("Update segment")


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - remove / reorder
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolRemoveReorder:
    @pytest.mark.asyncio
    async def test_remove_segment_unknown_id_rejected(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "remove_segment", "id": "does-not-exist"}]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_remove_media_unknown_id_rejected(self):
        doc = make_timeline_doc(mode="i2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "remove_media", "id": "does-not-exist"}]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_reorder_segments_requires_permutation(self):
        doc = make_timeline_doc(mode="director")
        doc["chain"]["segments"] = [
            {"id": "a", "prompt": "one", "duration": 2, "loras": None, "keyframe": None,
             "keyframe_strength": 1, "sub_type_override": None},
            {"id": "b", "prompt": "two", "duration": 2, "loras": None, "keyframe": None,
             "keyframe_strength": 1, "sub_type_override": None},
        ]
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        bad = await UpdateVideoDirectorTool().execute(ctx, operations=[{"op": "reorder_segments", "ids": ["a"]}])
        assert bad.success is False

        good = await UpdateVideoDirectorTool().execute(ctx, operations=[{"op": "reorder_segments", "ids": ["b", "a"]}])
        assert good.success is True


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - approval preview / summary
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolApprovalPreview:
    @pytest.mark.asyncio
    async def test_preview_carries_summary_lines(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_mode", "mode": "director"}]
        )
        assert result.success is True
        assert result.preview is not None
        assert result.preview.action == "Update Video Director"
        assert result.preview.items == ["Set mode: director"]

    @pytest.mark.asyncio
    async def test_status_is_pending_approval(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_mode", "mode": "director"}]
        )
        payload = json.loads(result.data)
        assert payload["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_reason_included_when_provided(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_mode", "mode": "director"}],
            reason="User wants an image-conditioned shot",
        )
        payload = json.loads(result.data)
        assert payload["reason"] == "User wants an image-conditioned shot"


# ---------------------------------------------------------------------------
# UpdateVideoDirectorTool - execute_confirmed
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_confirmed_returns_apply_action_payload(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute_confirmed(
            ctx, operations=[{"op": "set_mode", "mode": "director"}]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "apply_video_director_ops"
        assert payload["operations"] == [{"op": "set_mode", "mode": "director"}]
        assert payload["summary"] == ["Set mode: director"]

    @pytest.mark.asyncio
    async def test_confirmed_rejects_invalid_operations(self):
        doc = make_timeline_doc(mode="t2v")
        form_state = make_form_state(doc, make_timeline_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute_confirmed(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "path": "/media/x.png"}}],
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_confirmed_fills_ids_for_new_segments(self):
        doc = make_timeline_doc(mode="director")
        form_state = make_form_state(doc, make_chain_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute_confirmed(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "new shot", "frames": 30}}],
        )
        payload = json.loads(result.data)
        assert payload["operations"][0]["segment"]["id"]


# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_tools_are_generation_mode_only(self):
        assert GetVideoDirectorTool().modes == ["generation"]
        assert UpdateVideoDirectorTool().modes == ["generation"]


class TestVideoDirectorToolsIsAvailable:
    """Tool visibility mirrors the execute()-time gate: both tools disappear
    from the advertised set whenever there is no active Video Director document."""

    @pytest.mark.parametrize("tool_cls", [GetVideoDirectorTool, UpdateVideoDirectorTool])
    def test_unavailable_when_form_state_is_none(self, tool_cls):
        assert tool_cls().is_available(None) is False

    @pytest.mark.parametrize("tool_cls", [GetVideoDirectorTool, UpdateVideoDirectorTool])
    def test_unavailable_when_video_director_absent(self, tool_cls):
        form_state = {"preset": "p", "mode": "m", "form_data": {}}
        assert tool_cls().is_available(form_state) is False

    @pytest.mark.parametrize("tool_cls", [GetVideoDirectorTool, UpdateVideoDirectorTool])
    def test_unavailable_when_inactive(self, tool_cls):
        form_state = make_form_state(make_timeline_doc(), make_timeline_capabilities(), active=False)
        assert tool_cls().is_available(form_state) is False

    @pytest.mark.parametrize("tool_cls", [GetVideoDirectorTool, UpdateVideoDirectorTool])
    def test_available_when_active(self, tool_cls):
        form_state = make_form_state(make_timeline_doc(), make_timeline_capabilities(), active=True)
        assert tool_cls().is_available(form_state) is True


# ---------------------------------------------------------------------------
# Current chain-composer contract: keyframes anywhere, chain-wide audio,
# continuation and join control (mirrors content/presets/marketplace/MiniMax-H3/preset.yml)
# ---------------------------------------------------------------------------

def make_h3_capabilities() -> dict:
    """The capability shape a keyframes-anywhere, audio-capable chain preset
    declares -- copied from `vars.video_director` in MiniMax-H3's preset."""
    return {
        "preset_modes": ["video"],
        "segment_routing": True,
        "modes": {
            "director": {
                "keyframes": "anywhere",
                "audio": True,
                "max_keyframes": 8,
                "max_segments": 6,
                "max_frames_per_segment": 345,
                "continuation": {"source": "tail_frames", "overlap_frames": 17, "stitch": True},
                "max_overlap_frames": 34,
            },
        },
        "limits": {"default_duration": 5, "default_fps": 24, "max_duration": 15},
    }


def make_h3_doc(**chain_overrides) -> dict:
    chain = {
        "fps": 24,
        "segments": [
            {"id": "seg-a", "prompt": "a lighthouse in fog", "duration": 6, "loras": None,
             "keyframe": None, "keyframe_strength": 1, "sub_type_override": None},
            {"id": "seg-b", "prompt": "the beam sweeps out to sea", "duration": 6, "loras": None,
             "keyframe": None, "keyframe_strength": 1, "sub_type_override": None},
        ],
        "continuation": {"overlap_frames": 17, "stitch": True},
        "keyframes": [],
        "audio": [],
    }
    chain.update(chain_overrides)
    doc = make_timeline_doc(mode="director")
    doc["chain"] = chain
    return doc


def make_h3_refs_capabilities() -> dict:
    """MiniMax-H3's real `preset_mode_overrides.refs` shape (see `vars.
    video_director.preset_mode_overrides` in content/presets/marketplace/MiniMax-H3/
    preset.yml) layered onto the `video`-mode capabilities above."""
    caps = make_h3_capabilities()
    caps["preset_mode_overrides"] = {
        "refs": {
            "references": "per_shot",
            "reference_fields": ["references", "reference_videos", "reference_audios"],
            "modes": {"director": {
                "keyframes": None, "audio": False, "continuation": None, "max_overlap_frames": None,
            }},
        },
    }
    return caps


class TestGetVideoDirectorToolChainContract:
    @pytest.mark.asyncio
    async def test_reports_keyframes_audio_continuation_and_joins(self):
        doc = make_h3_doc(
            keyframes=[{"id": "ckf-1", "at": 7.5, "strength": 0.8, "media": {"path": "/media/kf.png"}}],
            audio=[{"id": "aud-1", "role": "mux", "start": 0, "trim_start": 0, "length": 12,
                    "media": {"path": "/media/track.mp3"}}],
        )
        doc["chain"]["segments"][0]["keyframe"] = {"path": "/media/open.png"}
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)

        assert payload["style"] == "chain"
        assert payload["settings"]["continuation"] == {"overlap_frames": 17, "stitch": True}
        assert [s["duration"] for s in payload["segments"]] == [6, 6]
        assert [s["frames"] for s in payload["segments"]] == [144, 144]
        # First shot carries a start image -> i2v; the next one continues it.
        assert [s["sub_type"] for s in payload["segments"]] == ["i2v", "chain"]
        assert payload["audio"] == [{
            "id": "aud-1", "role": "mux", "start": 0, "trim_start": 0,
            "length": 12, "path": "/media/track.mp3",
        }]
        placed = [m for m in payload["media"] if m["role"] == "keyframe"]
        assert placed == [{"id": "ckf-1", "role": "keyframe", "segment_id": None, "at": 7.5,
                           "strength": 0.8, "path": "/media/kf.png"}]
        leading = [m for m in payload["media"] if m["role"] == "first"]
        assert leading == [{"id": "kf-seg-a", "role": "first", "segment_id": "seg-a", "at": 0,
                            "strength": 1, "path": "/media/open.png"}]

    @pytest.mark.asyncio
    async def test_sub_type_override_makes_a_later_shot_a_hard_cut(self):
        doc = make_h3_doc()
        doc["chain"]["segments"][1]["sub_type_override"] = "t2v"
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        assert [s["sub_type"] for s in payload["segments"]] == ["t2v", "t2v"]
        assert payload["segments"][1]["sub_type_override"] == "t2v"

    @pytest.mark.asyncio
    async def test_capability_summary_is_generated_from_capabilities(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        caps = payload["capabilities"]

        assert "upsert_audio" in caps["available_operations"]
        assert "set_continuation" in caps["available_operations"]
        assert caps["audio"]["supported"] is True
        assert caps["audio"]["recommended_role"] == "mux"
        assert "condition" in caps["audio"]["role_reality"]
        assert caps["chain"]["max_segments"] == 6
        assert caps["chain"]["max_frames_per_segment"] == 345
        assert caps["chain"]["max_overlap_frames"] == 34
        assert "keyframe" in caps["media_rules"]["director"]
        assert "anywhere" in caps["media_rules"]["director"]
        assert "sub_type_override" in caps["segment_fields_by_style"]["chain"]
        assert "duration" in caps["segment_fields_by_style"]["chain"]

    @pytest.mark.asyncio
    async def test_how_to_edit_names_units_joins_and_audio(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        how_to = json.loads((await GetVideoDirectorTool().execute(ctx)).data)["how_to_edit"]
        assert "SECONDS" in how_to
        assert "sub_type_override" in how_to
        assert "set_continuation" in how_to
        assert "upsert_audio" in how_to

    @pytest.mark.asyncio
    async def test_capability_summary_hides_absent_capabilities(self):
        doc = make_timeline_doc(mode="director")
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_chain_capabilities())})

        caps = json.loads((await GetVideoDirectorTool().execute(ctx)).data)["capabilities"]
        assert caps["audio"] == {"supported": False}
        assert "upsert_audio" not in caps["available_operations"]
        assert "not available" in caps["media_rules"]["director"]


class TestVideoDirectorPresetModeOverlay:
    """`get_video_director` must expose the EFFECTIVE, post-overlay capability
    set for the CURRENT preset mode (form_state['mode']) -- see
    apply_preset_mode_overlay() in src/features/video_director/normalize.py."""

    @pytest.mark.asyncio
    async def test_refs_mode_gets_the_references_capability_and_loses_keyframes_and_audio(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        caps = payload["capabilities"]

        assert caps["references"] == {
            "supported": True,
            "selection": "per_shot",
            "fields": ["references", "reference_videos", "reference_audios"],
        }
        assert caps["audio"] == {"supported": False}
        assert "upsert_audio" not in caps["available_operations"]
        assert "not available" in caps["media_rules"]["director"]

    @pytest.mark.asyncio
    async def test_video_mode_is_byte_identical_to_before_the_override_existed(self):
        doc = make_h3_doc()
        # `video` has no entry in preset_mode_overrides, so the overlay is a no-op.
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="video")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        caps = payload["capabilities"]

        assert caps["references"] == {"supported": False}
        assert caps["audio"]["supported"] is True
        assert "upsert_audio" in caps["available_operations"]
        assert "anywhere" in caps["media_rules"]["director"]

    @pytest.mark.asyncio
    async def test_how_to_edit_names_the_reference_pool_fields_in_refs_mode(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        how_to = json.loads((await GetVideoDirectorTool().execute(ctx)).data)["how_to_edit"]
        assert "reference_videos" in how_to
        assert "reference_audios" in how_to

    @pytest.mark.asyncio
    async def test_how_to_edit_says_nothing_about_references_when_capability_is_off(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_capabilities(), mode="video")
        ctx = make_context(session_metadata={"form_state": form_state})

        how_to = json.loads((await GetVideoDirectorTool().execute(ctx)).data)["how_to_edit"]
        assert "reference_videos" not in how_to

    @pytest.mark.asyncio
    async def test_refs_mode_hides_set_continuation_and_reports_hard_cut_only_chain(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        caps = payload["capabilities"]

        assert "set_continuation" not in caps["available_operations"]
        assert "do not continue" in caps["chain"]["join_rule"]
        assert caps["chain"]["continuation_source"] is None
        assert caps["chain"]["max_overlap_frames"] is None

    @pytest.mark.asyncio
    async def test_video_mode_keeps_set_continuation_and_the_normal_join_rule(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="video")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        caps = payload["capabilities"]

        assert "set_continuation" in caps["available_operations"]
        assert "CONTINUES" in caps["chain"]["join_rule"]
        assert caps["chain"]["max_overlap_frames"] == 34

    @pytest.mark.asyncio
    async def test_how_to_edit_says_shots_do_not_continue_in_refs_mode(self):
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        how_to = json.loads((await GetVideoDirectorTool().execute(ctx)).data)["how_to_edit"]
        assert "do not continue" in how_to
        assert "set_continuation is not available" in how_to
        # The leading operations list (available_operations) must not offer it.
        assert "set_continuation" not in how_to.split(".")[0]

    @pytest.mark.asyncio
    async def test_read_model_derives_a_prompt_only_later_segment_as_a_hard_cut_in_refs_mode(self):
        # The SAME two-segment chain doc that derives ["i2v", "chain"] under
        # `video` mode (see TestGetVideoDirectorToolChainContract) derives a
        # hard cut for the second segment under `refs` mode instead.
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        assert [s["sub_type"] for s in payload["segments"]] == ["t2v", "t2v"]


class TestUpdateVideoDirectorToolContinuationDisabled:
    @pytest.mark.asyncio
    async def test_set_continuation_is_rejected_in_refs_mode(self):
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_continuation", "continuation": {"overlap_frames": 4}}],
        )
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_set_continuation_still_works_in_video_mode(self):
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="video")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_continuation", "continuation": {"overlap_frames": 4}}],
        )
        assert result.success is True


class TestUpdateVideoDirectorToolReferences:
    """upsert_segment's `references` field -- validated the same way
    normalize.py validates it at submission time (capability-gated, only
    `per_shot` accepts a selection)."""

    @pytest.mark.asyncio
    async def test_rejected_when_the_capability_is_off(self):
        form_state = make_form_state(make_h3_doc(), make_h3_capabilities(), mode="video")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "references": [{"path": "/media/a.png"}]}}],
        )
        assert result.success is False
        assert "not supported" in result.error

    @pytest.mark.asyncio
    async def test_a_path_entry_is_accepted_when_the_capability_is_per_shot(self):
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "references": [{"path": "/media/a.png"}]}}],
        )
        assert result.success is True
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["references"] == [{"path": "/media/a.png"}]

    @pytest.mark.asyncio
    async def test_a_form_media_entry_resolves_against_a_declared_reference_field(self):
        form_data = {"references": [{"path": "storage/uploads/hero.png", "label": "Hero"}]}
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="refs", form_data=form_data)
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_segment",
                "segment": {"id": "seg-a", "references": [{"form_media": {"field": "references", "label": "Hero"}}]},
            }],
        )
        assert result.success is True
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["references"] == [{"form_media": {"field": "references", "path": "storage/uploads/hero.png"}}]

    @pytest.mark.asyncio
    async def test_form_media_field_not_in_reference_fields_is_rejected(self):
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{
                "op": "upsert_segment",
                "segment": {"id": "seg-a", "references": [{"form_media": {"field": "not_a_pool_field", "label": "Hero"}}]},
            }],
        )
        assert result.success is False
        assert "reference_fields" in result.error

    @pytest.mark.asyncio
    async def test_null_references_inherits_the_pool_without_error(self):
        form_state = make_form_state(make_h3_doc(), make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "prompt": "same shot"}}],
        )
        assert result.success is True
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["references"] is None


class TestUpdateVideoDirectorToolChainSegmentLength:
    @pytest.mark.asyncio
    async def test_duration_in_seconds_becomes_frames(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "duration": 8}}],
        )
        assert result.success is True
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["duration"] == 8
        assert segment["frames"] == 192

    @pytest.mark.asyncio
    async def test_duration_and_frames_together_are_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "duration": 8, "frames": 192}}],
        )
        assert result.success is False
        assert "not both" in result.error

    @pytest.mark.asyncio
    async def test_new_segment_without_a_length_uses_the_preset_default(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"prompt": "the keeper climbs the stair"}}],
        )
        assert result.success is True
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["duration"] == 5
        assert segment["frames"] == 120

    @pytest.mark.asyncio
    async def test_frames_over_the_declared_per_segment_cap_are_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "duration": 20}}],
        )
        assert result.success is False
        assert "345" in result.error

    @pytest.mark.asyncio
    async def test_more_shots_than_max_segments_are_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_segment", "segment": {"prompt": f"shot {i}", "duration": 6}}
                for i in range(5)
            ],
        )
        assert result.success is False
        assert "at most 6 shots" in result.error


class TestUpdateVideoDirectorToolChainJoins:
    @pytest.mark.asyncio
    async def test_sub_type_override_t2v_is_accepted(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-b", "sub_type_override": "t2v"}}],
        )
        assert result.success is True
        assert json.loads(result.data)["operations"][0]["segment"]["sub_type_override"] == "t2v"

    @pytest.mark.asyncio
    async def test_sub_type_override_null_restores_continuation(self):
        doc = make_h3_doc()
        doc["chain"]["segments"][1]["sub_type_override"] = "t2v"
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-b", "sub_type_override": None}}],
        )
        assert result.success is True
        assert json.loads(result.data)["operations"][0]["segment"]["sub_type_override"] is None

    @pytest.mark.asyncio
    async def test_unknown_sub_type_override_is_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-b", "sub_type_override": "i2v"}}],
        )
        assert result.success is False
        assert "sub_type_override" in result.error

    @pytest.mark.asyncio
    async def test_sub_type_override_is_rejected_in_timeline_style(self):
        doc = make_timeline_doc(mode="director")
        doc["timeline"]["segments"] = [{"id": "tl-0", "start": 0, "end": 5, "text": "shot", "prompt_segments": []}]
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_timeline_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "tl-0", "sub_type_override": "t2v"}}],
        )
        assert result.success is False
        assert "chain style" in result.error

    @pytest.mark.asyncio
    async def test_start_image_on_a_later_shot_is_accepted_join_aware(self):
        # Join-aware, not index-pinned: a non-first shot may carry its own
        # start image -- it resolves to 'i2v' (a fresh open), same as the
        # explicit sub_type_override 't2v' cut.
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "first", "segment_id": "seg-b", "path": "/media/x.png"}}],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["role"] == "first"
        assert media["segment_id"] == "seg-b"

    @pytest.mark.asyncio
    async def test_role_last_alone_is_rejected_as_a_dead_knob(self):
        # 'last' is only ever honoured paired with 'first' on the SAME
        # segment (that combination resolves to 'flf') -- an unpaired 'last'
        # would have no effect, so it's rejected rather than silently dropped.
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "last", "path": "/media/x.png"}}],
        )
        assert result.success is False
        assert "'first'" in result.error
        assert "'last'" in result.error

    @pytest.mark.asyncio
    async def test_role_last_paired_with_first_on_the_same_segment_is_accepted(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_media", "media": {"role": "first", "segment_id": "seg-b", "path": "/media/x.png"}},
                {"op": "upsert_media", "media": {"role": "last", "segment_id": "seg-b", "path": "/media/y.png"}},
            ],
        )
        assert result.success is True
        ops = json.loads(result.data)["operations"]
        assert {op["media"]["role"] for op in ops} == {"first", "last"}
        assert all(op["media"]["segment_id"] == "seg-b" for op in ops)

    @pytest.mark.asyncio
    async def test_role_last_on_an_unknown_segment_id_is_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "last", "segment_id": "no-such-shot", "path": "/media/x.png"}}],
        )
        assert result.success is False
        assert "no-such-shot" in result.error


class TestUpdateVideoDirectorToolChainKeyframes:
    @pytest.mark.asyncio
    async def test_keyframe_accepted_when_capability_is_anywhere(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "keyframe", "at": 7.5, "path": "/media/kf.png"}}],
        )
        assert result.success is True
        media = json.loads(result.data)["operations"][0]["media"]
        assert media["role"] == "keyframe"
        assert media["at"] == 7.5
        assert media["id"].startswith("media_")

    @pytest.mark.asyncio
    async def test_keyframe_past_the_chain_total_is_rejected(self):
        # Two 6s shots at 24 fps -> a 12s chain; 20s falls outside it.
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "keyframe", "at": 20, "path": "/media/kf.png"}}],
        )
        assert result.success is False
        assert "12.00" in result.error

    @pytest.mark.asyncio
    async def test_keyframe_rejected_when_capability_is_absent(self):
        doc = make_h3_doc()
        caps = make_h3_capabilities()
        del caps["modes"]["director"]["keyframes"]
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, caps)})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_media", "media": {"role": "keyframe", "at": 3, "path": "/media/kf.png"}}],
        )
        assert result.success is False
        assert "anywhere" in result.error

    @pytest.mark.asyncio
    async def test_more_keyframes_than_max_are_rejected(self):
        doc = make_h3_doc()
        caps = make_h3_capabilities()
        caps["modes"]["director"]["max_keyframes"] = 1
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, caps)})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_media", "media": {"role": "keyframe", "at": 2, "path": "/media/a.png"}},
                {"op": "upsert_media", "media": {"role": "keyframe", "at": 4, "path": "/media/b.png"}},
            ],
        )
        assert result.success is False
        assert "at most 1 keyframes" in result.error


class TestUpdateVideoDirectorToolAudio:
    @pytest.mark.asyncio
    async def test_upsert_audio_defaults_to_the_role_the_generators_implement(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_audio", "audio": {"path": "/media/track.mp3"}}],
        )
        assert result.success is True
        entry = json.loads(result.data)["operations"][0]["audio"]
        assert entry["role"] == "mux"
        assert entry["id"].startswith("audio_")
        # Length defaults to the composition's own total: two 6s shots.
        assert entry["length"] == 12
        assert entry["start"] == 0.0

    @pytest.mark.asyncio
    async def test_upsert_audio_keeps_an_explicit_role_and_offsets(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_audio", "audio": {
                "path": "/media/track.mp3", "role": "condition", "start": 1.5,
                "trim_start": 0.5, "length": 4,
            }}],
        )
        assert result.success is True
        entry = json.loads(result.data)["operations"][0]["audio"]
        assert entry == {"id": entry["id"], "role": "condition", "start": 1.5,
                         "trim_start": 0.5, "length": 4, "path": "/media/track.mp3"}

    @pytest.mark.asyncio
    async def test_unknown_audio_role_is_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_audio", "audio": {"path": "/media/track.mp3", "role": "soundtrack"}}],
        )
        assert result.success is False
        assert "role must be one of" in result.error

    @pytest.mark.asyncio
    async def test_audio_without_a_path_is_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_audio", "audio": {"role": "mux"}}],
        )
        assert result.success is False
        assert "'path' is required" in result.error

    @pytest.mark.asyncio
    async def test_audio_rejected_when_the_mode_does_not_declare_it(self):
        """Wan-like chain caps: segment routing, no audio capability."""
        doc = make_timeline_doc(mode="director")
        doc["chain"]["segments"] = [
            {"id": "c1", "prompt": "a shot", "duration": 3, "loras": None,
             "keyframe": None, "keyframe_strength": 1, "sub_type_override": None},
        ]
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_chain_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_audio", "audio": {"path": "/media/track.mp3", "role": "mux"}}],
        )
        assert result.success is False
        assert "does not support audio" in result.error

    @pytest.mark.asyncio
    async def test_remove_audio_needs_a_known_id(self):
        doc = make_h3_doc(audio=[{"id": "aud-1", "role": "mux", "start": 0, "trim_start": 0,
                                  "length": 12, "media": {"path": "/media/track.mp3"}}])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        ok = await UpdateVideoDirectorTool().execute(ctx, operations=[{"op": "remove_audio", "id": "aud-1"}])
        assert ok.success is True
        assert json.loads(ok.data)["summary"] == ["Remove audio aud-1"]

        missing = await UpdateVideoDirectorTool().execute(ctx, operations=[{"op": "remove_audio", "id": "nope"}])
        assert missing.success is False
        assert "unknown audio id" in missing.error


class TestUpdateVideoDirectorToolContinuation:
    @pytest.mark.asyncio
    async def test_set_continuation_merges_onto_the_current_settings(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_continuation", "continuation": {"stitch": False}}],
        )
        assert result.success is True
        assert json.loads(result.data)["operations"][0]["continuation"] == {"overlap_frames": 17, "stitch": False}

    @pytest.mark.asyncio
    async def test_overlap_frames_are_bounded_by_max_overlap_frames(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        ok = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_continuation", "continuation": {"overlap_frames": 34}}]
        )
        assert ok.success is True

        over = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_continuation", "continuation": {"overlap_frames": 35}}]
        )
        assert over.success is False
        assert "max_overlap_frames 34" in over.error

    @pytest.mark.asyncio
    async def test_negative_overlap_is_rejected(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_h3_doc(), make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_continuation", "continuation": {"overlap_frames": -1}}]
        )
        assert result.success is False
        assert "non-negative integer" in result.error

    @pytest.mark.asyncio
    async def test_set_continuation_is_rejected_in_timeline_style(self):
        doc = make_timeline_doc(mode="director")
        doc["timeline"]["segments"] = [{"id": "tl-0", "start": 0, "end": 5, "text": "shot", "prompt_segments": []}]
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_timeline_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_continuation", "continuation": {"stitch": False}}]
        )
        assert result.success is False
        assert "only valid in chain style" in result.error


class TestUpdateVideoDirectorToolOneShotComposition:
    @pytest.mark.asyncio
    async def test_a_whole_chain_composition_lands_in_one_call(self):
        """The shape the tool description promises a model can produce in one
        call: shots in seconds, a hard cut, a placed keyframe, a muxed track
        and the join settings."""
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "set_prompt", "prompt": "a lighthouse at dusk, 35mm"},
                {"op": "upsert_segment", "segment": {"prompt": "the beam wakes", "duration": 6}},
                {"op": "upsert_segment", "segment": {"prompt": "gulls scatter", "duration": 6}},
                {"op": "upsert_segment", "segment": {"prompt": "cut to the keeper", "duration": 6,
                                                     "sub_type_override": "t2v"}},
                {"op": "upsert_media", "media": {"role": "keyframe", "at": 12, "path": "/media/kf.png"}},
                {"op": "upsert_audio", "audio": {"path": "/media/waves.mp3", "role": "mux"}},
                {"op": "set_continuation", "continuation": {"overlap_frames": 17, "stitch": True}},
            ],
        )
        assert result.success is True, result.error
        payload = json.loads(result.data)
        assert payload["operation_count"] == 7
        assert [op["op"] for op in payload["operations"]] == [
            "set_prompt", "upsert_segment", "upsert_segment", "upsert_segment",
            "upsert_media", "upsert_audio", "set_continuation",
        ]
        assert all(op["segment"]["frames"] == 144 for op in payload["operations"] if op["op"] == "upsert_segment")


# ---------------------------------------------------------------------------
# Weak-model ergonomics: the shapes a local model actually sends
# ---------------------------------------------------------------------------

class TestUpdateVideoDirectorToolMalformedOperations:
    @pytest.mark.asyncio
    async def test_operations_sent_as_a_json_string_is_read_not_iterated(self):
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations='[{"op": "upsert_segment", "segment": {"prompt": "a dune", "duration": 4}}]',
        )
        assert result.success is True, result.error
        payload = json.loads(result.data)
        assert payload["operations"][0]["segment"]["frames"] == 96

    @pytest.mark.asyncio
    async def test_operations_sent_as_a_python_repr_string_is_read(self):
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations="[{'op': 'upsert_segment', 'segment': {'prompt': 'a dune', 'duration': 4}}]",
        )
        assert result.success is True, result.error
        assert json.loads(result.data)["operation_count"] == 1

    @pytest.mark.asyncio
    async def test_a_single_operation_object_is_accepted_unwrapped(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations={"op": "set_prompt", "prompt": "a lighthouse"},
        )
        assert result.success is True, result.error
        assert json.loads(result.data)["operations"] == [{"op": "set_prompt", "prompt": "a lighthouse"}]

    @pytest.mark.asyncio
    async def test_unparseable_operations_text_names_the_fix_instead_of_iterating_characters(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations="[{'op': 'set_prompt': 'a whole script'}]",
        )
        assert result.success is False
        assert "must be an ARRAY of operation objects" in result.error
        assert "two colons" in result.error
        assert '"op": "upsert_segment"' in result.error
        # The character-iteration failure mode: one error per character.
        assert "operations[3]" not in result.error

    @pytest.mark.asyncio
    async def test_confirmed_path_refuses_the_same_unparseable_text(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute_confirmed(
            ctx, operations="not an array at all",
        )
        assert result.success is False
        assert "must be an ARRAY of operation objects" in result.error


class TestUpdateVideoDirectorToolForgivingInput:
    @pytest.mark.asyncio
    async def test_operation_is_accepted_as_a_spelling_of_op(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"operation": "set_prompt", "prompt": "a dune sea"}],
        )
        assert result.success is True, result.error
        assert json.loads(result.data)["operations"][0]["op"] == "set_prompt"

    @pytest.mark.asyncio
    async def test_quoted_numbers_are_accepted_where_a_number_belongs(self):
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_segment", "segment": {"prompt": "a dune", "duration": "4"}},
                {"op": "set_continuation", "continuation": {"overlap_frames": "17"}},
            ],
        )
        assert result.success is True, result.error
        ops = json.loads(result.data)["operations"]
        assert ops[0]["segment"]["duration"] == 4
        assert ops[0]["segment"]["frames"] == 96
        assert ops[1]["continuation"]["overlap_frames"] == 17

    @pytest.mark.asyncio
    async def test_a_flattened_payload_is_read_as_if_it_were_nested(self):
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "upsert_segment", "prompt": "a dune", "duration": 4}],
        )
        assert result.success is True, result.error
        assert json.loads(result.data)["operations"][0]["segment"]["prompt"] == "a dune"

    @pytest.mark.asyncio
    async def test_an_op_with_no_name_is_told_which_names_exist(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(ctx, operations=[{"prompt": "hello"}])
        assert result.success is False
        assert "needs an 'op'" in result.error
        assert "upsert_segment" in result.error and "set_continuation" in result.error


class TestUpdateVideoDirectorToolShotMarkers:
    """A real observed failure: a whole multi-shot script pushed into one
    set_prompt with [Shot N] markers instead of one segment per shot."""

    @pytest.mark.asyncio
    async def test_a_multi_shot_script_in_one_prompt_is_rejected_with_the_fix(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "set_prompt", "prompt": (
                "[Shot 1] Live-action, cinematic, a rider crests the dune. "
                "[Shot 2] Close on the reins. [Shot 3] The camera pulls back."
            )}],
        )
        assert result.success is False
        assert "one upsert_segment per shot" in result.error
        assert "3 shot markers" in result.error

    @pytest.mark.asyncio
    async def test_a_single_marker_in_a_segment_prompt_is_rejected(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_segment", "segment": {"id": "seg-a", "prompt": "[Shot 2] the beam sweeps"}}],
        )
        assert result.success is False
        assert "segment.prompt" in result.error
        assert "a segment IS one shot" in result.error

    @pytest.mark.asyncio
    async def test_scene_and_clip_markers_are_caught_too(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_prompt", "prompt": "[Scene 1] dawn [Clip 2] dusk"}],
        )
        assert result.success is False
        assert "2 shot markers" in result.error

    @pytest.mark.asyncio
    async def test_ordinary_bracketed_text_in_a_prompt_is_left_alone(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_prompt", "prompt": "a lighthouse [35mm, f/1.4] at dusk"}],
        )
        assert result.success is True, result.error


class TestUpdateVideoDirectorToolDescriptionRecipe:
    def test_the_composition_recipe_leads_the_description(self):
        """Small models truncate attention over a long spec, so the rule they
        break most has to be in the opening paragraph."""
        opening = UpdateVideoDirectorTool().description.split("\n\n")[0]
        assert "one" in opening.lower() and "upsert_segment" in opening
        assert "[Shot 1]" in opening
        assert "ARRAY" in opening

    def test_the_operations_parameter_carries_an_example(self):
        described = UpdateVideoDirectorTool().parameters["properties"]["operations"]["description"]
        assert '"op": "upsert_segment"' in described


class TestUpdateVideoDirectorToolTimelineAudioLength:
    @pytest.mark.asyncio
    async def test_an_unmeasured_track_defaults_to_the_timeline_duration(self):
        """Timeline style has no per-segment frame counts to sum, so the
        composition's length is settings.duration."""
        caps = make_timeline_capabilities()
        caps["modes"]["director"]["audio"] = True
        doc = make_timeline_doc(mode="director")
        doc["timeline"]["segments"] = [{"id": "s1", "start": 0, "end": 10, "text": "a storm"}]
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, caps)})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "upsert_audio", "audio": {"path": "/media/rain.mp3", "role": "mux"}}],
        )
        assert result.success is True, result.error
        assert json.loads(result.data)["operations"][0]["audio"]["length"] == 10


class TestUpdateVideoDirectorToolNormalizeParity:
    """Rules normalize.py enforces on a submitted document, reported here so the
    model fixes them before approval instead of after a rejected generation."""

    @pytest.mark.asyncio
    async def test_fps_outside_the_generator_range_is_refused(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_settings", "settings": {"fps": 120}}],
        )
        assert result.success is False
        assert "'fps' must be between 1.0 and 60.0" in result.error

    @pytest.mark.asyncio
    async def test_an_in_range_fps_is_accepted(self):
        doc = make_h3_doc()
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx, operations=[{"op": "set_settings", "settings": {"fps": 30}}],
        )
        assert result.success is True, result.error

    @pytest.mark.asyncio
    async def test_token_mangled_quotes_in_a_native_call_are_restored(self):
        """The same local tokenizer that wraps quotes in `<|"|>` inside a
        <tool_action> tag does it inside a native tool call's argument too."""
        doc = make_h3_doc(segments=[])
        ctx = make_context(session_metadata={"form_state": make_form_state(doc, make_h3_capabilities())})

        result = await UpdateVideoDirectorTool().execute(
            ctx,
            operations='[{op:<|"|>upsert_segment<|"|>,segment:{duration:3,prompt:<|"|>a wide dune<|"|>}}]',
        )
        assert result.success is True, result.error
        segment = json.loads(result.data)["operations"][0]["segment"]
        assert segment["prompt"] == "a wide dune"
        assert segment["frames"] == 72


class TestUpdateVideoDirectorToolExampleShape:
    """Every example the model reads is a whole tool call, never a bare
    `operations = [...]` assignment. Sitting beside the
    `<tool_action type="update_segment" ...>` markup that the generation-mode
    system prompt teaches, an assignment is what a local model concatenated into
    `<tool_action type="update_video_director" operations=[{op:...}]>`."""

    def _rendered_prompts(self) -> list:
        tool = UpdateVideoDirectorTool()
        return [tool.description, tool.parameters["properties"]["operations"]["description"]]

    def test_no_example_is_written_as_an_assignment(self):
        for text in self._rendered_prompts():
            assert "operations = " not in text
            assert "operations=" not in text

    def test_the_description_shows_the_whole_call(self):
        description = UpdateVideoDirectorTool().description
        assert '<tool_call>{"name": "update_video_director", "arguments": {"operations": ' in description
        assert "</tool_call>" in description

    def test_the_operations_property_shows_a_value_not_a_call(self):
        # A <tool_call> block here would invite nesting the call inside its own
        # operations array.
        described = UpdateVideoDirectorTool().parameters["properties"]["operations"]["description"]
        assert "<tool_call>" not in described
        assert described.rstrip().endswith("]")

    @pytest.mark.asyncio
    async def test_the_no_operations_error_teaches_the_whole_call(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(
            make_h3_doc(), make_h3_capabilities())})
        result = await UpdateVideoDirectorTool().execute(ctx, operations=[])
        assert result.success is False
        assert "operations = " not in result.error
        assert '<tool_call>{"name": "update_video_director"' in result.error

    @pytest.mark.asyncio
    async def test_the_unparseable_operations_error_teaches_the_whole_call(self):
        """The retry strings matter most: they are read at the exact moment the
        model is already confused about the shape."""
        ctx = make_context(session_metadata={"form_state": make_form_state(
            make_h3_doc(), make_h3_capabilities())})
        result = await UpdateVideoDirectorTool().execute(
            ctx, operations="[{'op': 'set_prompt': 'a whole script'}]",
        )
        assert result.success is False
        assert "operations = " not in result.error
        assert '<tool_call>{"name": "update_video_director"' in result.error

    @pytest.mark.asyncio
    async def test_a_wrong_typed_operations_argument_teaches_the_whole_call(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(
            make_h3_doc(), make_h3_capabilities())})
        result = await UpdateVideoDirectorTool().execute(ctx, operations=42)
        assert result.success is False
        assert "operations = " not in result.error
        assert '<tool_call>{"name": "update_video_director"' in result.error
