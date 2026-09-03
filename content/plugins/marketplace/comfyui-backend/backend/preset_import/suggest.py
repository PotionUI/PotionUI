"""Structural analysis of a parsed ComfyUI workflow: detect the sampler,
prompts, resolution, model loaders, an optional LoRA chain and any input
images, and suggest a form field for every remaining configurable input.

Detection is structural (follows connections), not name-based: a workflow's
positive/negative prompt is whatever is wired into the sampler's `positive`/
`negative` inputs, not a node whose title happens to say "prompt".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


def suggest_fields(workflow: Workflow) -> AnalyzeResult:
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
        (CLIP_CLASSES, "clip", "clip", "CLIP Model", "clip", ("clip_name", "clip_name1", "clip_name2")),
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

    return AnalyzeResult(
        candidates=candidates,
        mode=mode,
        node_count=len(workflow.nodes),
        sampler_node_id=sampler.id if sampler else None,
        lora_chain=lora_chain,
    )
