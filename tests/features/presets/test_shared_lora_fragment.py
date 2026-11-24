import pytest

from src.features.presets.extensions import walk_fields
from src.features.presets.loader import PresetTemplateLoader

ROOT = "content/presets/marketplace"

SDXL = "01K0W24A3RADXXABH16YQ7KE90"
MOSS = "01M52YQH5XRAH60J82RY5NTM2V"
H3_IMAGE = "01M732Q89GSPYP058BTVGJDR89"
KREA2 = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"


@pytest.fixture(scope="module")
def loader():
    return PresetTemplateLoader([ROOT])


def lora_configs(loader, preset_id, mode):
    template = next(t for t in loader.all() if t.id == preset_id)
    found = []
    for form in template.modes[mode].forms:
        for field, _ in walk_fields(form.fields):
            if field.type == "lora_picker" and field.name == "loras":
                found.append(field.configuration)
    return found


CASES = [
    (SDXL, "txt2img", 10, -10.0, 10.0, False),
    (SDXL, "inpaint", 10, -10.0, 10.0, False),
    (SDXL, "restyle", 10, -10.0, 10.0, False),
    (SDXL, "control", 10, -10.0, 10.0, False),
    (MOSS, "txt2img", 10, -10.0, 10.0, False),
    (MOSS, "inpaint", 10, -10.0, 10.0, False),
    (H3_IMAGE, "txt2img", 10, -2.0, 2.0, False),
    (H3_IMAGE, "edit", 10, -2.0, 2.0, False),
    (KREA2, "txt2img", 10, -10.0, 10.0, True),
    (KREA2, "enhance", 10, -10.0, 10.0, True),
    (KREA2, "upscale", 10, -10.0, 10.0, True),
]


@pytest.mark.parametrize("preset_id,mode,max_items,low,high,windowed", CASES)
def test_lora_tab_limits_come_from_the_shared_fragment(loader, preset_id, mode, max_items, low, high, windowed):
    configs = lora_configs(loader, preset_id, mode)
    assert configs
    for config in configs:
        assert config["max_items"] == max_items
        assert (config["strength_min"], config["strength_max"]) == (low, high)
        assert config["filter_tags"] == "@config:lora_tags"
        names = [row["name"] for row in config.get("row_fields") or []]
        assert names == (["step_start", "step_end"] if windowed else [])


def test_the_hint_and_placeholder_stay_per_family(loader):
    (sdxl,) = lora_configs(loader, SDXL, "txt2img")
    (moss,) = lora_configs(loader, MOSS, "txt2img")
    assert sdxl["placeholder"] == "Select a LoRA..."
    assert moss["placeholder"] == "Select a MOSS-Image LoRA..."
