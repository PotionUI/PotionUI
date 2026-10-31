from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from scripts.preset_render import (
    ROOT,
    build_fixture_form_data,
    build_generation_data,
    build_processor,
    load_all_presets,
)

SDXL_ROOT = Path("content/presets/marketplace/SDXL")


def load_sdxl():
    presets, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return next(p for p in presets if p.path.replace("\\", "/").rstrip("/").endswith("/SDXL"))


def render(preset, mode: str, positive: Optional[str] = None, **overrides: Any) -> List[Dict[str, Any]]:
    form = build_fixture_form_data(preset, mode)
    form.update(overrides)
    data = build_generation_data(mode, form, preset.modes.get(mode))
    if positive is not None:
        data["prompts"] = [{"positive": positive, "negative": "bad"}] * len(data["prompts"])
    return build_processor().process(preset, data)


def pipe(pipes: List[Dict[str, Any]], pipe_id: str) -> Dict[str, Any]:
    return next(p for p in pipes if p.get("id") == pipe_id)


def wired(p: Dict[str, Any]):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in p["input"]}


def provider(p: Dict[str, Any], name: str) -> str:
    return next(edge["provider"] for edge in p["input"] if edge["name"] == name)


def is_enabled(p: Dict[str, Any]) -> bool:
    return str(p["enabled"]).lower() == "true"


def mode_form(mode: str) -> Dict[str, Any]:
    return yaml.safe_load((SDXL_ROOT / "modes" / mode / "form.yml").read_text(encoding="utf-8"))


def mode_fields(mode: str, tab: str) -> Dict[str, Dict[str, Any]]:
    data = yaml.safe_load((SDXL_ROOT / "modes" / mode / "tabs" / f"{tab}.yml").read_text(encoding="utf-8"))
    found: Dict[str, Dict[str, Any]] = {}

    def walk(items):
        for item in items or []:
            if "name" in item:
                found[item["name"]] = item
            walk(item.get("children"))

    walk(data["fields"])
    return found


def preset_manifest() -> Dict[str, Any]:
    return yaml.safe_load((SDXL_ROOT / "preset.yml").read_text(encoding="utf-8"))
