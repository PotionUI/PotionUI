#!/usr/bin/env python3
"""
Recipe lint CLI - validates every `content/recipes/{marketplace,local}/*.yml`
file against the Phase-3 setup-recipe schema (see
`src/features/setup/recipe_schema.py`).

Usage:
    python scripts/recipe_lint.py [paths...]    # defaults to marketplace + local

Two layers of checks, both exposed as plain functions any test can import:

  1. `validate_recipe_dict` (offline, schema-only): required fields, types,
     unique plugin/artifact/preset/step ids, recognized step kinds, and every
     step's `params` referencing only ids the recipe itself declares.
  2. `lint_recipe_file` (live, loads the real preset tree): every
     `presets:`/`preset.ensure`/`pipeline.render`/`generation.smoke`
     `preset_id` must resolve to a preset that actually exists under
     `content/presets/marketplace/` or `content/presets/local/`, and any
     `mode` referenced must be one that preset declares.
"""

import sys
from pathlib import Path
from typing import Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.presets.loader import PresetTemplateLoader, plugin_preset_roots  # noqa: E402
from src.features.presets.templates import PresetTemplate  # noqa: E402
from src.features.setup.recipe_catalog import plugin_recipe_roots  # noqa: E402
from src.features.setup.recipe_schema import parse_recipe, validate_recipe_dict  # noqa: E402
from src.platform.plugins.loader import PluginLoader  # noqa: E402


def load_preset_index(
    roots=("content/presets/marketplace", "content/presets/local"), plugin_manifests=None
) -> Dict[str, PresetTemplate]:
    """id -> loaded PresetTemplate, across every core preset root that exists
    plus every plugin-contributed `presets:` root (a recipe can reference a
    preset that ships inside the plugin it requires, e.g. comfyui-backend)."""
    existing = [str(ROOT / r) if not Path(r).is_absolute() else r for r in roots]
    existing += [str(p) for p in plugin_preset_roots(plugin_manifests or [])]
    loader = PresetTemplateLoader(existing)
    loader.load_presets()
    return {p.id: p for p in loader.presets}


def lint_recipe_file(path: Path, preset_index: Dict[str, PresetTemplate]) -> List[str]:
    """Validate one recipe file. Returns a list of issue strings (empty = OK)."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        return [f"Could not parse YAML: {exc}"]

    issues = validate_recipe_dict(data)
    if issues:
        return issues

    recipe = parse_recipe(data, source_path=str(path))

    for preset_ref in recipe.presets:
        if preset_ref.preset_id not in preset_index:
            issues.append(
                f"presets: preset_id '{preset_ref.preset_id}' does not exist under "
                f"content/presets/marketplace/ or content/presets/local/"
            )

    for step in recipe.steps:
        if step.kind not in ("preset.ensure", "pipeline.render", "generation.smoke"):
            continue
        preset_id = step.params.get("preset_id")
        if not preset_id:
            continue
        template = preset_index.get(preset_id)
        if template is None:
            issues.append(f"step '{step.key}': preset_id '{preset_id}' does not exist")
            continue
        mode = step.params.get("mode")
        if mode and mode not in template.modes:
            issues.append(f"step '{step.key}': preset '{preset_id}' has no '{mode}' mode")

    return issues


def main() -> int:
    # Default run: core trees plus every plugin-contributed recipe root
    # (discovered from manifests on disk, same as preset_lint.py). Explicit
    # paths are honoured as-is - plugin discovery is skipped, same reasoning
    # as preset_lint.py's --paths override.
    plugin_manifests: List = []
    if sys.argv[1:]:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        plugin_manifests = PluginLoader().discover_plugins()
        paths = [Path("content/recipes/marketplace"), Path("content/recipes/local")] + list(
            plugin_recipe_roots(plugin_manifests)
        )

    preset_index = load_preset_index(plugin_manifests=plugin_manifests)

    total_issues = 0
    found_any = False

    for base in paths:
        if not base.exists():
            continue
        for recipe_file in sorted(base.glob("*.yml")):
            found_any = True
            issues = lint_recipe_file(recipe_file, preset_index)
            if issues:
                total_issues += len(issues)
                print(f"{recipe_file}:")
                for issue in issues:
                    print(f"  - {issue}")
            else:
                print(f"{recipe_file}: OK")

    if not found_any:
        print("No recipe files found.")
        return 0

    if total_issues:
        print(f"\n{total_issues} issue(s) found.")
        return 1

    print("\nNo issues found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
