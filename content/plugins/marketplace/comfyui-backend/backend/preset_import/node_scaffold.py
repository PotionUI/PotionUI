"""Best-effort extraction of a `node_catalog.yml` entry skeleton for one
ComfyUI node class, from either a saved `/object_info` dump or ComfyUI's own
Python source (`nodes.py` / `comfy_extras/*.py`) - never by importing or
executing ComfyUI code.

Three input shapes feed the same `ExtractedNode`/`ExtractedInput` model:

- `extract_from_object_info` reads a `/object_info` entry (same shape
  `suggest._find_input_spec` reads: `{"input": {"required"|"optional":
  {name: [type_spec, config]}}}`).
- `extract_from_source` `ast`-parses a Python source string for a class
  named `class_name`, trying the legacy `INPUT_TYPES` classmethod (a
  literal `{"required": {...}, "optional": {...}}` dict of `(type, config)`
  tuples) first, then the newer `define_schema` classmethod (an `io.Schema`
  call with an `inputs=[io.X.Input(...), ...]` list).
- `extract_from_comfyui_src` locates the file (`find_class_file`, a regex
  scan of `nodes.py` then `comfy_extras/*.py` for `class <name>(` or
  `node_id="<name>"` - a class registered under `define_schema` need not
  share its Python class name with its node id) and hands its text to
  `extract_from_source`.

`render_scaffold` turns an `ExtractedNode` into paste-ready YAML text for
`node_catalog.yml`, with `# TODO` markers wherever a human must confirm a
guess (the category, and any input this module could only classify as a
generic "option" widget).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .node_catalog import MODEL_FILE_BY_INPUT_NAME as _MODEL_FILE_BY_INPUT_NAME

# Connection (non-widget) ComfyUI types this module recognizes. Anything
# else reaching `_classify` is treated as a widget, including a type this
# module has never seen - a widget guess is more useful than silently
# dropping the input.
CONNECTION_TYPES = frozenset(
    {
        "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "NOISE",
        "GUIDER", "SAMPLER", "SIGMAS", "CLIP_VISION", "CONTROL_NET",
        "STYLE_MODEL", "MASK", "AUDIO", "VIDEO",
    }
)

# Connected input name -> node_catalog.yml link kind, per NodeEntry.links'
# closed LINK_KINDS set. An input outside this map still gets a
# `# connected: <TYPE>` comment rather than a guessed link - a wrong link
# kind wires the graph wrong; a missing one just means more manual work.
_LINK_NAME_MAP = {
    "positive": "prompt_positive",
    "negative": "prompt_negative",
    "conditioning": "prompt_positive",
    "model": "model_chain",
    "clip": "clip_chain",
    "latent_image": "latent",
    "noise": "sampling",
    "guider": "sampling",
    "sampler": "sampling",
    "sigmas": "sampling",
}

# `clip_name` and `model_name` aren't in the shared `MODEL_FILE_BY_INPUT_NAME`
# below - they're ambiguous by name alone (see `_infer_model_file`);
# `lora_name` isn't either - it gets its own `lora_picker` rendering (see
# `_is_lora_node`/`_render_lora_slot_input`), never the generic `field: model`
# treatment.
_SAMPLING_NAME_HINTS = ("sampler", "scheduler", "guider", "noise")


@dataclass
class ExtractedInput:
    name: str
    type_name: str  # "INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", or a CONNECTION_TYPES member
    connected: bool
    config: Dict[str, Any] = field(default_factory=dict)
    # Raw source text for a value this module couldn't statically evaluate
    # (e.g. `comfy.samplers.KSampler.SAMPLERS`) - shown as a comment rather
    # than dropped silently.
    source_expr: Optional[str] = None


@dataclass
class ExtractedNode:
    class_type: str
    style: str  # "object_info" | "legacy" | "define_schema"
    inputs: List[ExtractedInput]
    category_guess: str
    file_path: Optional[Path] = None
    line_no: Optional[int] = None


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _camel_to_upper_snake(word: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", word).upper()


def _classify(type_name: str) -> bool:
    """Whether `type_name` is a connection type (vs. a widget)."""
    return type_name in CONNECTION_TYPES


def _is_lora_node(inputs: List[ExtractedInput]) -> bool:
    """A `lora_name` combo is what makes a node a LoRA loader to this
    catalog (see `LoraLoader`/`LoraLoaderModelOnly` in `node_catalog.yml`) -
    not its class name, which a third-party LoRA node has no obligation to
    contain "lora" in."""
    return any(i.type_name == "COMBO" and i.name == "lora_name" for i in inputs)


def guess_category(class_type: str, inputs: List[ExtractedInput]) -> str:
    """A best-effort `NodeEntry.category` guess from the class name and its
    input shape - always worth double-checking (`render_scaffold` marks it
    with a TODO unless the caller passes an explicit override, or the guess
    is certain enough not to need one - see `_is_lora_node`)."""
    lname = class_type.lower()
    input_names = {i.name for i in inputs}
    if "note" in lname:
        return "ignore"
    if _is_lora_node(inputs) or "lora" in lname:
        return "lora"
    if "loader" in lname:
        return "loader"
    if "loadimage" in lname.replace("_", ""):
        return "image_input"
    if "save" in lname or "preview" in lname:
        return "output"
    if "latent" in lname:
        return "latent"
    if {"positive", "negative"} <= input_names:
        return "sampler"
    if any(hint in lname for hint in _SAMPLING_NAME_HINTS):
        return "sampling"
    return "modifier"


def _infer_model_file(class_type: str, input_name: str) -> Optional[Tuple[str, str]]:
    """`(role, folder)` for a `*_name` combo whose form-file target can be
    guessed from its name (and, for the two ambiguous names below, the
    owning class) - `None` when nothing here is confident enough to guess."""
    if input_name in _MODEL_FILE_BY_INPUT_NAME:
        return _MODEL_FILE_BY_INPUT_NAME[input_name]
    lname = class_type.lower()
    if input_name == "clip_name":
        if "clipvision" in lname.replace("_", ""):
            return "clip_vision", "clip_vision"
        return "clip", "text_encoders"
    if input_name.startswith("clip_name"):
        return "clip", "text_encoders"
    if input_name == "model_name" and "upscale" in lname:
        return "upscale_model", "upscale_models"
    return None


def _guess_widget_field(type_name: str, config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """`(field_type, extra_config)` for a widget input whose model-file
    inference didn't apply - see `node_catalog.FIELD_TYPES`."""
    if type_name == "BOOLEAN":
        return "checkbox", {}
    if type_name == "STRING":
        return "textbox", {}
    if type_name == "COMBO":
        return "select", {"options": list(config.get("options") or [])}
    if type_name in ("INT", "FLOAT"):
        bounds = {k: config[k] for k in ("min", "max", "step") if k in config}
        return ("slider", bounds) if bounds else ("number", {})
    return "textbox", {}


# --- object_info extraction -------------------------------------------------


def extract_from_object_info(class_type: str, class_info: Dict[str, Any]) -> Optional[ExtractedNode]:
    """`class_info` is one `/object_info` entry, same shape
    `suggest._find_input_spec` reads. `None` if it carries no usable
    `input.required`/`input.optional`."""
    input_defs = class_info.get("input") if isinstance(class_info, dict) else None
    if not isinstance(input_defs, dict):
        return None

    inputs: List[ExtractedInput] = []
    for section in ("required", "optional"):
        section_defs = input_defs.get(section)
        if not isinstance(section_defs, dict):
            continue
        for name, spec in section_defs.items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            type_spec = spec[0]
            config = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if isinstance(type_spec, str):
                type_name = type_spec
                cfg = dict(config)
            elif isinstance(type_spec, (list, tuple)):
                type_name = "COMBO"
                cfg = {**config, "options": list(type_spec)}
            else:
                continue
            inputs.append(ExtractedInput(name=name, type_name=type_name, connected=_classify(type_name), config=cfg))

    return ExtractedNode(
        class_type=class_type,
        style="object_info",
        inputs=inputs,
        category_guess=guess_category(class_type, inputs),
    )


# --- ast-based source extraction --------------------------------------------


def _find_class_def(tree: ast.AST, class_name: str) -> Optional[ast.ClassDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    # `define_schema`-registered classes need not share the node id with
    # their Python class name - find whichever class's body declares
    # `node_id="<class_name>"`.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.keyword)
                and sub.arg == "node_id"
                and isinstance(sub.value, ast.Constant)
                and sub.value.value == class_name
            ):
                return node
    return None


def _find_method(class_def: ast.ClassDef, name: str) -> Optional[ast.FunctionDef]:
    for item in class_def.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            return item
    return None


def _literal_or_none(node: Optional[ast.AST]) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _parse_legacy_input_tuple(value_node: ast.AST) -> Tuple[Any, Dict[str, Any], Optional[str]]:
    elts = value_node.elts if isinstance(value_node, (ast.Tuple, ast.List)) else [value_node]
    type_elt = elts[0] if elts else None
    config_elt = elts[1] if len(elts) > 1 else None

    source_expr: Optional[str] = None
    if isinstance(type_elt, ast.Constant) and isinstance(type_elt.value, str):
        type_repr: Any = type_elt.value
    else:
        type_repr = _literal_or_none(type_elt)
        if type_repr is None and type_elt is not None:
            source_expr = ast.unparse(type_elt)

    config = _literal_or_none(config_elt)
    if not isinstance(config, dict):
        config = {}
    return type_repr, config, source_expr


def _build_extracted_input(name: str, type_repr: Any, config: Dict[str, Any], source_expr: Optional[str]) -> ExtractedInput:
    if isinstance(type_repr, str):
        type_name = type_repr
        cfg = dict(config)
    elif isinstance(type_repr, (list, tuple)):
        type_name = "COMBO"
        cfg = {**config, "options": list(type_repr)}
    else:
        type_name = "COMBO"
        cfg = dict(config)
    return ExtractedInput(name=name, type_name=type_name, connected=_classify(type_name), config=cfg, source_expr=source_expr)


def _extract_legacy(method: ast.FunctionDef, class_name: str) -> Optional[ExtractedNode]:
    return_dict = next(
        (n.value for n in ast.walk(method) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)),
        None,
    )
    if return_dict is None:
        return None

    inputs: List[ExtractedInput] = []
    for section_key, section_value in zip(return_dict.keys, return_dict.values):
        if not (isinstance(section_key, ast.Constant) and section_key.value in ("required", "optional")):
            continue
        if not isinstance(section_value, ast.Dict):
            continue
        for name_node, value_node in zip(section_value.keys, section_value.values):
            if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
                continue
            type_repr, config, source_expr = _parse_legacy_input_tuple(value_node)
            inputs.append(_build_extracted_input(name_node.value, type_repr, config, source_expr))

    return ExtractedNode(
        class_type=class_name,
        style="legacy",
        inputs=inputs,
        category_guess=guess_category(class_name, inputs),
    )


def _is_io_schema_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "Schema"


def _parse_io_input_call(elt: ast.AST) -> Optional[ExtractedInput]:
    if not isinstance(elt, ast.Call) or not isinstance(elt.func, ast.Attribute) or elt.func.attr != "Input":
        return None
    owner = elt.func.value
    if not isinstance(owner, ast.Attribute):
        return None
    type_name = _camel_to_upper_snake(owner.attr)

    if not elt.args or not isinstance(elt.args[0], ast.Constant) or not isinstance(elt.args[0].value, str):
        return None
    name = elt.args[0].value

    config: Dict[str, Any] = {}
    source_expr: Optional[str] = None
    for kw in elt.keywords:
        if kw.arg is None:
            continue
        value = _literal_or_none(kw.value)
        if value is not None or isinstance(kw.value, ast.Constant):
            config[kw.arg] = value
        elif kw.arg == "options":
            source_expr = ast.unparse(kw.value)

    return ExtractedInput(name=name, type_name=type_name, connected=_classify(type_name), config=config, source_expr=source_expr)


def _extract_schema(method: ast.FunctionDef, class_name: str) -> Optional[ExtractedNode]:
    schema_call = next(
        (
            n.value
            for n in ast.walk(method)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Call) and _is_io_schema_call(n.value)
        ),
        None,
    )
    if schema_call is None:
        return None

    inputs_list = next(
        (kw.value for kw in schema_call.keywords if kw.arg == "inputs" and isinstance(kw.value, ast.List)),
        None,
    )
    if inputs_list is None:
        return None

    inputs = [i for i in (_parse_io_input_call(elt) for elt in inputs_list.elts) if i is not None]
    return ExtractedNode(
        class_type=class_name,
        style="define_schema",
        inputs=inputs,
        category_guess=guess_category(class_name, inputs),
    )


def extract_from_source(source: str, class_name: str) -> Optional[ExtractedNode]:
    """`ast`-parse `source` (never executed) for `class_name`'s
    `INPUT_TYPES` (legacy) or `define_schema` (current) classmethod. `None`
    if the class, or a recognizable input declaration on it, isn't found."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    class_def = _find_class_def(tree, class_name)
    if class_def is None:
        return None

    legacy_method = _find_method(class_def, "INPUT_TYPES")
    if legacy_method is not None:
        result = _extract_legacy(legacy_method, class_name)
        if result is not None:
            result.line_no = class_def.lineno
            return result

    schema_method = _find_method(class_def, "define_schema")
    if schema_method is not None:
        result = _extract_schema(schema_method, class_name)
        if result is not None:
            result.line_no = class_def.lineno
            return result

    return None


def find_class_file(class_name: str, comfyui_src: Path) -> Optional[Path]:
    """The first of `nodes.py`, then `comfy_extras/*.py` (sorted) whose text
    matches `class <class_name>(` or `node_id="<class_name>"` - a plain text
    scan, so this never has to import ComfyUI to find the right file."""
    class_re = re.compile(r"class\s+" + re.escape(class_name) + r"\s*[:(]")
    node_id_re = re.compile(r"node_id\s*=\s*[\"']" + re.escape(class_name) + r"[\"']")

    candidates: List[Path] = []
    nodes_py = comfyui_src / "nodes.py"
    if nodes_py.is_file():
        candidates.append(nodes_py)
    extras_dir = comfyui_src / "comfy_extras"
    if extras_dir.is_dir():
        candidates.extend(sorted(extras_dir.glob("*.py")))

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if class_re.search(text) or node_id_re.search(text):
            return path
    return None


def extract_from_comfyui_src(class_name: str, comfyui_src: Path) -> Optional[ExtractedNode]:
    path = find_class_file(class_name, comfyui_src)
    if path is None:
        return None
    source = path.read_text(encoding="utf-8", errors="replace")
    result = extract_from_source(source, class_name)
    if result is not None:
        result.file_path = path
    return result


def scaffold_for_class(
    class_name: str,
    *,
    object_info: Optional[Dict[str, Any]] = None,
    comfyui_src: Optional[Path] = None,
) -> Optional[ExtractedNode]:
    """Dispatch to `extract_from_object_info` when `object_info` is given,
    else `extract_from_comfyui_src` against `comfyui_src` - the precedence
    order the `scaffold` CLI command documents."""
    if object_info is not None:
        class_info = object_info.get(class_name)
        return extract_from_object_info(class_name, class_info) if isinstance(class_info, dict) else None
    if comfyui_src is not None:
        return extract_from_comfyui_src(class_name, comfyui_src)
    return None


# --- rendering ---------------------------------------------------------------


_BAREWORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_scalar(v) for v in value) + "]"
    # The catalog's own flow-map values are bare words (`model_type: lora`,
    # never `model_type: "lora"`) - only quote a string that needs it.
    if isinstance(value, str) and _BAREWORD_RE.match(value):
        return value
    return json.dumps(value)


def _format_flow_map(config: Dict[str, Any]) -> str:
    return "{" + ", ".join(f"{k}: {_format_scalar(v)}" for k, v in config.items()) + "}"


def _model_type_for_folder(folder: str) -> Optional[str]:
    """The catalog's own folder -> `model` field `model_type` config, read
    from `backend.comfyui_backend.FOLDER_TO_MODEL_TYPE` at call time (not
    duplicated here) so this module never drifts from that mapping's own
    additions. Imported lazily - `comfyui_backend` needs `aiohttp`, a
    dependency this otherwise-pure-stdlib module has no other reason to
    carry."""
    from backend.comfyui_backend import FOLDER_TO_MODEL_TYPE

    return FOLDER_TO_MODEL_TYPE.get(folder)


def _render_model_file_input(inp: ExtractedInput, role: str, folder: str) -> List[str]:
    label = _humanize(role)
    lines = [f"    {inp.name}:", f"      role: {role}", "      field: model", f"      name: {role}", f"      label: {label}"]
    model_type = _model_type_for_folder(folder)
    config = {"model_type": model_type, "allow_info_modal": True} if model_type else {"allow_info_modal": True}
    lines.append(f"      config: {_format_flow_map(config)}")
    if model_type is None:
        lines.append(f"      # TODO: no model_type found for folder '{folder}' in FOLDER_TO_MODEL_TYPE")
    lines += ["      transform: strip_model_prefix", f"      folder: {folder}", "      section: Models"]
    return lines


def _render_lora_slot_input(inp: ExtractedInput) -> List[str]:
    """Matches the shipped `LoraLoader`/`LoraLoaderModelOnly` entries
    exactly - key order included."""
    return [
        f"    {inp.name}:",
        "      role: lora_slot",
        "      field: lora_picker",
        "      name: loras",
        "      label: LoRAs",
        f"      config: {_format_flow_map({'model_type': 'lora', 'max_items': 6})}",
        "      history: list",
        "      folder: loras",
    ]


def _render_lora_strength_input(inp: ExtractedInput, role: str) -> List[str]:
    bounds = {k: inp.config[k] for k in ("min", "max", "step") if k in inp.config}
    lines = [f"    {inp.name}:", f"      role: {role}", "      field: slider", f"      name: {inp.name}", f"      label: {_humanize(inp.name)}"]
    if bounds:
        lines.append(f"      config: {_format_flow_map(bounds)}")
    return lines


def _render_generic_widget_input(inp: ExtractedInput) -> List[str]:
    field_type, extra_config = _guess_widget_field(inp.type_name, inp.config)
    lines = [
        f"    {inp.name}:",
        "      role: option  # TODO: role?",
        f"      field: {field_type}",
        f"      name: {inp.name}",
        f"      label: {_humanize(inp.name)}",
    ]
    if extra_config:
        lines.append(f"      config: {_format_flow_map(extra_config)}")
    if inp.type_name in ("INT", "FLOAT"):
        lines.append("      section: Sampling")
    if inp.source_expr:
        lines.append(f"      # source: {inp.source_expr}")
    return lines


def render_scaffold(node: ExtractedNode, category_override: Optional[str] = None) -> str:
    """Paste-ready `node_catalog.yml` entry text for `node` - `# TODO`
    markers wherever a human must confirm a guess. A `lora_name` combo is
    certain enough (see `_is_lora_node`) that its category never gets one."""
    is_lora_node = _is_lora_node(node.inputs)
    category = category_override or node.category_guess
    cat_line = f"  category: {category}"
    if not category_override and not is_lora_node:
        cat_line += "  # TODO: confirm category"
    lines = [f"{node.class_type}:", cat_line]

    link_lines: List[str] = []
    input_lines: List[str] = []
    for inp in node.inputs:
        if inp.connected:
            link_kind = _LINK_NAME_MAP.get(inp.name)
            if link_kind:
                link_lines.append(f"    {inp.name}: {link_kind}")
            else:
                comment = f"# connected: {inp.type_name}"
                if inp.source_expr:
                    comment += f" ({inp.source_expr})"
                link_lines.append(f"    # {inp.name}: ???  {comment}")
            continue

        if is_lora_node and inp.type_name == "COMBO" and inp.name == "lora_name":
            input_lines.extend(_render_lora_slot_input(inp))
            continue
        if is_lora_node and inp.name in ("strength_model", "strength_clip"):
            role = "lora_strength_model" if inp.name == "strength_model" else "lora_strength_clip"
            input_lines.extend(_render_lora_strength_input(inp, role))
            continue

        model_file = _infer_model_file(node.class_type, inp.name) if inp.type_name == "COMBO" else None
        if model_file:
            input_lines.extend(_render_model_file_input(inp, *model_file))
        else:
            input_lines.extend(_render_generic_widget_input(inp))

    if link_lines:
        lines.append("  links:")
        lines.extend(link_lines)
    if input_lines:
        lines.append("  inputs:")
        lines.extend(input_lines)
    return "\n".join(lines) + "\n"
