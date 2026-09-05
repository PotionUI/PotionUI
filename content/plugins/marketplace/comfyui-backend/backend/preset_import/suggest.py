"""Structural analysis of a parsed ComfyUI workflow: detect the sampler,
prompts, resolution, model loaders, an optional LoRA chain and any input
images, and suggest a form field for every remaining configurable input.

Detection is structural (follows connections and `node_catalog.yml`'s
declared categories/links), not name-based: a workflow's positive/negative
prompt is whatever is wired into the sampling cluster's own conditioning
inputs, not a node whose title happens to say "prompt". The catalog is what
lets this module recognize a node's role without hard-coding its class name;
see `node_catalog.py`'s module docstring for the schema.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .node_catalog import MODEL_FILE_BY_INPUT_NAME, NodeCatalog, NodeEntry, get_catalog, resolve_model_folder
from .parser import Workflow, WorkflowNode

# `MODEL_FILE_BY_INPUT_NAME` extended with names this module can afford to be
# less conservative about than `node_scaffold.py` (which uses the same role
# string to draft a catalog `role:`, where an ambiguous guess costs a human a
# wrong TODO to fix; here it only ever becomes a `model` field's
# `model_type`, corrected at worst by the admin re-picking it) - see
# `_infer_model_file_field`.
_ENRICHMENT_MODEL_FILE_BY_INPUT_NAME: Dict[str, Tuple[str, str]] = {
    **MODEL_FILE_BY_INPUT_NAME,
    "lora_name": ("lora", "loras"),
    "clip_name": ("text_encoder", "text_encoders"),
    "clip_name1": ("text_encoder", "text_encoders"),
    "clip_name2": ("text_encoder", "text_encoders"),
    "clip_name3": ("text_encoder", "text_encoders"),
    "clip_name4": ("text_encoder", "text_encoders"),
}

# A combo option list this fraction or more of whose values end in a known
# model-file extension is treated as a model-name combo even when its input
# name isn't one this module recognizes (see `_looks_like_model_file_combo`).
_MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")

# Inputs ComfyUI's own UI drives (never present as real user-facing data even
# though they may show up as a literal in an API-format export).
_INTERNAL_INPUT_NAMES = frozenset({"control_after_generate"})

# Candidate text input names on whatever node a prompt-kind link ultimately
# resolves to, checked in preference order.
_PROMPT_TEXT_INPUT_NAMES = ("text", "prompt")

# Roles a catalog-driven candidate is "obvious" (offered in the wizard's
# default form) for even without a catalog `section` - the foundational and
# merged ones (seed, prompts, resolution, batch, LoRA slots). Everything
# else is obvious exactly when its catalog entry gives it a `section`: the
# catalog, not this module, decides what belongs in a starter form.
_OBVIOUS_ROLES = frozenset(
    {
        "seed", "steps", "cfg", "sampler", "scheduler", "denoise", "guidance", "shift",
        "resolution_width", "resolution_height", "batch_size", "frames", "fps",
        "checkpoint", "diffusion_model", "clip", "vae", "lora_slot",
    }
)

_PROMPT_LINK_KINDS = frozenset({"prompt_positive", "prompt_negative"})

# A `min`/`max` this large in an INT/FLOAT `/object_info` spec is ComfyUI's
# own "effectively unbounded" sentinel (e.g. PrimitiveInt's ±2^63), not a
# real slider bound - it survives the JSON round trip through the browser
# rounded to a nearby float (9223372036854776000), which is worse than no
# bound at all. `step` is unaffected.
_UNBOUNDED_BOUND_MAGNITUDE = 2**31

# Fallback (name-based) prompt detection - see `_fallback_prompt_roles`'s
# docstring for why this is the one deliberate exception to this module's
# "structural, not name-based" rule.
_FALLBACK_POSITIVE_INPUT_NAMES = ("prompt", "positive", "positive_prompt")
_FALLBACK_NEGATIVE_INPUT_NAMES = ("negative", "negative_prompt")
_IMAGE_OR_VIDEO_OUTPUT_TYPES = frozenset({"IMAGE", "VIDEO"})

# A prompt-kind link chain longer than this is treated as unresolvable - a
# guard against a cyclic/malformed workflow, never expected in practice.
_MAX_PROMPT_WALK_DEPTH = 8


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
    section: Optional[str] = None
    history: Optional[str] = None
    # The `FieldMapping.transform` a field built from this candidate should
    # default to (see `schema.FieldMapping.transform`) - "none" unless this
    # candidate is a model-file field the wizard should strip the `models/`
    # prefix off of by default (see `_infer_model_file_field`). Catalog-driven
    # model candidates still get their transform from `defaults._model_item`,
    # not this field - it exists for candidates `defaults.py` doesn't already
    # special-case by role.
    suggested_transform: str = "none"
    # The ComfyUI `models/` folder this candidate's value lives under, for a
    # model-file candidate no catalog `InputSpec.folder` already covers (see
    # `emit._infer_requirements`) - `None` for everything else.
    suggested_folder: Optional[str] = None

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
            "section": self.section,
            "history": self.history,
            "suggested_transform": self.suggested_transform,
            "suggested_folder": self.suggested_folder,
        }


@dataclass
class LoraChainNode:
    """One LoRA node found while walking backward from the sampling
    cluster's own model-chain link - see `_detect_lora_chain`.
    `model_source`/`model_consumer` are the connection either side of this
    node along that walk (needed to splice a subset of the chain back
    together when only some nodes are replaced by a `lora_picker` field -
    see `emit._lora_node_manipulations`).

    `strength_clip`/`clip_source`/`clip_consumers` are only ever populated
    for a LoRA node whose catalog entry declares a `clip_chain` link (a
    plain `LoraLoader`, never `LoraLoaderModelOnly`); `clip_consumers` is a
    list, not a single pair, because a CLIP output routinely fans out to
    both the positive and negative `CLIPTextEncode` nodes.
    """

    node_id: str
    class_type: str
    lora_name: Optional[str]
    strength_model: Optional[float]
    strength_clip: Optional[float]
    model_source: Tuple[str, int]
    model_consumer: Tuple[str, str]
    clip_source: Optional[Tuple[str, int]] = None
    clip_consumers: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class LoraChainInfo:
    source_node_id: str
    target_node_id: str
    lora_node_ids: List[str]  # source -> target order
    nodes: List[LoraChainNode] = field(default_factory=list)  # source -> target order
    has_clip_path: bool = False


@dataclass
class ModelChainInfo:
    """The single connection feeding the sampling cluster's own model-chain
    input (`_model_chain_start`), independent of whether anything along it
    is a LoRA node - unlike `LoraChainInfo` (which walks all the way back to
    the loader and is `None` when the walk finds no `lora`-category node),
    this is populated whenever the cluster consumes a model at all, so a
    `lora_picker` field can be spliced in even for a workflow with no LoRA
    node, or with every existing LoRA node kept fixed."""

    source_node_id: str
    source_output_index: int
    target_node_id: str
    target_input: str


@dataclass
class AnalyzeResult:
    candidates: List[InputCandidate]
    mode: str
    node_count: int
    sampler_node_id: Optional[str]
    lora_chain: Optional[LoraChainInfo]
    model_chain: Optional[ModelChainInfo] = None
    sampling_cluster_node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "node_count": self.node_count,
            "sampler_node_id": self.sampler_node_id,
            "sampling_cluster_node_ids": self.sampling_cluster_node_ids,
            "model_chain": (
                {
                    "source_node_id": self.model_chain.source_node_id,
                    "source_output_index": self.model_chain.source_output_index,
                    "target_node_id": self.model_chain.target_node_id,
                    "target_input": self.model_chain.target_input,
                }
                if self.model_chain
                else None
            ),
            "lora_chain": (
                {
                    "source_node_id": self.lora_chain.source_node_id,
                    "target_node_id": self.lora_chain.target_node_id,
                    "lora_node_ids": self.lora_chain.lora_node_ids,
                    "has_clip_path": self.lora_chain.has_clip_path,
                    "nodes": [
                        {
                            "node_id": n.node_id,
                            "class_type": n.class_type,
                            "lora_name": n.lora_name,
                            "strength_model": n.strength_model,
                            "strength_clip": n.strength_clip,
                        }
                        for n in self.lora_chain.nodes
                    ],
                }
                if self.lora_chain
                else None
            ),
            "candidates": [c.to_dict() for c in self.candidates],
        }


def classification_fingerprint(analysis: AnalyzeResult) -> str:
    """A stable hash of the structural facts `emit.emit_preset` actually
    depends on: `mode`, the sampling cluster's model-chain/LoRA-chain
    identity, and each candidate's `(node_id, input_name, role,
    suggested_field_type, suggested_folder)` - the classification a mapping's
    legality (`schema.validate_against_workflow`'s prompt-target rejection)
    and a model file's `comfyui_model` requirement (`emit._infer_requirements`'s
    `suggested_folder` reads) are decided from.

    Two analyses of the same workflow that resolve the same way hash equal
    regardless of whether `object_info` happened to be present for both;
    deliberately blind to cosmetic-only enrichment (live combo option lists,
    numeric min/max/step) that changes a field's presentation but not what
    gets wired or required. A mismatch between what a client was shown
    (`/presets/import/analyze` or `.../source`) and what saving would now
    produce means the workflow's own classification drifted in between - see
    `emit._check_schema_drift`, the only place this is compared."""
    signature = {
        "mode": analysis.mode,
        "sampler_node_id": analysis.sampler_node_id,
        "model_chain": (
            [
                analysis.model_chain.source_node_id,
                analysis.model_chain.source_output_index,
                analysis.model_chain.target_node_id,
                analysis.model_chain.target_input,
            ]
            if analysis.model_chain
            else None
        ),
        "lora_chain_node_ids": analysis.lora_chain.lora_node_ids if analysis.lora_chain else None,
        "candidates": sorted(
            (c.node_id, c.input_name, c.role, c.suggested_field_type, c.suggested_folder or "")
            for c in analysis.candidates
        ),
    }
    payload = json.dumps(signature, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _is_hidden_category(catalog: NodeCatalog, node: WorkflowNode) -> bool:
    """Whether `node` never carries a configurable input worth surfacing -
    an output/preview node, or a note the author left for humans."""
    entry = catalog.get(node.class_type)
    return entry is not None and entry.category in ("output", "ignore")


def _find_sampler(workflow: Workflow, catalog: NodeCatalog) -> Optional[WorkflowNode]:
    for node in workflow.nodes.values():
        entry = catalog.get(node.class_type)
        if entry is not None and entry.category == "sampler":
            return node
    # Structural fallback for a sampler class this catalog doesn't name: any
    # node wired to both a positive and a negative conditioning.
    for node in workflow.nodes.values():
        conns = node.connections()
        if "positive" in conns and "negative" in conns:
            return node
    return None


def _sampling_cluster(workflow: Workflow, catalog: NodeCatalog, sampler: WorkflowNode) -> Dict[str, WorkflowNode]:
    """`{node_id: node}`, in discovery order, for the sampler itself plus
    every node reachable by following its (and each further node's) own
    `sampling`-kind links - the sampler's noise/guider/sampler-selector/
    sigmas graph, however many hops of `sampling`-category nodes deep."""
    cluster: Dict[str, WorkflowNode] = {}

    def visit(node: WorkflowNode) -> None:
        if node.id in cluster:
            return
        cluster[node.id] = node
        entry = catalog.get(node.class_type)
        if entry is None:
            return
        for input_name, link_kind in entry.links.items():
            if link_kind != "sampling":
                continue
            conn = node.connection_source(input_name)
            if conn is None:
                continue
            target = workflow.resolve(conn)
            if target is None:
                continue
            target_entry = catalog.get(target.class_type)
            if target_entry is not None and target_entry.category == "sampling":
                visit(target)

    visit(sampler)
    return cluster


def _unique_field_name(base: str, used: Dict[str, int]) -> Tuple[str, int]:
    """`(name, count)` - `base` unchanged the first time it's requested,
    `base_2`/`base_3`/... after - the field-name dedup `suggest_fields`
    applies across the WHOLE analysis (not per node, the way a single
    multi-input node's own suffixing worked before this catalog existed):
    two different loader nodes both wanting "vae" must not collide."""
    count = used.get(base, 0) + 1
    used[base] = count
    return (base if count == 1 else f"{base}_{count}"), count


def _catalog_candidates_for_node(
    node: WorkflowNode,
    entry: NodeEntry,
    claimed: set,
    used_field_names: Dict[str, int],
) -> List[InputCandidate]:
    """One `InputCandidate` per literal input on `node` the catalog declares
    a spec for, deduping the suggested field name across the whole analysis
    (see `_unique_field_name`) - except a `lora_slot` input (always shares
    its catalog name verbatim so every LoRA node in a chain collapses onto
    the same `loras` field) or a `resolution_width`/`resolution_height` one
    (the pair is always merged into one `resolution` field by
    `defaults._resolution_item`, never counted against each other)."""
    literals = node.literals()
    candidates: List[InputCandidate] = []
    for input_name, spec in entry.inputs.items():
        if input_name not in literals or (node.id, input_name) in claimed:
            continue
        claimed.add((node.id, input_name))
        if spec.role in ("lora_slot", "resolution_width", "resolution_height"):
            field_name, count = spec.name, 1
        else:
            field_name, count = _unique_field_name(spec.name, used_field_names)
        label = spec.label if count == 1 else f"{spec.label} {count}"
        candidates.append(
            InputCandidate(
                node_id=node.id,
                class_type=node.class_type,
                node_title=node.title,
                input_name=input_name,
                current_value=literals[input_name],
                value_type=_infer_value_type(literals[input_name]),
                suggested_field_type=spec.field,
                suggested_field_name=field_name,
                suggested_label=label,
                suggested_config=dict(spec.config),
                suggested_transform=spec.transform,
                role=spec.role,
                obvious=spec.role in _OBVIOUS_ROLES or spec.section is not None,
                section=spec.section,
                history=spec.history,
            )
        )
    return candidates


def _prompt_text_input(node: WorkflowNode) -> Optional[str]:
    literals = node.literals()
    for name in _PROMPT_TEXT_INPUT_NAMES:
        if name in literals and isinstance(literals[name], str):
            return name
    return None


def _branch_selected_input(node: WorkflowNode, entry: NodeEntry) -> Optional[str]:
    """Which of `entry.branch`'s `on_true`/`on_false` passthrough inputs
    `node` selects (see `node_catalog.NodeEntry.branch`) - the branch
    input's own literal truthiness, or `on_true` when that input is
    connected to something else or missing from `node` entirely (a runtime
    switch's value not knowable ahead of generation defaults to the "keep
    going" side rather than dead-ending the walk). `None` when `entry`
    declares no `branch` at all."""
    if entry.branch is None:
        return None
    literals = node.literals()
    if entry.branch.input in literals:
        return entry.branch.on_true if literals[entry.branch.input] else entry.branch.on_false
    return entry.branch.on_true


def _forward_link_input(catalog: NodeCatalog, node: WorkflowNode, link_kind: str) -> Optional[str]:
    """The input name on `node` a `link_kind`-kind connection continues
    through - the input `node_catalog.yml` declares with that exact link
    kind, or, when `node` has none but declares a `branch` over two
    `passthrough` inputs instead (a runtime switch), whichever of those the
    branch selects (a switch is untyped, so it forwards a model chain, a
    prompt walk, or anything else the same way). `None` when `node` isn't
    catalogued, or is catalogued but forwards neither."""
    entry = catalog.get(node.class_type)
    if entry is None:
        return None
    for input_name, kind in entry.links.items():
        if kind == link_kind:
            return input_name
    selected = _branch_selected_input(node, entry)
    if selected is not None and entry.links.get(selected) == "passthrough":
        return selected
    return None


def _resolve_prompt_text_node(
    workflow: Workflow,
    catalog: NodeCatalog,
    conn: Tuple[str, int],
    link_kind: str,
    depth: int = 0,
) -> Optional[Tuple[WorkflowNode, str]]:
    """Follow a `prompt_positive`/`prompt_negative`-kind connection to the
    text node it ultimately resolves to, through any number of intermediate
    nodes (e.g. `FluxGuidance`, or a runtime switch - see
    `_forward_link_input`) that forward a conditioning of the same kind
    without originating it themselves - a node with its own `text`/`prompt`
    literal always wins outright, even if its catalog entry ALSO declares a
    same-kind outgoing link (there is nothing further to follow past the
    actual prompt text)."""
    if depth > _MAX_PROMPT_WALK_DEPTH:
        return None
    node = workflow.resolve(conn)
    if node is None:
        return None
    text_input = _prompt_text_input(node)
    if text_input is not None:
        return node, text_input
    forward_input = _forward_link_input(catalog, node, link_kind)
    if forward_input is None:
        return None
    next_conn = node.connection_source(forward_input)
    if next_conn is None:
        return None
    return _resolve_prompt_text_node(workflow, catalog, next_conn, link_kind, depth + 1)


def _find_clip_consumers(workflow: Workflow, node_id: str) -> List[Tuple[str, str]]:
    """`[(consumer_node_id, input_name), ...]` for every input in the whole
    workflow wired to `node_id`'s CLIP output (index 1 - a `LoraLoader`'s
    `RETURN_TYPES` is `("MODEL", "CLIP")`). Usually two: the positive and
    negative `CLIPTextEncode` nodes."""
    consumers: List[Tuple[str, str]] = []
    for other in workflow.nodes.values():
        for input_name, (src_id, src_index) in other.connections().items():
            if src_id == node_id and src_index == 1:
                consumers.append((other.id, input_name))
    return consumers


def _model_chain_start(
    catalog: NodeCatalog, cluster: Dict[str, WorkflowNode]
) -> Optional[Tuple[WorkflowNode, str, Tuple[str, int]]]:
    """`(node, input_name, connection)` for the first `model_chain`-linked
    input, connected to something, on any node in the sampling cluster (in
    its own discovery order) - the sampler itself for a plain `KSampler`,
    since it IS the cluster's only member and carries its own `model` link;
    a guider or scheduler reached via the `sampling` link walk for a
    `SamplerCustomAdvanced`-style graph, which has no `model` input of its
    own. `None` if nothing in the cluster consumes a model at all."""
    for node in cluster.values():
        entry = catalog.get(node.class_type)
        if entry is None:
            continue
        for input_name, link_kind in entry.links.items():
            if link_kind != "model_chain":
                continue
            conn = node.connection_source(input_name)
            if conn is not None:
                return node, input_name, conn
    return None


def _detect_lora_chain(
    workflow: Workflow,
    catalog: NodeCatalog,
    start: Tuple[WorkflowNode, str, Tuple[str, int]],
) -> Optional[LoraChainInfo]:
    """Walk backward from the sampling cluster's own model-chain input
    (`start`, from `_model_chain_start`), collecting every `lora`-category
    node found along the way - through any number of pass-through patcher
    nodes (`ModelSamplingAuraFlow`, `CFGNorm`, `FreeU`, ..., and a runtime
    switch between two model sources - see `_forward_link_input`), not just
    an unbroken run of LoRA nodes back to back. The walk stops at the first
    node with nothing further to follow - the loader (`CheckpointLoaderSimple`/
    `UNETLoader`/...), or an uncatalogued node with no connected `model`
    input (the historical, catalog-independent default this falls back to).
    Contiguity among the LoRA nodes themselves is not required, and
    collecting a node as a LoRA depends only on its OWN catalog category at
    the moment it's visited, not on any category the walk itself is
    following."""
    start_node, start_input, conn = start

    nodes: List[LoraChainNode] = []  # target -> source order while walking
    consumer: Tuple[str, str] = (start_node.id, start_input)
    last_source_conn = conn
    current = workflow.resolve(conn)

    while current is not None:
        current_entry = catalog.get(current.class_type)
        own_input = _forward_link_input(catalog, current, "model_chain") or "model"
        own_source = current.connection_source(own_input)
        is_lora = current_entry is not None and current_entry.category == "lora"
        if is_lora:
            has_clip = current_entry.links.get("clip") == "clip_chain"
            literals = current.literals()
            nodes.append(
                LoraChainNode(
                    node_id=current.id,
                    class_type=current.class_type,
                    lora_name=literals.get("lora_name"),
                    strength_model=literals.get("strength_model"),
                    strength_clip=literals.get("strength_clip") if has_clip else None,
                    model_source=own_source if own_source is not None else last_source_conn,
                    model_consumer=consumer,
                    clip_source=current.connection_source("clip") if has_clip else None,
                    clip_consumers=_find_clip_consumers(workflow, current.id) if has_clip else [],
                )
            )
        if own_source is None:
            break  # current has nothing further upstream to follow - it's the loader
        consumer = (current.id, own_input)
        last_source_conn = own_source
        current = workflow.resolve(own_source)

    if not nodes:
        return None

    nodes.reverse()  # source -> target order
    source_node_id = current.id if current is not None else last_source_conn[0]
    has_clip_path = any(n.clip_source is not None for n in nodes)
    return LoraChainInfo(
        source_node_id=source_node_id,
        target_node_id=start_node.id,
        lora_node_ids=[n.node_id for n in nodes],
        nodes=nodes,
        has_clip_path=has_clip_path,
    )


def _off_chain_lora_candidates(
    workflow: Workflow,
    catalog: NodeCatalog,
    lora_chain: Optional[LoraChainInfo],
    claimed: set,
    used_field_names: Dict[str, int],
) -> List[InputCandidate]:
    """A `lora`-category node the detected chain (if any) doesn't
    include - a second sampler's own chain, or a LoRA feeding something the
    model backbone never reaches - still needs its `lora_name` offered as a
    real field, since nothing else will ever surface it: unlike a chain
    node's (claimed for the `lora_picker` conversion by `suggest_fields`'s
    own LoRA-chain loop before this runs), it's rendered as a plain `model`
    field, not `lora_picker` - there is no chain here to convert, just one
    specific file to pick. Its `strength_model`/`strength_clip` inputs are
    left to the ordinary catalog pass right after (not claimed here), which
    picks them up as regular sliders now that the two LoRA node entries
    declare them."""
    chain_ids = set(lora_chain.lora_node_ids) if lora_chain is not None else set()
    candidates: List[InputCandidate] = []
    for node in workflow.nodes.values():
        if node.id in chain_ids:
            continue
        entry = catalog.get(node.class_type)
        if entry is None or entry.category != "lora":
            continue
        literals = node.literals()
        if "lora_name" not in literals or (node.id, "lora_name") in claimed:
            continue
        claimed.add((node.id, "lora_name"))
        field_name, count = _unique_field_name("lora", used_field_names)
        label = node.title or "LoRA"
        if count > 1:
            label = f"{label} {count}"
        candidates.append(
            InputCandidate(
                node_id=node.id,
                class_type=node.class_type,
                node_title=node.title,
                input_name="lora_name",
                current_value=literals["lora_name"],
                value_type="str",
                suggested_field_type="model",
                suggested_field_name=field_name,
                suggested_label=label,
                suggested_config={"model_type": "lora", "allow_info_modal": True},
                suggested_transform="strip_model_prefix",
                suggested_folder="loras",
                role="lora_file",
                obvious=True,
                section="Models",
            )
        )
        candidates.extend(_catalog_candidates_for_node(node, entry, claimed, used_field_names))
    return candidates


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


def _model_file_type_for_combo(class_type: str, input_name: str) -> Optional[Tuple[str, str]]:
    """`(model_type, folder)` for a combo confidently identifiable as a
    model-file picker from its input name alone (and, for the one ambiguous
    name below, its owning class) - `None` when nothing here is sure enough
    to guess (see `_looks_like_model_file_combo` for the values-based
    fallback `_enrich_with_object_info` tries next)."""
    if input_name in _ENRICHMENT_MODEL_FILE_BY_INPUT_NAME:
        return _ENRICHMENT_MODEL_FILE_BY_INPUT_NAME[input_name]
    if input_name == "model_name" and "upscale" in class_type.lower():
        return "upscaler", "upscale_models"
    return None


def _looks_like_model_file_combo(options: List[Any]) -> bool:
    """Whether at least half of `options` end in a known model-file
    extension - a combo this consistently full of filenames is a model
    picker even when nothing about its input name says so, and must never
    become a `select` field baking the live server's whole model directory
    into the preset as a static option list (see `_enrich_with_object_info`)."""
    values = [v for v in options if isinstance(v, str)]
    if not values:
        return False
    matches = sum(1 for v in values if v.lower().endswith(_MODEL_FILE_EXTENSIONS))
    return matches * 2 >= len(values)


# Types a combo option is trusted to be - anything else (a nested list/dict
# shape, `None`, ...) means this importer doesn't understand what the widget
# actually is, so `_combo_options` refuses the whole list rather than
# guessing (see its own docstring).
_COMBO_OPTION_SCALAR_TYPES = (str, int, float, bool)


def _combo_options(type_spec: Any, config: Dict[str, Any]) -> Optional[List[Any]]:
    """The resolved option list for a combo input, under either of ComfyUI's
    two equivalent `/object_info` spellings - the inline
    `[[opt1, opt2, ...], {...}]` form, or the named
    `["COMBO", {"options": [...]}]` form (`comfy/comfy_types/node_typing.py`'s
    own preferred spelling; `node_scaffold.extract_from_object_info` already
    normalizes both the same way for catalogue drafts, this is the
    suggestion path's equivalent).

    `None` for anything this can't safely resolve to a static, complete
    option list - not a combo at all, `options` missing/not a list/empty, an
    option that isn't a plain scalar (a nested widget shape this importer
    doesn't model), or a remotely-populated combo (a `remote` config key -
    ComfyUI's own UI fetches those from a live endpoint at edit time, which
    this importer never does). Every `None` case is the caller's cue to
    leave the candidate as its generic literal-field guess - never invent
    options, never guess at a dynamic combo's real choices."""
    if "remote" in config:
        return None
    if isinstance(type_spec, (list, tuple)):
        options = list(type_spec)
    elif type_spec == "COMBO":
        raw = config.get("options")
        if not isinstance(raw, (list, tuple)):
            return None
        options = list(raw)
    else:
        return None
    if not options:
        return None
    if any(not isinstance(v, _COMBO_OPTION_SCALAR_TYPES) for v in options):
        return None
    return options


def _enrich_with_object_info(
    candidates: List[InputCandidate], workflow: Workflow, object_info: Dict[str, Any]
) -> None:
    """Sharpen each candidate's suggested field using the live server's own
    declared input schema: real min/max/step for a slider, a checkbox for a
    boolean, multiline for a text area, and - for a combo - either the real
    option list (this importer's generic `select` guess) or, when the combo
    is a model-file picker (by input name, or because most of its own option
    values look like model filenames - see `_model_file_type_for_combo`/
    `_looks_like_model_file_combo`), a `model` field with no static options
    at all: baking the live server's whole model directory into the preset
    as a giant `select` is exactly the failure mode this guards against.
    Independent of which structural role (if any) the candidate already
    has."""
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

        combo_options = _combo_options(type_spec, config)
        if combo_options is not None:
            if candidate.suggested_field_type in ("model", "lora_picker"):
                pass  # already a catalog/chain-driven model field - leave it alone
            else:
                model_file = _model_file_type_for_combo(node.class_type, candidate.input_name)
                if model_file is None and _looks_like_model_file_combo(combo_options):
                    # Sure it's a model file, not sure which folder - "checkpoint"
                    # is this field's own default `model_type` (src/features/
                    # fields/model.py) when none is set, and no `folder` means
                    # no `comfyui_model` requirement gets emitted for it.
                    model_file = ("checkpoint", None)
                if model_file is not None:
                    model_type, folder = model_file
                    candidate.suggested_field_type = "model"
                    candidate.suggested_config = {"model_type": model_type, "allow_info_modal": True}
                    candidate.suggested_transform = "strip_model_prefix"
                    # A GGUF loader's own selected file (or, on a GGUF CLIP
                    # loader that can mix an ordinary and a GGUF encoder
                    # across its own separate clip_name*/inputs, THIS
                    # input's own selection) is invisible under the ordinary
                    # folder name - see node_catalog.resolve_model_folder.
                    candidate.suggested_folder = resolve_model_folder(folder, candidate.current_value)
                else:
                    # The real option list replaces a generic "select" guess's
                    # config outright - a static `file:` pointer and a live
                    # `options:` list would otherwise both be present at once.
                    candidate.suggested_field_type = "select"
                    candidate.suggested_config = {"options": combo_options}
        elif type_spec in ("INT", "FLOAT"):
            numeric_config = {k: config[k] for k in ("min", "max", "step") if k in config}
            for bound_key in ("min", "max"):
                bound = numeric_config.get(bound_key)
                if isinstance(bound, (int, float)) and abs(bound) >= _UNBOUNDED_BOUND_MAGNITUDE:
                    del numeric_config[bound_key]
            if numeric_config:
                # The server's own bounds are authoritative over this
                # importer's generic slider guess.
                candidate.suggested_config = {**candidate.suggested_config, **numeric_config}
                if (
                    candidate.suggested_field_type == "number"
                    and "min" in numeric_config
                    and "max" in numeric_config
                ):
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
    workflow: Workflow, catalog: NodeCatalog, object_info: Optional[Dict[str, Any]], claimed: set
) -> List["InputCandidate"]:
    """Prompt detection by input name, used only when the structural
    (sampling-cluster) detection above found no positive/negative at all -
    an all-in-one node with its own baked-in sampling (Krea2ImageNode, no
    separate sampler to follow a conditioning link from) still has a real
    prompt input, and it must never be offered as a choosable form field:
    prompts always come from the Prompts section, never the dynamic form
    (see emit.py's module docstring on foundational fields).

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
        if _is_hidden_category(catalog, node):
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


def _latent_source_node(
    workflow: Workflow, catalog: NodeCatalog, sampler: Optional[WorkflowNode]
) -> Optional[WorkflowNode]:
    """The `latent`-category node the sampler's own `latent`-kind link
    resolves to, when there is one. Falls back to the first `latent`-
    category node anywhere in the workflow - the existing img2img-preset
    path, where the source latent comes from a `VAEEncode` (not itself
    catalogued) rather than an `EmptyLatentImage`/`EmptySD3LatentImage`, or
    where there is no sampler at all to carry the link."""
    if sampler is not None:
        entry = catalog.get(sampler.class_type)
        if entry is not None:
            for input_name, link_kind in entry.links.items():
                if link_kind != "latent":
                    continue
                conn = sampler.connection_source(input_name)
                if conn is None:
                    continue
                target = workflow.resolve(conn)
                if target is None:
                    continue
                target_entry = catalog.get(target.class_type)
                if target_entry is not None and target_entry.category == "latent":
                    return target
    for node in workflow.nodes.values():
        entry = catalog.get(node.class_type)
        if entry is not None and entry.category == "latent":
            return node
    return None


def suggest_fields(
    workflow: Workflow,
    *,
    object_info: Optional[Dict[str, Any]] = None,
) -> AnalyzeResult:
    catalog = get_catalog()
    candidates: List[InputCandidate] = []
    claimed: set = set()  # (node_id, input_name) already covered by a structural role
    used_field_names: Dict[str, int] = {}

    sampler = _find_sampler(workflow, catalog)
    cluster: Dict[str, WorkflowNode] = _sampling_cluster(workflow, catalog, sampler) if sampler else {}
    model_chain_start = _model_chain_start(catalog, cluster) if cluster else None
    model_chain = (
        ModelChainInfo(
            source_node_id=model_chain_start[2][0],
            source_output_index=model_chain_start[2][1],
            target_node_id=model_chain_start[0].id,
            target_input=model_chain_start[1],
        )
        if model_chain_start is not None
        else None
    )
    lora_chain = _detect_lora_chain(workflow, catalog, model_chain_start) if model_chain_start else None

    # Loaders, image inputs and modifiers - scanned across the whole
    # workflow, independent of the sampling cluster: a loader or an inline
    # patch node (FluxGuidance, ModelSamplingFlux, ...) contributes its
    # catalogued inputs wherever it sits in the graph. Enumerated before the
    # sampling cluster below so a default form's sections come out "Models
    # before Sampling" (`defaults.build_default_form` groups by each
    # candidate's section in first-seen order) without needing a hardcoded
    # section priority.
    for node in workflow.nodes.values():
        entry = catalog.get(node.class_type)
        if entry is None or entry.category not in ("loader", "image_input", "modifier"):
            continue
        candidates.extend(_catalog_candidates_for_node(node, entry, claimed, used_field_names))

    # Sampling cluster - every cluster node's own catalogued inputs (seed,
    # steps, cfg, sampler, scheduler, denoise, ...), wherever in the cluster
    # they actually live.
    for node in cluster.values():
        entry = catalog.get(node.class_type)
        if entry is None:
            continue
        candidates.extend(_catalog_candidates_for_node(node, entry, claimed, used_field_names))

    # Extra sampler-category nodes outside the primary sampler's own cluster
    # - a base+refiner two-stage pipeline's second KSampler(Advanced), for
    # instance. `_find_sampler`/`_sampling_cluster` only ever walk from ONE
    # sampler, so without this such a node's literals would fall through to
    # the generic "everything else literal" pass below instead of using its
    # catalogued field types/config. Added after the cluster loop (not the
    # loader/image_input/modifier one above) so it never disturbs that
    # loop's "Models before Sampling" section-ordering guarantee.
    for node in workflow.nodes.values():
        if node.id in cluster:
            continue
        entry = catalog.get(node.class_type)
        if entry is None or entry.category != "sampler":
            continue
        candidates.extend(_catalog_candidates_for_node(node, entry, claimed, used_field_names))

    # Prompts - the first prompt_positive/prompt_negative-kind link found on
    # any cluster node, followed through to the text node it resolves to.
    for role, link_kind in (("prompt_positive", "prompt_positive"), ("prompt_negative", "prompt_negative")):
        resolved = None
        for node in cluster.values():
            entry = catalog.get(node.class_type)
            if entry is None:
                continue
            for input_name, kind in entry.links.items():
                if kind != link_kind:
                    continue
                conn = node.connection_source(input_name)
                if conn is None:
                    continue
                resolved = _resolve_prompt_text_node(workflow, catalog, conn, link_kind)
                if resolved is not None:
                    break
            if resolved is not None:
                break
        if resolved is None:
            continue
        prompt_node, text_input = resolved
        if (prompt_node.id, text_input) in claimed:
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
        candidates.extend(_fallback_prompt_roles(workflow, catalog, object_info, claimed))

    # Resolution + batch size (+ frames/fps, for a video latent) - from the
    # sampler's own latent source when there is one, else the first
    # latent-category node anywhere (the img2img path).
    latent_node = _latent_source_node(workflow, catalog, sampler)
    if latent_node is not None:
        latent_entry = catalog.get(latent_node.class_type)
        if latent_entry is not None:
            candidates.extend(_catalog_candidates_for_node(latent_node, latent_entry, claimed, used_field_names))

    # LoRA chain -> one "lora_name" candidate per node, sharing field_name
    # "loras". Its strength_model/strength_clip inputs are pre-claimed
    # (never left to _catalog_candidates_for_node below) so a chain node
    # never doubles up as an individual slider field on top of the
    # lora_picker conversion the chain is for - see _off_chain_lora_candidates
    # for the node that DOES want those as real fields.
    if lora_chain is not None:
        for lora_node_id in lora_chain.lora_node_ids:
            lora_node = workflow.node(lora_node_id)
            if lora_node is None:
                continue
            lora_entry = catalog.get(lora_node.class_type)
            if lora_entry is None:
                continue
            claimed.add((lora_node.id, "strength_model"))
            claimed.add((lora_node.id, "strength_clip"))
            candidates.extend(_catalog_candidates_for_node(lora_node, lora_entry, claimed, used_field_names))

    candidates.extend(_off_chain_lora_candidates(workflow, catalog, lora_chain, claimed, used_field_names))

    # Input images - "the first LoadImage is the obvious source image, any
    # further ones are optional reference images" isn't something a static
    # catalog entry can express (it depends on how many the WORKFLOW has),
    # so it stays this module's own numbering on top of the catalog's plain
    # `image` role/field.
    image_candidates = [c for c in candidates if c.role == "image"]
    for idx, candidate in enumerate(image_candidates):
        if idx == 0:
            candidate.suggested_field_name = "source_image"
            candidate.suggested_label = "Source Image"
            candidate.obvious = True
        else:
            candidate.suggested_field_name = f"ref_image_{idx + 1}"
            candidate.suggested_label = f"Reference Image {idx + 1}"
            candidate.obvious = False

    mode = "img2img" if image_candidates else "txt2img"

    # Everything else literal and configurable
    for node in workflow.nodes.values():
        if _is_hidden_category(catalog, node):
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
        model_chain=model_chain,
        sampling_cluster_node_ids=list(cluster.keys()),
    )
