"""Structural analysis of a parsed ComfyUI workflow: detect the sampler,
prompts, resolution, model loaders, an optional LoRA chain and any input
images, and suggest a form field for every remaining configurable input.

Detection is structural (follows connections), not name-based: a workflow's
positive/negative prompt is whatever is wired into the sampler's `positive`/
`negative` inputs, not a node whose title happens to say "prompt".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .parser import Workflow, WorkflowNode

SAMPLER_CLASSES = ("KSampler", "KSamplerAdvanced")
LATENT_IMAGE_CLASSES = ("EmptyLatentImage", "EmptySD3LatentImage")
CHECKPOINT_CLASSES = ("CheckpointLoaderSimple",)
DIFFUSION_MODEL_CLASSES = ("UNETLoader", "UnetLoaderGGUF")
CLIP_CLASSES = ("CLIPLoader", "DualCLIPLoader")
VAE_CLASSES = ("VAELoader",)
IMAGE_LOADER_CLASSES = ("LoadImage",)
LORA_CLASS_PREFIX = "LoraLoader"

# Output/preview nodes never carry a configurable input worth surfacing.
SKIP_NODE_CLASSES = frozenset(
    {"PreviewImage", "SaveImage", "PreviewAudio", "SaveAudio", "VHS_VideoCombine"}
)

# Inputs ComfyUI's own UI drives (never present as real user-facing data even
# though they may show up as a literal in an API-format export).
_INTERNAL_INPUT_NAMES = frozenset({"control_after_generate"})

# Candidate text input names on whatever node a sampler's positive/negative
# connection resolves to, checked in preference order.
_PROMPT_TEXT_INPUT_NAMES = ("text", "prompt")

_SEED_INPUT_NAMES = ("seed", "noise_seed")

# Fallback (name-based) prompt detection - see `_fallback_prompt_roles`'s
# docstring for why this is the one deliberate exception to this module's
# "structural, not name-based" rule.
_FALLBACK_POSITIVE_INPUT_NAMES = ("prompt", "positive", "positive_prompt")
_FALLBACK_NEGATIVE_INPUT_NAMES = ("negative", "negative_prompt")
_IMAGE_OR_VIDEO_OUTPUT_TYPES = frozenset({"IMAGE", "VIDEO"})


@dataclass
class InputCandidate:
    node_id: str
    class_type: str
    node_title: Optional[str]
    input_name: str
    current_value: Any
    value_type: str
    suggested_field_type: str
    suggested_field_name: str
    suggested_label: str
    suggested_config: Dict[str, Any] = field(default_factory=dict)
    role: str = "literal"
    obvious: bool = False
    suggested_tab: Optional[str] = None

    def key(self) -> tuple:
        return (self.node_id, self.input_name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "class_type": self.class_type,
            "node_title": self.node_title,
            "input_name": self.input_name,
            "current_value": self.current_value,
            "value_type": self.value_type,
            "suggested_field_type": self.suggested_field_type,
            "suggested_field_name": self.suggested_field_name,
            "suggested_label": self.suggested_label,
            "suggested_config": self.suggested_config,
            "role": self.role,
            "obvious": self.obvious,
            "suggested_tab": self.suggested_tab,
        }


@dataclass
class LoraChainInfo:
    source_node_id: str
    target_node_id: str
    lora_node_ids: List[str]  # source -> target order


@dataclass
class AnalyzeResult:
    candidates: List[InputCandidate]
    mode: str
    node_count: int
    sampler_node_id: Optional[str]
    lora_chain: Optional[LoraChainInfo]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "node_count": self.node_count,
            "sampler_node_id": self.sampler_node_id,
            "lora_chain": (
                {
                    "source_node_id": self.lora_chain.source_node_id,
                    "target_node_id": self.lora_chain.target_node_id,
                    "lora_node_ids": self.lora_chain.lora_node_ids,
                }
                if self.lora_chain
                else None
            ),
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _infer_value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "unsupported"


def _humanize(input_name: str) -> str:
    return input_name.replace("_", " ").strip().title()


def _find_sampler(workflow: Workflow) -> Optional[WorkflowNode]:
    for node in workflow.find_by_class(*SAMPLER_CLASSES):
        return node
    # Structural fallback for a sampler class this importer doesn't name
    # explicitly: any node wired to both a positive and a negative conditioning.
    for node in workflow.nodes.values():
        conns = node.connections()
        if "positive" in conns and "negative" in conns:
            return node
    return None


def _seed_input_name(sampler: WorkflowNode) -> Optional[str]:
    for name in _SEED_INPUT_NAMES:
        if name in sampler.literals():
            return name
    return None


def _prompt_text_input(node: WorkflowNode) -> Optional[str]:
    literals = node.literals()
    for name in _PROMPT_TEXT_INPUT_NAMES:
        if name in literals and isinstance(literals[name], str):
            return name
    return None


def _detect_lora_chain(workflow: Workflow, sampler: WorkflowNode) -> Optional[LoraChainInfo]:
    conn = sampler.connection_source("model")
    if conn is None:
        return None

    chain: List[WorkflowNode] = []  # target -> source order while walking
    current = workflow.resolve(conn)
    while current is not None and current.class_type.startswith(LORA_CLASS_PREFIX):
        chain.append(current)
        next_conn = current.connection_source("model")
        current = workflow.resolve(next_conn) if next_conn else None

    if not chain:
        return None

    source_node_id = current.id if current is not None else conn[0]
    return LoraChainInfo(
        source_node_id=source_node_id,
        target_node_id=sampler.id,
        lora_node_ids=[n.id for n in reversed(chain)],  # source -> target order
    )


def _find_input_spec(class_info: Dict[str, Any], input_name: str) -> Optional[Tuple[Any, Dict[str, Any]]]:
    """`(type_spec, config)` for `input_name` in one `/object_info` entry's
    `input.required`/`input.optional`, or `None` if that entry doesn't
    declare it (an unrecognized custom-node class, or an input this
    importer's own structural detection added that the class doesn't
    actually have - neither should ever crash the enrichment pass)."""
    input_defs = class_info.get("input") if isinstance(class_info, dict) else None
    if not isinstance(input_defs, dict):
        return None
    for section in ("required", "optional"):
        section_defs = input_defs.get(section)
        if not isinstance(section_defs, dict) or input_name not in section_defs:
            continue
        spec = section_defs[input_name]
        if not isinstance(spec, (list, tuple)) or not spec:
            return None
        type_spec = spec[0]
        config = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        return type_spec, config
    return None


def _enrich_with_object_info(
    candidates: List[InputCandidate], workflow: Workflow, object_info: Dict[str, Any]
) -> None:
    """Sharpen each candidate's suggested field using the live server's own
    declared input schema: real min/max/step for a slider, the actual
    option list for a combo (rather than this importer's generic
    `select`/`model` guess), a checkbox for a boolean, and multiline for a
    text area - independent of which structural role (if any) the candidate
    already has."""
    for candidate in candidates:
        node = workflow.node(candidate.node_id)
        if node is None:
            continue
        class_info = object_info.get(node.class_type)
        if not isinstance(class_info, dict):
            continue
        spec = _find_input_spec(class_info, candidate.input_name)
        if spec is None:
            continue
        type_spec, config = spec

        if isinstance(type_spec, list):
            if candidate.suggested_field_type not in ("model", "lora_picker"):
                # The real option list replaces a generic "select" guess's
                # config outright - a static `file:` pointer and a live
                # `options:` list would otherwise both be present at once.
                candidate.suggested_field_type = "select"
                candidate.suggested_config = {"options": list(type_spec)}
        elif type_spec in ("INT", "FLOAT"):
            numeric_config = {k: config[k] for k in ("min", "max", "step") if k in config}
            if numeric_config:
                # The server's own bounds are authoritative over this
                # importer's generic slider guess.
                candidate.suggested_config = {**candidate.suggested_config, **numeric_config}
                if candidate.suggested_field_type == "number":
                    candidate.suggested_field_type = "slider"
        elif type_spec == "BOOLEAN":
            candidate.suggested_field_type = "checkbox"
        elif type_spec == "STRING" and config.get("multiline"):
            candidate.suggested_field_type = "textbox"
            candidate.suggested_config = {**candidate.suggested_config, "multiline": True}


def _produces_image_or_video(node: WorkflowNode, object_info: Optional[Dict[str, Any]]) -> bool:
    """Whether the live server's own `/object_info` says this node's class
    outputs an IMAGE or VIDEO - `None`/unknown-class safe (`False`, never a
    crash), since a plain Export (API) import has no `object_info` to ask
    at all."""
    if not object_info:
        return False
    class_info = object_info.get(node.class_type)
    if not isinstance(class_info, dict):
        return False
    outputs = class_info.get("output") or []
    return any(o in _IMAGE_OR_VIDEO_OUTPUT_TYPES for o in outputs)


def _fallback_prompt_roles(
    workflow: Workflow, object_info: Optional[Dict[str, Any]], claimed: set
) -> List["InputCandidate"]:
    """Prompt detection by input name, used only when the structural
    (sampler-conditioning) detection above found no positive/negative at
    all - an all-in-one node with its own baked-in sampling (Krea2ImageNode,
    no separate KSampler to follow a conditioning link from) still has a
    real prompt input, and it must never be offered as a choosable form
    field: prompts always come from the Prompts section, never the dynamic
    form (see emit.py's module docstring on foundational fields).

    This is the one deliberate exception to this module's "structural, not
    name-based" rule (see the module docstring) - it only ever runs as a
    fallback, never instead of the structural detection above.

    Prefers nodes the live server's `/object_info` says produce an IMAGE or
    VIDEO output; falls back to every node in the workflow when that can't
    be determined (no `object_info`, or nothing qualifies) - missing a real
    prompt input is worse than checking a few extra nodes."""
    candidates: List[InputCandidate] = []
    scope = [n for n in workflow.nodes.values() if _produces_image_or_video(n, object_info)]
    if not scope:
        scope = list(workflow.nodes.values())

    for node in scope:
        if node.class_type in SKIP_NODE_CLASSES:
            continue
        literals = node.literals()
        string_input_names = [name for name, value in literals.items() if isinstance(value, str)]

        negative_name = next((n for n in _FALLBACK_NEGATIVE_INPUT_NAMES if n in literals), None)

        positive_name = next((n for n in _FALLBACK_POSITIVE_INPUT_NAMES if n in literals), None)
        if positive_name is None and "text" in literals:
            has_negative_sibling = negative_name is not None or any(
                name.startswith("negative") for name in literals
            )
            is_only_text_input = len(string_input_names) == 1
            if has_negative_sibling or is_only_text_input:
                positive_name = "text"

        for role, name, field_name, label in (
            ("prompt_positive", positive_name, "positive_prompt", "Positive Prompt"),
            ("prompt_negative", negative_name, "negative_prompt", "Negative Prompt"),
        ):
            if name is None or (node.id, name) in claimed:
                continue
            claimed.add((node.id, name))
            candidates.append(
                InputCandidate(
                    node_id=node.id,
                    class_type=node.class_type,
                    node_title=node.title,
                    input_name=name,
                    current_value=literals[name],
                    value_type="str",
                    suggested_field_type="textbox",
                    suggested_field_name=field_name,
                    suggested_label=label,
                    role=role,
                    obvious=True,
                )
            )

    return candidates


def suggest_fields(
    workflow: Workflow,
    *,
    object_info: Optional[Dict[str, Any]] = None,
) -> AnalyzeResult:
    candidates: List[InputCandidate] = []
    claimed: set = set()  # (node_id, input_name) already covered by a structural role

    sampler = _find_sampler(workflow)
    lora_chain = _detect_lora_chain(workflow, sampler) if sampler else None

    if sampler is not None:
        seed_name = _seed_input_name(sampler)
        if seed_name is not None:
            claimed.add((sampler.id, seed_name))
            candidates.append(
                InputCandidate(
                    node_id=sampler.id,
                    class_type=sampler.class_type,
                    node_title=sampler.title,
                    input_name=seed_name,
                    current_value=sampler.literals()[seed_name],
                    value_type="int",
                    suggested_field_type="seed",
                    suggested_field_name="seed",
                    suggested_label="Seed",
                    role="seed",
                    obvious=True,
                )
            )

        for input_name, field_type, field_name, label, config in (
            ("steps", "slider", "steps", "Steps", {"min": 1, "max": 150, "step": 1}),
            ("cfg", "slider", "cfg", "CFG Scale", {"min": 1.0, "max": 30.0, "step": 0.1}),
            (
                "sampler_name",
                "select",
                "sampler_name",
                "Sampler",
                {"file": {"path": "{{ paths._shared }}/comfyui/form/samplers/all.yml"}},
            ),
            (
                "scheduler",
                "select",
                "scheduler",
                "Scheduler",
                {"file": {"path": "{{ paths._shared }}/comfyui/form/schedulers/all.yml"}},
            ),
            ("denoise", "slider", "denoise", "Denoise", {"min": 0.0, "max": 1.0, "step": 0.05}),
        ):
            literals = sampler.literals()
            if input_name not in literals:
                continue
            claimed.add((sampler.id, input_name))
            candidates.append(
                InputCandidate(
                    node_id=sampler.id,
                    class_type=sampler.class_type,
                    node_title=sampler.title,
                    input_name=input_name,
                    current_value=literals[input_name],
                    value_type=_infer_value_type(literals[input_name]),
                    suggested_field_type=field_type,
                    suggested_field_name=field_name,
                    suggested_label=label,
                    suggested_config=config,
                    role=input_name if input_name not in ("sampler_name",) else "sampler",
                    obvious=input_name in ("steps", "cfg"),
                )
            )

        for role, input_name in (("prompt_positive", "positive"), ("prompt_negative", "negative")):
            conn = sampler.connection_source(input_name)
            if conn is None:
                continue
            prompt_node = workflow.resolve(conn)
            if prompt_node is None:
                continue
            text_input = _prompt_text_input(prompt_node)
            if text_input is None:
                continue
            claimed.add((prompt_node.id, text_input))
            candidates.append(
                InputCandidate(
                    node_id=prompt_node.id,
                    class_type=prompt_node.class_type,
                    node_title=prompt_node.title,
                    input_name=text_input,
                    current_value=prompt_node.literals()[text_input],
                    value_type="str",
                    suggested_field_type="textbox",
                    suggested_field_name="positive_prompt" if role == "prompt_positive" else "negative_prompt",
                    suggested_label="Positive Prompt" if role == "prompt_positive" else "Negative Prompt",
                    role=role,
                    obvious=True,
                )
            )

    if not any(c.role in ("prompt_positive", "prompt_negative") for c in candidates):
        candidates.extend(_fallback_prompt_roles(workflow, object_info, claimed))

    # Resolution + batch size
    for node in workflow.find_by_class(*LATENT_IMAGE_CLASSES):
        literals = node.literals()
        if "width" in literals and "height" in literals:
            for input_name, role in (("width", "resolution_width"), ("height", "resolution_height")):
                claimed.add((node.id, input_name))
                candidates.append(
                    InputCandidate(
                        node_id=node.id,
                        class_type=node.class_type,
                        node_title=node.title,
                        input_name=input_name,
                        current_value=literals[input_name],
                        value_type="int",
                        suggested_field_type="resolution",
                        suggested_field_name="resolution",
                        suggested_label="Image Resolution",
                        role=role,
                        obvious=True,
                    )
                )
        if "batch_size" in literals:
            claimed.add((node.id, "batch_size"))
            candidates.append(
                InputCandidate(
                    node_id=node.id,
                    class_type=node.class_type,
                    node_title=node.title,
                    input_name="batch_size",
                    current_value=literals["batch_size"],
                    value_type="int",
                    suggested_field_type="slider",
                    suggested_field_name="quantity",
                    suggested_label="Batch Size",
                    suggested_config={"min": 1, "max": 8, "step": 1},
                    role="batch_size",
                    obvious=True,
                )
            )
        break  # a workflow has at most one active latent-image source in scope

    # Model loaders
    for classes, role, field_name, label, model_type, input_names in (
        (CHECKPOINT_CLASSES, "checkpoint", "checkpoint", "Checkpoint", "checkpoint", ("ckpt_name",)),
        (
            DIFFUSION_MODEL_CLASSES,
            "diffusion_model",
            "diffusion_model",
            "Diffusion Model (UNET)",
            "diffusion_model",
            ("unet_name",),
        ),
        (CLIP_CLASSES, "clip", "clip", "CLIP Model", "text_encoder", ("clip_name", "clip_name1", "clip_name2")),
        (VAE_CLASSES, "vae", "vae", "VAE Model", "vae", ("vae_name",)),
    ):
        for node in workflow.find_by_class(*classes):
            literals = node.literals()
            matches = [name for name in input_names if name in literals]
            for idx, input_name in enumerate(matches):
                claimed.add((node.id, input_name))
                suffix = "" if idx == 0 else f"_{idx + 1}"
                candidates.append(
                    InputCandidate(
                        node_id=node.id,
                        class_type=node.class_type,
                        node_title=node.title,
                        input_name=input_name,
                        current_value=literals[input_name],
                        value_type="str",
                        suggested_field_type="model",
                        suggested_field_name=f"{field_name}{suffix}",
                        suggested_label=label if not suffix else f"{label} {idx + 1}",
                        suggested_config={"model_type": model_type, "allow_info_modal": True},
                        role=role,
                        obvious=True,
                    )
                )

    # LoRA chain -> one "lora_name" candidate per node, sharing field_name "loras"
    if lora_chain is not None:
        for lora_node_id in lora_chain.lora_node_ids:
            lora_node = workflow.node(lora_node_id)
            if lora_node is None:
                continue
            literals = lora_node.literals()
            if "lora_name" not in literals:
                continue
            claimed.add((lora_node.id, "lora_name"))
            candidates.append(
                InputCandidate(
                    node_id=lora_node.id,
                    class_type=lora_node.class_type,
                    node_title=lora_node.title,
                    input_name="lora_name",
                    current_value=literals["lora_name"],
                    value_type="str",
                    suggested_field_type="lora_picker",
                    suggested_field_name="loras",
                    suggested_label="LoRAs",
                    suggested_config={"model_type": "lora", "max_items": 6},
                    role="lora_slot",
                    obvious=True,
                )
            )

    # Input images
    image_nodes = workflow.find_by_class(*IMAGE_LOADER_CLASSES)
    for idx, node in enumerate(image_nodes):
        literals = node.literals()
        if "image" not in literals:
            continue
        claimed.add((node.id, "image"))
        field_name = "source_image" if idx == 0 else f"ref_image_{idx + 1}"
        label = "Source Image" if idx == 0 else f"Reference Image {idx + 1}"
        candidates.append(
            InputCandidate(
                node_id=node.id,
                class_type=node.class_type,
                node_title=node.title,
                input_name="image",
                current_value=literals["image"],
                value_type="str",
                suggested_field_type="image",
                suggested_field_name=field_name,
                suggested_label=label,
                suggested_config={"formats": [".jpg", ".jpeg", ".png", ".webp"]},
                role="image",
                obvious=idx == 0,
            )
        )

    mode = "img2img" if image_nodes else "txt2img"

    # Everything else literal and configurable
    for node in workflow.nodes.values():
        if node.class_type in SKIP_NODE_CLASSES:
            continue
        for input_name, value in node.literals().items():
            if (node.id, input_name) in claimed:
                continue
            if input_name in _INTERNAL_INPUT_NAMES or input_name.startswith("_"):
                continue
            value_type = _infer_value_type(value)
            if value_type == "unsupported":
                continue

            if value_type == "bool":
                suggested_field_type = "checkbox"
            elif value_type in ("int", "float"):
                suggested_field_type = "number"
            else:
                suggested_field_type = "textbox"

            label = f"{node.title or node.class_type} - {_humanize(input_name)}"
            field_name = f"{node.class_type}_{node.id}_{input_name}".lower()
            candidates.append(
                InputCandidate(
                    node_id=node.id,
                    class_type=node.class_type,
                    node_title=node.title,
                    input_name=input_name,
                    current_value=value,
                    value_type=value_type,
                    suggested_field_type=suggested_field_type,
                    suggested_field_name=field_name,
                    suggested_label=label,
                    role="literal",
                    obvious=False,
                )
            )

    if object_info:
        _enrich_with_object_info(candidates, workflow, object_info)

    return AnalyzeResult(
        candidates=candidates,
        mode=mode,
        node_count=len(workflow.nodes),
        sampler_node_id=sampler.id if sampler else None,
        lora_chain=lora_chain,
    )
