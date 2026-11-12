from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from src.features.forms.binding import bind_form
from src.features.forms.exceptions import FormNotFoundException
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.presets import retired_modes
from src.features.presets.exceptions import ModeNotFoundException
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.operations.form_overrides import _validate_mode_exists
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from tests.features.presets.test_preset_extensions import PRESET_ID, _field_ext, _load, _plugin, _tab, _names, _write_preset

H3_PRESET_ID = "01KX47H3MINIMAX000000000VA"
RETIRED = {"retired_modes": {"long": "video"}}


def preset_with(vars_):
    form = FormTemplate(name="custom", fields=[FieldTemplate(type="string", name="steps")], default=True, order=0)
    return PresetTemplate(
        id="h3", name="H3", version="1.0.0", path="/presets/h3",
        modes={"video": ModeTemplate(forms=[form], pipes=[])}, vars=vars_,
    )


class TestHelpers:
    def test_the_replacement_is_read_from_the_preset_vars(self):
        assert retired_modes.replacement_for(RETIRED, "long") == "video"
        assert retired_modes.is_retired(RETIRED, "long") is True

    @pytest.mark.parametrize("preset_vars", [None, {}, {"retired_modes": None}, {"retired_modes": ["long"]}, {"retired_modes": {"long": ""}}, MagicMock()])
    def test_nothing_is_retired_without_a_valid_declaration(self, preset_vars):
        assert retired_modes.replacement_for(preset_vars, "long") is None
        assert retired_modes.replacement_note(preset_vars, "long") == ""

    def test_another_mode_is_not_retired(self):
        assert retired_modes.is_retired(RETIRED, "video") is False


class TestBinding:
    def test_a_retired_mode_names_its_replacement(self):
        with pytest.raises(FormNotFoundException) as raised:
            bind_form(preset_with(RETIRED), "long", None, {}, "user_1")

        assert raised.value.replaced_by == "video"
        assert "Mode 'long' was replaced by 'video'" in str(raised.value)
        assert "No form configuration found for mode 'long'" in str(raised.value)

    def test_an_unknown_mode_keeps_the_old_message(self):
        with pytest.raises(FormNotFoundException) as raised:
            bind_form(preset_with(RETIRED), "nope", None, {}, "user_1")

        assert raised.value.replaced_by is None
        assert "replaced" not in str(raised.value)

    def test_a_preset_without_a_declaration_is_unchanged(self):
        with pytest.raises(FormNotFoundException) as raised:
            bind_form(preset_with(None), "long", None, {}, "user_1")

        assert str(raised.value) == "No form configuration found for mode 'long' in preset 'h3'"

    def test_the_constructor_still_takes_the_three_v010_arguments(self):
        assert str(FormNotFoundException("p", "m", "f")) == "No form configuration found for mode 'm' and form 'f' in preset 'p'"


class TestModeLookup:
    def test_the_form_overrides_mode_check_names_the_replacement(self):
        found = preset_with(RETIRED)

        with pytest.raises(ModeNotFoundException) as raised:
            _validate_mode_exists(found, "h3", "long")

        assert raised.value.replaced_by == "video"
        assert "Mode 'long' was replaced by 'video'." in str(raised.value)

    def test_the_v010_three_argument_form_still_builds(self):
        assert str(ModeNotFoundException("p", "m", ["a"])) == "Mode 'm' not found in preset 'p'. Available modes: ['a']"


class TestStartGenerationTool:
    @staticmethod
    def collaborators(vars_):
        collaborators = MagicMock()
        collaborators.db_repo.get_available_preset_ids_for_user.return_value = ["h3"]
        collaborators.file_repo.find_preset_by_id.return_value = SimpleNamespace(
            modes={"video": {}, "txt2video": {}}, vars=vars_
        )
        return collaborators

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["execute", "execute_confirmed"])
    async def test_a_retired_mode_is_refused_with_the_replacement_named(self, method):
        context = ToolContext(
            user_id="u1", is_admin=False, preset_collaborators=self.collaborators(RETIRED),
            generation_orchestrator=MagicMock(),
        )

        result = await getattr(StartGenerationTool(), method)(context, preset_id="h3", mode="long")

        assert result.success is False
        assert "has no mode 'long'. Mode 'long' was replaced by 'video'." in result.error
        assert "Its modes are: video, txt2video" in result.error

    @pytest.mark.asyncio
    async def test_a_plain_unknown_mode_keeps_the_old_message(self):
        context = ToolContext(user_id="u1", is_admin=False, preset_collaborators=self.collaborators(RETIRED))

        result = await StartGenerationTool().execute(context, preset_id="h3", mode="nope")

        assert result.error == "Preset 'h3' has no mode 'nope'. Its modes are: video, txt2video."


class TestPluginExtensionsListingARetiredMode:
    def test_the_live_modes_still_get_the_extension(self, tmp_path):
        root = tmp_path / "presets"
        _write_preset(root, modes=("txt2img",), extra_manifest={"vars": {"retired_modes": {"long": "txt2img"}}})

        loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", _field_ext(), modes=("txt2img", "long"))])

        assert loader.load_errors == {}
        assert "strength" in _names(_tab(template.modes["txt2img"].forms[0], "main").children)

    def test_an_extension_naming_only_the_retired_mode_is_a_quiet_no_op(self, tmp_path):
        root = tmp_path / "presets"
        _write_preset(root, modes=("txt2img",), extra_manifest={"vars": {"retired_modes": {"long": "txt2img"}}})

        loader, template = _load(tmp_path, [_plugin(tmp_path, "ext-a", _field_ext(), modes=("long",))])

        assert loader.load_errors == {}
        assert "strength" not in _names(_tab(template.modes["txt2img"].forms[0], "main").children)

    def test_a_mode_nobody_retired_still_rejects_the_extension(self, tmp_path):
        root = tmp_path / "presets"
        _write_preset(root, modes=("txt2img",), extra_manifest={"vars": {"retired_modes": {"long": "txt2img"}}})

        loader, _ = _load(tmp_path, [_plugin(tmp_path, "ext-a", _field_ext(), modes=("txt2img", "nope"))])

        assert "has no mode 'nope'" in " ".join(m for msgs in loader.load_errors.values() for m in msgs)


class TestShippedPreset:
    @pytest.fixture(scope="class")
    def h3(self):
        loader = PresetTemplateLoader(["content/presets"])
        loader.load_presets()
        return next(p for p in loader.presets if p.id == H3_PRESET_ID)

    def test_long_is_gone_and_declared_retired_in_favour_of_video_director(self, h3):
        assert "long" not in h3.modes
        assert h3.vars["retired_modes"] == {"long": "video"}
        assert "video" in h3.modes

    def test_a_v010_generate_call_naming_long_gets_the_replacement_in_its_404(self, h3):
        with pytest.raises(FormNotFoundException) as raised:
            bind_form(h3, "long", None, {}, "user_1")

        assert raised.value.replaced_by == "video"

    def test_the_retired_target_is_a_real_mode(self, h3):
        assert h3.vars["retired_modes"]["long"] in h3.modes
