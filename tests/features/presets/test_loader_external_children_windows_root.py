"""External `children:` fragments resolve against a Windows-shaped preset root.

The loader and the linter substitute `{{ paths.preset }}` with the preset
directory through `re.sub`. Given as a template string, a Windows path such
as `D:\\a\\_temp\\potionui` is parsed for escapes and `\\p` raises
`re.error: bad escape` - which dropped every shipped preset on the first
Windows CI run, since each one loads its tabs from external files.
"""
from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader

WINDOWS_ROOT = Path(r"D:\a\_temp\potionui-install-smoke\content\presets\marketplace\Anima\v1")


def test_loader_resolves_children_path_literally(monkeypatch):
    loader = PresetTemplateLoader([])
    seen = {}

    def fake_load(children_file, prefix):
        seen["path"] = children_file
        return []

    monkeypatch.setattr(loader, "_load_external_children_file", fake_load)
    monkeypatch.setattr(
        "src.features.presets.loader._known_field_types", lambda: {"section"}
    )

    loader._build_field_template(
        {"name": "advanced", "type": "section", "children": "{{ paths.preset }}/modes/txt2img/tabs/advanced.yml"},
        WINDOWS_ROOT,
        "modes/txt2img/form.yml",
    )

    assert seen["path"] == Path(str(WINDOWS_ROOT) + "/modes/txt2img/tabs/advanced.yml")


def test_linter_resolves_children_path_literally():
    resolved = PresetLinter._resolve_children_path("{{ paths.preset }}/modes/txt2img/tabs/advanced.yml", WINDOWS_ROOT)
    assert resolved == Path(str(WINDOWS_ROOT) + "/modes/txt2img/tabs/advanced.yml")
