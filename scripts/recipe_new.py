import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.presets.loader import PresetTemplateLoader, plugin_preset_roots
from src.features.presets.schema import CATEGORIES
from src.features.recipes.derived import provider_plugin_resolver, recommendation_providers
from src.features.recipes.mode_slots import derive_mode_slots
from src.features.recipes.schema import validate_recipe_dict
from src.platform.plugins.loader import PluginLoader

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class _Manifests:
    def __init__(self, manifests):
        self._by_id = {m.id: m for m in manifests}

    def get_plugin(self, plugin_id):
        return self._by_id.get(plugin_id)


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def find_preset(ref: str, presets):
    for preset in presets:
        if preset.id == ref:
            return preset
    lowered = ref.lower()
    for preset in presets:
        if preset.name.lower() == lowered or Path(preset.path).name.lower() == lowered:
            return preset
    return None


def render(preset, base_mode: str, plugin_ids: List[str], extras: List[str]) -> str:
    slug = _SLUG_RE.sub("-", preset.name.lower()).strip("-") or "preset"
    category = preset.category if preset.category in CATEGORIES else "utility"
    lines = [
        "schema_version: 1",
        f"id: {_q(slug + '-starter')}",
        "version: 1",
        f"name: {_q(preset.name + ' Starter')}",
        f"engine: {_q(preset.engine or 'native')}",
        f"category: {_q(category)}",
        f"summary: {_q('Get a working ' + preset.name + ' setup and produce a first result.')}",
    ]
    if plugin_ids:
        lines.append("plugins:")
        for plugin_id in plugin_ids:
            lines += [f"  - id: {_q(plugin_id)}", f"    reason: {_q('Resolves the files this preset recommends.')}"]
    lines += [
        "backend:",
        f"  engine: {_q(preset.engine or 'native')}",
        "artifacts: []",
        "presets:",
        f"  - preset_id: {_q(preset.id)}",
        "    assign_to_owner: true",
        "smoke:",
        f"  preset_id: {_q(preset.id)}",
        f"  mode: {_q(base_mode)}",
        "  seed: 12345",
        "  form: {}",
        "steps:",
    ]
    steps = []
    if plugin_ids:
        steps.append(("plugins.ensure", "plugins.ensure", "Enable required plugins", [f"plugin_ids: [{', '.join(_q(p) for p in plugin_ids)}]"]))
    engine = preset.engine or "native"
    plan_params = [
        f"preset_id: {_q(preset.id)}",
        'modes: "all"',
        f"selected: [{_q(base_mode)}]",
        f"locked: [{_q(base_mode)}]",
        'extras: "offer"',
    ]
    if extras:
        plan_params.append(f"preselect_extras: [{', '.join(_q(e) for e in extras)}]")
    steps += [
        ("backend.ensure", "backend.ensure", "Prepare the backend", [f"engine: {_q(engine)}"]),
        ("models.index", "models.index", "Find models you already have", [f"engine: {_q(engine)}"]),
        ("modes.plan", "modes.plan", "Pick modes and review downloads", plan_params),
        ("artifacts.fetch", "artifacts.fetch", "Download the files", ['plan: "modes.plan"']),
        ("models.index.downloads", "models.index", "Register downloaded models", [f"engine: {_q(engine)}"]),
        ("preset.ensure", "preset.ensure", "Install and assign the preset", [f"preset_id: {_q(preset.id)}"]),
        ("preset.defaults", "preset.defaults", "Use the downloaded files by default", ['plan: "modes.plan"']),
        ("pipeline.render", "pipeline.render", "Validate each mode", [f"preset_id: {_q(preset.id)}", 'plan: "modes.plan"']),
        ("generation.smoke", "generation.smoke", "Run a first real generation", [f"preset_id: {_q(preset.id)}", f"mode: {_q(base_mode)}"]),
    ]
    for key, kind, title, params in steps:
        lines += [f"  - key: {_q(key)}", f"    kind: {_q(kind)}", f"    title: {_q(title)}", "    params:"]
        lines += [f"      {param}" for param in params]
    lines += [
        '  - key: "workspace.activate"',
        '    kind: "workspace.activate"',
        '    title: "Finish onboarding"',
        "    onboarding_only: true",
        "    params: {}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a mode-aware starter recipe for a preset.")
    parser.add_argument("preset", help="preset id, name, or folder name")
    parser.add_argument("--mode", help="the base mode the starter locks and smoke-tests (default: the preset's first mode)")
    parser.add_argument("--extras", nargs="*", default=[], help="extra slot ids to tick by default")
    parser.add_argument("--out", help="where to write (default: content/recipes/local/<slug>-starter.yml)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args(argv)

    manifests = PluginLoader().discover_plugins()
    roots = [str(ROOT / "content/presets/marketplace"), str(ROOT / "content/presets/local")]
    roots += [str(p) for p in plugin_preset_roots(manifests)]
    loader = PresetTemplateLoader([r for r in roots if Path(r).exists()])
    loader.load_presets()
    preset = find_preset(args.preset, loader.presets)
    if preset is None:
        print(f"No preset matches '{args.preset}'.")
        return 1
    modes = list(preset.modes or {})
    base_mode = args.mode or (modes[0] if modes else None)
    if base_mode not in (preset.modes or {}):
        print(f"'{preset.name}' has no '{base_mode}' mode (modes: {', '.join(modes)}).")
        return 1
    plan = derive_mode_slots(preset)
    offered = set(plan.modes[base_mode].extra_slot_ids) if base_mode in plan.modes else set()
    unknown = [e for e in args.extras if e not in offered]
    if unknown:
        print(f"Not extra files of '{base_mode}': {', '.join(unknown)} (extras: {', '.join(sorted(offered)) or 'none'}).")
        return 1
    resolve = provider_plugin_resolver(_Manifests(manifests))
    plugin_ids = [pid for pid in dict.fromkeys(resolve(p) for p in recommendation_providers(preset)) if pid]
    text = render(preset, base_mode, plugin_ids, list(args.extras))
    issues = validate_recipe_dict(yaml.safe_load(text), extra_kinds={"workspace.activate"})
    if issues:
        print("The scaffold does not validate:\n" + "\n".join(issues))
        return 1
    slug = _SLUG_RE.sub("-", preset.name.lower()).strip("-") or "preset"
    out = Path(args.out) if args.out else ROOT / "content/recipes/local" / f"{slug}-starter.yml"
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}. Check it with: python scripts/recipe_lint.py --explain {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
