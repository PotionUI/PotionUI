"""Recipe catalog and recipe-run DTOs, plus the redaction gate.

Everything that lands in ``safe_input`` / ``safe_output`` passes through
``redact_safe_payload`` first. The rule is deliberately paranoid: recipe runs
touch tokens, provider credentials, and download URLs, and none of that may be
persisted or returned. Redaction is a *whitelist of plain fields* - only JSON
scalars (and nested dicts/lists of them) survive - combined with a blocklist of
secret-looking key names that are dropped outright even when their value looks
plain.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.features.recipes.records import (
    MODE_ONBOARDING,
    RecipeRun,
    RecipeStepAttempt,
)
from src.features.recipes.schema import Recipe

# Key names whose value is never persisted, matched case-insensitively as a
# substring so `api_key`, `X-Auth-Token`, `claim_token`, `db_password`, etc. are
# all caught.
_SECRET_KEY_PATTERNS = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|apikey|"
    r"authorization|auth|private[_-]?key|access[_-]?key|session|cookie|"
    r"bearer|signature)",
    re.IGNORECASE,
)

# The only value types allowed to survive into a safe payload.
_PLAIN_SCALARS = (str, int, float, bool)

_MAX_DEPTH = 6


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERNS.search(key))


def redact_safe_payload(value: Any, _depth: int = 0) -> Any:
    """Return a copy of ``value`` safe to persist/return.

    Drops secret-looking keys, keeps only plain scalars and nested plain
    containers, and coerces anything else (service objects, callables, unknown
    types) out. Returns ``None`` for a non-plain top-level value so a caller can
    never smuggle a serialized object into ``safe_input``.
    """
    if _depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, bool):
        # bool must be checked before int (bool is an int subclass).
        return value
    if isinstance(value, _PLAIN_SCALARS):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _is_secret_key(key):
                continue
            cleaned = redact_safe_payload(raw_val, _depth + 1)
            if cleaned is not None or raw_val is None:
                out[key] = cleaned
        return out
    if isinstance(value, (list, tuple)):
        return [
            redact_safe_payload(item, _depth + 1)
            for item in value
            if redact_safe_payload(item, _depth + 1) is not None or item is None
        ]
    # Anything else (objects, callables, bytes, sets, ...) is dropped.
    return None


def redact_safe_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Redact and guarantee a dict result for the *_input / *_output columns."""
    if not value:
        return {}
    cleaned = redact_safe_payload(value)
    return cleaned if isinstance(cleaned, dict) else {}


# --- request DTOs ----------------------------------------------------------


class CreateRecipeRunRequest(BaseModel):
    """Body of ``POST /api/setup/runs`` (the onboarding wizard names the recipe
    it wants). ``safe_input`` is redacted server-side regardless of what the
    client sends."""

    recipe_id: str = Field(..., min_length=1)
    recipe_version: int = 1
    safe_input: Dict[str, Any] = Field(default_factory=dict)


class StartRecipeRunRequest(BaseModel):
    """Body of ``POST /api/recipes/{recipe_id}/runs``. The recipe is already
    in the path, so only the pinned version is a body field; a null version
    means "whatever the catalog serves right now"."""

    recipe_version: Optional[int] = None


class RecipeRunActionRequest(BaseModel):
    """Body of ``POST /api/recipes/runs/{run_id}/actions`` (and of the
    onboarding wizard's ``POST /api/setup/runs/{run_id}/actions/{action}``,
    which names the action in the path instead). Only ``grant_consent`` uses
    ``step_key``; the other actions (pause/resume/cancel/retry_step) ignore
    it, so a client may always POST this shape without branching on which
    action it's calling."""

    action: Optional[str] = None
    step_key: Optional[str] = None


# --- response DTOs ---------------------------------------------------------


class RecipeStepAttemptView(BaseModel):
    step_key: str
    attempt: int
    status: str
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    progress_unit: Optional[str] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    # A step executor's repair hint (`StepResult.suggested_repair`), split out
    # of `safe_output` into its own field on the wire - mirrors
    # `ReadinessCheck`'s message/action split, so the UI never has to fish a
    # magic key out of a free-form output dict.
    safe_suggested_action: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, attempt: RecipeStepAttempt) -> "RecipeStepAttemptView":
        safe_output = dict(attempt.safe_output) if attempt.safe_output else {}
        # See executors/base.py `StepResult.to_safe_output`: it merges
        # `suggested_repair` into `safe_output` so the hint survives the
        # append-only attempt row even on failure. Promote it back out here.
        # `consent_request` (see `StepResult.awaiting`) is deliberately left
        # in place inside `safe_output` rather than promoted - the frontend
        # reads it at `attempt.safe_output.consent_request`.
        suggested_action = safe_output.pop("suggested_repair", None)
        return cls(
            step_key=attempt.step_key,
            attempt=attempt.attempt,
            status=attempt.status.value,
            progress_current=attempt.progress_current,
            progress_total=attempt.progress_total,
            progress_unit=attempt.progress_unit,
            safe_output=safe_output or None,
            error_code=attempt.error_code,
            safe_error_detail=attempt.safe_error_detail,
            safe_suggested_action=suggested_action,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )


class RecipeRunStepView(BaseModel):
    """One step of the run's ordered execution plan (from the recipe), whether
    or not it has been attempted yet - so the UI can render not-yet-started
    steps as "pending" instead of them simply being absent."""

    step_key: str
    title: str
    kind: str
    ordinal: int
    status: str  # "pending", or the latest attempt's status
    attempts: List[RecipeStepAttemptView] = Field(default_factory=list)


class RecipeRunView(BaseModel):
    """The durable run as the admin UI renders it. Only safe fields; the run's
    ``safe_output`` (never raw service output) and redacted attempts."""

    id: str
    recipe_id: str
    recipe_version: int
    scope: str
    # "onboarding" (the first-run wizard, runs every step) or "admin" (started
    # from Admin -> Recipes, skips `onboarding_only` steps).
    mode: str = MODE_ONBOARDING
    status: str
    current_step: Optional[str] = None
    safe_input: Optional[Dict[str, Any]] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # The recipe's ordered execution plan, each entry carrying whatever
    # attempts exist for it (possibly none - "pending"). Empty when the run's
    # recipe can no longer be resolved (e.g. it was removed from disk); in
    # that case `attempts` below is still the full, if unordered, history.
    steps: List[RecipeRunStepView] = Field(default_factory=list)
    # Flat attempt history. Ordered by recipe execution order (then attempt
    # number) whenever `steps` is populated; falls back to whatever order the
    # repository returned otherwise.
    attempts: List[RecipeStepAttemptView] = Field(default_factory=list)

    @classmethod
    def from_record(
        cls,
        run: RecipeRun,
        attempts: Optional[List[RecipeStepAttempt]] = None,
        recipe_steps: Optional[List[Tuple[str, str, str]]] = None,
    ) -> "RecipeRunView":
        """``recipe_steps`` is the run's recipe's ordered ``(step_key, kind,
        title)`` list (see ``Recipe.steps``) when the recipe could be
        resolved. Without it, this degrades to the old flat, repository-order
        ``attempts``-only shape (``steps`` stays empty).
        """
        attempts = attempts or []
        by_step: Dict[str, List[RecipeStepAttempt]] = {}
        for a in attempts:
            by_step.setdefault(a.step_key, []).append(a)
        for step_attempts in by_step.values():
            step_attempts.sort(key=lambda a: a.attempt)

        steps: List[RecipeRunStepView] = []
        ordered_attempt_views: List[RecipeStepAttemptView] = []

        if recipe_steps:
            for ordinal, (step_key, kind, title) in enumerate(recipe_steps):
                step_attempts = by_step.pop(step_key, [])
                views = [RecipeStepAttemptView.from_record(a) for a in step_attempts]
                ordered_attempt_views.extend(views)
                status = views[-1].status if views else "pending"
                steps.append(
                    RecipeRunStepView(
                        step_key=step_key,
                        title=title,
                        kind=kind,
                        ordinal=ordinal,
                        status=status,
                        attempts=views,
                    )
                )
            # Attempts for a step_key the recipe no longer declares (e.g. it
            # was edited after this run started) still surface, appended
            # after the known steps - history is never silently dropped.
            for step_key, step_attempts in by_step.items():
                ordered_attempt_views.extend(RecipeStepAttemptView.from_record(a) for a in step_attempts)
        else:
            ordered_attempt_views = [RecipeStepAttemptView.from_record(a) for a in attempts]

        return cls(
            id=run.id,
            recipe_id=run.recipe_id,
            recipe_version=run.recipe_version,
            scope=run.scope,
            mode=run.mode,
            status=run.status.value,
            current_step=run.current_step,
            safe_input=run.safe_input,
            safe_output=run.safe_output,
            error_code=run.error_code,
            safe_error_detail=run.safe_error_detail,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            steps=steps,
            attempts=ordered_attempt_views,
        )


# --- catalog DTOs ----------------------------------------------------------


class RecipeSummary(BaseModel):
    """A recipe as the catalog lists it - not the full parsed `Recipe`
    (steps/params are an execution detail a picker screen doesn't need)."""

    id: str
    name: str
    summary: str
    description: str
    engine: str
    category: str
    artifact_count: int
    step_count: int
    preset_ids: List[str] = Field(default_factory=list)
    source: str
    plugin_id: Optional[str] = None
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
            step_count=len(recipe.steps),
            preset_ids=[p.preset_id for p in recipe.presets],
            source=recipe.source,
            plugin_id=recipe.plugin_id,
            total_download_bytes=total_bytes,
            preset_name=preset_name,
            last_completed_at=last_completed_at,
        )


class RecipeStepView(BaseModel):
    """One declared step of a recipe, as the detail panel lists it."""

    key: str
    kind: str
    title: str
    onboarding_only: bool = False


class RecipeArtifactView(BaseModel):
    """One file a recipe needs before its content is usable."""

    id: str
    kind: str
    model_type: str
    filename: str
    display_name: str = ""
    size_bytes: Optional[int] = None
    required: bool = True


class RecipePresetView(BaseModel):
    """One preset a recipe installs."""

    preset_id: str
    path_hint: str = ""


class RecipeSmokeView(BaseModel):
    """The preset/mode a recipe's `generation.smoke` step runs."""

    preset_id: str
    mode: str


class RecipeDetail(RecipeSummary):
    """A recipe with everything it declares - what Admin -> Recipes renders
    before an admin decides to run it."""

    steps: List[RecipeStepView] = Field(default_factory=list)
    artifacts: List[RecipeArtifactView] = Field(default_factory=list)
    presets: List[RecipePresetView] = Field(default_factory=list)
    smoke: Optional[RecipeSmokeView] = None
    # Issues the catalog recorded for this recipe's own file, if any.
    load_errors: List[str] = Field(default_factory=list)

    @classmethod
    def from_recipe(
        cls,
        recipe: Recipe,
        *,
        preset_name: Optional[str] = None,
        last_completed_at: Optional[datetime] = None,
        load_errors: Optional[List[str]] = None,
    ) -> "RecipeDetail":
        summary = RecipeSummary.from_recipe(
            recipe, preset_name=preset_name, last_completed_at=last_completed_at
        )
        return cls(
            **summary.model_dump(),
            steps=[
                RecipeStepView(
                    key=s.key, kind=s.kind, title=s.title, onboarding_only=s.onboarding_only
                )
                for s in recipe.steps
            ],
            artifacts=[
                RecipeArtifactView(
                    id=a.id,
                    kind=a.kind,
                    model_type=a.model_type,
                    filename=a.filename,
                    display_name=a.display_name,
                    size_bytes=a.size_bytes,
                    required=a.required,
                )
                for a in recipe.artifacts
            ],
            presets=[
                RecipePresetView(preset_id=p.preset_id, path_hint=p.path_hint)
                for p in recipe.presets
            ],
            smoke=(
                RecipeSmokeView(preset_id=recipe.smoke.preset_id, mode=recipe.smoke.mode)
                if recipe.smoke
                else None
            ),
            load_errors=list(load_errors or []),
        )


class StepKindView(BaseModel):
    """One runnable recipe step kind, and where it came from."""

    kind: str
    source: str  # "core" | "plugin"
    plugin_id: Optional[str] = None
