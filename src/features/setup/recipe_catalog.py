"""Recipe catalog: discovers, parses, and validates Phase-3 setup recipes.

Mirrors `PresetTemplateLoader`'s discovery shape (a directory of YAML files,
`.reload()`/lazy-load semantics, errors collected rather than raised) so the
Phase-3 wizard gets the same guarantee presets already have: one broken file on
disk never takes down the whole catalog - it just doesn't appear, and its
parse/validation issues are reported on `load_errors`.

This is a Manager in the house sense (no "Service" classes) - it owns recipe
discovery/parsing/validation and nothing else. Turning a `Recipe` into actual
setup-run progress is the executor registry's job (see `executors/`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from src.features.setup.recipe_schema import Recipe, parse_recipe, validate_recipe_dict

logger = logging.getLogger(__name__)


#: Scanned in this order so a `local` recipe id colliding with a `marketplace`
#: one is reported as a duplicate rather than silently shadowing it.
_ROOTS = ("marketplace", "local")


def plugin_recipe_roots(manifests) -> List[Path]:
    """Resolve the recipe roots contributed by a set of plugin manifests.

    Each manifest's ``recipes:`` entries name a directory (relative to the
    plugin dir) scanned for ``*.yml`` recipe files, exactly like the core
    ``content/recipes/`` tree. Returns absolute directory paths; manifests
    without a ``recipes`` section contribute nothing. Mirrors
    ``src.features.presets.loader.plugin_preset_roots``.
    """
    roots: List[Path] = []
    for manifest in manifests:
        entries = getattr(manifest, "recipes", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            if path:
                roots.append(base / path)
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

    def __init__(self, recipes_dir: str = "content/recipes", plugin_registry=None):
        self.recipes_dir = Path(recipes_dir)
        self.plugin_registry = plugin_registry
        self._recipes: Dict[str, Recipe] = {}
        #: source path (str) -> list of human-readable issue strings, for any
        #: file that failed to parse/validate or collided with another recipe's id.
        self.load_errors: Dict[str, List[str]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def _scan_root(
        self, root: Path, recipes: Dict[str, Recipe], errors: Dict[str, List[str]]
    ) -> None:
        for path in sorted(root.glob("*.yml")):
            source = str(path)
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except Exception as exc:
                errors[source] = [f"Could not parse YAML: {exc}"]
                continue

            issues = validate_recipe_dict(data)
            if issues:
                errors[source] = issues
                continue

            try:
                recipe = parse_recipe(data, source_path=source)
            except Exception as exc:
                # Should be unreachable once validate_recipe_dict passed,
                # but a recipe file must never crash the whole catalog.
                errors[source] = [f"Failed to parse a validated recipe: {exc}"]
                continue

            existing = recipes.get(recipe.id)
            if existing is not None:
                errors[source] = [
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
            self._scan_root(root, recipes, errors)

        if self.plugin_registry is not None:
            for root in plugin_recipe_roots(self.plugin_registry.get_enabled_plugins()):
                if not root.exists():
                    continue
                any_root_exists = True
                self._scan_root(root, recipes, errors)

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
