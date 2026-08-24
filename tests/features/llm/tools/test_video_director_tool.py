"""Tests for GetVideoDirectorTool -- the only Video Director chat tool left.

Editing the document (shot count, durations, media, mode, settings) is
user-only; the model's only lever is a prompt VERSION suggestion via the
frontend's `update_director_segment` tag markup, taught in
`GetVideoDirectorTool`'s `how_to_edit` field (see the module's own tests
in test_registry.py / test_manager.py for how that's carried into the
system prompt and per-turn workspace summary).
"""

import json
import pytest
from typing import Any, Optional

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.video_director_tool import GetVideoDirectorTool


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
        assert "update_director_segment" in payload["how_to_edit"]
        assert "user-only" in payload["how_to_edit"]

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
# Registration sanity
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_tools_are_generation_mode_only(self):
        assert GetVideoDirectorTool().modes == ["generation"]


class TestVideoDirectorToolsIsAvailable:
    """Tool visibility mirrors the execute()-time gate: the tool disappears
    from the advertised set whenever there is no active Video Director document."""

    def test_unavailable_when_form_state_is_none(self):
        assert GetVideoDirectorTool().is_available(None) is False

    def test_unavailable_when_video_director_absent(self):
        form_state = {"preset": "p", "mode": "m", "form_data": {}}
        assert GetVideoDirectorTool().is_available(form_state) is False

    def test_unavailable_when_inactive(self):
        form_state = make_form_state(make_timeline_doc(), make_timeline_capabilities(), active=False)
        assert GetVideoDirectorTool().is_available(form_state) is False

    def test_available_when_active(self):
        form_state = make_form_state(make_timeline_doc(), make_timeline_capabilities(), active=True)
        assert GetVideoDirectorTool().is_available(form_state) is True


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
    async def test_read_model_derives_a_prompt_only_later_segment_as_a_hard_cut_in_refs_mode(self):
        # The SAME two-segment chain doc that derives ["i2v", "chain"] under
        # `video` mode (see TestGetVideoDirectorToolChainContract) derives a
        # hard cut for the second segment under `refs` mode instead.
        doc = make_h3_doc()
        form_state = make_form_state(doc, make_h3_refs_capabilities(), mode="refs")
        ctx = make_context(session_metadata={"form_state": form_state})

        payload = json.loads((await GetVideoDirectorTool().execute(ctx)).data)
        assert [s["sub_type"] for s in payload["segments"]] == ["t2v", "t2v"]


