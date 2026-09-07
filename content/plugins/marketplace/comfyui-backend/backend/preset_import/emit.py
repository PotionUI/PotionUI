"""Writing a lint-clean preset directory from a parsed workflow plus the
admin's `form`/`history` (see `schema.py` for the wire contract, `defaults.py`
for the "obvious fields" starting point `/presets/import/analyze` offers).

Wiring is split into two tiers:

- Foundational (always wired, never an `Item` the admin arranges): the
  sampler's seed, the positive/negative prompt text nodes, and the latent
  image's batch size. A comfyui image preset that doesn't wire these isn't
  useful, so they are not something an admin opts out of - the wizard shows
  them locked, and this module wires them from the workflow's own structure
  (`suggest.suggest_fields`), independent of whatever `form` was submitted.
- Form-driven: every `FieldItem` in `form` becomes a real field (in the tab
  it was placed under) and, via its `mappings`, a `field_mappings` entry per
  mapped node input - each with its own `transform` (see
  `_field_mapping_entry`). A `lora_picker` field is the one exception: its
  wiring is a node-graph rewrite keyed off the workflow's own detected LoRA
  chain (`_lora_node_manipulations`), not a single mapped value.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from .node_catalog import get_catalog, resolve_model_folder
from .parser import Workflow
from .schema import (
    FieldItem,
    HistoryEntry,
    ImportForm,
    Item,
    PresetEmitError,
    _typed_default,
    all_field_items,
    validate_against_workflow,
)
from .suggest import AnalyzeResult, InputCandidate, _infer_value_type, classification_fingerprint, suggest_fields

# `description.md`'s opening line for every preset this module emits - the
# provenance marker `GET /api/plugins/comfyui-backend/presets/imported`
# (backend/api.py) keys its listing on, since nothing else records that a
# preset under content/presets/local came from this importer rather than
# being hand-authored there.
IMPORT_PROVENANCE_PREFIX = "Imported from a ComfyUI workflow"

# `import.json`'s schema version - bump if its shape changes in a way an
# older sidecar wouldn't have. Read by backend/api.py's reload/source
# endpoints; a preset imported before this sidecar existed simply has none
# (`GET .../presets/imported`'s `has_sidecar: false`), which is a supported,
# non-error state - see api.py's reload fallback.
IMPORTER_VERSION = 3

# The sidecar file name written alongside preset.yml/description.md - not
# read by PresetLinter/the preset engine, purely this importer's own record
# of what it was told to build, so reload/modify can reproduce it exactly
# instead of re-analyzing with only the "obvious" defaults.
IMPORT_SIDECAR_FILENAME = "import.json"

# `strip_model_prefix`'s per-model-type root - a field's own `config.model_type`
# (set on a "model" field by the wizard/defaults.py) selects which of these
# applies; a field with no recognizable model_type falls back to stripping
# every one of them in a chain (see `_ALL_MODEL_PREFIXES`).
MODEL_TYPE_STRIP_PREFIXES = {
    "checkpoint": ("models/checkpoints/",),
    "diffusion_model": ("models/diffusion_models/", "models/checkpoints/"),
    "clip": ("models/clip/",),
    # The catalog's CLIP/text-encoder loaders (CLIPLoader, DualCLIPLoader, ...)
    # all set `config.model_type: "text_encoder"`, never "clip" - the depot
    # symlinks `models/text_encoders -> .../models/clip`, so a picker value
    # can carry either spelling and both must be stripped.
    "text_encoder": ("models/text_encoders/", "models/clip/"),
    "vae": ("models/vae/",),
    "lora": ("models/loras/",),
}
_ALL_MODEL_PREFIXES: Tuple[str, ...] = tuple(
    dict.fromkeys(prefix for prefixes in MODEL_TYPE_STRIP_PREFIXES.values() for prefix in prefixes)
)

# ComfyUI built-ins the `comfyui_node` requirement never needs to name - a
# node class outside this set is assumed to come from a custom node pack
# and gets a `requirements:` entry so a missing install surfaces before
# generation instead of as a pipeline error. Used only as a fallback when no
# `object_info` is available at import time (a plain Export (API) import
# never fetches it - see `_is_core_node_class`); when it is, the live
# server's own `python_module` per class is authoritative and this set is
# never consulted. Extend this set as new core node classes show up in
# workflows imported without a reachable backend.
CORE_NODE_CLASS_TYPES = frozenset({
    "KSampler", "KSamplerAdvanced", "CheckpointLoaderSimple", "UNETLoader",
    "CLIPLoader", "DualCLIPLoader", "VAELoader", "CLIPTextEncode",
    "EmptyLatentImage", "EmptySD3LatentImage", "VAEDecode", "VAEEncode",
    "SaveImage", "PreviewImage", "LoadImage", "LoraLoader", "LoraLoaderModelOnly",
    "ModelSamplingFlux", "ModelSamplingAuraFlow", "FluxGuidance", "ConditioningZeroOut",
    "CFGGuider", "BasicGuider", "BasicScheduler", "KSamplerSelect", "SamplerCustom",
    "SamplerCustomAdvanced", "RandomNoise", "VAEDecodeTiled", "ModelSamplingSD3",
    "CLIPTextEncodeFlux", "EmptyHunyuanLatentVideo", "EmptyLTXVLatentVideo",
    "LTXVConditioning", "LTXVScheduler", "LTXVImgToVideo", "LoadImageMask",
    "ImageScale", "ImageScaleBy", "UpscaleModelLoader", "ImageUpscaleWithModel",
    "ControlNetLoader", "ControlNetApplyAdvanced", "ConditioningCombine",
    "ConditioningConcat", "ConditioningSetTimestepRange", "LatentUpscaleBy",
    "RepeatLatentBatch", "SaveAnimatedWEBP", "SaveVideo", "CreateVideo",
})

def _is_core_node_class(class_type: str, object_info: Optional[Dict[str, Any]]) -> bool:
    """Whether `class_type` ships with ComfyUI itself. Prefers the live
    server's own `/object_info` when available at import time - its
    `python_module` is authoritative: "nodes" (the single-file core
    registry) or a "comfy_extras.*" submodule means core, "custom_nodes.*"
    or a class `/object_info` doesn't recognize at all means custom. Catches
    core additions like `CFGGuider` the hand-kept allowlist hasn't caught up
    with yet, and correctly flags an unrecognized class as custom rather
    than guessing. Falls back to `CORE_NODE_CLASS_TYPES` when no
    `object_info` is available (a plain Export (API) import has no reachable
    backend to ask - see `api.py`'s `_parse_incoming_workflow`)."""
    if object_info is not None:
        class_info = object_info.get(class_type)
        if class_info is None:
            return False
        python_module = class_info.get("python_module") or ""
        return python_module == "nodes" or python_module.startswith("comfy_extras")
    return class_type in CORE_NODE_CLASS_TYPES

# One path segment: no "/", "\", "..", and no leading dot (so it can't be
# hidden or resolve as a relative-parent trick on any OS).
_SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


def _tab_slug(label: str) -> str:
    """A safe tabs/<slug>.yml filename stem for a tab id/label - never used
    verbatim, since a `form.tabs[].id` comes straight from the request body:
    non-alphanumeric runs collapse to one "_"."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
    return slug or "tab"


def _validate_path_segment(value: str, field_label: str) -> None:
    """Reject anything that isn't a single, safe path segment - guards
    `model_family`/`variant` (used verbatim in a filesystem path under
    content/presets/local) against path traversal via "..", "/", "\\", an
    absolute path, or a leading dot."""
    if not _SAFE_PATH_SEGMENT_RE.match(value) or ".." in value:
        raise PresetEmitError(
            f"{field_label} must be a single path segment matching "
            f"{_SAFE_PATH_SEGMENT_RE.pattern!r} with no '..' - got {value!r}."
        )


@dataclass
class EmittedPreset:
    preset_id: str
    preset_dir: Path
    mode: str
    paths: List[str]


def _yaml_value(value: Any) -> Any:
    """Coerce a JSON-decoded literal to the type pydantic's typed-default
    check expects (bool must not silently pass as int, etc. - json already
    gives us the right Python type, this only guards against numpy-ish
    surprises from a hand-edited fixture)."""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _cast_name(value_type: str) -> str:
    return {"bool": "bool", "int": "int", "float": "float", "str": "str"}.get(value_type, "str")


class _DoubleQuoted(str):
    """A YAML scalar forced to double-quote style on dump.

    Hand-authored presets write `children:` Jinja paths as `"{{ paths.preset }}/..."`
    (double-quoted); `tests/features/presets/test_references_tab_layout.py::test_no_tab_body_file_is_orphaned`
    finds which tab files a form.yml composes by regexing for that exact
    quoting, so plain `yaml.safe_dump` picking single quotes for these
    strings (PyYAML's default style choice for a scalar starting with `{`)
    makes every emitted tab file look orphaned.
    """


def _double_quoted_representer(dumper: yaml.Dumper, data: str):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.SafeDumper.add_representer(_DoubleQuoted, _double_quoted_representer)


def _tab_children_path(mode: str, *parts: str) -> _DoubleQuoted:
    return _DoubleQuoted("/".join(["{{ paths.preset }}/modes/" + mode, "tabs", *parts]))


def _dump_yaml(data: Dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _dump_json(data: Any) -> str:
    return json.dumps(data, indent=2) + "\n"


def _write_file(path: Path, content: str) -> None:
    """One staged file write - a module-level seam so a failure-injection
    test can monkeypatch this exact call instead of `emit_preset`'s own
    write loop."""
    path.write_text(content, encoding="utf-8")


def _replace_dir(src: Path, dst: Path) -> None:
    """Rename `src` to `dst`. Every caller in this module only ever targets
    a `dst` that was just removed or never existed, so this stays the single
    directory-rename syscall `os.replace` performs - atomic on the
    filesystems content/presets/local is expected to live on, but that is a
    filesystem property this function relies on, not one it can guarantee;
    see `_publish_preset_dir` for what a failure here does and does not
    protect against."""
    os.replace(src, dst)


def _publish_preset_dir(target_dir: Path, staging_dir: Path, *, temp_root: Path) -> None:
    """Swap a fully-written `staging_dir` into `target_dir`'s place.

    Sequence: rename `target_dir` aside to a backup under `temp_root` (only
    if it currently exists), rename `staging_dir` into `target_dir`, then
    drop the backup. `temp_root` is outside every scanned preset root, so a
    backup that outlives the swap is never discoverable as a preset.
    On any failure in either rename, `target_dir` is put back exactly as it
    was - the backup (if one was made) is renamed back into place - and
    `staging_dir` is discarded; a `PresetEmitError` is raised naming the
    original failure. If restoring the backup itself also fails, the backup
    directory is deliberately NOT deleted - it holds the only intact copy of
    the previous preset - and the error names its path so it can be moved
    back by hand.

    This is a two-rename swap, not a single power-loss-atomic transaction:
    on any filesystem, a crash between the two renames could leave
    `target_dir` briefly absent with only the backup present, needing manual
    reconciliation on restart. What this guards against is an ordinary
    Python exception during staging, renaming, or cleanup - a full disk, a
    permission error, a serialization bug - not a mid-swap power loss.
    """
    backup_dir: Optional[Path] = None
    try:
        if target_dir.exists():
            backup_dir = temp_root / f".import-backup-{uuid.uuid4().hex}"
            _replace_dir(target_dir, backup_dir)
        _replace_dir(staging_dir, target_dir)
    except Exception as publish_error:
        if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            try:
                _replace_dir(backup_dir, target_dir)
            except Exception as restore_error:
                raise PresetEmitError(
                    "Failed to publish the updated preset, and failed to restore the previous "
                    f"version from its backup - the previous complete preset directory is preserved "
                    f"at {backup_dir}; move it back to {target_dir} by hand. "
                    f"Publish error: {publish_error}. Restore error: {restore_error}"
                ) from restore_error
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise PresetEmitError(f"Failed to publish the updated preset: {publish_error}") from publish_error
    else:
        if backup_dir is not None:
            shutil.rmtree(backup_dir, ignore_errors=True)


def _pipe(
    name: str,
    *,
    id: Optional[str] = None,
    enabled: Any = True,
    input: Optional[List[List[Any]]] = None,
    configuration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    d: Dict[str, Any] = {"name": name}
    if id:
        d["id"] = id
    d["enabled"] = enabled
    if input:
        d["input"] = input
    if configuration is not None:
        d["configuration"] = configuration
    return d


def _infer_requirements(
    workflow: Workflow,
    object_info: Optional[Dict[str, Any]] = None,
    *,
    replaced_lora_node_ids: Optional[Set[str]] = None,
    candidates: Optional[List[InputCandidate]] = None,
) -> List[Dict[str, Any]]:
    """`requirements:` entries for this workflow, independent of `form`: one
    `comfyui_node` per non-core node class (see `_is_core_node_class`), and
    one `comfyui_model` per checkpoint/UNET/CLIP/VAE/LoRA file it
    references. A loader the admin didn't turn into a field still needs its
    file present to run, so this reads the whole graph rather than
    `form`'s field list.

    `replaced_lora_node_ids` marks the LoRA nodes a `lora_picker` field's
    graph rewrite excludes from the baked workflow (see
    `emit_preset`/`schema.LoraChainSelection`) - their LoRA is a seeded
    default the admin can remove at will, not something the preset can't run
    without, so its `comfyui_model` entry is `optional: true`. A kept
    (non-replaced) chain node, or any LoRA node when no selection was made
    at all, still gets a hard requirement exactly as before.

    `candidates` (`suggest.suggest_fields`'s own analysis, when given) adds
    one more requirement per candidate carrying a `suggested_folder` - a
    model-file field `_enrich_with_object_info` built for a class/input the
    catalog above never declared a `folder` for in the first place, so the
    catalog-driven pass can't find it on its own."""
    requirements: List[Dict[str, Any]] = []

    node_classes = sorted({n.class_type for n in workflow.nodes.values()})
    for class_type in node_classes:
        if not _is_core_node_class(class_type, object_info):
            requirements.append({"type": "comfyui_node", "class_type": class_type})

    seen: set = set()

    def _add_model(folder: str, name: Any, *, optional: bool = False) -> None:
        if not isinstance(name, str) or not name:
            return
        # A catalog `InputSpec.folder`/candidate `suggested_folder` names the
        # ordinary folder regardless of which specific file got selected -
        # redirect to the ComfyUI-GGUF alias when this particular file is a
        # `.gguf` one, so the emitted requirement's `folder` is the listing
        # key that actually carries it (see node_catalog.resolve_model_folder).
        resolved_folder = resolve_model_folder(folder, name)
        if (resolved_folder, name) in seen:
            return
        seen.add((resolved_folder, name))
        entry: Dict[str, Any] = {"type": "comfyui_model", "folder": resolved_folder, "name": name}
        if optional:
            entry["optional"] = True
        requirements.append(entry)

    catalog = get_catalog()
    for node in workflow.nodes.values():
        entry = catalog.get(node.class_type)
        if entry is None:
            continue
        literals = node.literals()
        for input_name, spec in entry.inputs.items():
            if not spec.folder or input_name not in literals:
                continue
            is_replaced = (
                spec.role == "lora_slot"
                and replaced_lora_node_ids is not None
                and node.id in replaced_lora_node_ids
            )
            _add_model(spec.folder, literals[input_name], optional=is_replaced)

    for candidate in candidates or []:
        if candidate.suggested_folder:
            _add_model(candidate.suggested_folder, candidate.current_value)

    return requirements


# ----------------------------------------------------------------------
# form -> YAML
# ----------------------------------------------------------------------


def _item_to_field_yaml(item: Item) -> Dict[str, Any]:
    """Dispatches on `item.kind` rather than `isinstance` - see
    `schema.iter_field_items`'s docstring for why."""
    if item.kind == "field":
        field: Dict[str, Any] = {"name": item.field_name, "type": item.field_type, "label": item.label}
        if item.required:
            field["required"] = True
        default = _typed_default(item.field_type, item.field_name, item.default)
        if default is not None:
            field["default"] = _yaml_value(default)
        if item.config:
            field["configuration"] = dict(item.config)
        return field
    if item.kind == "row":
        return {
            "type": "row",
            "configuration": {"columns": item.columns},
            "children": [_item_to_field_yaml(child) for child in item.items],
        }
    if item.kind == "group":
        return {"type": "group", "label": item.title, "children": [_item_to_field_yaml(child) for child in item.items]}
    if item.kind == "section":
        return {
            "type": "section",
            "label": item.title,
            "configuration": {"collapsed": item.collapsed},
            "children": [_item_to_field_yaml(child) for child in item.items],
        }
    if item.kind == "header":
        return {"type": "header", "label": item.text}
    raise PresetEmitError(f"Unsupported form item: {item!r}")  # pragma: no cover - pydantic already discriminates


_FOUNDATIONAL_GENERATION_FIELDS: List[Dict[str, Any]] = [
    {"name": "seed", "type": "seed", "label": "Seed", "default": -1},
    {
        "name": "quantity",
        "type": "slider",
        "label": "Batch Size",
        "configuration": {"min": 1, "max": 8, "step": 1},
        "default": 1,
    },
]


def _build_form_files(form: ImportForm, mode: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """`(tab_files, tabs_yaml)` - `tab_files["<slug>.yml"]` is that tab's
    `{"fields": [...]}` body, `tabs_yaml` is the `tabs:` container's own
    `children` list pointing at each. The seed/quantity foundational fields
    (see the module docstring) are always prepended to the first tab -
    every emitted preset needs them to be usable, regardless of what the
    admin's own tabs contain."""
    tab_files: Dict[str, Dict[str, Any]] = {}
    tabs_yaml: List[Dict[str, Any]] = []
    used_slugs: Set[str] = set()

    for tab_index, tab in enumerate(form.tabs):
        slug = _tab_slug(tab.id)
        base_slug = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}_{n}"
            n += 1
        used_slugs.add(slug)
        filename = f"{slug}.yml"

        fields = [_item_to_field_yaml(item) for item in tab.items]
        if tab_index == 0:
            fields = list(_FOUNDATIONAL_GENERATION_FIELDS) + fields

        tab_files[filename] = {"fields": fields}
        tab_config: Dict[str, Any] = {"icon_display": tab.icon_display}
        if tab.icon:
            tab_config["icon"] = tab.icon
        tabs_yaml.append(
            {
                "type": "tab",
                "label": tab.label,
                "configuration": tab_config,
                "children": _tab_children_path(mode, filename),
            }
        )

    return tab_files, tabs_yaml


# ----------------------------------------------------------------------
# form -> pipeline (field_mappings / param_emitter)
# ----------------------------------------------------------------------


def _field_mapping_entry(field: FieldItem, mapping) -> List[Any]:
    """One `field_mappings` triple (`[value_template, "<node>.inputs.<input>", cast]`)
    for one of `field`'s mappings, per its `transform`."""
    if mapping.transform == "seed":
        return ["@seed", f"{mapping.node_id}.inputs.{mapping.input_name}", "int"]

    if mapping.transform in ("split_wh_width", "split_wh_height"):
        default = field.default if isinstance(field.default, str) else ""
        index = "0" if mapping.transform == "split_wh_width" else "1"
        value = "{{ (form." + field.field_name + " | default(" + json.dumps(default) + ")).split('x')[" + index + "] }}"
        return [value, f"{mapping.node_id}.inputs.{mapping.input_name}", "int"]

    if mapping.transform == "strip_model_prefix":
        model_type = (field.config or {}).get("model_type")
        prefixes = MODEL_TYPE_STRIP_PREFIXES.get(model_type, _ALL_MODEL_PREFIXES)
        value = "{{ (form." + field.field_name + " | default('') or '')"
        for prefix in prefixes:
            value += " | replace('" + prefix + "', '')"
        value += " }}"
        return [value, f"{mapping.node_id}.inputs.{mapping.input_name}", "str"]

    if field.field_type == "image":
        # An image field never carries a literal `default` (see
        # `defaults._image_item`) - a required image (the workflow's primary
        # LoadImage) has no fallback at all, exactly like a hand-authored
        # preset's own `source_image` mapping, so a missing upload surfaces
        # as an error rather than submitting whatever the workflow's own
        # placeholder filename was. An optional one falls back to "" so its
        # `remove_node` manipulation (see the wizard's ref-image handling)
        # can detect "not provided" and drop the node instead.
        if field.required:
            value = "{{ form." + field.field_name + " }}"
            cast = "image_required"
        else:
            value = "{{ form." + field.field_name + " | default('') }}"
            cast = "image"
        return [value, f"{mapping.node_id}.inputs.{mapping.input_name}", cast]

    # "none" - a plain literal value, cast from the field's own shape.
    cast = _cast_name(_infer_value_type(field.default))
    value = "{{ form." + field.field_name + " | default(" + json.dumps(_yaml_value(field.default)) + ") }}"
    return [value, f"{mapping.node_id}.inputs.{mapping.input_name}", cast]


def _history_param_entry(entry: HistoryEntry) -> List[Any]:
    """One `param_emitter.parameters` pair for one `history` entry, per its
    `format` - see the contract's value-template table in `schema.py`'s
    module docstring / `docs/presets.md`."""
    field = entry.field
    if entry.format == "jinja":
        value = entry.template
    elif entry.format == "model_name":
        value = "{{ (form." + field + " | default('')).split('/')[-1] }}"
    elif entry.format == "list":
        value = (
            "{{ form." + field + " | default([]) | active_loras | length }}x "
            "{{ form." + field + " | default([]) | active_loras | map(attribute='model', default='') "
            "| map('replace', 'models/loras/', '') | select('string') | join(', ') }}"
        )
    else:  # as_is, number, wxh - the field's own value, verbatim
        value = "{{ form." + field + " }}"
    return [field, value]


# output index -> the input name that same output kind would come in on -
# `LoraLoader`'s RETURN_TYPES is `("MODEL", "CLIP")`, so a MODEL connection
# is always `[node_id, 0]` and a CLIP one `[node_id, 1]`. Used only by
# `_resolve_bypass_source` to walk backward through a removed node.
_OUTPUT_INDEX_TO_INPUT_NAME = {0: "model", 1: "clip"}


def _resolve_bypass_source(
    workflow: Workflow, connection: Tuple[str, int], replaced_node_ids: Set[str]
) -> Tuple[str, int]:
    """Follow `connection` back through any run of replaced nodes (see
    `schema.LoraChainSelection`) to the nearest real (non-replaced)
    source - one hop per replaced node in the way, so several replaced nodes
    back to back (or a kept/pass-through node sandwiched between two
    replaced ones, whose OWN wiring is fixed up the same way by
    `_bypass_replaced_lora_nodes`) all collapse to a single real connection.
    Returns `connection` itself unchanged once it reaches a non-replaced
    node, an unresolvable output index, or a dangling node id."""
    node_id, output_index = connection
    visited: Set[str] = set()
    while node_id in replaced_node_ids and node_id not in visited:
        visited.add(node_id)
        node = workflow.node(node_id)
        input_name = _OUTPUT_INDEX_TO_INPUT_NAME.get(output_index)
        if node is None or input_name is None:
            break
        next_connection = node.connection_source(input_name)
        if next_connection is None:
            break
        node_id, output_index = next_connection
    return node_id, output_index


def _bypass_replaced_lora_nodes(
    workflow_out: Dict[str, Any], workflow: Workflow, replaced_node_ids: Set[str]
) -> None:
    """Rewire every connection left in `workflow_out` (every node NOT itself
    replaced - see `emit_preset`) that still targets a removed node's
    output, to the nearest real source instead (`_resolve_bypass_source`).
    Runs over the whole graph, not just the chain's own source/target
    boundary: a kept LoRA or an unrelated pass-through node (a
    `ModelSamplingAuraFlow`/`CFGNorm` patcher) can sit anywhere relative to
    the replaced nodes, including between two of them, and would otherwise
    point at a node id that no longer exists in `workflow_out`."""
    for node_data in workflow_out.values():
        inputs = node_data.get("inputs", {})
        for input_name, value in list(inputs.items()):
            if not (isinstance(value, list) and len(value) == 2):
                continue
            source_id, source_index = value
            if not isinstance(source_id, str) or source_id not in replaced_node_ids:
                continue
            inputs[input_name] = list(
                _resolve_bypass_source(workflow, (source_id, source_index), replaced_node_ids)
            )


def _lora_chain_clip_boundary(
    chain, replaced_node_ids: Set[str]
) -> Tuple[Optional[str], Optional[List[Tuple[str, str]]]]:
    """`(clip_source_node_id, clip_consumers)` for the replaced span, or
    `(None, None)` if no replaced node carries a CLIP path (see
    `suggest.LoraChainNode`) - i.e. every replaced node is a
    `LoraLoaderModelOnly`, or nothing downstream actually reads its CLIP
    output. Only ever non-`None` for a chain with a real `LoraLoader` among
    the replaced nodes."""
    ordered = [n for n in chain.nodes if n.node_id in replaced_node_ids]
    clip_entry = next((n for n in ordered if n.clip_source is not None), None)
    clip_exit = next((n for n in reversed(ordered) if n.clip_consumers), None)
    if clip_entry is None or clip_exit is None:
        return None, None
    return clip_entry.clip_source[0], clip_exit.clip_consumers


def _lora_loop_boundary(
    chain, replaced_node_ids: Set[str]
) -> Tuple[str, int, str, str, Optional[Tuple[str, List[Tuple[str, str]]]]]:
    """The `(source_node_id, source_output_index, target_node_id,
    target_input, clip_boundary)` a `lora_picker` field's `@loop` splices
    into, for the replaced span of a detected LoRA chain: the FIRST
    replaced node's own source, and whoever consumed the LAST replaced
    node's MODEL output. `clip_boundary` is `(clip_source_node_id,
    clip_consumers)` when any replaced node carries a CLIP path (see
    `_lora_chain_clip_boundary`), else `None`."""
    ordered = [n for n in chain.nodes if n.node_id in replaced_node_ids]
    first, last = ordered[0], ordered[-1]
    source_id, source_index = first.model_source
    target_id, target_input = last.model_consumer
    clip_source_id, clip_consumers = _lora_chain_clip_boundary(chain, replaced_node_ids)
    clip_boundary = (clip_source_id, clip_consumers) if clip_source_id is not None else None
    return source_id, source_index, target_id, target_input, clip_boundary


def _lora_node_manipulations(
    lora_field: FieldItem,
    source_id: str,
    source_index: int,
    target_id: str,
    target_input: str,
    clip_boundary: Optional[Tuple[str, List[Tuple[str, str]]]],
) -> List[Any]:
    """The `@loop` rewrite that turns `form.<lora_field>` into a fresh chain
    of LoRA nodes spliced between `(source_id, source_index)` and
    `(target_id, target_input)` - either a detected LoRA chain's replaced
    span (`_lora_loop_boundary`) or, when there is nothing to replace (no
    chain at all, or every chain node kept fixed), the sampling cluster's
    own model-chain boundary (`suggest.ModelChainInfo`) - splicing right
    after whatever the workflow already has wired there.

    `clip_boundary`, when given, is `(clip_source_node_id, clip_consumers)`
    and makes the loop emit `LoraLoader` nodes instead of
    `LoraLoaderModelOnly` so CLIP keeps flowing through it too, each with
    `strength_clip` falling back to the entry's own `strength` (the
    `lora_picker` field's value shape has no separate clip-strength key -
    see `src.platform.templating.dict_utils.active_loras`)."""
    loras_field = lora_field.field_name
    has_clip = clip_boundary is not None
    clip_source_id, clip_consumers = clip_boundary if has_clip else (None, None)
    node_class = "LoraLoader" if has_clip else "LoraLoaderModelOnly"

    # `source_index` only matters for `loop.first` - every OTHER iteration
    # reads our own previously-emitted node, whose MODEL output is always
    # index 0. The common case (source_index == 0) stays a plain int so the
    # emitted YAML doesn't grow a ternary for nothing.
    model_index: Any = source_index if source_index == 0 else f"{{{{ {source_index} if loop.first else 0 }}}}"

    node_inputs: Dict[str, Any] = {
        "lora_name": "{{ item.model | replace('models/loras/', '') }}",
        "strength_model": "{{ item.strength }}",
        "model": [
            "{% if loop.first %}" + source_id + "{% else %}lora_{{ loop.index0 }}{% endif %}",
            model_index,
        ],
    }
    if has_clip:
        node_inputs["strength_clip"] = "{{ item.strength_clip | default(item.strength) }}"
        node_inputs["clip"] = [
            "{% if loop.first %}" + clip_source_id + "{% else %}lora_{{ loop.index0 }}{% endif %}",
            1,
        ]

    manipulations: List[Any] = [
        {
            "@loop": {
                "items": "{{ form." + loras_field + " | default([]) | active_loras }}",
                "template": {
                    "type": "add_node",
                    "node_id": "lora_{{ loop.index }}",
                    "node_config": {
                        "inputs": node_inputs,
                        "class_type": node_class,
                        "_meta": {"title": "LoRA {{ loop.index }}"},
                    },
                },
            }
        },
        {
            "type": "update_node_input",
            "node_id": target_id,
            "input_key": target_input,
            "input_value": [
                "{% set loras = form."
                + loras_field
                + " | default([]) | active_loras %}{% if loras %}lora_{{ loras | length }}{% else %}"
                + source_id
                + "{% endif %}",
                0,
            ],
        },
    ]

    if has_clip:
        for clip_target_id, clip_target_input in clip_consumers:
            manipulations.append(
                {
                    "type": "update_node_input",
                    "node_id": clip_target_id,
                    "input_key": clip_target_input,
                    "input_value": [
                        "{% set loras = form."
                        + loras_field
                        + " | default([]) | active_loras %}{% if loras %}lora_{{ loras | length }}{% else %}"
                        + clip_source_id
                        + "{% endif %}",
                        1,
                    ],
                }
            )

    return manipulations


def _check_schema_drift(
    analysis: AnalyzeResult,
    object_info: Optional[Dict[str, Any]],
    expected_schema_fingerprint: Optional[str],
    expected_object_info_used: Optional[bool],
) -> None:
    """Refuses to save when the workflow's node classification (prompt/model
    field roles - `suggest.classification_fingerprint`) has drifted from what
    the caller was shown at analyze/source time, rather than silently
    re-classifying and either mis-wiring the preset or spuriously rejecting a
    mapping the wizard itself offered. Never trusts a client-declared role -
    only ever compares the schema-derived fingerprint the caller echoes back
    against what saving would actually produce right now.

    `expected_schema_fingerprint=None` means the caller has no baseline to
    compare against (a direct `emit_preset` call, or a reload whose sidecar
    predates this check) - saving proceeds unchecked, exactly as before this
    existed. Otherwise the rule is deliberately asymmetric: `object_info`
    available when analyzed but unreachable now always refuses (a save must
    never silently fall back to a weaker classification than what the caller
    saw); the reverse - unavailable then, reachable now - proceeds when the
    richer classification agrees with what was shown, and refuses when it
    doesn't; with `object_info` equally available (or equally unavailable)
    both times, any fingerprint change refuses. An unmodified workflow run
    through this twice with `object_info=None` both times - the offline,
    no-backend-configured path - always agrees with itself and is never
    refused by this."""
    if expected_schema_fingerprint is None:
        return
    if expected_object_info_used and object_info is None:
        raise PresetEmitError(
            "This workflow was analyzed with a reachable ComfyUI backend, but none is reachable now - "
            "its field classification (which inputs are prompts, model files, or plain fields) can't be "
            "re-verified. Re-open the import wizard once the backend is reachable again before saving."
        )
    actual_fingerprint = classification_fingerprint(analysis)
    if actual_fingerprint != expected_schema_fingerprint:
        raise PresetEmitError(
            "This workflow's node classification (which inputs are prompts, model files, or plain form "
            "fields) has changed since this form was analyzed - re-open the import wizard to refresh the "
            "form before saving."
        )


def emit_preset(
    workflow: Workflow,
    form: ImportForm,
    history: List[HistoryEntry],
    *,
    model_family: str,
    variant: str,
    display_name: str,
    dest_root: Path,
    object_info: Optional[Dict[str, Any]] = None,
    expected_schema_fingerprint: Optional[str] = None,
    expected_object_info_used: Optional[bool] = None,
    overwrite: bool = False,
    preset_id: Optional[str] = None,
) -> EmittedPreset:
    """`overwrite`/`preset_id` back the reload/modify flow
    (`POST .../presets/imported/{id}/reload`, `overwrite_preset_id` on
    `POST .../presets/import`): re-emitting into the SAME directory under
    the SAME id rather than a fresh one. Without `overwrite=True` an
    existing directory is always refused, exactly as before; `overwrite=True`
    requires `preset_id` (there is nothing to keep the identity of
    otherwise) and refuses a directory that doesn't already exist - it is
    "replace this", never "create or replace"."""
    if not model_family or not variant:
        raise PresetEmitError("model_family and variant are both required.")
    _validate_path_segment(model_family, "model_family")
    _validate_path_segment(variant, "variant")
    if overwrite and not preset_id:
        raise PresetEmitError("overwrite=True requires preset_id.")

    # `object_info` mirrors what /presets/import/analyze was given for this
    # exact workflow - the same structural facts (sampler, prompts, mode,
    # LoRA chain) analyze/defaults.py used to seed the wizard.
    analysis = suggest_fields(workflow, object_info=object_info)
    _check_schema_drift(analysis, object_info, expected_schema_fingerprint, expected_object_info_used)

    validate_against_workflow(form, history, workflow, object_info=object_info)

    mode = analysis.mode

    dest_root_resolved = Path(dest_root).resolve()
    preset_dir = Path(dest_root) / model_family / variant
    # Belt and braces on top of _validate_path_segment: the written directory
    # must actually land inside dest_root once symlinks/".." are resolved.
    if dest_root_resolved not in preset_dir.resolve().parents:
        raise PresetEmitError(f"Resolved preset directory escapes {dest_root_resolved}: {preset_dir}")
    dir_exists = preset_dir.exists()
    if dir_exists and not overwrite:
        raise PresetEmitError(f"Preset directory already exists: {preset_dir}")
    if overwrite and not dir_exists:
        raise PresetEmitError(f"overwrite=True but no preset exists at {preset_dir}")

    image_fields = [f for f in all_field_items(form) if f.field_type == "image"]
    if mode == "img2img" and not image_fields:
        raise PresetEmitError(
            "This workflow reads an input image (LoadImage) but the form has no 'image' field; "
            "the workflow can't run without one."
        )

    lora_field = next((f for f in all_field_items(form) if f.field_type == "lora_picker"), None)

    # `form.lora_chain` is optional (see `schema.LoraChainSelection`): a
    # hand-built form, or a preset imported before this selection existed,
    # has none, and gets its pre-existing behavior back - the WHOLE detected
    # chain is replaced by the picker's rewrite, same as always.
    replaced_lora_node_ids: Set[str] = set()
    if lora_field is not None and analysis.lora_chain is not None:
        if form.lora_chain is not None:
            replaced_lora_node_ids = set(form.lora_chain.replaced_node_ids)
        else:
            replaced_lora_node_ids = set(analysis.lora_chain.lora_node_ids)

    # ------------------------------------------------------------------
    # Forms
    # ------------------------------------------------------------------
    form_files, tabs_yaml = _build_form_files(form, mode)
    form_yml = {"name": "custom", "fields": [{"type": "tabs", "children": tabs_yaml}]}

    description_md = (
        f"{IMPORT_PROVENANCE_PREFIX} ({analysis.node_count} nodes). "
        "This preset runs the copied workflow file through the `comfyui` pipe; "
        "form fields drive only the node inputs the admin mapped at import time - everything "
        "else keeps the value it had in the source workflow.\n"
    )

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    field_mappings: List[List[Any]] = []
    param_emitter_parameters: List[Any] = [
        ["positive_prompt", "{{ generation.prompts.first.positive }}"],
        ["negative_prompt", "{{ generation.prompts.first.negative }}"],
    ]

    for prompt_role in ("prompt_positive", "prompt_negative"):
        candidate = next((c for c in analysis.candidates if c.role == prompt_role), None)
        if candidate is None:
            continue
        template_source = (
            "{{ generation.prompts.first.positive }}"
            if prompt_role == "prompt_positive"
            else "{{ generation.prompts.first.negative }}"
        )
        field_mappings.append([template_source, f"{candidate.node_id}.inputs.{candidate.input_name}", "str"])

    seed_candidate = next((c for c in analysis.candidates if c.role == "seed"), None)
    if seed_candidate is not None:
        field_mappings.append(["@seed", f"{seed_candidate.node_id}.inputs.{seed_candidate.input_name}", "int"])

    batch_candidate = next((c for c in analysis.candidates if c.role == "batch_size"), None)
    if batch_candidate is not None:
        field_mappings.append(
            ["{{ form.quantity | default(1) }}", f"{batch_candidate.node_id}.inputs.{batch_candidate.input_name}", "int"]
        )

    for field in all_field_items(form):
        if field is lora_field:
            continue
        for mapping in field.mappings:
            field_mappings.append(_field_mapping_entry(field, mapping))

    for entry in history:
        param_emitter_parameters.append(_history_param_entry(entry))

    node_manipulations: List[Any] = []
    excluded_node_ids: Set[str] = set()
    if lora_field is not None and replaced_lora_node_ids:
        excluded_node_ids.update(replaced_lora_node_ids)
        node_manipulations.extend(
            _lora_node_manipulations(
                lora_field, *_lora_loop_boundary(analysis.lora_chain, replaced_lora_node_ids)
            )
        )
    elif lora_field is not None and analysis.model_chain is not None:
        # Nothing detected to replace (no chain at all, or every chain node
        # kept fixed) - splice the picker right after whatever the workflow
        # already has feeding the sampling cluster's own model input.
        node_manipulations.extend(
            _lora_node_manipulations(
                lora_field,
                analysis.model_chain.source_node_id,
                analysis.model_chain.source_output_index,
                analysis.model_chain.target_node_id,
                analysis.model_chain.target_input,
                None,
            )
        )

    workflow_filename = f"{mode}.json"
    comfyui_configuration: Dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 8188,
        "workflow_file": "{{ paths.preset }}/modes/" + mode + "/files/workflows/" + workflow_filename,
        "timeout": 300,
        "secure": False,
    }
    if node_manipulations:
        comfyui_configuration["node_manipulations"] = node_manipulations
    comfyui_configuration["field_mappings"] = field_mappings

    pipeline_pipes = [
        _pipe(
            "dynamic_prompts_renderer",
            id="dynamic_prompts_renderer",
            configuration={
                "pairs": "{{ generation.prompts.pairs }}",
                "quantity": "{{ form.quantity | default(1) }}",
            },
        ),
        _pipe(
            "seed_generator",
            id="seed_generator",
            configuration={
                "seed": "{{ form.seed | default(-1) }}",
                "quantity": "{{ form.quantity | default(1) }}",
            },
        ),
        _pipe(
            "from_iotype",
            id="from_iotype",
            input=[["seed", "seed_generator", "seed"]],
            configuration={"from": "seed"},
        ),
        _pipe(
            "param_emitter",
            id="param_emitter",
            input=[["seed", "from_iotype", "seed"]],
            configuration={
                "quantity": "{{ form.quantity | default(1) }}",
                "parameters": param_emitter_parameters,
            },
        ),
        _pipe(
            "comfyui",
            input=[["seed", "seed_generator", "seed"]],
            configuration=comfyui_configuration,
        ),
        _pipe(
            "output_skipper",
            input=[["image", "comfyui", "image"]],
            configuration={"rules": []},
        ),
        _pipe(
            "gallery",
            input=[["image", "output_skipper", "image"]],
            configuration={"mode": "save", "support_video": False},
        ),
    ]
    pipeline_yml = {"pipeline": pipeline_pipes}

    # ------------------------------------------------------------------
    # preset.yml
    # ------------------------------------------------------------------
    from src.plugin_api.storage import generate_ulid  # local import: only needed at emit time

    preset_id = preset_id or generate_ulid()

    preset_yml: Dict[str, Any] = {
        "schema": 1,
        "id": preset_id,
        "name": display_name,
        "category": "image",
        "version": "1.0.0",
        "engine": "comfyui",
        "modes": [mode],
    }
    requirements_block = _infer_requirements(
        workflow, object_info=object_info, replaced_lora_node_ids=replaced_lora_node_ids, candidates=analysis.candidates
    )
    if requirements_block:
        preset_yml["requirements"] = requirements_block

    # ------------------------------------------------------------------
    # workflow.json
    # ------------------------------------------------------------------
    workflow_out: Dict[str, Any] = {}
    for node_id, node in workflow.nodes.items():
        if node_id in excluded_node_ids:
            continue
        entry = {"class_type": node.class_type, "inputs": dict(node.inputs)}
        if node.title:
            entry["_meta"] = {"title": node.title}
        workflow_out[node_id] = entry

    if lora_field is not None and analysis.lora_chain is not None and replaced_lora_node_ids:
        _bypass_replaced_lora_nodes(workflow_out, workflow, replaced_lora_node_ids)

    # ------------------------------------------------------------------
    # Sidecar (import.json) - this importer's own record of what it was
    # asked to build, so reload/modify can reproduce it exactly instead of
    # re-analyzing with only the "obvious" defaults. Never read by
    # PresetLinter or the preset engine itself.
    # ------------------------------------------------------------------
    sidecar = {
        "importer_version": IMPORTER_VERSION,
        "source_file": f"modes/{mode}/files/workflows/{workflow_filename}",
        "form": form.model_dump(mode="json"),
        "history": [entry.model_dump(mode="json") for entry in history],
        "model_family": model_family,
        "variant": variant,
        "display_name": display_name,
        "mode": mode,
        "created_at": int(time.time()),
        # This save's own classification, so a later reload can refuse
        # (rather than silently re-classify and possibly mis-save) if the
        # workflow's structural facts have drifted since - see
        # `_check_schema_drift`. A sidecar written before this existed
        # simply lacks these keys, which `api.reload_imported_preset` reads
        # as "no baseline to compare" (unchecked, same as before).
        "schema_fingerprint": classification_fingerprint(analysis),
        "schema_object_info_used": object_info is not None,
    }

    # ------------------------------------------------------------------
    # Write everything into a staging directory first, then publish it into
    # `preset_dir`'s place (`_publish_preset_dir`). A serialization, mkdir,
    # or write failure here must never destroy a previously-working preset
    # (an overwrite) or leave a partial directory a later catalogue scan
    # (`rglob("preset.yml")`, see `src/features/presets/loader.py`) could
    # discover (a first import). `staging_dir` and the publication backup
    # therefore live OUTSIDE `dest_root` entirely, one level above it. A
    # dot-prefixed name inside the root would not hide them: the catalogue's
    # `rglob` descends into dot directories, and this plugin's own
    # `_scan_imported_presets` iterates every directory under
    # content/presets/local unfiltered. `dest_root`'s parent is the nearest
    # place no scanned root contains that is still on the same filesystem,
    # which is what keeps publication a single rename.
    # ------------------------------------------------------------------
    temp_root = dest_root_resolved.parent
    temp_root.mkdir(parents=True, exist_ok=True)
    preset_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = temp_root / f".import-staging-{uuid.uuid4().hex}"

    mode_dir = staging_dir / "modes" / mode
    tabs_dir = mode_dir / "tabs"
    workflows_dir = mode_dir / "files" / "workflows"

    try:
        for d in (tabs_dir, workflows_dir):
            d.mkdir(parents=True, exist_ok=True)

        staged: List[Path] = []

        def write(path: Path, content: str) -> None:
            _write_file(path, content)
            staged.append(path)

        write(staging_dir / "preset.yml", _dump_yaml(preset_yml))
        write(staging_dir / "description.md", description_md)
        write(staging_dir / IMPORT_SIDECAR_FILENAME, _dump_json(sidecar))
        write(mode_dir / "form.yml", _dump_yaml(form_yml))
        write(mode_dir / "pipeline.yml", _dump_yaml(pipeline_yml))
        for filename, data in form_files.items():
            write(tabs_dir / filename, _dump_yaml(data))
        write(workflows_dir / workflow_filename, _dump_json(workflow_out))
    except Exception as staging_error:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise PresetEmitError(
            f"Failed to stage the imported preset for writing: {staging_error}"
        ) from staging_error

    _publish_preset_dir(preset_dir, staging_dir, temp_root=temp_root)

    return EmittedPreset(
        preset_id=preset_id,
        preset_dir=preset_dir,
        mode=mode,
        paths=[str(preset_dir / p.relative_to(staging_dir)) for p in staged],
    )
