import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = ROOT / "content" / "presets" / "marketplace"
KREA2 = "Krea2"
SDXL = "SDXL"
KREA2_ID = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"

KREA_EDITOR = {
    "schema_version": 1,
    "task": "edit_region",
    "from": "edit",
    "region": "crop",
    "inputs": {"source": "source_image"},
    "fixed": {"match_source": True, "edit_task": "edit", "fit_mode": "fit", "ref_position": "aligned"},
    "fields": {
        "hide": ["source_image_b", "ref_position_a", "ref_boost_a"],
        "change": {"resolution": {"label": "Detail", "ai_hint": "The pixel budget of the marked area."}},
        "add": [
            {
                "into": {"tab": "advanced"},
                "fields": [
                    {
                        "name": "region_context",
                        "type": "select",
                        "label": "Surroundings",
                        "audience": "advanced",
                        "default": "0.5",
                        "configuration": {"options": [{"label": "Tight", "value": "0.25"}, {"label": "Balanced", "value": "0.5"}, {"label": "Wide", "value": "1.0"}]},
                    }
                ],
            }
        ],
    },
}

SDXL_INPAINT_EDITOR = {
    "schema_version": 1,
    "task": "edit_region",
    "from": "inpaint",
    "region": "mask",
    "inputs": {"source": "source_image", "mask": "source_image_inpaint_mask"},
}


def copy_preset(name: str, root: Path) -> Path:
    destination = root / name
    shutil.copytree(MARKETPLACE / name, destination, ignore=shutil.ignore_patterns("public", "tests.yml", "styles.yml"))
    return destination


def write_editor_mode(preset_dir: Path, name: str, spec, files: dict = None) -> Path:
    directory = preset_dir / "editor_modes" / name
    directory.mkdir(parents=True)
    text = spec if isinstance(spec, str) else yaml.safe_dump(spec, sort_keys=False)
    (directory / "editor_mode.yml").write_text(text, encoding="utf-8")
    for relative, content in (files or {}).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False), encoding="utf-8"
        )
    return directory
