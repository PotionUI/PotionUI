"""Writing a lint-clean preset directory from a parsed workflow plus the
admin's field choices (see `suggest.suggest_fields` for the candidate list
choices are drawn from).

Wiring is split into two tiers:

- Foundational (always wired, never gated by a choice): the sampler's seed,
  the positive/negative prompt text nodes, and the latent image's batch
  size. A comfyui image preset that doesn't wire these isn't useful, so they
  are not something an admin opts out of - they simply aren't form fields at
  all (seed/quantity are the two standard fields every mode gets; prompts
  come from `generation.prompts`, never a form field).
- Choice-gated: steps/cfg/sampler/scheduler/denoise, resolution
  (width+height together), each model loader, the LoRA chain, input images,
  and any other literal input become a form field, and therefore a
  `field_mappings` entry, only when the caller includes them in `choices`.
  Anything left out keeps the literal value baked into the copied workflow
  JSON exactly as the source workflow had it.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .convert import extract_node_groups
from .parser import Workflow
from .suggest import (
    CHECKPOINT_CLASSES,
    CLIP_CLASSES,
    DIFFUSION_MODEL_CLASSES,
    LORA_CLASS_PREFIX,
    VAE_CLASSES,
    InputCandidate,
    suggest_fields,
)

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
IMPORTER_VERSION = 1

# The sidecar file name written alongside preset.yml/description.md - not
# read by PresetLinter/the preset engine, purely this importer's own record
# of what it was told to build, so reload/modify can reproduce it exactly
# instead of re-analyzing with only the "obvious" defaults.
IMPORT_SIDECAR_FILENAME = "import.json"

MODEL_TYPE_STRIP_PREFIXES = {
    "checkpoint": ("models/checkpoints/",),
    "diffusion_model": ("models/diffusion_models/", "models/checkpoints/"),
    "clip": ("models/clip/",),
    "vae": ("models/vae/",),
}

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

# Loader class -> (ComfyUI `models/` subfolder, the input names it loads a
# filename from) - same classes `suggest.suggest_fields` detects as model
# loaders, folder names matching `ComfyUIBackend.FOLDER_TO_MODEL_TYPE`.
_MODEL_LOADER_FOLDERS = (
    (CHECKPOINT_CLASSES, "checkpoints", ("ckpt_name",)),
    (DIFFUSION_MODEL_CLASSES, "diffusion_models", ("unet_name",)),
    (CLIP_CLASSES, "text_encoders", ("clip_name", "clip_name1", "clip_name2")),
    (VAE_CLASSES, "vae", ("vae_name",)),
)

_ADVANCED_NAMED_ROLES = ("steps", "cfg", "sampler", "scheduler", "denoise")
_MODEL_ROLES = ("checkpoint", "diffusion_model", "clip", "vae")

# One path segment: no "/", "\", "..", and no leading dot (so it can't be
# hidden or resolve as a relative-parent trick on any OS).
_SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


def _tab_slug(label: str) -> str:
    """A safe tabs/<slug>.yml filename stem for a ComfyUI group title (or
    the "Advanced" fallback) - non-alphanumeric runs collapse to one "_"."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
    return slug or "advanced"


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
class FieldChoice:
    node_id: str
    input_name: str
    field_name: Optional[str] = None
    field_type: Optional[str] = None
    label: Optional[str] = None


@dataclass
class EmittedPreset:
    preset_id: str
    preset_dir: Path
    mode: str
    paths: List[str]


class PresetEmitError(ValueError):
    """The requested emission can't be carried out as asked."""


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


def _resolve_choices(
    candidates_by_key: Dict[tuple, InputCandidate], choices: List[FieldChoice]
) -> Dict[tuple, Dict[str, Any]]:
    resolved: Dict[tuple, Dict[str, Any]] = {}
    for choice in choices:
        key = (choice.node_id, choice.input_name)
        candidate = candidates_by_key.get(key)
        if candidate is None:
            raise PresetEmitError(
                f"No such candidate node_id={choice.node_id!r} input_name={choice.input_name!r}. "
                "Run /presets/import/analyze on this exact workflow first."
            )
        resolved[key] = {
            "candidate": candidate,
            "field_name": choice.field_name or candidate.suggested_field_name,
            "field_type": choice.field_type or candidate.suggested_field_type,
            "label": choice.label or candidate.suggested_label,
        }
    return resolved


def _infer_requirements(
    workflow: Workflow, object_info: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """`requirements:` entries for this workflow, independent of which
    inputs the admin chose as form fields: one `comfyui_node` per non-core
    node class (see `_is_core_node_class`), and one `comfyui_model` per
    checkpoint/UNET/CLIP/VAE/LoRA file it references. An unchosen model
    loader still needs its file present to run, so this reads the whole
    graph rather than the choice-gated candidate list `emit_preset`'s
    form-building uses."""
    requirements: List[Dict[str, Any]] = []

    node_classes = sorted({n.class_type for n in workflow.nodes.values()})
    for class_type in node_classes:
        if not _is_core_node_class(class_type, object_info):
            requirements.append({"type": "comfyui_node", "class_type": class_type})

    seen: set = set()

    def _add_model(folder: str, name: Any) -> None:
        if not isinstance(name, str) or not name or (folder, name) in seen:
            return
        seen.add((folder, name))
        requirements.append({"type": "comfyui_model", "folder": folder, "name": name})

    for classes, folder, input_names in _MODEL_LOADER_FOLDERS:
        for node in workflow.find_by_class(*classes):
            literals = node.literals()
            for input_name in input_names:
                _add_model(folder, literals.get(input_name))

    for node in workflow.find_by_class_prefix(LORA_CLASS_PREFIX):
        _add_model("loras", node.literals().get("lora_name"))

    return requirements


def emit_preset(
    workflow: Workflow,
    choices: List[FieldChoice],
    *,
    model_family: str,
    variant: str,
    display_name: str,
    dest_root: Path,
    object_info: Optional[Dict[str, Any]] = None,
    ui_workflow: Optional[Dict[str, Any]] = None,
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

    # `object_info`/`ui_workflow` mirror what /presets/import/analyze was
    # given for this exact workflow, so a UI-format import's candidates -
    # richer suggested_config, a suggested_tab per ComfyUI group - come out
    # identical here to what the admin already picked fields from.
    node_groups = extract_node_groups(ui_workflow) if ui_workflow else None
    analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
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

    candidates_by_key = {c.key(): c for c in analysis.candidates}
    resolved = _resolve_choices(candidates_by_key, choices)

    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for entry in resolved.values():
        by_role.setdefault(entry["candidate"].role, []).append(entry)

    resolution_entries = by_role.get("resolution_width", []) + by_role.get("resolution_height", [])
    if resolution_entries and (
        "resolution_width" not in by_role or "resolution_height" not in by_role
    ):
        raise PresetEmitError("resolution requires both the width and height candidates to be chosen together.")

    model_entries = [e for role in _MODEL_ROLES for e in by_role.get(role, [])]
    lora_entries = by_role.get("lora_slot", [])
    image_entries = by_role.get("image", [])
    if mode == "img2img" and not image_entries:
        raise PresetEmitError(
            "This workflow reads an input image (LoadImage) but no image candidate was chosen; "
            "the workflow can't run without one. Include its candidate in `fields`."
        )
    advanced_named_entries = [e for role in _ADVANCED_NAMED_ROLES for e in by_role.get(role, [])]
    literal_entries = by_role.get("literal", [])

    if lora_entries:
        field_names = {e["field_name"] for e in lora_entries}
        if len(field_names) > 1:
            raise PresetEmitError(f"All LoRA slot choices must share one field_name, got: {sorted(field_names)}")

    # ------------------------------------------------------------------
    # Forms
    # ------------------------------------------------------------------
    generation_fields: List[Dict[str, Any]] = [
        {"name": "seed", "type": "seed", "label": "Seed", "default": -1},
        {
            "name": "quantity",
            "type": "slider",
            "label": "Batch Size",
            "configuration": {"min": 1, "max": 8, "step": 1},
            "default": 1,
        },
    ]

    if resolution_entries:
        width_candidate = by_role["resolution_width"][0]["candidate"]
        height_candidate = by_role["resolution_height"][0]["candidate"]
        default_resolution = f"{width_candidate.current_value}x{height_candidate.current_value}"
        generation_fields.append(
            {
                "name": "resolution",
                "type": "resolution",
                "label": "Image Resolution",
                "configuration": {"options": [default_resolution]},
                "default": default_resolution,
            }
        )

    if model_entries:
        model_children = []
        for entry in model_entries:
            candidate = entry["candidate"]
            config = dict(candidate.suggested_config)
            config.setdefault("placeholder", f"Select {entry['label']}...")
            model_children.append(
                {
                    "name": entry["field_name"],
                    "type": "model",
                    "label": entry["label"],
                    "required": True,
                    "configuration": config,
                }
            )
        generation_fields.append({"type": "group", "label": "Models", "children": model_children})

    form_files: Dict[str, Dict[str, Any]] = {"generation.yml": {"fields": generation_fields}}

    tabs: List[Dict[str, Any]] = [
        {
            "type": "tab",
            "label": "Generation",
            "configuration": {"icon": "model", "icon_display": "icon_only"},
            "children": _tab_children_path(mode, "generation.yml"),
        }
    ]

    if image_entries:
        image_fields = []
        for entry in image_entries:
            candidate = entry["candidate"]
            image_fields.append(
                {
                    "name": entry["field_name"],
                    "type": "image",
                    "label": entry["label"],
                    "required": candidate.role == "image" and entry["field_name"] == "source_image",
                    "configuration": dict(candidate.suggested_config),
                }
            )
        form_files["image.yml"] = {"fields": image_fields}
        tabs.append(
            {
                "type": "tab",
                "label": "Source Image",
                "configuration": {"icon": "image", "icon_display": "icon_only"},
                "children": _tab_children_path(mode, "image.yml"),
            }
        )

    if lora_entries:
        form_files["lora.yml"] = {
            "fields": [
                {
                    "name": lora_entries[0]["field_name"],
                    "type": "lora_picker",
                    "label": "LoRAs",
                    "default": [],
                    "configuration": {
                        "model_type": "lora",
                        "placeholder": "Select a LoRA...",
                        "allow_info_modal": True,
                        "strength_min": -2.0,
                        "strength_max": 2.0,
                        "strength_step": 0.1,
                        "strength_default": 1.0,
                        "max_items": 6,
                    },
                }
            ]
        }
        tabs.append(
            {
                "type": "tab",
                "label": "LoRA",
                "configuration": {"icon": "lora", "icon_display": "icon_only"},
                "children": _tab_children_path(mode, "lora.yml"),
            }
        )

    # steps/cfg/sampler/scheduler/denoise and any other chosen literal input
    # get one tab per ComfyUI group they belonged to in a UI-format import
    # (candidate.suggested_tab, see suggest.suggest_fields' node_groups
    # parameter), falling back to a single "Advanced" tab exactly as before
    # when the source workflow carried no group info (an API-format import,
    # or a UI-format one with no groups drawn).
    advanced_fields_by_tab: Dict[str, List[Dict[str, Any]]] = {}
    for entry in advanced_named_entries + literal_entries:
        candidate = entry["candidate"]
        field: Dict[str, Any] = {
            "name": entry["field_name"],
            "type": entry["field_type"],
            "label": entry["label"],
            "default": _yaml_value(candidate.current_value),
        }
        if candidate.suggested_config:
            field["configuration"] = dict(candidate.suggested_config)
        advanced_fields_by_tab.setdefault(candidate.suggested_tab or "Advanced", []).append(field)

    for tab_label, fields in advanced_fields_by_tab.items():
        filename = f"{_tab_slug(tab_label)}.yml"
        form_files[filename] = {"fields": fields}
        tabs.append(
            {
                "type": "tab",
                "label": tab_label,
                "configuration": {"icon": "settings", "icon_display": "icon_only"},
                "children": _tab_children_path(mode, filename),
            }
        )

    description_md = (
        f"{IMPORT_PROVENANCE_PREFIX} ({analysis.node_count} nodes). "
        "This preset runs the copied workflow file through the `comfyui` pipe; "
        "form fields drive only the node inputs picked at import time - everything "
        "else keeps the value it had in the source workflow.\n"
    )
    if ui_workflow is not None:
        description_md += (
            f"Imported from a UI-format export; the original `{mode}.ui.json` is kept "
            f"alongside the converted `{mode}.json` for reference.\n"
        )

    form_yml = {"name": "custom", "fields": [{"type": "tabs", "children": tabs}]}

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    field_mappings: List[List[Any]] = []
    param_emitter_parameters: List[Any] = []

    for entry in model_entries:
        candidate = entry["candidate"]
        model_type = candidate.role
        strip_value = "{{ (form." + entry["field_name"] + " | default('') or '') "
        for prefix in MODEL_TYPE_STRIP_PREFIXES.get(model_type, ()):
            strip_value += f" | replace('{prefix}', '')"
        strip_value += " }}"
        field_mappings.append([strip_value, f"{candidate.node_id}.inputs.{candidate.input_name}", "str"])
        param_emitter_parameters.append(["model", "{{ form." + entry["field_name"] + " | default('') }}"])

    if lora_entries:
        loras_field = lora_entries[0]["field_name"]
        param_emitter_parameters.append(
            {
                "@loop": {
                    "items": "{{ form." + loras_field + " | default([]) | active_loras }}",
                    "template": ["model", "{{ item.model }}"],
                }
            }
        )

    param_emitter_parameters.append(["positive_prompt", "{{ generation.prompts.first.positive }}"])
    param_emitter_parameters.append(["negative_prompt", "{{ generation.prompts.first.negative }}"])

    for prompt_role, sampler_input in (("prompt_positive", "positive"), ("prompt_negative", "negative")):
        for candidate in candidates_by_key.values():
            if candidate.role == prompt_role:
                template_source = (
                    "{{ generation.prompts.first.positive }}"
                    if prompt_role == "prompt_positive"
                    else "{{ generation.prompts.first.negative }}"
                )
                field_mappings.append(
                    [template_source, f"{candidate.node_id}.inputs.{candidate.input_name}", "str"]
                )

    for candidate in candidates_by_key.values():
        if candidate.role == "seed":
            field_mappings.append(["@seed", f"{candidate.node_id}.inputs.{candidate.input_name}", "int"])
        if candidate.role == "batch_size":
            field_mappings.append(
                [
                    "{{ form.quantity | default(1) }}",
                    f"{candidate.node_id}.inputs.{candidate.input_name}",
                    "int",
                ]
            )

    if resolution_entries:
        width_candidate = by_role["resolution_width"][0]["candidate"]
        height_candidate = by_role["resolution_height"][0]["candidate"]
        default_resolution = f"{width_candidate.current_value}x{height_candidate.current_value}"
        field_mappings.append(
            [
                "{{ (form.resolution | default('" + default_resolution + "')).split('x')[0] }}",
                f"{width_candidate.node_id}.inputs.{width_candidate.input_name}",
                "int",
            ]
        )
        field_mappings.append(
            [
                "{{ (form.resolution | default('" + default_resolution + "')).split('x')[1] }}",
                f"{height_candidate.node_id}.inputs.{height_candidate.input_name}",
                "int",
            ]
        )
        param_emitter_parameters.append(
            ["resolution", "{{ form.resolution | default('" + default_resolution + "') }}"]
        )

    for entry in advanced_named_entries:
        candidate = entry["candidate"]
        default_literal = json.dumps(_yaml_value(candidate.current_value))
        field_mappings.append(
            [
                "{{ form." + entry["field_name"] + " | default(" + default_literal + ") }}",
                f"{candidate.node_id}.inputs.{candidate.input_name}",
                _cast_name(candidate.value_type),
            ]
        )
        param_emitter_parameters.append(
            [candidate.role, "{{ form." + entry["field_name"] + " | default(" + default_literal + ") }}"]
        )

    for entry in image_entries:
        candidate = entry["candidate"]
        field_mappings.append(
            ["{{ form." + entry["field_name"] + " }}", f"{candidate.node_id}.inputs.{candidate.input_name}", "image"]
        )

    for entry in literal_entries:
        candidate = entry["candidate"]
        default_literal = json.dumps(_yaml_value(candidate.current_value))
        field_mappings.append(
            [
                "{{ form." + entry["field_name"] + " | default(" + default_literal + ") }}",
                f"{candidate.node_id}.inputs.{candidate.input_name}",
                _cast_name(candidate.value_type),
            ]
        )

    node_manipulations: List[Any] = []
    excluded_node_ids: set = set()
    if lora_entries and analysis.lora_chain is not None:
        loras_field = lora_entries[0]["field_name"]
        source_id = analysis.lora_chain.source_node_id
        target_id = analysis.lora_chain.target_node_id
        excluded_node_ids.update(analysis.lora_chain.lora_node_ids)
        node_manipulations.append(
            {
                "@loop": {
                    "items": "{{ form." + loras_field + " | default([]) }}",
                    "template": {
                        "type": "add_node",
                        "node_id": "lora_{{ loop.index }}",
                        "node_config": {
                            "inputs": {
                                "lora_name": "{{ item.model | replace('models/loras/', '') }}",
                                "strength_model": "{{ item.strength }}",
                                "model": [
                                    "{% if loop.first %}" + source_id + "{% else %}lora_{{ loop.index0 }}{% endif %}",
                                    0,
                                ],
                            },
                            "class_type": "LoraLoaderModelOnly",
                            "_meta": {"title": "LoRA {{ loop.index }}"},
                        },
                    },
                }
            }
        )
        node_manipulations.append(
            {
                "type": "update_node_input",
                "node_id": target_id,
                "input_key": "model",
                "input_value": [
                    "{% set loras = form."
                    + loras_field
                    + " | default([]) %}{% if loras %}lora_{{ loras | length }}{% else %}"
                    + source_id
                    + "{% endif %}",
                    0,
                ],
            }
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
    vars_block: Dict[str, Any] = {}
    for entry in advanced_named_entries:
        candidate = entry["candidate"]
        vars_block[f"default_{candidate.role}"] = _yaml_value(candidate.current_value)

    configuration_block: Dict[str, Any] = {}
    for entry in model_entries:
        configuration_block[f"{entry['candidate'].role}_tags"] = {
            "type": "model_tags",
            "label": f"{entry['label']} tags",
            "description": f"Only models with any of these tags appear in the {entry['label']} picker.",
        }
    if lora_entries:
        configuration_block["lora_tags"] = {
            "type": "model_tags",
            "label": "LoRA tags",
            "description": "Only LoRAs with any of these tags appear in the LoRA picker.",
        }

    preset_yml: Dict[str, Any] = {
        "schema": 1,
        "id": preset_id,
        "name": display_name,
        "category": "image",
        "version": "1.0.0",
        "engine": "comfyui",
        "modes": [mode],
    }
    if configuration_block:
        preset_yml["configuration"] = configuration_block
    if vars_block:
        preset_yml["vars"] = vars_block
    requirements_block = _infer_requirements(workflow, object_info=object_info)
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

    if lora_entries and analysis.lora_chain is not None:
        target_node = workflow_out.get(analysis.lora_chain.target_node_id)
        if target_node is not None:
            target_node["inputs"]["model"] = [analysis.lora_chain.source_node_id, 0]

    # ------------------------------------------------------------------
    # Sidecar (import.json) - this importer's own record of what it was
    # asked to build, so reload/modify can reproduce it exactly instead of
    # re-analyzing with only the "obvious" defaults. Never read by
    # PresetLinter or the preset engine itself.
    # ------------------------------------------------------------------
    source_filename = f"{mode}.ui.json" if ui_workflow is not None else workflow_filename
    sidecar = {
        "importer_version": IMPORTER_VERSION,
        "format": "ui" if ui_workflow is not None else "api",
        "source_file": f"modes/{mode}/files/workflows/{source_filename}",
        "choices": [
            {
                "node_id": node_id,
                "input_name": input_name,
                "field_name": entry["field_name"],
                "field_type": entry["field_type"],
                "label": entry["label"],
            }
            for (node_id, input_name), entry in resolved.items()
        ],
        "model_family": model_family,
        "variant": variant,
        "display_name": display_name,
        "mode": mode,
        "created_at": int(time.time()),
    }

    # ------------------------------------------------------------------
    # Write everything - overwrite drops the whole directory first (after
    # every validation above has already passed) so a reload/modify never
    # leaves a stale tab/file behind from a previous shape (e.g. the mode
    # name, or which tabs exist, changed between the original import and now).
    # ------------------------------------------------------------------
    if overwrite:
        shutil.rmtree(preset_dir)

    mode_dir = preset_dir / "modes" / mode
    tabs_dir = mode_dir / "tabs"
    workflows_dir = mode_dir / "files" / "workflows"
    for d in (tabs_dir, workflows_dir):
        d.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []

    def write(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        written.append(path)

    write(preset_dir / "preset.yml", _dump_yaml(preset_yml))
    write(preset_dir / "description.md", description_md)
    write(preset_dir / IMPORT_SIDECAR_FILENAME, json.dumps(sidecar, indent=2) + "\n")
    write(mode_dir / "form.yml", _dump_yaml(form_yml))
    write(mode_dir / "pipeline.yml", _dump_yaml(pipeline_yml))
    for filename, data in form_files.items():
        write(tabs_dir / filename, _dump_yaml(data))
    write(workflows_dir / workflow_filename, json.dumps(workflow_out, indent=2) + "\n")
    if ui_workflow is not None:
        write(workflows_dir / f"{mode}.ui.json", json.dumps(ui_workflow, indent=2) + "\n")

    return EmittedPreset(
        preset_id=preset_id,
        preset_dir=preset_dir,
        mode=mode,
        paths=[str(p) for p in written],
    )
