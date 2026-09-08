"""Tests for FilePresetRepository.preset_to_info - the PresetTemplate ->
PresetInfo conversion."""

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.templates import PresetTemplate, GenerationMode


def _preset(vars=None, llm=None):
    return PresetTemplate(
        id="test-preset",
        name="Test Preset",
        version="1.0.0",
        path="/presets/test",
        modes={GenerationMode.TXT2IMG: []},
        vars=vars,
        llm=llm,
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
