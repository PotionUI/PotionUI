"""Recipe catalog: discovers, parses, and validates recipes.

Mirrors `PresetTemplateLoader`'s discovery shape (a directory of YAML files,
`.reload()`/lazy-load semantics, errors collected rather than raised) so the
Phase-3 wizard gets the same guarantee presets already have: one broken file on
disk never takes down the whole catalog - it just doesn't appear, and its
parse/validation issues are reported on `load_errors`.

This is a Manager in the house sense (no "Service" classes) - it owns recipe
discovery/parsing/validation and nothing else. Turning a `Recipe` into actual
recipe-run progress is the executor registry's job (see `executors/`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


from src.platform.plugins.manifest_roots import PluginManifestRoot, plugin_manifest_roots

from src.features.recipes.derived import build_derived_recipes
from src.features.recipes.records import LintContext

from src.features.recipes.schema import (
    SOURCE_LOCAL,
    SOURCE_MARKETPLACE,
    SOURCE_PLUGIN,
    Recipe,
    parse_recipe,
    validate_recipe_dict,
)
from src.platform.filesystem.yaml_io import load_yaml_file

logger = logging.getLogger(__name__)


#: Scanned in this order so a `local` recipe id colliding with a `marketplace`
#: one is reported as a duplicate rather than silently shadowing it.
_ROOTS = ("marketplace", "local")


def plugin_recipe_roots(manifests) -> List[PluginManifestRoot]:
    """Resolve the recipe roots contributed by a set of plugin manifests.

    Each manifest's ``recipes:`` entries name a directory (relative to the
    plugin dir) scanned for ``*.yml`` recipe files, exactly like the core
    ``content/recipes/`` tree. Returns absolute directory paths; manifests
    without a ``recipes`` section contribute nothing. Mirrors
    ``src.features.presets.loader.plugin_preset_roots``.
    """
    return plugin_manifest_roots(manifests, "recipes")


class RecipeCatalog:
    """Loads `<recipes_dir>/{marketplace,local}/*.yml` into validated `Recipe`
    objects, mirroring the `content/presets/{marketplace,local}` convention.

    A plugin can also contribute recipes by declaring a `recipes:` root in
    its manifest (mirrors plugin-shipped presets) - those roots are scanned
    in addition to the two core roots whenever `plugin_registry` is given and
    the plugin is enabled. A plugin recipe id colliding with a core recipe id
    is reported on `load_errors`; the core recipe wins, same precedence as a
    `local` recipe colliding with a `marketplace` one.
    """

    def __init__(
        self,
        recipes_dir: str = "content/recipes",
        plugin_registry=None,
        step_kind_registry=None,
    ):
        self.recipes_dir = Path(recipes_dir)
        self.plugin_registry = plugin_registry
        self.step_kind_registry = step_kind_registry
        self._recipes: Dict[str, Recipe] = {}
        self._by_preset: Dict[str, List[str]] = {}
        #: source path (str) -> list of human-readable issue strings, for any
        #: file that failed to parse/validate or collided with another recipe's id.
        self.load_errors: Dict[str, List[str]] = {}
        self._loaded = False
        self.preset_loader = None
        self._derived: Dict[str, Recipe] = {}
        self._derived_by_preset: Dict[str, str] = {}
        self._derived_errors: Dict[str, List[str]] = {}
        self._derived_key = None

    def attach_presets(self, preset_loader) -> None:
        self.preset_loader = preset_loader
        self._derived_key = None

    def _refresh_derived(self) -> None:
        if self.preset_loader is None:
            return
        try:
            presets = list(self.preset_loader.all())
        except Exception:
            logger.exception("Listing presets for derived recipes failed")
            return
        key = tuple((p.id, id(p)) for p in presets)
        if key == self._derived_key:
            return
        derived, errors = build_derived_recipes(presets, self.plugin_registry, taken_ids=set(self._recipes))
        self._derived = derived
        self._derived_by_preset = {r.presets[0].preset_id: r.id for r in derived.values() if r.presets}
        self._derived_errors = errors
        self._derived_key = key

    def _all(self) -> Dict[str, Recipe]:
        self._ensure_loaded()
        self._refresh_derived()
        if not self._derived:
            return self._recipes
        merged = dict(self._derived)
        merged.update(self._recipes)
        return merged

    def install_recipe_for(self, preset_id: str) -> Optional[Recipe]:
        self._all()
        recipe_id = self._derived_by_preset.get(preset_id)
        return self._derived.get(recipe_id) if recipe_id else None

    def derived_errors(self) -> Dict[str, List[str]]:
        self._all()
        return dict(self._derived_errors)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def _plugin_step_kinds(self) -> Set[str]:
        """The step kinds plugins have registered on this instance, so a
        recipe that uses one validates instead of failing as unknown."""
        if self.step_kind_registry is None:
            return set()
        return {reg.kind for reg in self.step_kind_registry.all()}

    def _scan_root(
        self,
        root: Path,
        recipes: Dict[str, Recipe],
        errors: Dict[str, List[str]],
        source: str,
        plugin_id: Optional[str] = None,
    ) -> None:
        extra_kinds = self._plugin_step_kinds()
        for path in sorted(root.glob("*.yml")):
            source_key = str(path)
            try:
                data = load_yaml_file(path) or {}
            except Exception as exc:
                errors[source_key] = [f"Could not parse YAML: {exc}"]
                continue

            issues = validate_recipe_dict(data, extra_kinds=extra_kinds)
            if issues:
                errors[source_key] = issues
                continue

            try:
                recipe = parse_recipe(
                    data, source_path=source_key, source=source, plugin_id=plugin_id
                )
            except Exception as exc:
                # Should be unreachable once validate_recipe_dict passed,
                # but a recipe file must never crash the whole catalog.
                errors[source_key] = [f"Failed to parse a validated recipe: {exc}"]
                continue

            plugin_issues = self._plugin_param_issues(recipe)
            if plugin_issues:
                errors[source_key] = plugin_issues
                continue

            existing = recipes.get(recipe.id)
            if existing is not None:
                errors[source_key] = [
                    f"Duplicate recipe id '{recipe.id}' - already defined by {existing.source_path}"
                ]
                continue

            recipes[recipe.id] = recipe

    def _plugin_param_issues(self, recipe: Recipe) -> List[str]:
        if self.step_kind_registry is None:
            return []
        index = {}
        if self.preset_loader is not None:
            try:
                index = {p.id: p for p in self.preset_loader.all()}
            except Exception:
                index = {}
        registrations = {reg.kind: reg for reg in self.step_kind_registry.all()}
        issues: List[str] = []
        for step in recipe.steps:
            registration = registrations.get(step.kind)
            validate = getattr(getattr(registration, "executor", None), "validate_params", None)
            if validate is None:
                continue
            try:
                found = validate(dict(step.params), recipe, LintContext(preset_index=index)) or []
            except Exception as exc:
                found = [f"its params check raised {exc}"]
            issues.extend(f"steps '{step.key}' ({step.kind}): {issue}" for issue in found)
        return issues

    def reload(self) -> None:
        """(Re)scan `<recipes_dir>/marketplace`, `<recipes_dir>/local`, and
        every enabled plugin's `recipes:` root, then rebuild the catalog."""
        recipes: Dict[str, Recipe] = {}
        errors: Dict[str, List[str]] = {}
        any_root_exists = False

        for root_name in _ROOTS:
            root = self.recipes_dir / root_name
            if not root.exists():
                continue
            any_root_exists = True
            self._scan_root(
                root,
                recipes,
                errors,
                SOURCE_LOCAL if root_name == "local" else SOURCE_MARKETPLACE,
            )

        if self.plugin_registry is not None:
            for plugin_root in plugin_recipe_roots(self.plugin_registry.get_enabled_plugins()):
                if not plugin_root.path.exists():
                    continue
                any_root_exists = True
                self._scan_root(
                    plugin_root.path,
                    recipes,
                    errors,
                    SOURCE_PLUGIN,
                    plugin_id=plugin_root.plugin_id,
                )

        if not any_root_exists:
            logger.debug("Recipes directory '%s' does not exist; catalog is empty", self.recipes_dir)

        by_preset: Dict[str, List[str]] = {}
        for recipe in sorted(recipes.values(), key=lambda r: r.id):
            for preset_ref in recipe.presets:
                ids = by_preset.setdefault(preset_ref.preset_id, [])
                if recipe.id not in ids:
                    ids.append(recipe.id)

        self._recipes = recipes
        self._by_preset = by_preset
        self.load_errors = errors
        self._loaded = True
        self._derived_key = None

        if errors:
            logger.warning("Recipe catalog loaded with %d file(s) failing validation: %s", len(errors), list(errors))
        logger.info("Recipe catalog loaded %d recipe(s) from '%s'", len(recipes), self.recipes_dir)

    def list_recipes(self) -> List[Recipe]:
        """All valid recipes, sorted by id."""
        return sorted(self._all().values(), key=lambda r: r.id)

    def get_recipe(self, recipe_id: str, version: Optional[int] = None) -> Optional[Recipe]:
        """The recipe named `recipe_id`, or `None` if it doesn't exist (or
        failed validation). When `version` is given, also returns `None` if
        the on-disk recipe's `version` no longer matches (the run was created
        against a revision this catalog no longer serves)."""
        recipe = self._all().get(recipe_id)
        if recipe is None:
            return None
        if version is not None and recipe.version != version:
            return None
        return recipe

    def recipes_for_preset(self, preset_id: str) -> List[Recipe]:
        recipes = self._all()
        found = [recipes[recipe_id] for recipe_id in self._by_preset.get(preset_id, [])]
        derived = self.install_recipe_for(preset_id)
        return found + ([derived] if derived is not None and derived.id not in self._recipes else [])

    def preset_recipe_index(self) -> Dict[str, List[Recipe]]:
        recipes = self._all()
        index = {
            preset_id: [recipes[recipe_id] for recipe_id in recipe_ids]
            for preset_id, recipe_ids in self._by_preset.items()
        }
        for preset_id, recipe_id in self._derived_by_preset.items():
            if recipe_id not in self._recipes:
                index.setdefault(preset_id, []).append(self._derived[recipe_id])
        return index
