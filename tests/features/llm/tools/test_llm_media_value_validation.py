"""LLM-proposed media values are shape- and existence-checked at the tool
boundary, and `search_gallery` hands the model a reusable input path.

A model given only a `generation_id` and a thumbnail constructs
`generations/<id>/0.png` - wrong twice over (the date segment is not
derivable, and videos have no index 0 because the counter pre-increments).
`bind_form` downstream checks containment but never existence, so an invented
path reaches the pipeline and fails there, far from where the model could have
corrected itself.
"""

import json
from unittest.mock import MagicMock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.builtin.update_form_settings_tool import UpdateFormSettingsTool
from src.features.llm.tools.media_values import media_field_names, validate_media_value


@pytest.fixture
def storage(tmp_path):
    """A storage root holding one real generation output."""
    root = tmp_path / "storage"
    gen_dir = root / "generations" / "2026-08-12" / "01ABCDEF"
    gen_dir.mkdir(parents=True)
    (gen_dir / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "uploads").mkdir()
    (root / "uploads" / "u.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


REAL_PATH = "generations/2026-08-12/01ABCDEF/1.png"
# What a model invents from a generation id and nothing else.
INVENTED_PATH = "generations/01ABCDEF/0.png"

# Real presets never lay `properties` out as a flat `{name: spec}` map -
# fields nest under a `tabs` root's `children` tree arbitrarily deep (see
# test_model_values.py's NESTED_SCHEMA_PROPERTIES for the same shape).
NESTED_SCHEMA_PROPERTIES = {
    "tabs": {
        "type": "tabs",
        "children": [
            {
                "type": "tab",
                "title": "Input",
                "children": [
                    {"type": "slider", "name": "steps"},
                    {
                        "type": "section",
                        "children": [
                            {"type": "image", "name": "source_image"},
                        ],
                    },
                ],
            },
        ],
    },
}


def make_settings(storage_root):
    settings = MagicMock()
    settings.get_file_storage_directory.return_value = str(storage_root)
    return settings


def make_preset_manager(media_field="source_image"):
    manager = MagicMock()
    manager.get_form_schema.return_value = {
        "form_schema": {
            "properties": {
                "steps": {"type": "slider", "name": "steps"},
                media_field: {"type": "image", "name": media_field},
            }
        }
    }
    return manager


def make_nested_preset_manager():
    manager = MagicMock()
    manager.get_form_schema.return_value = {"form_schema": {"properties": NESTED_SCHEMA_PROPERTIES}}
    return manager


def make_context(storage_root, form_state=None):
    return ToolContext(
        user_id="user-1",
        session_metadata={"form_state": form_state} if form_state else {},
        preset_manager=make_preset_manager(),
        settings=make_settings(storage_root),
    )


def form_state(form_data=None):
    return {
        "preset": "preset-sdxl",
        "mode": "img2img",
        "form_data": form_data if form_data is not None else {"steps": 30},
    }


class TestMediaFieldDetection:
    def test_media_field_types_are_recognised(self):
        props = {
            "prompt": {"type": "text"},
            "steps": {"type": "slider"},
            "source_image": {"type": "image"},
            "clip": {"type": "video"},
            "track": {"type": "audio"},
            "any": {"type": "media"},
            "blob": {"type": "file"},
        }
        assert media_field_names(props) == {"source_image", "clip", "track", "any", "blob"}

    def test_no_schema_means_no_media_fields(self):
        assert media_field_names(None) == set()

    def test_walks_nested_tabs_sections_and_rows(self):
        """A real preset's `properties` nests fields under `tabs` ->
        `children` several levels deep - a flat `.items()` scan over the
        top-level dict finds nothing on a schema shaped like this."""
        assert media_field_names(NESTED_SCHEMA_PROPERTIES) == {"source_image"}

    def test_ignores_non_media_fields_at_depth(self):
        assert "steps" not in media_field_names(NESTED_SCHEMA_PROPERTIES)


class TestValidateMediaValue:
    def test_real_storage_relative_path_is_accepted(self, storage):
        assert validate_media_value("source_image", REAL_PATH, str(storage)) == []

    def test_invented_path_is_rejected_and_the_message_names_the_valid_form(self, storage):
        errors = validate_media_value("source_image", INVENTED_PATH, str(storage))

        assert errors, "an invented path must not be accepted"
        message = " ".join(errors)
        assert INVENTED_PATH in message
        assert "search_gallery" in message
        assert "videos have no index 0" in message

    def test_media_ref_dict_is_accepted(self, storage):
        value = {"path": REAL_PATH, "relative_path": REAL_PATH, "type": "image"}
        assert validate_media_value("source_image", value, str(storage)) == []

    def test_a_number_is_not_a_media_value(self, storage):
        errors = validate_media_value("source_image", 42, str(storage))
        assert errors and "not a media value" in " ".join(errors)

    def test_traversal_outside_the_root_is_rejected(self, storage, tmp_path):
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"x")
        errors = validate_media_value("source_image", str(outside), str(storage))
        assert errors and "outside your storage" in " ".join(errors)

    def test_clearing_a_media_field_is_allowed(self, storage):
        assert validate_media_value("source_image", None, str(storage)) == []
        assert validate_media_value("source_image", "", str(storage)) == []

    def test_multi_item_list_checks_every_item(self, storage):
        errors = validate_media_value(
            "source_image", [REAL_PATH, INVENTED_PATH], str(storage)
        )
        assert len(errors) == 1
        assert INVENTED_PATH in errors[0]

    def test_no_storage_dir_skips_rather_than_guesses(self):
        assert validate_media_value("source_image", INVENTED_PATH, None) == []


class TestUpdateFormSettingsMediaValidation:
    @pytest.mark.asyncio
    async def test_invented_path_is_refused_with_a_teaching_message(self, storage):
        tool = UpdateFormSettingsTool()
        context = make_context(storage, form_state())

        result = await tool.execute(
            context, changes=[{"field_name": "source_image", "value": INVENTED_PATH}]
        )

        assert result.success is False
        assert "search_gallery" in result.error

    @pytest.mark.asyncio
    async def test_a_real_path_is_previewed(self, storage):
        tool = UpdateFormSettingsTool()
        context = make_context(storage, form_state())

        result = await tool.execute(
            context, changes=[{"field_name": "source_image", "value": REAL_PATH}]
        )

        assert result.success is True
        payload = json.loads(result.data)
        assert payload["change_count"] == 1
        assert "warnings" not in payload

    @pytest.mark.asyncio
    async def test_non_media_fields_are_untouched_by_the_check(self, storage):
        tool = UpdateFormSettingsTool()
        context = make_context(storage, form_state())

        result = await tool.execute(context, changes=[{"field_name": "steps", "value": 12}])

        assert result.success is True
        assert json.loads(result.data)["change_count"] == 1

    @pytest.mark.asyncio
    async def test_approval_does_not_make_an_invented_path_valid(self, storage):
        """`execute_confirmed` replays the model's arguments from storage, so
        the preview's check does not carry over."""
        tool = UpdateFormSettingsTool()
        context = make_context(storage, form_state())

        result = await tool.execute_confirmed(
            context, changes=[{"field_name": "source_image", "value": INVENTED_PATH}]
        )

        assert result.success is False
        assert "search_gallery" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_real_path_is_applied(self, storage):
        tool = UpdateFormSettingsTool()
        context = make_context(storage, form_state())

        result = await tool.execute_confirmed(
            context, changes=[{"field_name": "source_image", "value": REAL_PATH}]
        )

        assert result.success is True
        payload = json.loads(result.data)
        assert payload["applied_changes"][0]["new_value"] == REAL_PATH


class TestRunGenerationMediaOverrides:
    @pytest.mark.asyncio
    async def test_invented_override_path_is_refused_before_preview(self, storage):
        tool = RunGenerationTool()
        context = make_context(storage, form_state())

        result = await tool.execute(context, override_values={"source_image": INVENTED_PATH})

        assert result.success is False
        assert "search_gallery" in result.error

    @pytest.mark.asyncio
    async def test_invented_override_path_never_reaches_the_orchestrator(self, storage):
        tool = RunGenerationTool()
        context = make_context(storage, form_state())
        orchestrator = MagicMock()
        context.generation_orchestrator = orchestrator

        result = await tool.execute_confirmed(
            context, override_values={"source_image": INVENTED_PATH}
        )

        assert result.success is False
        orchestrator.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_override_path_is_accepted(self, storage):
        tool = RunGenerationTool()
        context = make_context(storage, form_state())

        result = await tool.execute(context, override_values={"source_image": REAL_PATH})

        assert result.success is True
        assert json.loads(result.data)["overrides_applied"] == {"source_image": REAL_PATH}

    @pytest.mark.asyncio
    async def test_non_media_overrides_are_untouched(self, storage):
        tool = RunGenerationTool()
        context = make_context(storage, form_state())

        result = await tool.execute(context, override_values={"steps": 8})

        assert result.success is True


class TestGuardFiresOnANestedPreset:
    """`update_form_settings` reads its media fields off a preset's real,
    tabbed `form_schema.properties` - not a flattened stand-in. A field
    buried under `tabs -> tab -> section` must still be caught."""

    @pytest.mark.asyncio
    async def test_invented_path_on_a_nested_field_is_refused(self, storage):
        """`source_image` only exists three levels deep under `tabs`. Before
        the recursive walk, `media_field_names` found nothing on this schema
        shape, so the invented path sailed through untouched."""
        tool = UpdateFormSettingsTool()
        # form_data is flat by field name regardless of the schema's tab
        # nesting - that half of the "known field" check was never the bug.
        context = ToolContext(
            user_id="user-1",
            session_metadata={"form_state": form_state({"steps": 30, "source_image": REAL_PATH})},
            preset_manager=make_nested_preset_manager(),
            settings=make_settings(storage),
        )

        result = await tool.execute(
            context, changes=[{"field_name": "source_image", "value": INVENTED_PATH}]
        )

        assert result.success is False
        assert "search_gallery" in result.error

    @pytest.mark.asyncio
    async def test_real_path_on_a_nested_field_is_still_accepted(self, storage):
        tool = UpdateFormSettingsTool()
        context = ToolContext(
            user_id="user-1",
            session_metadata={"form_state": form_state({"steps": 30, "source_image": None})},
            preset_manager=make_nested_preset_manager(),
            settings=make_settings(storage),
        )

        result = await tool.execute(
            context, changes=[{"field_name": "source_image", "value": REAL_PATH}]
        )

        assert result.success is True


class TestSearchGalleryReturnsAReusablePath:
    @pytest.mark.asyncio
    async def test_match_carries_the_real_file_path(self):
        manager = MagicMock()
        manager.search_gallery.return_value = [
            {"file_id": "f1", "generation_id": "01ABCDEF", "similarity": 0.5}
        ]
        manager.describe_files.return_value = {
            "f1": {
                "file_type": "IMAGE",
                "thumbnail": "generations/2026-08-12/01ABCDEF/thumbs/1_medium.webp",
                "file_path": REAL_PATH,
            }
        }

        result = await SearchGalleryTool().execute(
            ToolContext(user_id="user-1", media_indexer=manager), queries=["fox"]
        )

        match = json.loads(result.data)["results"][0]["matches"][0]
        assert match["path"] == REAL_PATH
        # The thumbnail is a preview and must never be mistaken for the input.
        assert match["path"] != match["thumbnail"]

    @pytest.mark.asyncio
    async def test_the_returned_path_is_one_the_form_boundary_accepts(self, storage):
        """End to end: what search_gallery hands back must survive the check
        `update_form_settings` applies - otherwise the model is being taught a
        value it cannot use."""
        manager = MagicMock()
        manager.search_gallery.return_value = [
            {"file_id": "f1", "generation_id": "01ABCDEF", "similarity": 0.5}
        ]
        manager.describe_files.return_value = {
            "f1": {"file_type": "IMAGE", "thumbnail": "t.webp", "file_path": REAL_PATH}
        }

        search = await SearchGalleryTool().execute(
            ToolContext(user_id="user-1", media_indexer=manager), queries=["fox"]
        )
        found = json.loads(search.data)["results"][0]["matches"][0]["path"]

        applied = await UpdateFormSettingsTool().execute(
            make_context(storage, form_state()),
            changes=[{"field_name": "source_image", "value": found}],
        )

        assert applied.success is True

    def test_the_description_points_at_path_not_thumbnail(self):
        description = SearchGalleryTool().description
        assert "`path`" in description
        assert "never construct a path from a generation id" in description
