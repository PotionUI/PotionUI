"""Tests for scripts/preset_new.py's scaffold output against scripts/preset_lint.py's
PresetLinter.

`preset_new.py`'s own docstring promises: "Emits a minimal, schema-valid preset skeleton
that passes `scripts/preset_lint.py`." These tests hold that promise to account - both for
the `native` engine and for the `comfyui` engine example the docstring itself advertises.
"""

from pathlib import Path

from scripts.preset_new import scaffold
from src.features.presets.linter import PresetLinter
from src.platform.util.ids import generate_ulid


def _scaffold(tmp_path: Path, engine_dir: str, engine: str, modes) -> Path:
    target = tmp_path / "presets" / engine_dir / "MyModel" / "standard"
    scaffold(
        target=target,
        preset_id=generate_ulid(),
        name="MyModel standard",
        category="image",
        engine=engine,
        modes=list(modes),
        force=False,
    )
    return target


def _errors(target: Path):
    issues = PresetLinter([str(target)]).lint()
    return [i for i in issues if i.level == "error"]


class TestPresetNewScaffoldLintsClean:
    def test_native_single_mode_lints_clean(self, tmp_path):
        target = _scaffold(tmp_path, "native", "native", ["txt2img"])
        assert _errors(target) == []

    def test_native_multiple_modes_lints_clean(self, tmp_path):
        target = _scaffold(tmp_path, "native", "native", ["txt2img", "img2img"])
        assert _errors(target) == []

    def test_comfyui_multiple_modes_lints_clean(self, tmp_path):
        # Mirrors the docstring's own example:
        #   python scripts/preset_new.py comfyui/MyModel/official --category video \
        #       --modes txt2vid,img2vid
        target = _scaffold(tmp_path, "comfyui", "comfyui", ["txt2vid", "img2vid"])
        assert _errors(target) == []
