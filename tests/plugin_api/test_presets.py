"""`src.plugin_api.presets.lint_preset_dir` must validate `requirements:`
against the live, process-wide requirement-checker registry - a plugin
enabled at runtime (not just at boot) registers its checkers there, and a
lint built purely from discovered manifests never sees it.
"""

from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from src.plugin_api.presets import lint_preset_dir
from src.platform.plugins.requirement_checkers import (
    RequirementCheckerRegistration,
    RequirementCheckerRegistry,
)


class _LiveOnlyCheckSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str


class _LiveOnlyChecker:
    type = "live_only_check"
    schema = _LiveOnlyCheckSchema

    async def check(self, spec, ctx):
        raise NotImplementedError


def _write_preset(preset_dir, preset_id, requirements_yaml):
    preset_dir = preset_dir / preset_id
    preset_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test Preset"
version: "1.0.0"
category: "image"
engine: "native"
requirements:
{requirements_yaml}
modes:
  - txt2img
"""
    )
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    return preset_dir


def test_lint_preset_dir_uses_the_live_container_registry(tmp_path):
    registry = RequirementCheckerRegistry()
    registry.register(RequirementCheckerRegistration(
        type_name="live_only_check",
        checker=_LiveOnlyChecker(),
        source="fixture-plugin",
    ))
    container = SimpleNamespace(requirement_checker_registry=registry)
    preset_dir = _write_preset(tmp_path, "01LLLLLLLLLLLLLLLLLLLLLLLLL", "  - type: live_only_check\n")

    with patch("src.plugin_api.presets.get_container", return_value=container):
        errors, warnings = lint_preset_dir(str(preset_dir))

    assert not any("unknown type" in e for e in errors)


def test_lint_preset_dir_reports_unknown_type_absent_from_live_registry(tmp_path):
    container = SimpleNamespace(requirement_checker_registry=RequirementCheckerRegistry())
    preset_dir = _write_preset(tmp_path, "01MMMMMMMMMMMMMMMMMMMMMMMMM", "  - type: live_only_check\n")

    with patch("src.plugin_api.presets.get_container", return_value=container):
        errors, warnings = lint_preset_dir(str(preset_dir))

    assert any("unknown type 'live_only_check'" in e for e in errors)


def test_lint_preset_dir_tolerates_an_uninitialized_container(tmp_path):
    """A plugin's own unit tests call this without booting the app - must
    not crash, just skip the live-registry override (mirrors the pre-fix
    core-checkers-only behavior)."""
    preset_dir = _write_preset(tmp_path, "01NNNNNNNNNNNNNNNNNNNNNNNNN", "  - type: binary\n    name: ffmpeg\n")

    with patch("src.plugin_api.presets.get_container", side_effect=RuntimeError("AppContainer not initialized yet")):
        errors, warnings = lint_preset_dir(str(preset_dir))

    assert not any("requirements[" in e for e in errors)
