from typing import Dict, Iterable, List, Optional, Set

from src.features.recipes.dto import (
    PresetLinkedRecipeView,
    RecipeDetail,
    RecipeLinkedPresetView,
    RecipeSummary,
    recipe_total_download_bytes,
)
from src.features.recipes.schema import Recipe
from src.platform.security.user import AccountType, User

READINESS_INSTALLED = "installed"
READINESS_AVAILABLE = "available"


def can_run_recipes(user: Optional[User]) -> bool:
    return user is not None and user.account_type == AccountType.ADMIN


class RecipePresetLinks:
    def __init__(self, recipe_catalog, recipe_runner, preset_loader=None, preset_db_repo=None):
        self.recipe_catalog = recipe_catalog
        self.recipe_runner = recipe_runner
        self.preset_loader = preset_loader
        self.preset_db_repo = preset_db_repo

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

    def _last_completed_at(self, recipe_id: str):
        completed = self.recipe_runner.get_latest_completed_run(recipe_id)
        return completed.completed_at if completed else None

    def summaries(self, recipes: Iterable[Recipe]) -> List[RecipeSummary]:
        installed = self._installed_preset_ids()
        return [
            RecipeSummary.from_recipe(
                recipe,
                presets=self.presets_for_recipe(recipe, installed),
                last_completed_at=self._last_completed_at(recipe.id),
            )
            for recipe in recipes
        ]

    def detail(self, recipe: Recipe, load_errors: Optional[List[str]] = None) -> RecipeDetail:
        return RecipeDetail.from_recipe(
            recipe,
            presets=self.presets_for_recipe(recipe),
            last_completed_at=self._last_completed_at(recipe.id),
            load_errors=load_errors,
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
                total_download_bytes=recipe_total_download_bytes(recipe),
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
