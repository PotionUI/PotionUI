"""@form resource provider.

Exposes the CURRENT values of the generate form the user is looking at, so the
chat LLM can reason about what the user has actually configured. A bare
``@form`` dumps every non-empty scalar (and model-valued) field.

Field resolution is TYPE-aware, keyed on the bound form's field types (via
``preset_manager.get_form_schema``), never on field names:

- Model-reference fields (schema type ``model``/``models``, plus any field
  whose value carries the self-describing ``model:<id>`` ref — the shape every
  model-picking field type stores) resolve to the same rich model resource the
  @models provider produces (metadata, trigger words, prompting guidance).
- ``lora_picker`` fields are browsable: ``@form.<field>`` still attaches the
  whole selection, while ``@form.<field>.<model_id>`` (or row index for legacy
  path values) attaches one selected LoRA's model resource plus its weight.
- Every other field type keeps the raw-value rendering.

Values come from the send-time form snapshot the chat panel already ships in
``context_metadata.form_state`` (threaded onto ``ResourceContext.form_state``),
so an attached @form ref is a point-in-time snapshot exactly like every other
@resource — no separate client-side resolution path.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
    stem,
)
from src.platform.resources.builtin.models_provider import ModelsResourceProvider
from src.platform.resources.prompt_variables import render_prompt_variable_lines

logger = logging.getLogger(__name__)

# Mirrors src.features.models.form_refs.MODEL_REF_PREFIX. The platform layer may
# not import src.features (tests/architecture/test_layering.py), and the contract
# is a stable one-line prefix, so it is duplicated rather than imported.
_MODEL_REF_PREFIX = "model:"

# Caps that keep a bare @form dump from bloating the prompt.
_MAX_DUMP_FIELDS = 40
_MAX_VALUE_CHARS = 200
_MAX_CONTENT_CHARS = 4000
# The bare-namespace dump is attachable under this reserved leaf too, so a user
# who navigated into `@form.` can still grab "everything".
_ALL_LEAF = "all"

_MODEL_FIELD_TYPES = {"model", "models"}
_LORA_PICKER_TYPE = "lora_picker"


def _is_model_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_MODEL_REF_PREFIX)


def _model_id_of(value: str) -> str:
    return value[len(_MODEL_REF_PREFIX):]


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _collect_model_refs(node: Any) -> List[Tuple[str, Any]]:
    """Every ``model:<id>`` referenced in a form value, with its nearest strength.

    LoRA-picker rows look like ``[{"model": "model:<id>", "strength": 0.8}]``;
    a plain model field is just the ``model:<id>`` string.
    """
    out: List[Tuple[str, Any]] = []

    def walk(n: Any, strength: Any) -> None:
        if _is_model_ref(n):
            out.append((_model_id_of(n), strength))
            return
        if isinstance(n, dict):
            s = n.get("strength", n.get("weight", strength))
            for key, child in n.items():
                if key in ("strength", "weight"):
                    continue
                walk(child, s)
        elif isinstance(n, (list, tuple)):
            for child in n:
                walk(child, strength)

    walk(node, None)
    return out


def _lora_rows(value: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalized selected-LoRA rows for a lora-picker shaped value, or None.

    Each row gets a ``selector`` (the model id when the row stores a
    ``model:<id>`` ref, otherwise the row index) used as the last uri segment.
    """
    if not isinstance(value, list) or not value:
        return None
    rows: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("model"), str):
            return None
        locator = item["model"]
        model_id = _model_id_of(locator) if _is_model_ref(locator) else None
        rows.append({
            "selector": model_id or str(index),
            "model_id": model_id,
            "locator": locator,
            "strength": item.get("strength"),
        })
    return rows


def _is_lora_field(field_type: Optional[str], rows: Optional[List[Dict[str, Any]]]) -> bool:
    if field_type == _LORA_PICKER_TYPE:
        return rows is not None
    if field_type:
        return False
    return rows is not None and all(r["model_id"] for r in rows)


def _weight_text(strength: Any) -> str:
    try:
        return f"{float(strength):g}" if strength is not None else "unknown"
    except (TypeError, ValueError):
        return "unknown"


class FormResourceProvider(BaseResourceProvider):
    """Exposes the live generate-form values as attachable resources."""

    icon = "sliders-horizontal"

    @property
    def namespace(self) -> str:
        return "form"

    # --- helpers ---

    @staticmethod
    def _form_data(ctx: ResourceContext) -> Optional[dict]:
        form_state = getattr(ctx, "form_state", None)
        if not isinstance(form_state, dict):
            return None
        form_data = form_state.get("form_data")
        return form_data if isinstance(form_data, dict) else None

    @staticmethod
    def _match_field(form_data: dict, name: str) -> Optional[str]:
        if name in form_data:
            return name
        return next((k for k in form_data if k.lower() == name.lower()), None)

    @staticmethod
    def _field_types(ctx: ResourceContext) -> Dict[str, str]:
        """Field name → schema type for the bound form, {} when unavailable."""
        form_state = getattr(ctx, "form_state", None)
        if not isinstance(form_state, dict) or not ctx.preset_manager:
            return {}
        preset_id = form_state.get("preset")
        if not preset_id:
            return {}
        try:
            schema = ctx.preset_manager.get_form_schema(preset_id, mode=form_state.get("mode"))
        except Exception:
            return {}
        props = ((schema or {}).get("form_schema") or {}).get("properties")
        if not isinstance(props, dict):
            return {}
        return {
            name: str(fs.get("type") or "")
            for name, fs in props.items()
            if isinstance(fs, dict)
        }

    @staticmethod
    def _lookup_model(ctx: ResourceContext, model_id: Optional[str] = None, locator: Optional[str] = None):
        if not ctx.model_index_manager:
            return None
        repo = ctx.model_index_manager.model_repo
        try:
            if model_id:
                return repo.get_by_id(model_id, include_providers=True, include_tags=False)
            if locator:
                return repo.get_by_file_path(locator, include_providers=True)
        except Exception:
            return None
        return None

    @staticmethod
    def _variable_lines(ctx: ResourceContext) -> List[str]:
        form_state = getattr(ctx, "form_state", None)
        if not isinstance(form_state, dict):
            return []
        return render_prompt_variable_lines(form_state.get("variables"))

    def _model_name(self, ctx: ResourceContext, model_id: str) -> Optional[str]:
        if not ctx.model_index_manager:
            return None
        try:
            model = ctx.model_index_manager.model_repo.get_by_id(
                model_id, include_providers=False, include_tags=False
            )
        except Exception:
            return None
        if not model:
            return None
        return getattr(model, "display_name", None) or stem(getattr(model, "filename", "")) or None

    def _value_text(self, value: Any, ctx: ResourceContext) -> Optional[str]:
        """Short human string for a field value, or None for a value worth skipping."""
        refs = _collect_model_refs(value)
        if refs:
            names = []
            for model_id, strength in refs:
                name = self._model_name(ctx, model_id) or model_id
                try:
                    weight = float(strength) if strength is not None else None
                except (ValueError, TypeError):
                    weight = None
                names.append(f"{name} @ {weight:g}" if (weight is not None and weight != 1.0) else name)
            return ", ".join(names)
        if _is_scalar(value):
            text = str(value)
            return text[:_MAX_VALUE_CHARS] + "…" if len(text) > _MAX_VALUE_CHARS else text
        return None

    def _render_field(self, field: str, value: Any, ctx: ResourceContext) -> str:
        text = self._value_text(value, ctx)
        if text is None:
            # Complex non-model value (segment list, nested config) — show a
            # compact JSON so the model still sees the shape it asked for.
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                text = str(value)
            if len(text) > _MAX_CONTENT_CHARS:
                text = text[:_MAX_CONTENT_CHARS] + "…"
        return f"Current form value — {field}: {text}"

    def _render_dump(self, form_data: dict, ctx: ResourceContext) -> str:
        lines = ["## Current form values (snapshot at send time)", ""]
        count = 0
        for name, value in form_data.items():
            if _is_empty(value):
                continue
            text = self._value_text(value, ctx)
            if text is None:
                continue  # skip complex fields in the overview dump
            lines.append(f"- {name}: {text}")
            count += 1
            if count >= _MAX_DUMP_FIELDS:
                lines.append("- …(more fields omitted)")
                break

        var_lines = self._variable_lines(ctx)
        if count == 0 and not var_lines:
            return "The form has no values set yet."
        if var_lines:
            lines.append("")
            lines.append("## Prompt variables")
            lines.extend(f"- {line}" for line in var_lines)
        return "\n".join(lines)

    # --- provider contract ---

    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        # Suggestions are normally served client-side from the live tab formData
        # (the chip input has form state the suggest endpoint doesn't). This
        # server-side path still works when form_state is present, keeping the
        # provider self-contained for plugins/tests.
        form_data = self._form_data(ctx)
        if not form_data:
            return []
        field_types = self._field_types(ctx)

        if path:
            if len(path) != 1:
                return []
            field = self._match_field(form_data, path[0])
            if field is None:
                return []
            rows = _lora_rows(form_data[field])
            if not _is_lora_field(field_types.get(field), rows):
                return []
            return self._suggest_lora_rows(field, rows, partial, ctx)[:limit]

        needle = partial.lower()
        suggestions: List[ResourceSuggestion] = []
        if not needle or _ALL_LEAF.startswith(needle) or "form".startswith(needle):
            suggestions.append(ResourceSuggestion(
                uri="form",
                label="All form values",
                kind="form",
                description="Every non-empty field",
                has_children=False,
                icon=self.icon,
            ))
        for name, value in form_data.items():
            if _is_empty(value) or (needle and not name.lower().startswith(needle)):
                continue
            rows = _lora_rows(value)
            if _is_lora_field(field_types.get(name), rows):
                suggestions.append(ResourceSuggestion(
                    uri=f"form.{name}",
                    label=name,
                    kind="lora_picker",
                    description=f"{len(rows)} LoRA{'s' if len(rows) != 1 else ''} selected",
                    has_children=True,
                    attachable=True,
                    icon=self.icon,
                ))
                continue
            preview = self._value_text(value, ctx)
            suggestions.append(ResourceSuggestion(
                uri=f"form.{name}",
                label=name,
                kind="form_value",
                description=(preview[:60] if preview else None),
                has_children=False,
                icon=self.icon,
            ))
        return suggestions[:limit]

    def _suggest_lora_rows(
        self,
        field: str,
        rows: List[Dict[str, Any]],
        partial: str,
        ctx: ResourceContext,
    ) -> List[ResourceSuggestion]:
        needle = partial.lower()
        suggestions: List[ResourceSuggestion] = []
        if not needle:
            suggestions.append(ResourceSuggestion(
                uri=f"form.{field}",
                label=field,
                kind="lora_picker",
                description=f"Attach all {len(rows)} selected LoRA{'s' if len(rows) != 1 else ''}",
                has_children=True,
                attachable=True,
                icon=self.icon,
            ))
        for row in rows:
            name = None
            if row["model_id"]:
                name = self._model_name(ctx, row["model_id"])
            name = name or stem(row["locator"].rsplit("/", 1)[-1]) or row["selector"]
            if needle and needle not in name.lower():
                continue
            suggestions.append(ResourceSuggestion(
                uri=f"form.{field}.{row['selector']}",
                label=f"{name} @ {_weight_text(row['strength'])}",
                kind="lora",
                has_children=False,
                icon=self.icon,
            ))
        return suggestions

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        form_data = self._form_data(ctx)
        if form_data is None:
            return None

        if not path or path == [_ALL_LEAF]:
            content = self._render_dump(form_data, ctx)
            return ResolvedResource(
                uri="form",
                namespace=self.namespace,
                kind="form",
                title="Form values",
                content=content[:_MAX_CONTENT_CHARS],
                metadata={"field_count": sum(1 for v in form_data.values() if not _is_empty(v))},
            )

        field = self._match_field(form_data, ".".join(path))
        selector: Optional[str] = None
        if field is None and len(path) >= 2:
            field = self._match_field(form_data, ".".join(path[:-1]))
            selector = path[-1] if field is not None else None
        if field is None:
            return None

        value = form_data[field]
        field_type = self._field_types(ctx).get(field)
        rows = _lora_rows(value)

        if selector is not None:
            if not _is_lora_field(field_type, rows):
                return None
            return self._resolve_lora_row(field, selector, rows, ctx)

        if field_type in _MODEL_FIELD_TYPES or _is_model_ref(value):
            return self._resolve_model_field(field, value, ctx)

        content = self._render_field(field, value, ctx)
        return ResolvedResource(
            uri=f"form.{field}",
            namespace=self.namespace,
            kind="form_value",
            title=field,
            content=content[:_MAX_CONTENT_CHARS],
            metadata={"field": field},
        )

    def _resolve_model_field(
        self, field: str, value: Any, ctx: ResourceContext
    ) -> ResolvedResource:
        model_id = _model_id_of(value) if _is_model_ref(value) else None
        locator = None if model_id else (value if isinstance(value, str) and value.strip() else None)
        model = self._lookup_model(ctx, model_id=model_id, locator=locator)
        if model is None:
            raw = value if isinstance(value, str) else str(value)
            content = (
                f"Current form value — {field}: {raw}\n"
                "(This model reference is not in the model index — treat it as an opaque value.)"
            )
            return ResolvedResource(
                uri=f"form.{field}",
                namespace=self.namespace,
                kind="form_value",
                title=field,
                content=content[:_MAX_CONTENT_CHARS],
                metadata={"field": field, "model_id": model_id},
            )

        name = getattr(model, "display_name", None) or stem(getattr(model, "filename", "")) or field
        model_type = getattr(model, "model_type", None) or "model"
        block = ModelsResourceProvider._render(model, model_type, [])
        content = f"Current form value — {field}: {name}\n\n{block}"
        return ResolvedResource(
            uri=f"form.{field}",
            namespace=self.namespace,
            kind=model_type,
            title=name,
            content=content[:_MAX_CONTENT_CHARS],
            metadata={
                "field": field,
                "model_id": model.id,
                "model_type": model.model_type,
                "filename": model.filename,
            },
        )

    def _resolve_lora_row(
        self,
        field: str,
        selector: str,
        rows: List[Dict[str, Any]],
        ctx: ResourceContext,
    ) -> Optional[ResolvedResource]:
        row = next((r for r in rows if r["selector"] == selector), None)
        if row is None:
            row = next((r for i, r in enumerate(rows) if str(i) == selector), None)
        if row is None:
            return None

        weight = _weight_text(row["strength"])
        uri = f"form.{field}.{selector}"
        model = self._lookup_model(
            ctx,
            model_id=row["model_id"],
            locator=None if row["model_id"] else row["locator"],
        )
        if model is None:
            content = (
                f"Selected LoRA in form field '{field}' (weight {weight}): {row['locator']}\n"
                "(Not found in the model index — treat it as an opaque value.)"
            )
            return ResolvedResource(
                uri=uri,
                namespace=self.namespace,
                kind="form_value",
                title=row["locator"],
                content=content[:_MAX_CONTENT_CHARS],
                metadata={"field": field, "model_id": row["model_id"], "strength": row["strength"]},
            )

        name = getattr(model, "display_name", None) or stem(getattr(model, "filename", "")) or selector
        model_type = getattr(model, "model_type", None) or "lora"
        block = ModelsResourceProvider._render(model, model_type, [])
        content = f"Selected LoRA in form field '{field}': {name} at weight {weight}\n\n{block}"
        return ResolvedResource(
            uri=uri,
            namespace=self.namespace,
            kind=model_type,
            title=name,
            content=content[:_MAX_CONTENT_CHARS],
            metadata={
                "field": field,
                "model_id": model.id,
                "model_type": model.model_type,
                "filename": model.filename,
                "strength": row["strength"],
            },
        )
