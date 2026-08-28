"""Shared utilities for built-in LLM tools."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import ToolApprovalPreview
from src.features.models.form_refs import is_model_ref, model_id_of

logger = logging.getLogger(__name__)

# form_data keys the generation preview's settings grid reads, in display order.
_GENERATION_SETTINGS_FIELDS = (
    ("batch_size", "Batch"),
    ("steps", "Steps"),
    ("cfg", "CFG"),
    ("sampler", "Sampler"),
    ("scheduler", "Scheduler"),
)

# Model file extensions used to detect model paths in form data
MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}

# Form schema field types that reference a model selection.
MODEL_FIELD_TYPES = {"model"}


def extract_model_path(value: Any) -> Optional[str]:
    """Return a model locator if *value* looks like a model selection.

    Locators may be legacy file paths or the current picker format,
    ``model:<id>``. The historical function name is kept for callers.
    """
    # Frontend model fields store {modelPath: "...", tagFilters: [...]}
    if isinstance(value, dict) and "modelPath" in value:
        path = value["modelPath"]
        return path if path else None
    if is_model_ref(value):
        return value
    # Plain string ending with a known model extension
    if isinstance(value, str) and value:
        lower = value.lower()
        for ext in MODEL_EXTENSIONS:
            if lower.endswith(ext):
                return value
    return None


def extract_model_selections(value: Any) -> List[Tuple[str, Any]]:
    """Find model locators in scalar and nested form-field values.

    LoRA pickers store rows such as ``[{"model": "model:<id>",
    "strength": 0.8}]``. Returning the nearest row strength lets callers
    exclude disabled (zero-strength) selections without knowing field shapes.
    """
    selections: List[Tuple[str, Any]] = []

    def walk(node: Any, inherited_weight: Any = None) -> None:
        locator = extract_model_path(node)
        if locator:
            weight = inherited_weight
            if isinstance(node, dict):
                weight = node.get("strength", node.get("weight", weight))
            selections.append((locator, weight))
            return

        if isinstance(node, dict):
            weight = node.get("strength", node.get("weight", inherited_weight))
            for key, child in node.items():
                if key in {"strength", "weight"}:
                    continue
                walk(child, weight)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child, inherited_weight)

    walk(value)
    return selections


def resolve_active_models(
    form_data: Dict[str, Any],
    model_index_manager: Any,
) -> List[Tuple[str, str, Any, Dict[str, Any]]]:
    """Resolve every non-disabled model selection in *form_data*.

    Walks each field for model locators (including nested picker rows such as the
    LoRA list), pairs each with its nearest strength/weight, skips zero-weight
    (disabled) selections, and looks the model up in the index. Returns
    ``(field_name, model_path, weight, model_info)`` tuples — the shared resolution
    behind both get_active_models and the chat workspace block.
    """
    resolved: List[Tuple[str, str, Any, Dict[str, Any]]] = []
    for field_name, value in form_data.items():
        for model_path, nested_weight in extract_model_selections(value):
            weight_value = nested_weight
            if weight_value is None:
                weight_field_name = f"{field_name}_strength"
                if weight_field_name not in form_data:
                    weight_field_name = f"{field_name}_weight"
                weight_value = form_data.get(weight_field_name)

            if weight_value is not None:
                try:
                    if float(weight_value) == 0:
                        continue
                except (ValueError, TypeError):
                    pass

            model_info = lookup_model(model_index_manager, model_path)
            resolved.append((field_name, model_path, weight_value, model_info))
    return resolved


def _walk_model_field_metadata(
    node: Any, result: Dict[str, Dict[str, Any]], fallback_name: Optional[str] = None
) -> None:
    if isinstance(node, dict):
        name = node.get("name") or fallback_name
        if isinstance(name, str):
            config = node.get("configuration") or {}
            if node.get("type") in MODEL_FIELD_TYPES or config.get("model_type"):
                entry: Dict[str, Any] = {
                    "label": node.get("title", name),
                    "model_type": config.get("model_type", "unknown"),
                }
                ai_hint = node.get("ai_hint")
                if ai_hint:
                    entry["ai_hint"] = ai_hint
                result[name] = entry
        for child in node.get("children") or []:
            _walk_model_field_metadata(child, result)
    elif isinstance(node, list):
        for child in node:
            _walk_model_field_metadata(child, result)


def build_model_field_metadata(
    preset_manager: Any,
    form_state: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Build ``{field_name: {label, model_type, ai_hint?}}`` from the processed form schema.

    `form_schema.properties` is not a flat `{name: spec}` map - fields nest
    under a `tabs` root's `children` tree arbitrarily deep (mirrors
    model_values.model_field_names). This walks that tree; a top-level
    entry's own dict key still stands in for a spec that carries no `name`
    of its own."""
    result: Dict[str, Dict[str, Any]] = {}
    preset_id = form_state.get("preset")
    mode = form_state.get("mode")
    if not preset_id or not preset_manager:
        return result
    try:
        schema_data = preset_manager.get_form_schema(preset_id, mode=mode)
        props = schema_data.get("form_schema", {}).get("properties", {})
        for name, spec in props.items():
            _walk_model_field_metadata(spec, result, fallback_name=name)
    except Exception as e:
        logger.debug(f"Could not load form schema for field metadata: {e}")
    return result


def resolve_active_model_id(form_state: Optional[dict], model_index_manager: Any) -> Optional[str]:
    """Try to find the model_id of the currently selected checkpoint from form state."""
    if not form_state:
        return None
    form_data = form_state.get("form_data")
    if not form_data or not model_index_manager:
        return None

    for value in form_data.values():
        for model_locator, weight in extract_model_selections(value):
            try:
                if weight is not None and float(weight) == 0:
                    continue
            except (ValueError, TypeError):
                pass

            if is_model_ref(model_locator):
                model_id = model_id_of(model_locator)
                if model_id:
                    return model_id

            try:
                model = model_index_manager.model_repo.get_by_file_path(model_locator)
                if model and model.id:
                    return model.id
            except Exception:
                continue
    return None


def resolve_active_preset_id(form_state: Optional[dict]) -> Optional[str]:
    """Return the currently active preset id from form state, if any."""
    if not isinstance(form_state, dict):
        return None
    return form_state.get("preset")


def video_director_active(form_state: Optional[Dict[str, Any]]) -> bool:
    """Whether the Video Director document is the active editor on the form.

    When true, "segment #N" means a Video Director shot, not a plain prompt
    segment -- teach surfaces that talk about prompt segments (get_current_segments,
    enhance_prompt's update_segment instruction, the PROMPT STATE block) must not
    be offered, or the model has two conflicting meanings for the same word.
    """
    vd = (form_state or {}).get("video_director")
    return bool(vd and vd.get("active"))


def music_director_active(form_state: Optional[Dict[str, Any]]) -> bool:
    """Whether the Music Director document is the active editor on the form.
    Mirrors `video_director_active` exactly."""
    md = (form_state or {}).get("music_director")
    return bool(md and md.get("active"))


def lookup_model(model_index_manager: Any, model_path: str) -> Dict[str, Any]:
    """Look up model details by current model reference or legacy file path."""
    # Current picker values carry the stable database ID directly.
    if is_model_ref(model_path):
        try:
            model_id = model_id_of(model_path)
            repo = model_index_manager.model_repo
            model = repo.get_by_id(model_id, include_providers=True, include_tags=True)
            if model:
                return model_to_dict(
                    model.to_dict(include_providers=True, include_tags=True)
                )
        except Exception as e:
            logger.debug(f"Model ID lookup failed for {model_path}: {e}")
        return {
            "id": model_id_of(model_path),
            "model_ref": model_path,
            "description": "Model not found in index",
        }

    # Legacy form values use a file path. Try the exact path first.
    try:
        repo = model_index_manager.model_repo
        model = repo.get_by_file_path(model_path, include_providers=True)
        if model:
            return model_to_dict(model.to_dict(include_providers=True))
    except Exception as e:
        logger.debug(f"Exact path lookup failed for {model_path}: {e}")

    # Fallback: search by filename
    try:
        filename = model_path.rsplit("/", 1)[-1]
        repo = model_index_manager.model_repo
        models = repo.get_all(
            search=filename, limit=1,
            include_providers=True, include_tags=True,
        )
        if models:
            return model_to_dict(
                models[0].to_dict(include_providers=True, include_tags=True)
            )
    except Exception as e:
        logger.debug(f"Filename search failed for {model_path}: {e}")

    return {"path": model_path, "description": "Model not found in index"}


def model_to_dict(d: dict) -> Dict[str, Any]:
    """Slim model dict for LLM consumption."""
    tags = [
        (t.get("name", "") if isinstance(t, dict) else str(t))
        for t in d.get("tags", [])
    ]
    result: Dict[str, Any] = {
        "id": d.get("id", ""),
        "filename": d.get("filename", ""),
        "type": d.get("model_type", d.get("type", "")),
        "description": d.get("description", ""),
        "tags": tags,
    }
    # Trigger words and admin prompting guidance are exactly what the LLM
    # needs to prompt a LoRA correctly — include when present, omit when empty.
    if d.get("triggers"):
        result["trigger_words"] = d["triggers"]
    if d.get("prompting_guidance"):
        result["prompting_guidance"] = d["prompting_guidance"]
    # Provider info (e.g. civitai)
    for provider in d.get("providers", []):
        if isinstance(provider, dict) and provider.get("description"):
            result["provider_info"] = {
                "name": provider.get("name", ""),
                "description": provider.get("description", ""),
            }
            break
    else:
        pi = d.get("provider_info")
        if pi:
            result["provider_info"] = {
                "name": pi.get("name", ""),
                "description": pi.get("description", ""),
            }

    # Build combined_description from model description + provider description
    descriptions = []
    if result.get("description"):
        descriptions.append(result["description"])
    provider_desc = result.get("provider_info", {}).get("description", "")
    if provider_desc and provider_desc != result.get("description"):
        descriptions.append(provider_desc)
    if descriptions:
        result["combined_description"] = " | ".join(descriptions)

    return result


def build_generation_preview(
    *,
    preset_id: Optional[str],
    mode: str,
    prompt_text: str,
    negative_text: str,
    form_data: Dict[str, Any],
    old_form_data: Optional[Dict[str, Any]] = None,
) -> ToolApprovalPreview:
    """Build the `kind="generation"` approval preview shared by run_generation and
    start_generation.

    `form_data` holds the final (post-override) values the settings grid reads.
    `old_form_data` is the form's values *before* any override was applied --
    passed only by run_generation, which reads a live chat form; start_generation
    has no live form baseline, so it passes None and no field is ever flagged
    "old" (there is nothing real to diff against).
    """
    old_form_data = old_form_data or {}

    def _old(key: str, new_value: Any) -> Optional[str]:
        if key not in old_form_data:
            return None
        old_value = old_form_data[key]
        if old_value == new_value:
            return None
        return str(old_value)

    fields: List[Dict[str, Any]] = []
    if preset_id:
        fields.append({"label": "Preset", "value": str(preset_id)})
    fields.append({"label": "Mode", "value": mode})

    width, height = form_data.get("width"), form_data.get("height")
    if width is not None and height is not None:
        entry: Dict[str, Any] = {"label": "Resolution", "value": f"{width}×{height}", "mono": True}
        old_width, old_height = old_form_data.get("width"), old_form_data.get("height")
        if (old_width is not None or old_height is not None) and (old_width, old_height) != (width, height):
            entry["old"] = f"{old_width if old_width is not None else width}×{old_height if old_height is not None else height}"
        fields.append(entry)

    for key, label in _GENERATION_SETTINGS_FIELDS:
        # Presets use "cfg" natively; a few callers still pass "cfg_scale".
        lookup_key = key if key != "cfg" or key in form_data else "cfg_scale"
        if lookup_key not in form_data:
            continue
        value = form_data[lookup_key]
        entry = {"label": label, "value": str(value)}
        old = _old(lookup_key, value)
        if old is not None:
            entry["old"] = old
        fields.append(entry)

    if form_data.get("seed") is not None:
        entry = {"label": "Seed", "value": str(form_data["seed"]), "mono": True}
        old = _old("seed", form_data["seed"])
        if old is not None:
            entry["old"] = old
        fields.append(entry)

    summary = prompt_text[:90] + ("..." if len(prompt_text) > 90 else "") if prompt_text else "Generation"

    text_blocks = [{"label": "Prompt", "text": prompt_text}]
    if negative_text:
        text_blocks.append({"label": "Negative prompt", "text": negative_text})

    return ToolApprovalPreview(
        action="Start generation",
        target=str(preset_id) if preset_id else None,
        kind="generation",
        summary=summary,
        fields=fields,
        text_blocks=text_blocks,
    )
