from pathlib import Path

import pytest

from src.features.editor_tasks.declarations import DECLARATION_FILE, mode_fields, parse_file
from src.features.presets.loader import PresetTemplateLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"

EXPECTED = {
    ("Flux3", "edit"): "crop",
    ("QwenImage", "edit"): "crop",
    ("MiniMax-H3-Image", "edit"): "crop",
    ("Krea2", "edit"): "crop",
    ("QwenImage-2.1", "edit"): "mask",
    ("QwenImage-2.1", "inpaint"): "mask",
    ("ZImage", "inpaint"): "mask",
    ("MOSS-Image", "inpaint"): "mask",
    ("SDXL", "inpaint"): "mask",
}


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    return {Path(template.path).name: template for template in loader.all()}


@pytest.mark.parametrize("folder, mode", sorted(EXPECTED))
def test_every_named_field_exists_in_the_mode(templates, folder, mode):
    parsed = parse_file(MARKETPLACE / folder / "modes" / mode / DECLARATION_FILE)
    assert parsed.error is None
    fields = mode_fields(templates[folder].modes[mode])

    assert [item.region for item in parsed.declaration.tasks] == [EXPECTED[(folder, mode)]]
    for item in parsed.declaration.tasks:
        image = item.inputs.image if isinstance(item.inputs.image, str) else item.inputs.image.field
        assert image in fields
        if item.inputs.mask:
            assert item.inputs.mask == f"{image}_inpaint_mask"
            assert fields[image].configuration["allow_inpaint"] is True
        named = [*item.set_values, *item.owned]
        if item.size.field:
            named.append(item.size.field)
        assert all(name in fields for name in named)


def test_the_set_of_shipped_files_is_exactly_the_nine():
    found = {(path.parents[2].name, path.parent.name) for path in MARKETPLACE.glob("*/modes/*/editor.yml")}

    assert found == set(EXPECTED)
