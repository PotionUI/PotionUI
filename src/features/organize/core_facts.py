import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.well_known import WellKnownModelAttribute
from src.features.organize.evaluator import NEGATED_TEXT, as_list
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.presets.mode_labels import mode_display_name
from src.platform.filesystem.model_types import VIRTUAL_MODEL_TYPES
from src.platform.plugins.organize import (
    ATTRIBUTE_VALUE_OPERATORS,
    OrganizeFactDefinition,
    OrganizeItem,
    OrganizeRegistry,
)

BASE_MODEL_TYPES = ("checkpoint", "diffusion_model", "unet")

MODE_OPTIONS = tuple(
    {"value": key, "label": mode_display_name(key)}
    for key in ("txt2img", "img2img", "inpaint", "edit", "txt2vid", "img2vid", "upscale")
)

GENERATION_KINDS = (
    {"value": "image", "label": "Image"},
    {"value": "video", "label": "Video"},
    {"value": "audio", "label": "Audio"},
    {"value": "mesh", "label": "3D"},
)

ASPECT_OPTIONS = (
    {"value": "square", "label": "Square"},
    {"value": "landscape", "label": "Landscape"},
    {"value": "portrait", "label": "Portrait"},
)

RESOLUTION_PRESETS = (
    {"label": "Square 1024", "width": 1024, "height": 1024},
    {"label": "Landscape 1344 x 768", "width": 1344, "height": 768},
    {"label": "Portrait 832 x 1216", "width": 832, "height": 1216},
    {"label": "Portrait 768 x 1344", "width": 768, "height": 1344},
    {"label": "HD 720p", "width": 1280, "height": 720},
    {"label": "Full HD 1080p", "width": 1920, "height": 1080},
    {"label": "4K", "width": 3840, "height": 2160},
)

SQUARE_TOLERANCE = 50

BYTES_PER_MB = 1024 * 1024

SOURCE_OPTIONS = (
    {"value": "local", "label": "Local files"},
    {"value": "cloud", "label": "Cloud catalog"},
)

PROVIDER_LABELS = {"civitai": "CivitAI", "huggingface": "Hugging Face", "openrouter": "OpenRouter"}

ATTRIBUTE_TYPES = {
    "slider": ("number", "Number"),
    "number": ("number", "Number"),
    "range": ("number", "Number range"),
    "text": ("text", "Text"),
    "tags": ("text", "Tags"),
    "checkbox": ("bool", "Yes or no"),
    "select": ("enum", "Choice"),
}

SAFE_ATTRIBUTE_KEY = re.compile(r'^[^"\\]+$')

TEXT_SQL = {
    "contains": ("instr(LOWER({e}), LOWER(?)) > 0", 1),
    "starts_with": ("instr(LOWER({e}), LOWER(?)) = 1", 1),
    "ends_with": ("LOWER(substr({e}, -length(?))) = LOWER(?)", 2),
    "is": ("LOWER({e}) = LOWER(?)", 1),
}

VIRTUAL_TYPES_SQL = ",".join(f"'{t}'" for t in VIRTUAL_MODEL_TYPES)

MODEL_STEM_SQL = (
    f"CASE WHEN m.model_type IN ({VIRTUAL_TYPES_SQL}) OR instr(m.filename, '.') = 0 THEN m.filename "
    "ELSE COALESCE(NULLIF(substr(rtrim(m.filename, replace(m.filename, '.', '')), 1, "
    "length(rtrim(m.filename, replace(m.filename, '.', ''))) - 1), ''), m.filename) END"
)

SqlResult = Optional[Tuple[str, List[Any]]]


def aspect_of(width: Any, height: Any) -> Optional[str]:
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    if abs(width - height) * SQUARE_TOLERANCE <= max(width, height):
        return "square"
    return "landscape" if width > height else "portrait"


def prompt_texts(form_data: Any) -> List[str]:
    found: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if isinstance(value, str):
                    if "prompt" in lowered and "negative" not in lowered:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(form_data)
    return found


def _sizes(item: OrganizeItem) -> List[Dict[str, int]]:
    if item.subject == "upload":
        return [{"width": item.get("width"), "height": item.get("height")}] if item.get("width") else []
    return [
        {"width": f["width"], "height": f["height"]}
        for f in item.get("files", []) if f.get("width") and f.get("height")
    ]


def _durations(item: OrganizeItem) -> List[float]:
    if item.subject == "upload":
        return as_list(item.get("duration_seconds"))
    return [f["duration_seconds"] for f in item.get("files", []) if f.get("duration_seconds") is not None]


def _kinds(item: OrganizeItem) -> List[str]:
    if item.subject == "upload":
        return as_list(item.get("media_type"))
    return sorted({f["file_type"] for f in item.get("files", []) if f.get("file_type")})


def _placeholders(values: Sequence[Any]) -> str:
    return ",".join("?" * len(values))


def _in_or_not(column: str, operator: str, values: List[Any], lower: bool = False) -> SqlResult:
    if not values:
        return None
    expr = f"LOWER({column})" if lower else column
    values = [str(v).lower() for v in values] if lower else values
    if operator in ("is", "is_any_of"):
        return f"{expr} IN ({_placeholders(values)})", values
    if operator == "is_not":
        return f"({column} IS NULL OR {expr} NOT IN ({_placeholders(values)}))", values
    return None


def _model_ref_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if alias != "g":
        return None
    ids = [str(v) for v in as_list(value)]
    if not ids:
        return None
    parts = []
    params: List[Any] = []
    for model_id in ids:
        parts.append(
            "(EXISTS (SELECT 1 FROM generation_models gm WHERE gm.generation_id = g.id AND gm.model_id = ?) "
            "OR instr(g.form_data, ?) > 0)"
        )
        params.extend([model_id, f'"model:{model_id}"'])
    clause = " OR ".join(parts)
    if operator in ("is", "is_any_of"):
        return clause, params
    if operator == "is_not":
        return f"NOT ({clause})", params
    return None


def _file_exists(alias: str, predicate: str, params: List[Any]) -> SqlResult:
    if alias == "g":
        return (
            "EXISTS (SELECT 1 FROM generation_files gf JOIN files f ON f.id = gf.file_id "
            f"WHERE gf.generation_id = g.id AND f.is_derived = 0 AND {predicate})",
            params,
        )
    if alias == "u":
        return predicate.replace("f.", "u."), params
    return None


def _media_kind_sql(operator: str, value: Any, alias: str) -> SqlResult:
    values = [str(v).lower() for v in as_list(value)]
    if not values:
        return None
    if alias == "u":
        return _in_or_not("u.media_type", operator, values, lower=True)
    if alias != "g":
        return None
    exists = _file_exists("g", f"LOWER(f.file_type) IN ({_placeholders(values)})", values)
    if operator in ("is", "is_any_of"):
        return exists
    if operator == "is_not":
        return f"NOT {exists[0]}", exists[1]
    return None


def _size_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if not isinstance(value, dict):
        return None
    width, height = value.get("width"), value.get("height")
    comparator = {"is": "=", "at_least": ">=", "at_most": "<="}.get(operator)
    if comparator is None or width is None or height is None:
        return None
    return _file_exists(alias, f"f.width {comparator} ? AND f.height {comparator} ?", [int(width), int(height)])


def _aspect_predicate(aspect: str) -> Optional[str]:
    square = f"ABS(f.width - f.height) * {SQUARE_TOLERANCE} <= MAX(f.width, f.height)"
    if aspect == "square":
        return f"(f.width > 0 AND f.height > 0 AND {square})"
    if aspect == "landscape":
        return f"(f.width > f.height AND f.height > 0 AND NOT ({square}))"
    if aspect == "portrait":
        return f"(f.height > f.width AND f.width > 0 AND NOT ({square}))"
    return None


def _aspect_sql(operator: str, value: Any, alias: str) -> SqlResult:
    predicates = [_aspect_predicate(str(v).lower()) for v in as_list(value)]
    predicates = [p for p in predicates if p]
    if not predicates or operator not in ("is", "is_any_of"):
        return None
    return _file_exists(alias, "(" + " OR ".join(predicates) + ")", [])


def _duration_sql(operator: str, value: Any, alias: str) -> SqlResult:
    comparator = {"is": "=", "at_least": ">=", "at_most": "<="}.get(operator)
    if comparator is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return _file_exists(alias, f"f.duration_seconds IS NOT NULL AND f.duration_seconds {comparator} ?", [seconds])


def _prompt_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if alias != "g" or not isinstance(value, str) or not value:
        return None
    exists = (
        "CASE WHEN json_valid(g.form_data) THEN EXISTS (SELECT 1 FROM json_tree(g.form_data) jt "
        "WHERE jt.type = 'text' AND LOWER(jt.key) LIKE '%prompt%' AND LOWER(jt.key) NOT LIKE '%negative%' "
        "AND instr(LOWER(jt.value), LOWER(?)) > 0) ELSE 0 END"
    )
    if operator == "contains":
        return f"({exists}) = 1", [value]
    if operator == "not_contains":
        return f"({exists}) = 0", [value]
    return None


TextSource = Tuple[str, List[Any], str]


def _text_test(expr: str, operator: str, needle: str) -> SqlResult:
    template = TEXT_SQL.get(NEGATED_TEXT.get(operator, operator))
    if template is None:
        return None
    sql, count = template
    return f"COALESCE({sql.format(e=expr)}, 0)", [needle] * count


def _text_sources_sql(operator: str, value: Any, sources: Sequence[TextSource]) -> SqlResult:
    if not isinstance(value, str) or not value:
        return None
    parts: List[str] = []
    params: List[Any] = []
    for wrapper, wrapper_params, expr in sources:
        built = _text_test(expr, operator, value)
        if built is None:
            return None
        parts.append(f"COALESCE({wrapper.replace('{test}', built[0])}, 0)")
        params.extend([*wrapper_params, *built[1]])
    clause = "(" + " OR ".join(parts) + ")"
    return (f"NOT {clause}" if operator in NEGATED_TEXT else clause), params


def _filename_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if alias == "u":
        return _text_sources_sql(operator, value, [("{test}", [], "COALESCE(u.original_filename, '')")])
    if alias == "m":
        return _text_sources_sql(operator, value, [("{test}", [], "m.filename")])
    return None


def _filename_of(item: OrganizeItem) -> str:
    if item.subject == "upload":
        return item.get("original_filename") or ""
    return item.get("filename") or ""


def _model_names(item: OrganizeItem) -> List[str]:
    names = [item.get("custom_name"), *item.get("provider_names", []), item.get("stem")]
    return [n for n in names if isinstance(n, str) and n]


def _name_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if alias != "m":
        return None
    return _text_sources_sql(operator, value, [
        ("EXISTS (SELECT 1 FROM user_model_meta umm WHERE umm.model_id = m.id AND umm.user_id = viewer.user_id "
         "AND umm.custom_name IS NOT NULL AND umm.custom_name != '' AND {test})", [], "umm.custom_name"),
        ("EXISTS (SELECT 1 FROM providers p WHERE p.model_id = m.id AND p.name IS NOT NULL AND p.name != '' "
         "AND {test})", [], "p.name"),
        ("{test}", [], MODEL_STEM_SQL),
    ])


def _model_descriptions(item: OrganizeItem) -> List[str]:
    texts = [item.get("description"), *item.get("provider_descriptions", [])]
    return [t for t in texts if isinstance(t, str) and t]


def _description_sql(operator: str, value: Any, alias: str) -> SqlResult:
    if alias != "m":
        return None
    return _text_sources_sql(operator, value, [
        ("(m.description IS NOT NULL AND m.description != '' AND {test})", [], "m.description"),
        ("EXISTS (SELECT 1 FROM providers p WHERE p.model_id = m.id AND p.description IS NOT NULL "
         "AND p.description != '' AND {test})", [], "p.description"),
    ])


def _source_predicate(source: str) -> Tuple[str, List[Any]]:
    exists = "EXISTS (SELECT 1 FROM providers p WHERE p.model_id = m.id AND LOWER(p.provider) = ?)"
    if source == "cloud":
        return f"(m.model_type IN ({VIRTUAL_TYPES_SQL}) OR {exists})", [source]
    if source == "local":
        return (
            f"((m.model_type NOT IN ({VIRTUAL_TYPES_SQL}) AND NOT EXISTS (SELECT 1 FROM providers p "
            f"WHERE p.model_id = m.id AND p.provider IS NOT NULL AND p.provider != '')) OR {exists})",
            [source],
        )
    return exists, [source]


def _source_sql(operator: str, value: Any, alias: str) -> SqlResult:
    sources = [str(v).lower() for v in as_list(value) if str(v).strip()]
    if alias != "m" or not sources:
        return None
    parts: List[str] = []
    params: List[Any] = []
    for source in sources:
        clause, clause_params = _source_predicate(source)
        parts.append(clause)
        params.extend(clause_params)
    clause = "(" + " OR ".join(parts) + ")"
    if operator in ("is", "is_any_of"):
        return clause, params
    if operator == "is_not":
        return f"NOT {clause}", params
    return None


def _file_size_mb(item: OrganizeItem) -> List[float]:
    size = item.get("file_size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        return []
    return [size / BYTES_PER_MB]


def _file_size_sql(operator: str, value: Any, alias: str) -> SqlResult:
    comparator = {"is": "=", "at_least": ">=", "at_most": "<="}.get(operator)
    if alias != "m" or comparator is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return (
        f"(m.file_size IS NOT NULL AND m.file_size > 0 AND m.file_size {comparator} ?)",
        [float(value) * BYTES_PER_MB],
    )


def attribute_value_type(field_type: str) -> Tuple[str, str]:
    return ATTRIBUTE_TYPES.get(field_type, ("text", "Text"))


def attribute_option(definition: ModelAttributeDefinition) -> Dict[str, Any]:
    value_type, type_label = attribute_value_type(definition.field_type)
    config = definition.config or {}
    meta: Dict[str, Any] = {
        "type": value_type,
        "type_label": type_label,
        "field_type": definition.field_type,
        "model_types": list(definition.model_types or []),
    }
    if value_type == "enum":
        meta["choices"] = [
            {"value": str(o.get("value")), "label": str(o.get("label", o.get("value")))}
            for o in config.get("options") or [] if isinstance(o, dict) and o.get("value") is not None
        ]
    if value_type == "number":
        meta.update({k: config[k] for k in ("min", "max", "step") if isinstance(config.get(k), (int, float))})
    return {"value": definition.key, "label": definition.label or definition.key, "meta": meta}


def _effective_json_sql(definition: ModelAttributeDefinition) -> Tuple[str, List[Any]]:
    path = f'$."{definition.key}"'
    kind = "json_type(m.model_metadata, ?)"
    shared = (
        "CASE WHEN json_valid(m.model_metadata) THEN CASE "
        f"WHEN {kind} IS NULL OR {kind} = 'null' THEN NULL "
        f"WHEN {kind} IN ('array', 'object') THEN json_extract(m.model_metadata, ?) "
        f"WHEN {kind} = 'true' THEN 'true' WHEN {kind} = 'false' THEN 'false' "
        "ELSE json_quote(json_extract(m.model_metadata, ?)) END END"
    )
    parts: List[str] = []
    params: List[Any] = []
    if definition.per_user:
        parts.append(
            "(SELECT uma.value FROM user_model_attributes uma WHERE uma.user_id = viewer.user_id "
            "AND uma.model_id = m.id AND uma.key = ? AND json_valid(uma.value) AND json_type(uma.value) != 'null')"
        )
        params.append(definition.key)
    parts.append(shared)
    params.extend([path] * 7)
    parts.append("?")
    params.append(json.dumps(definition.default_value) if definition.default_value is not None else None)
    value = f"COALESCE({', '.join(parts)})"
    types = list(definition.model_types or [])
    if types:
        value = f"CASE WHEN m.model_type IN ({_placeholders(types)}) THEN {value} END"
        params = [*types, *params]
    return value, params


def _attribute_elements_sql(definition: ModelAttributeDefinition, value_type: str, operator: str,
                            target: Any) -> SqlResult:
    doc, doc_params = _effective_json_sql(definition)
    if value_type == "text":
        return _text_sources_sql(operator, target, [
            (f"EXISTS (SELECT 1 FROM json_each({doc}) je WHERE je.type = 'text' AND {{test}})", doc_params, "je.value"),
        ])
    if value_type == "number":
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            return None
        numbers = f"FROM json_each({doc}) je WHERE je.type IN ('integer', 'real')"
        low, high = f"(SELECT MIN(je.value) {numbers})", f"(SELECT MAX(je.value) {numbers})"
        target = float(target)
        if operator == "is":
            return f"COALESCE({low} <= ? AND {high} >= ?, 0)", [*doc_params, target + 1e-9, *doc_params, target - 1e-9]
        if operator == "at_least":
            return f"COALESCE({high} >= ?, 0)", [*doc_params, target]
        if operator == "at_most":
            return f"COALESCE({low} <= ?, 0)", [*doc_params, target]
        return None
    if value_type == "enum":
        wanted = [str(v).lower() for v in as_list(target)]
        if not wanted or operator not in ATTRIBUTE_VALUE_OPERATORS["enum"]:
            return None
        exists = (
            f"EXISTS (SELECT 1 FROM json_each({doc}) je WHERE je.type NOT IN ('array', 'object', 'null') "
            f"AND LOWER(CAST(je.value AS TEXT)) IN ({_placeholders(wanted)}))"
        )
        return (f"NOT {exists}" if operator == "is_not" else exists), [*doc_params, *wanted]
    if value_type == "bool":
        if operator != "is" or not isinstance(target, bool):
            return None
        exists = f"EXISTS (SELECT 1 FROM json_each({doc}) je WHERE je.type = 'true')"
        return (exists if target else f"NOT {exists}"), doc_params
    return None


def _attribute_values(item: OrganizeItem, key: str) -> Any:
    return (item.get("attributes") or {}).get(key)


_TAG_JOINS = {
    "g": ("generation_tags", "generation_id", "g.id"),
    "u": ("upload_tags", "upload_id", "u.id"),
    "m": ("model_tags", "model_id", "m.id"),
}


def _tags_sql(operator: str, value: Any, alias: str) -> SqlResult:
    names = [str(v).lower() for v in as_list(value) if str(v).strip()]
    if not names or alias not in _TAG_JOINS:
        return None
    link, column, owner = _TAG_JOINS[alias]
    exists = (
        f"EXISTS (SELECT 1 FROM {link} l JOIN tags t ON t.id = l.tag_id "
        f"WHERE l.{column} = {owner} AND LOWER(t.name) = ?)"
    )
    if operator == "has":
        return " AND ".join([exists] * len(names)), names
    if operator == "has_not":
        return " AND ".join([f"NOT {exists}"] * len(names)), names
    return None


def _preset_sql(operator: str, value: Any, alias: str) -> SqlResult:
    return _in_or_not("g.preset_id", operator, [str(v) for v in as_list(value)]) if alias == "g" else None


def _mode_sql(operator: str, value: Any, alias: str) -> SqlResult:
    return _in_or_not("g.mode", operator, [str(v) for v in as_list(value)], lower=True) if alias == "g" else None


def _model_type_sql(operator: str, value: Any, alias: str) -> SqlResult:
    return _in_or_not("m.model_type", operator, [str(v) for v in as_list(value)], lower=True) if alias == "m" else None


def _base_model_sql(operator: str, value: Any, alias: str) -> SqlResult:
    families = [str(v).lower() for v in as_list(value)]
    if alias != "m" or not families:
        return None
    exists = (
        "EXISTS (SELECT 1 FROM model_header_verdicts v WHERE v.sha256 = m.sha256 "
        f"AND LOWER(v.family) IN ({_placeholders(families)}))"
    )
    if operator in ("is", "is_any_of"):
        return exists, families
    if operator == "is_not":
        return f"NOT {exists}", families
    return None


def _options(values: Sequence[str], query: str, label: Callable[[str], str] = str) -> List[Dict[str, str]]:
    needle = (query or "").casefold()
    return [{"value": v, "label": label(v)} for v in values if needle in v.casefold() or needle in label(v).casefold()]


def register_core_facts(registry: OrganizeRegistry, items: OrganizeItemRepository,
                        presets: Callable[[], List[Tuple[str, str]]]) -> None:
    def preset_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        needle = (query or "").casefold()
        return [
            {"value": preset_id, "label": name}
            for preset_id, name in presets()
            if needle in name.casefold() or needle in preset_id.casefold()
        ]

    def mode_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        values = list(dict.fromkeys([o["value"] for o in MODE_OPTIONS] + items.used_modes(user_id)))
        return _options(values, query, mode_display_name)

    def model_type_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        return _options(items.model_types(), query)

    def family_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        return _options(items.model_families(), query)

    def tag_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        tag_type = {"generation": "GENERATION", "upload": "UPLOAD", "model": "MODEL"}[subject]
        owner = None if subject == "model" else user_id
        return _options(items.tag_names(tag_type, owner, query or "", 200), "")

    def source_options(user_id: str, subject: str, query: str) -> List[Dict[str, str]]:
        static = {o["value"]: o["label"] for o in SOURCE_OPTIONS}
        values = list(dict.fromkeys([*static, *items.provider_ids()]))

        def label(value: str) -> str:
            return static.get(value) or PROVIDER_LABELS.get(value) or value.replace("-", " ").replace("_", " ").title()

        return _options(values, query, label)

    def attribute_options(user_id: str, subject: str, query: str) -> List[Dict[str, Any]]:
        admin = items.is_admin(user_id)
        needle = (query or "").casefold()
        return [
            attribute_option(d) for d in items.attribute_definitions()
            if (admin or not d.admin_only) and SAFE_ATTRIBUTE_KEY.match(d.key)
            and (needle in d.key.casefold() or needle in (d.label or "").casefold())
        ]

    def trigger_words_sql(operator: str, value: Any, alias: str) -> SqlResult:
        if alias != "m":
            return None
        definition = items.attribute_definition(WellKnownModelAttribute.TRIGGERS)
        if definition is None:
            definition = ModelAttributeDefinition(key=WellKnownModelAttribute.TRIGGERS, label="Trigger words",
                                                  field_type="tags")
        return _attribute_elements_sql(definition, "text", operator, value)

    def attribute_sql(operator: str, value: Any, alias: str) -> SqlResult:
        if alias != "m" or not isinstance(value, dict) or not isinstance(value.get("key"), str):
            return None
        definition = items.attribute_definition(value["key"])
        if definition is None or not SAFE_ATTRIBUTE_KEY.match(definition.key):
            return None
        value_type = value.get("type")
        if operator not in ATTRIBUTE_VALUE_OPERATORS.get(value_type, ()):
            return None
        return _attribute_elements_sql(definition, value_type, operator, value.get("value"))

    definitions = (
        OrganizeFactDefinition(
            key="model", label="Model", subjects=("generation",), kind="model_ref",
            extract=lambda item: item.get("model_ids", []), sql=_model_ref_sql,
            picker={"model_types": list(BASE_MODEL_TYPES)},
        ),
        OrganizeFactDefinition(
            key="lora", label="LoRA used", subjects=("generation",), kind="model_ref",
            extract=lambda item: item.get("model_ids", []), sql=_model_ref_sql,
            picker={"model_types": ["lora"]},
        ),
        OrganizeFactDefinition(
            key="preset", label="Preset", subjects=("generation",), kind="enum",
            extract=lambda item: item.get("preset_id"), sql=_preset_sql,
            options_handler=preset_options, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="mode", label="Mode", subjects=("generation",), kind="enum",
            extract=lambda item: item.get("mode"), sql=_mode_sql,
            options=MODE_OPTIONS, options_handler=mode_options, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="media_kind", label="Media kind", subjects=("generation", "upload"), kind="enum",
            extract=_kinds, sql=_media_kind_sql, options=GENERATION_KINDS, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="resolution", label="Resolution", subjects=("generation", "upload"), kind="size",
            extract=_sizes, sql=_size_sql, picker={"presets": [dict(p) for p in RESOLUTION_PRESETS]},
        ),
        OrganizeFactDefinition(
            key="aspect", label="Aspect", subjects=("generation", "upload"), kind="enum",
            operators=("is", "is_any_of"),
            extract=lambda item: [a for a in (aspect_of(s["width"], s["height"]) for s in _sizes(item)) if a],
            sql=_aspect_sql, options=ASPECT_OPTIONS, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="duration", label="Duration", subjects=("generation", "upload"), kind="number",
            operators=("at_least", "at_most"), extract=_durations, sql=_duration_sql,
            picker={"min": 0, "max": 86400, "step": 1, "unit": "s"},
        ),
        OrganizeFactDefinition(
            key="prompt", label="Prompt", subjects=("generation",), kind="text",
            operators=("contains", "not_contains"),
            extract=lambda item: prompt_texts(item.get("form_data", {})), sql=_prompt_sql,
            picker={"placeholder": "a word or phrase"},
        ),
        OrganizeFactDefinition(
            key="tags", label="Tags", subjects=("generation", "upload", "model"), kind="tag_list",
            extract=lambda item: item.get("tags", []), sql=_tags_sql, options_handler=tag_options,
            triggers=("item_created", "tags_changed"),
        ),
        OrganizeFactDefinition(
            key="model_type", label="Model type", subjects=("model",), kind="enum",
            extract=lambda item: item.get("model_type"), sql=_model_type_sql,
            options_handler=model_type_options, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="base_model", label="Base model", subjects=("model",), kind="enum",
            extract=lambda item: item.get("family"), sql=_base_model_sql,
            options_handler=family_options, picker={"multi": True},
        ),
        OrganizeFactDefinition(
            key="filename", label="File name", subjects=("upload", "model"), kind="text",
            extract=_filename_of, sql=_filename_sql,
            picker={"placeholder": "part of the file name"},
        ),
        OrganizeFactDefinition(
            key="name", label="Name", subjects=("model",), kind="text",
            extract=_model_names, sql=_name_sql, triggers=("item_created", "metadata_changed"),
            picker={"placeholder": "part of the name"},
        ),
        OrganizeFactDefinition(
            key="description", label="Description", subjects=("model",), kind="text",
            extract=_model_descriptions, sql=_description_sql, triggers=("item_created", "metadata_changed"),
            picker={"placeholder": "a word or phrase"},
        ),
        OrganizeFactDefinition(
            key="trigger_words", label="Trigger words", subjects=("model",), kind="text",
            extract=lambda item: _attribute_values(item, WellKnownModelAttribute.TRIGGERS), sql=trigger_words_sql,
            triggers=("item_created", "metadata_changed"), picker={"placeholder": "a trigger word"},
        ),
        OrganizeFactDefinition(
            key="source", label="Source", subjects=("model",), kind="enum",
            extract=lambda item: item.get("sources", []), sql=_source_sql,
            options=SOURCE_OPTIONS, options_handler=source_options, picker={"multi": True},
            triggers=("item_created", "metadata_changed"),
        ),
        OrganizeFactDefinition(
            key="file_size", label="File size", subjects=("model",), kind="number",
            operators=("at_least", "at_most"), extract=_file_size_mb, sql=_file_size_sql,
            picker={"min": 0, "max": 1048576, "step": 1, "unit": "MB"},
        ),
        OrganizeFactDefinition(
            key="attribute", label="Attribute", subjects=("model",), kind="attribute",
            extract=lambda item: item.get("attributes") or {}, sql=attribute_sql,
            options_handler=attribute_options, triggers=("item_created", "metadata_changed"),
            picker={"operators_by_type": {k: list(v) for k, v in ATTRIBUTE_VALUE_OPERATORS.items()}},
        ),
    )
    for definition in definitions:
        if registry.fact(definition.key) is None:
            registry.register_fact(definition)
