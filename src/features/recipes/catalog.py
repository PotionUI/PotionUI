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

import yaml

from src.features.recipes.schema import (
    SOURCE_LOCAL,
    SOURCE_MARKETPLACE,
    SOURCE_PLUGIN,
    Recipe,
    parse_recipe,
    validate_recipe_dict,
)

logger = logging.getLogger(__name__)


#: Scanned in this order so a `local` recipe id colliding with a `marketplace`
#: one is reported as a duplicate rather than silently shadowing it.
_ROOTS = ("marketplace", "local")


@dataclass(frozen=True)
class PluginRecipeRoot:
    """One plugin-contributed ``recipes:`` directory, and the plugin that
    ships it - the id is what a recipe discovered there is attributed to."""

    plugin_id: str
    path: Path


def plugin_recipe_roots(manifests) -> List[PluginRecipeRoot]:
    """Resolve the recipe roots contributed by a set of plugin manifests.

    Each manifest's ``recipes:`` entries name a directory (relative to the
    plugin dir) scanned for ``*.yml`` recipe files, exactly like the core
    ``content/recipes/`` tree. Returns absolute directory paths; manifests
    without a ``recipes`` section contribute nothing. Mirrors
    ``src.features.presets.loader.plugin_preset_roots``.
    """
    roots: List[PluginRecipeRoot] = []
    for manifest in manifests:
        entries = getattr(manifest, "recipes", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        plugin_id = getattr(manifest, "id", "") or ""
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            if path:
                roots.append(PluginRecipeRoot(plugin_id=plugin_id, path=base / path))
    return roots


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
        #: source path (str) -> list of human-readable issue strings, for any
        #: file that failed to parse/validate or collided with another recipe's id.
        self.load_errors: Dict[str, List[str]] = {}
        self._loaded = False

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
                data = yaml.safe_load(path.read_text()) or {}
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

            existing = recipes.get(recipe.id)
            if existing is not None:
                errors[source_key] = [
                    f"Duplicate recipe id '{recipe.id}' - already defined by {existing.source_path}"
                ]
                continue

            recipes[recipe.id] = recipe

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

        self._recipes = recipes
        self.load_errors = errors
        self._loaded = True

        if errors:
            logger.warning("Recipe catalog loaded with %d file(s) failing validation: %s", len(errors), list(errors))
        logger.info("Recipe catalog loaded %d recipe(s) from '%s'", len(recipes), self.recipes_dir)

    def list_recipes(self) -> List[Recipe]:
        """All valid recipes, sorted by id."""
        self._ensure_loaded()
        return sorted(self._recipes.values(), key=lambda r: r.id)

    def get_recipe(self, recipe_id: str, version: Optional[int] = None) -> Optional[Recipe]:
        """The recipe named `recipe_id`, or `None` if it doesn't exist (or
        failed validation). When `version` is given, also returns `None` if
        the on-disk recipe's `version` no longer matches (the run was created
        against a revision this catalog no longer serves)."""
        self._ensure_loaded()
        recipe = self._recipes.get(recipe_id)
        if recipe is None:
            return None
        if version is not None and recipe.version != version:
            return None
        return recipe
