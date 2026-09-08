"""Tests for the pipeline-side lint/render tooling in
scripts/preset_lint.py (`--render`) and scripts/preset_render.py (`--root`,
the pre-print pipe/wiring lint).
"""

from pathlib import Path

import pytest

from scripts.preset_lint import _in_scope, render_check
from scripts.preset_render import load_all_presets


def _write_preset_tree(root: Path, preset_id: str, pipeline_yaml: str) -> Path:
    preset_dir = root / "RenderLint" / "std"
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Render Lint Test"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
    )
    (mode_dir / "pipeline.yml").write_text(pipeline_yaml)
    (mode_dir / "form.yml").write_text("name: default\nfields: []\n")
    return preset_dir


class TestInScope:
    def test_path_under_root_is_in_scope(self, tmp_path):
        root = tmp_path / "presets"
        preset_dir = root / "Foo" / "std"
        preset_dir.mkdir(parents=True)
        assert _in_scope(str(preset_dir), [root.resolve()])

    def test_path_outside_root_is_not_in_scope(self, tmp_path):
        root = tmp_path / "presets"
        other = tmp_path / "elsewhere" / "Foo" / "std"
        other.mkdir(parents=True)
        root.mkdir()
        assert not _in_scope(str(other), [root.resolve()])

    def test_root_itself_is_in_scope(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        assert _in_scope(str(root), [root.resolve()])


class TestRenderCheck:
    def test_template_evaluation_error_reported_with_pipe_id_and_config_path(self, tmp_path):
        preset_dir = _write_preset_tree(
            tmp_path, "render_check_div_zero",
            'pipeline:\n  - name: "gallery"\n    id: "gallery_stage"\n'
            '    enabled: true\n    configuration:\n      derived: "{{ 1 / 0 }}"\n',
        )
        issues = render_check([str(preset_dir)], presets_root=tmp_path)
        assert any(
            i.level == "error"
            and "pipe_id='gallery_stage'" in i.message
            and "config_path='config.derived'" in i.message
            for i in issues
        )

    def test_clean_render_produces_no_issues(self, tmp_path):
        preset_dir = _write_preset_tree(
            tmp_path, "render_check_clean",
            'pipeline:\n  - name: "gallery"\n    id: "gallery_stage"\n'
            '    enabled: true\n    configuration:\n      derived: false\n',
        )
        issues = render_check([str(preset_dir)], presets_root=tmp_path)
        assert issues == []

    def test_out_of_scope_preset_is_not_rendered(self, tmp_path):
        # Broken preset lives under tmp_path, but the scope passed to
        # render_check is a disjoint sibling directory - must produce nothing.
        _write_preset_tree(
            tmp_path, "render_check_out_of_scope",
            'pipeline:\n  - name: "gallery"\n    id: "gallery_stage"\n'
            '    enabled: true\n    configuration:\n      derived: "{{ 1 / 0 }}"\n',
        )
        unrelated_scope = tmp_path / "nothing_here"
        unrelated_scope.mkdir()
        issues = render_check([str(unrelated_scope)], presets_root=tmp_path)
        assert issues == []


class TestLoadAllPresetsRoot:
    """`preset_render.py --root` threads through to `load_all_presets`'s
    `presets_root` - the same parameter `render_check` now also exposes."""

    def test_custom_root_is_scanned(self, tmp_path):
        _write_preset_tree(tmp_path, "root_scan_test", 'pipeline: []\n')
        presets, _load_errors = load_all_presets(presets_root=tmp_path, include_plugins=False)
        assert any(p.id == "root_scan_test" for p in presets)
