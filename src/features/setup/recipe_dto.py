"""Wire shape for `GET /api/setup/recipes` - a catalog summary, not the full
parsed `Recipe` (steps/params are an execution detail the picker screen
doesn't need)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from src.features.setup.recipe_schema import Recipe


class RecipeSummary(BaseModel):
    id: str
    name: str
    summary: str
    description: str
    engine: str
    category: str
    artifact_count: int
    total_download_bytes: Optional[int] = None
    preset_name: Optional[str] = None
    # When set, a run of this recipe has already completed - the catalog
    # marks it "Installed" (with a "Run again" action) instead of offering
    # "Start" as if nothing had happened yet.
    last_completed_at: Optional[datetime] = None

    @classmethod
    def from_recipe(
        cls,
        recipe: Recipe,
        *,
        preset_name: Optional[str] = None,
        last_completed_at: Optional[datetime] = None,
    ) -> "RecipeSummary":
        sizes = [a.size_bytes for a in recipe.artifacts if a.size_bytes is not None]
        # Only a meaningful total when every artifact declares a size - a
        # partial sum would understate the real download and mislead the
        # consent screen this feeds into.
        total_bytes = sum(sizes) if sizes and len(sizes) == len(recipe.artifacts) else None
        return cls(
            id=recipe.id,
            name=recipe.name,
            summary=recipe.summary,
            description=recipe.description,
            engine=recipe.engine,
            category=recipe.category,
            artifact_count=len(recipe.artifacts),
            total_download_bytes=total_bytes,
            preset_name=preset_name,
            last_completed_at=last_completed_at,
        )
