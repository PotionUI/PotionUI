#!/usr/bin/env python3
"""
Preset lint/migration CLI.

Usage:
    python scripts/preset_lint.py [paths...]              # lint only, exit 1 on any error
    python scripts/preset_lint.py --fix [paths...]         # migrate to canonical schema, then lint

With no paths given, lints/migrates `content/presets/marketplace/` and
`content/presets/local/`.

--fix performs a mechanical, comment-preserving text migration of each preset.yml:
  1. add `schema: 1` if missing
  2. convert `modes:` from a mapping (with null/dict values) to a plain list
  3. move inline `description:` to description.md (only if no description.md exists yet)
  4. add explicit `engine:` using the auto-detect logic (comfyui path/pipe name ->
     "comfyui", else -> "native"). The linter separately enforces, as a live check,
     that a preset's pipes match the engine it declares.
  5. infer and add `category:` if missing (video/audio/3d/utility heuristics, else image) -
     ALWAYS reviewed and printed in the summary since inference can be wrong
  6. delete dead top-level keys never read by any loader: `resolutions`, `prompt_helpers`,
     `author`, `model`, `extras`

This script never touches `modes/<mode>/pipeline.yml` or form files - those are already
schema-compliant for all 22 shipped presets (only preset.yml needed migrating).
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.presets.linter import PresetLinter, LintIssue  # noqa: E402
from src.features.presets.loader import plugin_preset_roots  # noqa: E402
from src.features.presets.schema import SEMVER_RE  # noqa: E402
from src.platform.plugins.loader import PluginLoader  # noqa: E402

DEAD_TOP_LEVEL_KEYS = ("resolutions", "prompt_helpers", "author", "model", "extras")


# ---------------------------------------------------------------------------
# Text-level block helpers (indentation-aware, comment-preserving)
# ---------------------------------------------------------------------------


def _find_top_level_block(lines: List[str], key: str) -> Optional[Tuple[int, int]]:
    """Find a top-level (column 0) `key:` entry and the line range [start, end)
    covering it and all of its indented continuation lines (its value block)."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if pattern.match(line):
            end = i + 1
            while end < len(lines):
                nxt = lines[end]
                if nxt.strip() == "" or nxt.startswith((" ", "\t")):
                    end += 1
                    continue
                break
            return i, end
    return None


def _insert_after_key(lines: List[str], anchor_key: str, new_line: str) -> List[str]:
    """Insert `new_line` right after the top-level block for `anchor_key` (or at the
    top of the file if the anchor isn't found)."""
    block = _find_top_level_block(lines, anchor_key)
    if block is None:
        return [new_line] + lines
    _, end = block
    return lines[:end] + [new_line] + lines[end:]


def _remove_block(lines: List[str], key: str) -> List[str]:
    block = _find_top_level_block(lines, key)
    if block is None:
        return lines
    start, end = block
    return lines[:start] + lines[end:]


def _has_top_level_key(lines: List[str], key: str) -> bool:
    return _find_top_level_block(lines, key) is not None


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------


def add_schema_marker(lines: List[str]) -> List[str]:
    if _has_top_level_key(lines, "schema"):
        return lines
    id_block = _find_top_level_block(lines, "id")
    if id_block is None:
        return ["schema: 1"] + lines
    start, _ = id_block
    return lines[:start] + ["schema: 1"] + lines[start:]


def convert_modes_to_list(lines: List[str]) -> Tuple[List[str], List[str]]:
    """Convert `modes:` mapping (values null/dict) into a plain list of mode names.
    Returns (new_lines, mode_names)."""
    block = _find_top_level_block(lines, "modes")
    if block is None:
        return lines, []

    start, end = block
    mode_names: List[str] = []
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r'^([A-Za-z0-9_\-]+)\s*:', stripped)
        if m:
            mode_names.append(m.group(1))

    new_block = ["modes:"] + [f"  - {name}" for name in mode_names]
    return lines[:start] + new_block + lines[end:], mode_names


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def move_description_to_md(lines: List[str], preset_dir: Path) -> List[str]:
    block = _find_top_level_block(lines, "description")
    if block is None:
        return lines

    start, end = block
    description_md = preset_dir / "description.md"
    if description_md.exists():
        # description.md already exists - just drop the (now-redundant) inline key
        return lines[:start] + lines[end:]

    first_line = lines[start]
    inline_value = first_line.split(":", 1)[1] if ":" in first_line else ""
    inline_value = strip_quotes(inline_value)

    if inline_value:
        text = inline_value
    else:
        # multi-line block scalar (| or >) - join continuation lines, dedented
        body_lines = lines[start + 1:end]
        text = "\n".join(l.strip() for l in body_lines).strip()

    description_md.write_text(text + "\n")
    return lines[:start] + lines[end:]


def strip_dead_keys(lines: List[str]) -> List[str]:
    for key in DEAD_TOP_LEVEL_KEYS:
        lines = _remove_block(lines, key)
    return lines


def detect_engine(preset_dir: Path, mode_names: List[str]) -> str:
    """Auto-detect a preset's engine: `comfyui` in the preset path, or any pipe in
    any mode's pipeline.yml named with 'comfyui' in it -> "comfyui", else "native"."""
    if "comfyui" in str(preset_dir).lower():
        return "comfyui"

    for mode_name in mode_names:
        pipeline_file = preset_dir / "modes" / mode_name / "pipeline.yml"
        if not pipeline_file.exists():
            continue
        try:
            data = yaml.safe_load(pipeline_file.read_text()) or {}
        except Exception:
            continue
        for pipe in (data.get("pipeline") or []):
            name = str(pipe.get("name", "")).lower()
            if "comfyui" in name:
                return "comfyui"

    return "native"


CATEGORY_HEURISTICS = (
    (re.compile(r"maya", re.I), "audio"),
    (re.compile(r"videotools", re.I), "utility"),
    (re.compile(r"wan_?2_?2|wan22", re.I), "video"),
    (re.compile(r"seedvr2", re.I), "video"),
    (re.compile(r"carousel_demo", re.I), "utility"),
    (re.compile(r"trellis", re.I), "3d"),
)


def infer_category(preset_dir: Path) -> Tuple[str, bool]:
    """Returns (category, confident). confident=False -> flag for human review."""
    path_str = str(preset_dir)
    for pattern, category in CATEGORY_HEURISTICS:
        if pattern.search(path_str):
            # SeedVR2 (also has an image_upscale mode) and carousel_demo (a field-type
            # demo, not really a generator) are judgment calls - flag for human review.
            confident = "seedvr2" not in path_str.lower() and "carousel_demo" not in path_str.lower()
            return category, confident
    return "image", True


def migrate_preset(preset_path: Path) -> List[str]:
    """Migrate a single preset.yml in place. Returns a human-readable summary of changes."""
    changes: List[str] = []
    preset_dir = preset_path.parent
    original_text = preset_path.read_text()
    lines = original_text.splitlines()

    try:
        original_data = yaml.safe_load(original_text) or {}
    except Exception as e:
        return [f"SKIPPED (unparsable YAML: {e})"]

    if "schema" not in original_data:
        lines = add_schema_marker(lines)
        changes.append("added schema: 1")

    raw_version = str(original_data.get("version", ""))
    if not SEMVER_RE.match(raw_version):
        m = re.match(r"^v?(\d+)$", raw_version.strip(), re.I)
        new_version = f"{m.group(1)}.0.0" if m else "1.0.0"
        block = _find_top_level_block(lines, "version")
        if block is not None:
            start, end = block
            lines = lines[:start] + [f'version: "{new_version}"'] + lines[end:]
        changes.append(
            f"normalized non-semver version '{raw_version}' -> '{new_version}'  [REVIEW: low-confidence guess]"
        )

    modes_value = original_data.get("modes")
    mode_names: List[str] = []
    if isinstance(modes_value, dict):
        lines, mode_names = convert_modes_to_list(lines)
        changes.append(f"converted modes: mapping -> list ({', '.join(mode_names)})")
    elif isinstance(modes_value, list):
        mode_names = list(modes_value)

    if "description" in original_data and original_data.get("description"):
        description_md = preset_dir / "description.md"
        already_existed = description_md.exists()
        lines = move_description_to_md(lines, preset_dir)
        if already_existed:
            changes.append("removed inline description: (description.md already existed)")
        else:
            changes.append("moved inline description: -> description.md")

    for key in DEAD_TOP_LEVEL_KEYS:
        if key in original_data:
            changes.append(f"removed dead top-level key: {key}")
    lines = strip_dead_keys(lines)

    if "engine" not in original_data:
        engine = detect_engine(preset_dir, mode_names)
        lines = _insert_after_key(lines, "version", f'engine: "{engine}"')
        changes.append(f"added engine: {engine} (auto-detected)")

    if "category" not in original_data or original_data.get("category") not in (
        "image", "video", "audio", "3d", "utility"
    ):
        category, confident = infer_category(preset_dir)
        anchor = "version" if _has_top_level_key(lines, "version") else "id"
        if "category" in original_data:
            lines = _remove_block(lines, "category")
            changes.append(f"replaced invalid category: {original_data.get('category')!r} -> {category!r}"
                            + ("" if confident else "  [REVIEW: low-confidence guess]"))
        else:
            changes.append(f"added category: {category!r}" + ("" if confident else "  [REVIEW: low-confidence guess]"))
        lines = _insert_after_key(lines, anchor, f'category: "{category}"')

    new_text = "\n".join(lines) + "\n"
    if new_text != original_text:
        preset_path.write_text(new_text)

    return changes if changes else ["no changes needed"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument("--fix", action="store_true", help="Migrate preset.yml files to the canonical schema")
    args = parser.parse_args()

    # Default run: core trees plus every plugin-contributed preset root
    # (discovered from manifests on disk). Explicit paths are honoured as-is -
    # same reasoning for skipping plugin discovery (incl. preset_modes
    # cross-checks) as for plugin-owned presets: roots.
    plugin_manifests: List = []
    if args.paths:
        paths = list(args.paths)
    else:
        plugin_manifests = PluginLoader().discover_plugins()
        paths = ["content/presets/marketplace", "content/presets/local"] + [
            str(p) for p in plugin_preset_roots(plugin_manifests)
        ]

    if args.fix:
        print("=== Migrating presets ===\n")
        for base_path in paths:
            base = Path(base_path)
            if not base.exists():
                continue
            for preset_file in sorted(base.rglob("preset.yml")):
                changes = migrate_preset(preset_file)
                print(f"{preset_file}:")
                for change in changes:
                    print(f"  - {change}")
                print()

    print("=== Linting presets ===\n")
    linter = PresetLinter(paths, plugin_manifests=plugin_manifests)
    issues = linter.lint()

    if not issues:
        print("No issues found.")
        return 0

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    for issue in issues:
        print(str(issue))

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
