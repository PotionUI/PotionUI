"""Tests for FilePresetRepository.preset_to_info - the PresetTemplate ->
PresetInfo conversion."""

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.templates import PresetTemplate, GenerationMode


def _preset(vars=None, llm=None, styles=None):
    return PresetTemplate(
        id="test-preset",
        name="Test Preset",
        version="1.0.0",
        path="/presets/test",
        modes={GenerationMode.TXT2IMG: []},
        vars=vars,
        llm=llm,
        styles=styles or [],
    )


class TestPresetToInfo:
    def test_vars_pass_through_the_constructor(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset(vars={"key": "value"}))

        assert info.vars == {"key": "value"}

    def test_llm_pass_through_the_constructor(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset(llm={"guide": "Use tags."}))

        assert info.llm == {"guide": "Use tags."}

    def test_vars_and_llm_default_to_empty_dict(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset())

        assert info.vars == {}
        assert info.llm == {}


class TestPresetToInfoStyles:
    """`styles:` is gated by `include_styles` the same way `media.gallery` is
    gated by `include_gallery` - the list endpoint stays cheap, the detail
    endpoint (`operations.get_preset`) passes True."""

    STYLE = {
        "id": "retro-90s-cel",
        "name": "Retro 90s Anime Cel",
        "category": "Anime",
        "prepend": "old, ",
        "append": ", retro.",
        "example_prompt": "a cat",
    }

    def test_styles_omitted_by_default(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset(styles=[self.STYLE]))

        assert info.styles == []

    def test_styles_included_when_requested(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset(styles=[self.STYLE]), include_styles=True)

        assert len(info.styles) == 1
        assert info.styles[0].id == "retro-90s-cel"
        assert info.styles[0].description is None
        assert info.styles[0].preview is None

    def test_no_styles_yields_empty_list_even_when_included(self):
        repo = FilePresetRepository(preset_loader=None)

        info = repo.preset_to_info(_preset(), include_styles=True)

        assert info.styles == []
