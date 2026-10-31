import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.features.recipes.dto import (
    PresetLinkedRecipeView,
    RecipeDetail,
    RecipeLinkedPresetView,
    RecipeSummary,
    recipe_total_download_bytes,
)
from src.features.recipes.schema import Recipe
from src.platform.security.user import AccountType, User

logger = logging.getLogger(__name__)

READINESS_INSTALLED = "installed"
READINESS_AVAILABLE = "available"


def can_run_recipes(user: Optional[User]) -> bool:
    return user is not None and user.account_type == AccountType.ADMIN


class RecipePresetLinks:
    def __init__(self, recipe_catalog, recipe_runner, preset_loader=None, preset_db_repo=None, mode_planner=None):
        self.recipe_catalog = recipe_catalog
        self.recipe_runner = recipe_runner
        self.preset_loader = preset_loader
        self.preset_db_repo = preset_db_repo
        self.mode_planner = mode_planner

    def modes_summary(self, recipe: Recipe) -> Optional[Dict[str, Any]]:
        if self.mode_planner is None:
            return None
        step = next((s for s in recipe.steps if s.kind == "modes.plan"), None)
        if step is None:
            return None
        try:
            return self.mode_planner.summary(
                step.params.get("preset_id"),
                selected=tuple(step.params.get("selected") or ()),
                locked=tuple(step.params.get("locked") or ()),
            )
        except Exception:
            logger.exception("Summarizing the modes of recipe '%s' failed", recipe.id)
            return None

    def install_recipe_id(self, preset_id: str) -> Optional[str]:
        if self.recipe_catalog is None or not hasattr(self.recipe_catalog, "install_recipe_for"):
            return None
        recipe = self.recipe_catalog.install_recipe_for(preset_id)
        return recipe.id if recipe is not None else None

    def mode_install(self, preset_id: str, user: Optional[User]) -> Dict[str, Dict[str, Any]]:
        if not can_run_recipes(user) or self.recipe_catalog is None or self.mode_planner is None:
            return {}
        recipe = self.recipe_catalog.install_recipe_for(preset_id)
        if recipe is None:
            return {}
        summary = self.mode_planner.summary(preset_id)
        if summary is None:
            return {}
        return {
            mode["id"]: {"recipe_id": recipe.id, "ready": mode["ready"], "missing_bytes": mode["missing_bytes"]}
            for mode in summary["modes"]
        }

    def _installed_preset_ids(self) -> Set[str]:
        if self.preset_db_repo is None:
            return set()
        return {p.preset_id for p in self.preset_db_repo.get_all_installed_presets()}

    def _preset_view(self, preset_id: str, installed_ids: Set[str]) -> Optional[RecipeLinkedPresetView]:
        if self.preset_loader is None:
            return None
        template = self.preset_loader.load_preset_by_id(preset_id)
        if template is None:
            return None
        cover = (getattr(template, "media", None) or {}).get("cover")
        if isinstance(cover, str) and cover:
            cover_url = cover if cover.startswith(("/", "http://", "https://")) else f"/api/media/presets/{preset_id}/{cover}?size=small"
        else:
            cover_url = None
        return RecipeLinkedPresetView(
            id=preset_id,
            name=template.name,
            cover_url=cover_url,
            installed=preset_id in installed_ids,
        )

    def presets_for_recipe(
        self, recipe: Recipe, installed_ids: Optional[Set[str]] = None
    ) -> List[RecipeLinkedPresetView]:
        installed = self._installed_preset_ids() if installed_ids is None else installed_ids
        views: List[RecipeLinkedPresetView] = []
        seen: Set[str] = set()
        for ref in recipe.presets:
            if ref.preset_id in seen:
                continue
            seen.add(ref.preset_id)
            view = self._preset_view(ref.preset_id, installed)
            if view is not None:
                views.append(view)
        return views

    def vram_requirements(self, recipe: Recipe) -> Tuple[Optional[int], Optional[int]]:
        if self.preset_loader is None:
            return None, None
        minimums: List[float] = []
        recommended: List[float] = []
        for preset_id in {ref.preset_id for ref in recipe.presets}:
            template = self.preset_loader.load_preset_by_id(preset_id)
            requires = getattr(template, "requires", None) or {}
            if requires.get("min_vram_gb") is not None:
                minimums.append(requires["min_vram_gb"])
            if requires.get("recommended_vram_gb") is not None:
                recommended.append(requires["recommended_vram_gb"])
        return (
            math.ceil(max(minimums)) if minimums else None,
            math.ceil(max(recommended)) if recommended else None,
        )

    def _last_completed_at(self, recipe_id: str):
        completed = self.recipe_runner.get_latest_completed_run(recipe_id)
        return completed.completed_at if completed else None

    def summaries(self, recipes: Iterable[Recipe]) -> List[RecipeSummary]:
        installed = self._installed_preset_ids()
        summaries: List[RecipeSummary] = []
        for recipe in recipes:
            min_vram_gb, recommended_vram_gb = self.vram_requirements(recipe)
            summaries.append(
                RecipeSummary.from_recipe(
                    recipe,
                    presets=self.presets_for_recipe(recipe, installed),
                    last_completed_at=self._last_completed_at(recipe.id),
                    min_vram_gb=min_vram_gb,
                    recommended_vram_gb=recommended_vram_gb,
                    modes_summary=self.modes_summary(recipe),
                )
            )
        return summaries

    def detail(self, recipe: Recipe, load_errors: Optional[List[str]] = None) -> RecipeDetail:
        min_vram_gb, recommended_vram_gb = self.vram_requirements(recipe)
        return RecipeDetail.from_recipe(
            recipe,
            presets=self.presets_for_recipe(recipe),
            last_completed_at=self._last_completed_at(recipe.id),
            load_errors=load_errors,
            min_vram_gb=min_vram_gb,
            recommended_vram_gb=recommended_vram_gb,
            modes_summary=self.modes_summary(recipe),
        )

    def _recipe_view(self, recipe: Recipe, cache: Dict[str, PresetLinkedRecipeView]) -> PresetLinkedRecipeView:
        view = cache.get(recipe.id)
        if view is None:
            view = PresetLinkedRecipeView(
                id=recipe.id,
                name=recipe.name,
                readiness=(
                    READINESS_INSTALLED
                    if self.recipe_runner.get_latest_completed_run(recipe.id) is not None
                    else READINESS_AVAILABLE
                ),
                total_download_bytes=(self.modes_summary(recipe) or {}).get("total_bytes", recipe_total_download_bytes(recipe)),
            )
            cache[recipe.id] = view
        return view

    def recipes_for_preset(self, preset_id: str, user: Optional[User]) -> List[Dict]:
        if not can_run_recipes(user) or self.recipe_catalog is None:
            return []
        cache: Dict[str, PresetLinkedRecipeView] = {}
        return [
            self._recipe_view(recipe, cache).model_dump()
            for recipe in self.recipe_catalog.recipes_for_preset(preset_id)
        ]

    def recipes_by_preset(self, user: Optional[User]) -> Dict[str, List[Dict]]:
        if not can_run_recipes(user) or self.recipe_catalog is None:
            return {}
        cache: Dict[str, PresetLinkedRecipeView] = {}
        return {
            preset_id: [self._recipe_view(recipe, cache).model_dump() for recipe in recipes]
            for preset_id, recipes in self.recipe_catalog.preset_recipe_index().items()
        }
