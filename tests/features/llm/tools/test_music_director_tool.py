"""Tests for GetMusicDirectorTool and UpdateMusicDirectorTool."""

import json
import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.music_director_tool import (
    GetMusicDirectorTool,
    UpdateMusicDirectorTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(session_metadata: dict = None) -> ToolContext:
    return ToolContext(user_id="user-1", session_metadata=session_metadata or {})


def make_music3_capabilities() -> dict:
    """Mirrors docs/music-director.md's Music3-like preset example: song +
    director, no settings knobs, no references."""
    return {
        "preset_modes": ["song"],
        "modes": {
            "t2m": {},
            "song": {},
            "director": {"max_sections": 12, "per_section_prompts": True, "compile": "single_shot"},
        },
        "settings": {"bpm": False, "key": False, "time_signature": False},
        "limits": {"default_duration": 60, "max_duration": 360, "sample_rate": 44100, "stereo": True},
    }


def make_ace_capabilities() -> dict:
    """Mirrors docs/music-director.md's ACE-like preset example: adds
    style/repaint and structured bpm, per-section references."""
    return {
        "preset_modes": ["song"],
        "modes": {
            "t2m": {},
            "song": {},
            "style": {"max_reference_seconds": 30},
            "repaint": {},
            "director": {
                "max_sections": 12, "per_section_prompts": True,
                "references": "per_section", "compile": "single_shot",
            },
        },
        "settings": {"bpm": True, "key": False, "time_signature": False},
        "limits": {"default_duration": 120, "max_duration": 300, "sample_rate": 44100, "stereo": True},
    }


def make_doc(**overrides) -> dict:
    doc = {
        "schema_version": 1,
        "mode": "t2m",
        "description": "",
        "instrumental": False,
        "segments": [],
        "references": [],
        "extend_source": None,
        "repaint": {"source": None, "start": 0, "end": 10},
        "settings": {"duration": 60, "seed": -1, "bpm": None, "key": None, "time_signature": None},
    }
    doc.update(overrides)
    return doc


_KIND_LABELS_FOR_TESTS = {
    "intro": "Intro", "verse": "Verse", "pre_chorus": "Pre-Chorus", "chorus": "Chorus",
    "post_chorus": "Post-Chorus", "bridge": "Bridge", "instrumental": "Instrumental",
    "solo": "Solo", "outro": "Outro",
}


def seg(section_id: str, kind: str, lyrics: str) -> dict:
    """A `MusicDirectorValue.segments` entry (frontend Segment shape) --
    `name` carries the section kind, `content` the lyrics, mirroring
    `wireSection`/`canonicalizeSectionKind` in utils/musicDirector.ts."""
    return {"id": section_id, "type": "content", "content": lyrics, "name": _KIND_LABELS_FOR_TESTS[kind], "enabled": True}


def make_form_state(doc: dict, capabilities: dict, active: bool = True, mode: str = "song") -> dict:
    return {
        "preset": "preset-music3",
        "mode": mode,
        "form_data": {},
        "music_director": {"active": active, "doc": doc, "capabilities": capabilities},
    }


# ---------------------------------------------------------------------------
# GetMusicDirectorTool - schema
# ---------------------------------------------------------------------------

class TestGetMusicDirectorToolSchema:
    def test_name(self):
        assert GetMusicDirectorTool().name == "get_music_director"

    def test_hint_is_nonempty(self):
        assert len(GetMusicDirectorTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(GetMusicDirectorTool().description) > 0

    def test_requires_no_approval(self):
        assert GetMusicDirectorTool().requires_approval is False

    def test_to_schema_structure(self):
        schema = GetMusicDirectorTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "get_music_director"


# ---------------------------------------------------------------------------
# GetMusicDirectorTool - inactive / no-op errors
# ---------------------------------------------------------------------------

class TestGetMusicDirectorToolInactive:
    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_no_form_state(self):
        ctx = make_context()
        result = await GetMusicDirectorTool().execute(ctx)
        assert result.success is False
        assert "music director" in result.error.lower()
        assert "get_form_state" in result.error or "update_form_settings" in result.error

    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_music_director_absent(self):
        ctx = make_context(session_metadata={"form_state": {"preset": "p", "mode": "m", "form_data": {}}})
        result = await GetMusicDirectorTool().execute(ctx)
        assert result.success is False
        assert "no music director document active" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_inactive(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities(), active=False)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await GetMusicDirectorTool().execute(ctx)
        assert result.success is False
        assert "no music director document active" in result.error.lower()


# ---------------------------------------------------------------------------
# GetMusicDirectorTool - derived mode
# ---------------------------------------------------------------------------

class TestGetMusicDirectorToolModeDerivation:
    @pytest.mark.asyncio
    async def test_bare_document_derives_t2m(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert payload["mode"] == "t2m"

    @pytest.mark.asyncio
    async def test_single_plain_section_derives_song(self):
        doc = make_doc(segments=[seg("section-1", "verse", "one line")])
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert payload["mode"] == "song"
        assert payload["sections"][0]["lyrics"] == "one line"

    @pytest.mark.asyncio
    async def test_multiple_sections_derive_director(self):
        doc = make_doc(segments=[
            seg("s1", "verse", "verse one"),
            seg("s2", "chorus", "chorus one"),
        ])
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert payload["mode"] == "director"

    @pytest.mark.asyncio
    async def test_reference_only_document_derives_style(self):
        doc = make_doc(references=[{"id": "ref-1", "media": {"path": "/media/ref.mp3", "type": "audio"}}])
        form_state = make_form_state(doc, make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert payload["mode"] == "style"

    @pytest.mark.asyncio
    async def test_instrumental_toggle_derives_t2m(self):
        doc = make_doc(instrumental=True)
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert payload["mode"] == "t2m"
        assert payload["instrumental"] is True

    @pytest.mark.asyncio
    async def test_capability_summary_names_available_operations(self):
        form_state = make_form_state(make_doc(), make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        ops = payload["capabilities"]["available_operations"]
        assert "upsert_section" in ops
        assert "upsert_reference" in ops
        assert "set_description" in ops

    @pytest.mark.asyncio
    async def test_how_to_edit_explains_mode_is_derived_not_set(self):
        form_state = make_form_state(make_doc(), make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        payload = json.loads((await GetMusicDirectorTool().execute(ctx)).data)
        assert "no set_mode" in payload["how_to_edit"]
        assert "DERIVED" in payload["how_to_edit"]


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - schema
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolSchema:
    def test_name(self):
        assert UpdateMusicDirectorTool().name == "update_music_director"

    def test_requires_approval(self):
        assert UpdateMusicDirectorTool().requires_approval is True

    def test_parameters_requires_operations(self):
        schema = UpdateMusicDirectorTool().parameters
        assert "operations" in schema["properties"]
        assert "operations" in schema["required"]

    def test_reason_is_optional(self):
        schema = UpdateMusicDirectorTool().parameters
        assert "reason" in schema["properties"]
        assert "reason" not in schema.get("required", [])

    def test_no_set_mode_in_the_operation_enum(self):
        schema = UpdateMusicDirectorTool().parameters
        enum = schema["properties"]["operations"]["items"]["properties"]["op"]["enum"]
        assert "set_mode" not in enum


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - inactive / no-op errors
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolErrors:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_operations(self):
        ctx = make_context(session_metadata={"form_state": make_form_state(make_doc(), make_music3_capabilities())})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_error_when_music_director_inactive(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities(), active=False)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_description", "description": "warm synths"}]
        )
        assert result.success is False
        assert "music director" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_op_is_rejected(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "set_mode", "mode": "song"}])
        assert result.success is False
        assert "unknown op" in result.error.lower()


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - description / instrumental / settings
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolBasicFields:
    @pytest.mark.asyncio
    async def test_set_description(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_description", "description": "warm 90s boom-bap"}]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["operations"] == [{"op": "set_description", "description": "warm 90s boom-bap"}]

    @pytest.mark.asyncio
    async def test_set_instrumental(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "set_instrumental", "instrumental": True}])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_instrumental_rejects_non_boolean(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "set_instrumental", "instrumental": "yes"}])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_set_settings_duration_within_cap(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "set_settings", "settings": {"duration": 90}}])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_settings_duration_over_cap_rejected(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_settings", "settings": {"duration": 9999}}]
        )
        assert result.success is False
        assert "duration" in result.error.lower()

    @pytest.mark.asyncio
    async def test_set_settings_bpm_key_time_signature_pass_through_unchecked(self):
        """Capability-gating bpm/key/time_signature is the frontend's/backend's
        job (buildMusicDirectorSubmission / normalize_music_director) -- this
        tool only type/range-checks them, it does not reject a value this
        preset hasn't declared as a real field."""
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[{"op": "set_settings", "settings": {"bpm": 92, "key": "C minor", "time_signature": "4/4"}}],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["operations"][0]["settings"] == {"bpm": 92, "key": "C minor", "time_signature": "4/4"}

    @pytest.mark.asyncio
    async def test_set_settings_bpm_must_be_positive_number(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_settings", "settings": {"bpm": -5}}]
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - sections
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolSections:
    @pytest.mark.asyncio
    async def test_first_section_derives_song_mode(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "upsert_section", "section": {"kind": "verse", "lyrics": "riding through the city"}}]
        )
        assert result.success is True
        payload = json.loads(result.data)
        seg = payload["operations"][0]["section"]
        assert seg["id"]
        assert seg["kind"] == "verse"
        assert payload["summary"][0].startswith("Add section")

    @pytest.mark.asyncio
    async def test_two_sections_derive_director_and_are_accepted(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "verse one"}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "chorus one"}},
            ],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["operation_count"] == 2

    @pytest.mark.asyncio
    async def test_invalid_kind_is_rejected(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "upsert_section", "section": {"kind": "hook", "lyrics": "x"}}]
        )
        assert result.success is False
        assert "kind" in result.error.lower()

    @pytest.mark.asyncio
    async def test_explicit_id_updates_in_place(self):
        doc = make_doc(segments=[seg("section-1", "verse", "old lyrics")])
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "upsert_section", "section": {"id": "section-1", "lyrics": "new lyrics"}}]
        )
        assert result.success is True
        payload = json.loads(result.data)
        section = payload["operations"][0]["section"]
        assert section["id"] == "section-1"
        assert section["lyrics"] == "new lyrics"
        assert section["kind"] == "verse"  # preserved, not overwritten
        assert payload["summary"][0].startswith("Update section")

    @pytest.mark.asyncio
    async def test_remove_section_unknown_id_rejected(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "remove_section", "id": "nope"}])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_remove_last_section_falls_back_to_t2m_when_the_preset_enables_it(self):
        """The mode is DERIVED from the end state, not from what the section
        used to be: an empty section list simply derives back to 't2m' when
        this preset enables it -- not an error."""
        doc = make_doc(segments=[seg("section-1", "verse", "only one")])
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "remove_section", "id": "section-1"}])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_remove_last_section_is_rejected_when_song_is_the_only_fallback(self):
        caps = make_music3_capabilities()
        del caps["modes"]["t2m"]
        doc = make_doc(sections=[{"id": "section-1", "kind": "verse", "lyrics": "only one"}])
        form_state = make_form_state(doc, caps)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "remove_section", "id": "section-1"}])
        assert result.success is False
        assert "at least one section" in result.error.lower()

    @pytest.mark.asyncio
    async def test_reorder_sections_requires_permutation(self):
        doc = make_doc(segments=[
            seg("a", "verse", "one"),
            seg("b", "chorus", "two"),
        ])
        form_state = make_form_state(doc, make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})

        bad = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "reorder_sections", "ids": ["a"]}])
        assert bad.success is False

        good = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "reorder_sections", "ids": ["b", "a"]}])
        assert good.success is True

    @pytest.mark.asyncio
    async def test_max_sections_cap_enforced_in_director_mode(self):
        caps = make_music3_capabilities()
        caps["modes"]["director"]["max_sections"] = 2
        form_state = make_form_state(make_doc(), caps)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "one"}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "two"}},
                {"op": "upsert_section", "section": {"kind": "bridge", "lyrics": "three"}},
            ],
        )
        assert result.success is False
        assert "at most 2" in result.error

    @pytest.mark.asyncio
    async def test_style_hint_rejected_when_per_section_prompts_off(self):
        caps = make_music3_capabilities()
        caps["modes"]["director"]["per_section_prompts"] = False
        form_state = make_form_state(make_doc(), caps)
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "one", "style_hint": "moody"}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "two"}},
            ],
        )
        assert result.success is False
        assert "style_hint" in result.error.lower()

    @pytest.mark.asyncio
    async def test_style_hint_accepted_when_per_section_prompts_on(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "one", "style_hint": "moody"}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "two"}},
            ],
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - references
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolReferences:
    @pytest.mark.asyncio
    async def test_style_mode_requires_at_least_one_reference(self):
        """A bare document has no sections and no references -- adding one
        reference and nothing else derives 'style', which is valid."""
        form_state = make_form_state(make_doc(), make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_reference", "reference": {"media": {"path": "/media/ref.mp3", "type": "audio"}}}],
        )
        assert result.success is True
        payload = json.loads(result.data)
        ref = payload["operations"][0]["reference"]
        assert ref["id"]
        assert ref["media"]["path"] == "/media/ref.mp3"

    @pytest.mark.asyncio
    async def test_references_rejected_when_preset_declares_none(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[{"op": "upsert_reference", "reference": {"media": {"path": "/media/ref.mp3", "type": "audio"}}}],
        )
        assert result.success is False
        assert "does not accept references" in result.error

    @pytest.mark.asyncio
    async def test_upsert_reference_without_media_is_rejected(self):
        form_state = make_form_state(make_doc(), make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "upsert_reference", "reference": {}}]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_remove_reference_unknown_id_rejected(self):
        form_state = make_form_state(make_doc(), make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(ctx, operations=[{"op": "remove_reference", "id": "nope"}])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_per_section_reference_selection_requires_pool_membership(self):
        doc = make_doc(references=[{"id": "ref-1", "media": {"path": "/media/a.mp3", "type": "audio"}}])
        form_state = make_form_state(doc, make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "one", "references": ["ref-1"]}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "two", "references": ["ref-missing"]}},
            ],
        )
        assert result.success is False
        assert "ref-missing" in result.error

    @pytest.mark.asyncio
    async def test_per_section_reference_selection_accepted_when_valid(self):
        doc = make_doc(references=[{"id": "ref-1", "media": {"path": "/media/a.mp3", "type": "audio"}}])
        form_state = make_form_state(doc, make_ace_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[
                {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "one", "references": ["ref-1"]}},
                {"op": "upsert_section", "section": {"kind": "chorus", "lyrics": "two"}},
            ],
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# UpdateMusicDirectorTool - approval preview / execute_confirmed
# ---------------------------------------------------------------------------

class TestUpdateMusicDirectorToolApprovalPreview:
    @pytest.mark.asyncio
    async def test_preview_carries_summary_lines(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_description", "description": "dreamy synthwave"}]
        )
        assert result.success is True
        assert result.preview is not None
        assert result.preview.action == "Update Music Director"
        assert result.preview.items == ['Set description: "dreamy synthwave"']

    @pytest.mark.asyncio
    async def test_status_is_pending_approval(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx, operations=[{"op": "set_description", "description": "dreamy synthwave"}]
        )
        assert json.loads(result.data)["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_reason_included_when_provided(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute(
            ctx,
            operations=[{"op": "set_description", "description": "dreamy synthwave"}],
            reason="User asked for a mellower vibe",
        )
        assert json.loads(result.data)["reason"] == "User asked for a mellower vibe"


class TestUpdateMusicDirectorToolExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_confirmed_returns_apply_action_payload(self):
        form_state = make_form_state(make_doc(instrumental=True), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute_confirmed(
            ctx, operations=[{"op": "set_description", "description": "dreamy synthwave"}]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "apply_music_director_ops"
        assert payload["operations"] == [{"op": "set_description", "description": "dreamy synthwave"}]
        assert payload["summary"] == ['Set description: "dreamy synthwave"']

    @pytest.mark.asyncio
    async def test_confirmed_rejects_invalid_operations(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute_confirmed(
            ctx, operations=[{"op": "upsert_reference", "reference": {"media": {"path": "/media/x.mp3"}}}]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_confirmed_fills_ids_for_new_sections(self):
        form_state = make_form_state(make_doc(), make_music3_capabilities())
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await UpdateMusicDirectorTool().execute_confirmed(
            ctx, operations=[{"op": "upsert_section", "section": {"kind": "verse", "lyrics": "new lyrics"}}]
        )
        payload = json.loads(result.data)
        assert payload["operations"][0]["section"]["id"]


# ---------------------------------------------------------------------------
# Registration / availability
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_tools_are_generation_mode_only(self):
        assert GetMusicDirectorTool().modes == ["generation"]
        assert UpdateMusicDirectorTool().modes == ["generation"]


class TestMusicDirectorToolsIsAvailable:
    @pytest.mark.parametrize("tool_cls", [GetMusicDirectorTool, UpdateMusicDirectorTool])
    def test_unavailable_when_form_state_is_none(self, tool_cls):
        assert tool_cls().is_available(None) is False

    @pytest.mark.parametrize("tool_cls", [GetMusicDirectorTool, UpdateMusicDirectorTool])
    def test_unavailable_when_music_director_absent(self, tool_cls):
        form_state = {"preset": "p", "mode": "m", "form_data": {}}
        assert tool_cls().is_available(form_state) is False

    @pytest.mark.parametrize("tool_cls", [GetMusicDirectorTool, UpdateMusicDirectorTool])
    def test_unavailable_when_inactive(self, tool_cls):
        form_state = make_form_state(make_doc(), make_music3_capabilities(), active=False)
        assert tool_cls().is_available(form_state) is False

    @pytest.mark.parametrize("tool_cls", [GetMusicDirectorTool, UpdateMusicDirectorTool])
    def test_available_when_active(self, tool_cls):
        form_state = make_form_state(make_doc(), make_music3_capabilities(), active=True)
        assert tool_cls().is_available(form_state) is True
