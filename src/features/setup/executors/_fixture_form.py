"""Shared fixture-form-data construction for the two setup steps that need to
bind a preset's form without any real user input: `pipeline.render` (a dry
run - no GPU) and `generation.smoke` (a real generation, on tiny/fast values).

Mirrors `scripts/preset_render.py`'s developer harness (walk the mode's
default form fields, inject harmless placeholders for model/lora_picker/
required-media fields that can't have a real fixture value yet, then run the
real `bind_form`) - trimmed to what a single preset+mode needs, without that
harness's golden-snapshot determinism concerns. `generation.smoke` overrides
a caller-supplied subset of fields (the recipe's `smoke:` section) on top of
these defaults before binding.

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
from src.features.presets.form_serializer import PresetFormSerializer
from src.features.presets.templates import default_form_name
from src.platform.templating import TemplateProcessor

# These field types can never carry a real fixture value (models aren't known
# until download time; a required media field has no real file to point at), so
# a placeholder is injected instead of letting `bind_form`'s `required: true`
# check reject the whole render.
MODEL_FIELD_TYPES = frozenset({"model", "models"})
LORA_PICKER_FIELD_TYPE = "lora_picker"
MEDIA_FIELD_TYPES = frozenset({"image", "video", "audio", "media", "file"})
MEDIA_EXTENSIONS = {"image": "png", "video": "mp4", "audio": "wav", "media": "png", "file": "bin"}


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
    return f"/SETUP-CHECK/models/{name}.safetensors"


def _field_get(field_obj: Any, attr: str) -> Any:
    """Read an attribute off a field that may be a `FieldTemplate` or a raw
    dict (external-children files resolve to dicts - see
    `PresetFormSerializer._resolve_external_children`)."""
    if isinstance(field_obj, dict):
        return field_obj.get(attr)
    return getattr(field_obj, attr, None)


def _collect_named_fields(fields: List[Any], form_data: Dict[str, Any]) -> None:
    for field_obj in fields:
        name = _field_get(field_obj, "name")
        if name:
            value = _field_get(field_obj, "value")
            default = _field_get(field_obj, "default")
            if value is not None:
                form_data[name] = value
            elif default is not None:
                form_data[name] = default
        children = _field_get(field_obj, "children")
        if children and isinstance(children, list):
            _collect_named_fields(children, form_data)


def _inject_unresolvable_defaults(fields: List[Any], form_data: Dict[str, Any]) -> None:
    for field_obj in fields:
        name = _field_get(field_obj, "name")
        field_type = _field_get(field_obj, "type")
        if name and name not in form_data:
            if field_type in MODEL_FIELD_TYPES:
                form_data[name] = _model_sentinel(name)
            elif field_type == LORA_PICKER_FIELD_TYPE:
                form_data[name] = []
            elif field_type in MEDIA_FIELD_TYPES and _field_get(field_obj, "required"):
                ext = MEDIA_EXTENSIONS.get(field_type, "bin")
                placeholder = f"/SETUP-CHECK/media/{name}.{ext}"
                # A `configuration.multi` media field carries a LIST, and its
                # own validator rejects a scalar outright, so a placeholder of
                # the wrong shape fails the dry run on a field that is fine in
                # production.
                configuration = _field_get(field_obj, "configuration") or {}
                multi = bool(configuration.get("multi")) if isinstance(configuration, dict) else False
                form_data[name] = [placeholder] if multi else placeholder
        children = _field_get(field_obj, "children")
        if children and isinstance(children, list):
            _inject_unresolvable_defaults(children, form_data)


def _collect_model_field_specs(fields: List[Any]) -> List[Dict[str, str]]:
    """Flatten every `model`/`models`-typed field in `fields` (recursing into
    `children`) into `{"name", "model_type"}` pairs. `lora_picker` fields are
    handled separately (always `[]`, never a fixture path) and excluded."""
    specs: List[Dict[str, str]] = []
    for field_obj in fields:
        name = _field_get(field_obj, "name")
        field_type = _field_get(field_obj, "type")
        if name and field_type in MODEL_FIELD_TYPES:
            configuration = _field_get(field_obj, "configuration") or {}
            model_type = (
                configuration.get("model_type", "checkpoint")
                if isinstance(configuration, dict)
                else "checkpoint"
            )
            specs.append({"name": name, "model_type": model_type})
        children = _field_get(field_obj, "children")
        if children and isinstance(children, list):
            specs.extend(_collect_model_field_specs(children))
    return specs


def _resolve_model_fields(
    fields: List[Any],
    form_data: Dict[str, Any],
    *,
    recipe: Any,
    model_repository: Any,
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

    artifacts_by_type: Dict[str, Any] = {}
    for artifact in getattr(recipe, "artifacts", None) or []:
        artifacts_by_type.setdefault(artifact.model_type, artifact)

    for spec in specs:
        name = spec["name"]
        if form_data.get(name) != _model_sentinel(name):
            continue  # already a real default/value - leave it alone
        artifact = artifacts_by_type.get(spec["model_type"])
        if artifact is None:
            form_data[name] = ""
            continue
        model = (
            model_repository.get_by_identity(spec["model_type"], artifact.filename)
            if model_repository is not None
            else None
        )
        if model is None or not getattr(model, "file_path", None):
            raise RequiredModelMissing(artifact.display_name or artifact.filename, artifact.filename)
        form_data[name] = model.file_path


def build_fixture_form_data(
    preset_template,
    mode: str,
    *,
    preset_template_loader,
    template_processor: TemplateProcessor,
    overrides: Optional[Dict[str, Any]] = None,
    recipe: Optional[Any] = None,
    model_repository: Optional[Any] = None,
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
    form_serializer = PresetFormSerializer(
        preset_loader=preset_template_loader,
        template_processor=template_processor,
    )
    mode_data = preset_template.modes[mode]
    form_name = default_form_name(mode_data)
    form_data: Dict[str, Any] = {}
    resolved_fields: List[Any] = []

    if form_name is not None:
        form_template = next(f for f in mode_data.forms if f.name == form_name)
        context_dict = {"paths": {"preset": preset_template.path}}
        # Reach into the "private" `_resolve_external_children` to resolve
        # @loop / external tab files exactly like the real form-schema endpoint.
        resolved_fields = form_serializer._resolve_external_children(form_template.fields, context_dict)
        _collect_named_fields(resolved_fields, form_data)
        _inject_unresolvable_defaults(resolved_fields, form_data)

    form_data.setdefault("quantity", 1)
    form_data.setdefault("seed", 42)

    if recipe is not None:
        repo = model_repository
        if repo is None:
            from src.features.models.repository import model_repo as repo
        _resolve_model_fields(resolved_fields, form_data, recipe=recipe, model_repository=repo)

    if overrides:
        form_data.update(overrides)

    bound = bind_form(
        preset_template, mode, form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None
    )
    return dict(bound.values)
