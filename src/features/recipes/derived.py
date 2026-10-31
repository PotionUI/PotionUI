from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.presets.mode_fields import field_recommendations, install_recipe_id, model_fields
from src.features.presets.schema import CATEGORIES
from src.features.presets.templates import PresetTemplate
from src.features.recipes.mode_slots import has_downloadable_modes
from src.features.recipes.schema import SOURCE_DERIVED, Recipe, parse_recipe, validate_recipe_dict

DERIVED_VERSION = 1
DERIVED_ENGINE = "native"
PLAN_STEP_KEY = "modes.plan"
PROVIDER_CAPABILITY = "marketplace-provider"


def recommendation_providers(preset: PresetTemplate) -> List[str]:
    providers = set()
    for mode in (preset.modes or {}).values():
        for spec in model_fields(mode).values():
            for rec in field_recommendations(spec):
                if rec.provider and rec.ref:
                    providers.add(rec.provider)
    return sorted(providers)


def provider_plugin_resolver(plugin_registry: Any) -> Callable[[str], Optional[str]]:
    def resolve(provider_id: str) -> Optional[str]:
        if plugin_registry is None:
            return None
        plugin_id = f"{provider_id}-provider"
        try:
            manifest = plugin_registry.get_plugin(plugin_id)
        except Exception:
            return None
        if manifest is None:
            return None
        capabilities = getattr(manifest, "capabilities", None) or []
        return plugin_id if PROVIDER_CAPABILITY in capabilities else None

    return resolve


def derived_recipe_dict(preset: PresetTemplate, plugin_for: Callable[[str], Optional[str]]) -> Dict[str, Any]:
    plugin_ids = [pid for pid in dict.fromkeys(plugin_for(p) for p in recommendation_providers(preset)) if pid]
    steps: List[Dict[str, Any]] = []
    if plugin_ids:
        steps.append(
            {"key": "plugins.ensure", "kind": "plugins.ensure", "title": "Enable required plugins", "params": {"plugin_ids": plugin_ids}}
        )
    steps.extend(
        [
            {"key": "backend.ensure", "kind": "backend.ensure", "title": "Prepare the backend", "params": {"engine": DERIVED_ENGINE}},
            {
                "key": PLAN_STEP_KEY,
                "kind": "modes.plan",
                "title": "Pick modes and review downloads",
                "params": {"preset_id": preset.id, "modes": "all", "selected": [], "extras": "offer"},
            },
            {"key": "artifacts.fetch", "kind": "artifacts.fetch", "title": "Download the files", "params": {"plan": PLAN_STEP_KEY}},
            {"key": "models.index", "kind": "models.index", "title": "Register downloaded models", "params": {"engine": DERIVED_ENGINE}},
            {"key": "preset.ensure", "kind": "preset.ensure", "title": "Install and assign the preset", "params": {"preset_id": preset.id}},
            {"key": "preset.defaults", "kind": "preset.defaults", "title": "Use the downloaded files by default", "params": {"plan": PLAN_STEP_KEY}},
            {
                "key": "pipeline.render",
                "kind": "pipeline.render",
                "title": "Check each mode",
                "params": {"preset_id": preset.id, "plan": PLAN_STEP_KEY},
            },
        ]
    )
    return {
        "schema_version": 1,
        "id": install_recipe_id(preset.name),
        "version": DERIVED_VERSION,
        "name": f"{preset.name} modes",
        "engine": DERIVED_ENGINE,
        "category": preset.category if preset.category in CATEGORIES else "utility",
        "summary": f"Pick the {preset.name} modes you want and get their files.",
        "plugins": [{"id": pid, "reason": "Resolves the files this preset recommends."} for pid in plugin_ids],
        "backend": {"engine": DERIVED_ENGINE},
        "artifacts": [],
        "presets": [{"preset_id": preset.id, "assign_to_owner": True}],
        "steps": steps,
    }


def build_derived_recipes(
    presets: List[PresetTemplate],
    plugin_registry: Any = None,
    taken_ids: Optional[set] = None,
) -> Tuple[Dict[str, Recipe], Dict[str, List[str]]]:
    plugin_for = provider_plugin_resolver(plugin_registry)
    recipes: Dict[str, Recipe] = {}
    errors: Dict[str, List[str]] = {}
    taken = set(taken_ids or ())
    for preset in sorted(presets, key=lambda p: (p.name or "", p.id)):
        if (preset.engine or "") != DERIVED_ENGINE or not has_downloadable_modes(preset):
            continue
        data = derived_recipe_dict(preset, plugin_for)
        if data["id"] in taken or data["id"] in recipes:
            data["id"] = f"{data['id']}-{preset.id[-6:].lower()}"
        issues = validate_recipe_dict(data)
        if issues:
            errors[f"derived:{preset.id}"] = issues
            continue
        recipes[data["id"]] = parse_recipe(data, source_path="", source=SOURCE_DERIVED)
    return recipes, errors
