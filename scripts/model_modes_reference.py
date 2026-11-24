#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.features.presets.mode_labels import mode_display_name

START = "<!-- modes:start -->"
END = "<!-- modes:end -->"
NOTE = "_Generated from the preset definitions by `scripts/model_modes_reference.py`; edit the presets, not this list._"

PAGE_PRESETS: Dict[str, List[str]] = {
    "anima.md": ["Anima"],
    "flux.md": ["Flux1", "Flux2"],
    "flux3.md": ["Flux3"],
    "krea2.md": ["Krea2"],
    "ltx.md": ["LTX-2", "LTX-2.5"],
    "minimax_h3.md": ["MiniMax-H3", "MiniMax-H3-Fast", "MiniMax-H3-VDN"],
    "minimax_h3_image.md": ["MiniMax-H3-Image"],
    "minimax_music3.md": ["MiniMax-Music3"],
    "moss_image.md": ["MOSS-Image"],
    "pixal3d.md": ["Pixal3D"],
    "qwen_image.md": ["QwenImage"],
    "qwen_image_21.md": ["QwenImage-2.1"],
    "sdxl.md": ["SDXL"],
    "seedvr2.md": ["SeedVR2"],
    "wan.md": ["Wan"],
    "yue2.md": ["YuE2"],
    "z_image.md": ["ZImage"],
}


@dataclass
class CheckReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def preset_modes(presets_root: Path, preset: str) -> List[tuple]:
    preset_dir = presets_root / preset
    declared = _load_yaml(preset_dir / "preset.yml").get("modes") or []
    rows = []
    for mode in declared:
        if not isinstance(mode, str):
            continue
        label = _load_yaml(preset_dir / "modes" / mode / "form.yml").get("mode_label")
        rows.append((mode, mode_display_name(mode, label if isinstance(label, str) else None)))
    return rows


def render_block(presets_root: Path, presets: Sequence[str]) -> str:
    lines = [START, NOTE, ""]
    for preset in presets:
        if len(presets) > 1:
            lines.append(f"**{preset}**")
            lines.append("")
        rows = preset_modes(presets_root, preset)
        lines.extend(f"- `{mode}`: {label}" for mode, label in rows)
        if not rows:
            lines.append("_No modes declared._")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def _splice(text: str, block: str) -> Optional[str]:
    start = text.find(START)
    end = text.find(END)
    if start < 0 or end < start:
        return None
    return text[:start] + block + text[end + len(END):]


def sync(docs_models: Path, presets_root: Path, mapping: Dict[str, List[str]], write: bool) -> CheckReport:
    report = CheckReport()
    for page, presets in mapping.items():
        path = docs_models / page
        label = f"docs/models/{page}"
        if not path.exists():
            report.errors.append(f"{label}: missing page")
            continue
        missing = [p for p in presets if not (presets_root / p / "preset.yml").exists()]
        if missing:
            report.errors.append(f"{label}: preset(s) not found: {', '.join(missing)}")
            continue
        text = path.read_text(encoding="utf-8")
        fresh = _splice(text, render_block(presets_root, presets))
        if fresh is None:
            report.errors.append(f"{label}: missing {START} / {END} markers")
        elif fresh != text:
            if write:
                path.write_text(fresh, encoding="utf-8")
            else:
                report.errors.append(f"{label}: mode list out of date - run `python scripts/model_modes_reference.py`")
    return report


def check(
    repo_root: Path = REPO_ROOT,
    mapping: Optional[Dict[str, List[str]]] = None,
) -> CheckReport:
    return sync(
        repo_root / "docs" / "models",
        repo_root / "content" / "presets" / "marketplace",
        PAGE_PRESETS if mapping is None else mapping,
        write=False,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the mode lists in docs/models/*.md from the presets.")
    ap.add_argument("--check", action="store_true", help="exit 1 when a page is out of date")
    args = ap.parse_args(argv)
    if args.check:
        report = check()
    else:
        report = sync(
            REPO_ROOT / "docs" / "models",
            REPO_ROOT / "content" / "presets" / "marketplace",
            PAGE_PRESETS,
            write=True,
        )
    for e in report.errors:
        print(f"ERROR {e}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
