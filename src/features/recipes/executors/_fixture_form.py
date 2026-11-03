"""Shared fixture-form-data construction for the two recipe steps that need to
bind a preset's form without any real user input: `pipeline.render` (a dry
run - no GPU) and `generation.smoke` (a real generation, on tiny/fast values).

The field walk and placeholder injection live in
`src/features/presets/fixture_form.py`, shared with `scripts/preset_render.py`.
`generation.smoke` overrides a caller-supplied subset of fields (the recipe's
`smoke:` section) on top of these defaults before binding.

A `/SETUP-CHECK` model placeholder is a fine dry-run value for
`pipeline.render` (nothing ever loads it - templates just render the string),
but `generation.smoke` runs a REAL generation, and a placeholder reaching a
real loader crashes it
(`/SETUP-CHECK/models/model.safetensors` handed straight to the SDXL
checkpoint loader). `build_fixture_form_data`'s optional `recipe`/
`model_repository` params (passed only by `generation.smoke`) turn on
`_resolve_model_fields`, which replaces every model-typed placeholder with a
REAL indexed model's file path - see its docstring for the resolution and
failure rules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.features.forms.binding import bind_form
from src.features.models.form_refs import make_model_ref
from src.features.presets.fixture_form import (
    MODEL_FIELD_TYPES,
    build_default_form_data,
    fixture_video_director_document,
    leave_reactive_defaults_to_binding,
)
from src.features.recipes.executors._artifact_lookup import find_slot_model

FIXTURE_TAG = "SETUP-CHECK"


class RequiredModelMissing(Exception):
    """Raised by `build_fixture_form_data` (only reachable when `recipe`/
    `model_repository` are supplied - i.e. only from `generation.smoke`) when
    a model-typed field's `model_type` matches one of the recipe's declared
    `artifacts:`, but the model index has no row for that artifact's filename
    yet (not downloaded, or `models.index` hasn't run since it landed).
    `GenerationSmokeExecutor` catches this to fail the step up front with a
    plain sentence naming the missing file, instead of letting a
    `/SETUP-CHECK` sentinel reach a real loader."""

    def __init__(self, display_name: str, filename: str):
        self.display_name = display_name
        self.filename = filename
        super().__init__(f"'{display_name}' ({filename}) isn't indexed yet.")


def _model_sentinel(name: str) -> str:
    return f"/{FIXTURE_TAG}/models/{name}.safetensors"


def _collect_model_field_specs(fields: List[Any]) -> List[Dict[str, Any]]:
    """Flatten every `model`/`models`-typed field in `fields` (recursing into
    `children`) into `{"name", "model_type"}` pairs. `lora_picker` fields are
    handled separately (always `[]`, never a fixture path) and excluded."""
    specs: List[Dict[str, Any]] = []
    for field_obj in fields:
        name = field_obj.name
        if name and field_obj.type in MODEL_FIELD_TYPES:
            configuration = field_obj.configuration or {}
            model_type = configuration.get("model_type", "checkpoint")
            recommendations = configuration.get("recommendations")
            recommended = set()
            for entry in recommendations or []:
                if isinstance(entry, dict):
                    for key in ("name", "sha256"):
                        if entry.get(key):
                            recommended.add(str(entry[key]).lower())
            specs.append({"name": name, "model_type": model_type, "recommended": recommended})
        if isinstance(field_obj.children, list):
            specs.extend(_collect_model_field_specs(field_obj.children))
    return specs


def _artifact_identities(artifact: Any) -> set:
    identities = {str(artifact.filename).lower()} if getattr(artifact, "filename", None) else set()
    checksum = getattr(artifact, "checksum", None)
    value = getattr(checksum, "value", None) if checksum is not None else None
    if value:
        identities.add(str(value).lower())
    for variant in getattr(artifact, "variants", None) or ():
        if getattr(variant, "filename", None):
            identities.add(str(variant.filename).lower())
        variant_checksum = getattr(variant, "checksum", None)
        variant_value = getattr(variant_checksum, "value", None) if variant_checksum is not None else None
        if variant_value:
            identities.add(str(variant_value).lower())
    return identities


def _artifact_for_field(candidates: List[Any], recommended: set) -> Optional[Any]:
    if not candidates:
        return None
    if recommended:
        for artifact in candidates:
            if _artifact_identities(artifact) & recommended:
                return artifact
    return candidates[0]


def _resolve_model_fields(
    fields: List[Any],
    form_data: Dict[str, Any],
    *,
    recipe: Any,
    model_repository: Any,
    selections: Optional[Dict[str, str]] = None,
) -> None:
    """Replace every model-typed field's `/SETUP-CHECK` placeholder with a
    REAL indexed model's `file_path`, matched through the recipe's own
    declared `artifacts:` by `model_type` - the same `(model_type, filename)`
    identity `models.index`/`artifacts.fetch` use (see
    `ModelRepository.get_by_identity`).

    A field whose `model_type` has no matching recipe artifact (an optional
    picker this recipe never fetches - e.g. SDXL's controlnet/upscaler/
    embedding/detection_bbox/mediapipe pickers, when the recipe only declares
    a checkpoint artifact) is cleared to `""` instead of left as a
    placeholder: most presets gate that pipe stage on the field being
    non-empty (see e.g. SDXL's `pipeline.yml` embeddings/controlnet
    `enabled:` templates - a `/SETUP-CHECK` placeholder there reads as
    "enabled, with a fake file", which is worse than simply off).

    Only touches fields still holding their sentinel placeholder - a field
    that already resolved to something real via `_collect_named_fields` (an
    explicit preset default) is left untouched.

    Raises `RequiredModelMissing` when the recipe DOES declare an artifact
    for a field's `model_type` but the model index has no matching row yet
    (not downloaded, or `models.index` hasn't run) - the caller must fail the
    step rather than let the sentinel reach a real loader.
    """
    specs = _collect_model_field_specs(fields)
    if not specs:
        return

    artifacts_by_type: Dict[str, List[Any]] = {}
    for artifact in getattr(recipe, "artifacts", None) or []:
        artifacts_by_type.setdefault(artifact.model_type, []).append(artifact)

    for spec in specs:
        name = spec["name"]
        if form_data.get(name) != _model_sentinel(name):
            continue  # already a real default/value - leave it alone
        artifact = _artifact_for_field(artifacts_by_type.get(spec["model_type"]) or [], spec["recommended"])
        if artifact is None:
            form_data[name] = ""
            continue
        model = (
            find_slot_model(model_repository, artifact, (selections or {}).get(artifact.id))
            if model_repository is not None
            else None
        )
        if model is None:
            raise RequiredModelMissing(artifact.display_name or artifact.filename, artifact.filename)
        form_data[name] = make_model_ref(model.id)


def build_fixture_form_data(
    preset_template,
    mode: str,
    *,
    overrides: Optional[Dict[str, Any]] = None,
    recipe: Optional[Any] = None,
    model_repository: Optional[Any] = None,
    selections: Optional[Dict[str, str]] = None,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Fixture form data for `preset_template`'s `mode`, with `overrides`
    (e.g. a recipe's `smoke:` field values) applied on top before binding.

    `recipe`/`model_repository` are the real-model-resolution seam: pass
    `recipe` (only `generation.smoke` does) to have every model-typed field
    resolved to a real indexed model via `_resolve_model_fields` instead of
    keeping its `/SETUP-CHECK` dry-run placeholder - see that function's
    docstring for the resolution/failure rules. `pipeline.render` never
    passes `recipe`, so it always renders against the sentinel - a dry run
    that never loads anything for real.
    """
    reactive_defaults: Dict[str, Any] = {}
    form_data, fields = build_default_form_data(preset_template, mode, FIXTURE_TAG, reactive_defaults)

    form_data.setdefault("quantity", 1)
    form_data.setdefault("seed", 42)

    if recipe is not None:
        repo = model_repository
        if repo is None:
            from src.features.models.repository import model_repo as repo
        _resolve_model_fields(
            fields, form_data, recipe=recipe, model_repository=repo, selections=selections
        )

    if overrides:
        form_data.update(overrides)

    if not isinstance(form_data.get("video_director"), dict):
        document = fixture_video_director_document(preset_template, mode, form_data, prompt)
        if document is not None:
            form_data["video_director"] = document

    leave_reactive_defaults_to_binding(form_data, reactive_defaults, overrides or {})

    bound = bind_form(
        preset_template, mode, form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None
    )
    return dict(bound.values)
