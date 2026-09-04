"""Declarative catalog of built-in ComfyUI node classes: what role a class
plays in the generation graph (`category`), how its connected inputs relate
to neighboring nodes (`links`), and how its literal (widget) inputs map onto
importer roles/form fields (`inputs`). `suggest.py`'s structural analysis
walks the workflow graph generically against this data instead of naming
node classes directly - see `node_catalog.yml` for the data and the schema
below for the shape it must hold.

`links` values are one of:

- `prompt_positive`/`prompt_negative` - this input is (or forwards) a
  positive/negative prompt conditioning; `suggest.py` follows it, through any
  number of intermediate nodes declaring the same link kind, to the text
  node it ultimately resolves to.
- `model_chain`/`clip_chain` - this input carries a MODEL/CLIP that a LoRA
  chain can be spliced into.
- `latent` - this input is the sampler's latent source; when that source is
  a `latent`-category node, its own catalogued inputs (resolution, batch
  size, ...) become candidates.
- `sampling` - this input is part of the sampler's own configuration graph
  (noise, guider, sampler selection, sigmas); `suggest.py` walks these
  recursively from the sampler to build the "sampling cluster".
- `passthrough` - this input forwards whatever kind of connection reached the
  node (a model/clip chain, a prompt walk, ...) onward, unchanged - a runtime
  switch's `on_true`/`on_false`, not a link kind of its own. Which
  passthrough input a walk actually follows is decided by the entry's own
  `branch` (below) when one is declared; a passthrough input outside a
  `branch` is never chosen and the walk stops.

A `NodeEntry` may also declare `branch`: `{input, on_true, on_false}`, where
`input` names a literal (a `switch`-style boolean widget) and `on_true`/
`on_false` name two of the entry's own `passthrough`-tagged inputs. Following
a link through this node resolves to `on_true` or `on_false` by the branch
input's own value in the workflow being imported - the literal's truthiness
when it's a literal, `on_true` when it's connected to something else or
missing entirely (an unresolvable condition defaults to the "normal" path).

`inputs` values (`InputSpec`) describe one literal (non-connection) input:
the importer `role` it plays (validated against the closed `ROLES` set), the
core `field` type it should render as (`src/features/fields/builtin.py`),
the suggested form `name`/`label`, an optional `config` for the field, an
optional `transform` (`schema.FieldMapping.transform`, minus the
resolution-only split_wh_* pair which `defaults._resolution_item` applies on
its own), an optional `section` for default-form placement, an optional
`history` format override, and - for a model-file role - the ComfyUI
`models/` `folder` it lives under (needed to emit a `comfyui_model`
requirement).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

CATEGORIES = frozenset(
    {"loader", "sampler", "sampling", "latent", "image_input", "modifier", "lora", "output", "ignore"}
)

ROLES = frozenset(
    {
        "seed", "steps", "cfg", "sampler", "scheduler", "denoise", "guidance", "shift",
        "prompt_positive", "prompt_negative", "resolution_width", "resolution_height",
        "batch_size", "frames", "fps", "checkpoint", "diffusion_model", "clip", "vae",
        "clip_vision", "controlnet", "upscale_model", "style_model", "lora_slot",
        "lora_strength_model", "lora_strength_clip", "image", "video", "audio", "mask",
        "option",
    }
)

# Model-file roles - an `InputSpec` carrying one of these must declare `folder`
# (see `validate_catalog`), since that's what a `comfyui_model` requirement
# needs to point at the right `models/` subfolder.
MODEL_FILE_ROLES = frozenset(
    {"checkpoint", "diffusion_model", "clip", "vae", "clip_vision", "controlnet", "upscale_model", "style_model", "lora_slot"}
)

LINK_KINDS = frozenset(
    {"prompt_positive", "prompt_negative", "model_chain", "clip_chain", "latent", "sampling", "passthrough"}
)

TRANSFORMS = frozenset({"none", "strip_model_prefix", "seed"})

HISTORY_FORMATS = frozenset({"model_name", "number", "wxh", "list", "as_is"})

# Core field types an InputSpec may reference - see
# src/features/fields/builtin.py's registered definitions. Layout/container
# types (tabs, row, group, ...) are deliberately excluded: a catalog input
# spec describes one leaf field, never a container.
FIELD_TYPES = frozenset(
    {
        "string", "textbox", "number", "integer", "boolean", "checkbox", "slider", "stepper",
        "seed", "resolution", "select", "checkbox_group", "model", "models", "lora_picker",
        "image", "video", "audio", "media", "file", "carousel", "llm", "alert", "markdown",
        "header", "section", "gate", "prompt_timeline", "camera_shot",
    }
)


class InputSpec(BaseModel):
    role: str
    field: str
    name: str
    label: str
    config: Dict[str, Any] = Field(default_factory=dict)
    transform: str = "none"
    section: Optional[str] = None
    history: Optional[str] = None
    folder: Optional[str] = None


class BranchSpec(BaseModel):
    input: str
    on_true: str
    on_false: str


class NodeEntry(BaseModel):
    category: str
    links: Dict[str, str] = Field(default_factory=dict)
    inputs: Dict[str, InputSpec] = Field(default_factory=dict)
    branch: Optional[BranchSpec] = None


class NodeCatalog(BaseModel):
    version: int
    nodes: Dict[str, NodeEntry]

    def get(self, class_type: str) -> Optional[NodeEntry]:
        return self.nodes.get(class_type)

    @property
    def classes(self) -> List[str]:
        return list(self.nodes.keys())

    def by_category(self, category: str) -> List[str]:
        return [class_type for class_type, entry in self.nodes.items() if entry.category == category]


# Combo input name -> (catalog `role`, `models/` folder) for the small set
# of *_name widgets confidently identifiable by name alone, independent of
# which node class carries them - shared between `node_scaffold.py` (uses
# the role to draft a catalog `InputSpec`) and `suggest.py`'s live-object-info
# enrichment (uses the role as a `model` field's `model_type`; these two
# meanings coincide for every name below). A name not listed here still gets
# handled - `node_scaffold._infer_model_file` falls back to its own
# class-name-aware guesses, and `suggest.py` extends this mapping with a few
# more (`lora_name`, `clip_name*`) it can afford to be less conservative
# about.
MODEL_FILE_BY_INPUT_NAME: Dict[str, Tuple[str, str]] = {
    "ckpt_name": ("checkpoint", "checkpoints"),
    "unet_name": ("diffusion_model", "diffusion_models"),
    "vae_name": ("vae", "vae"),
    "control_net_name": ("controlnet", "controlnet"),
    "style_model_name": ("style_model", "style_models"),
}

_DEFAULT_CATALOG_PATH = Path(__file__).with_name("node_catalog.yml")


def load_catalog(path: Optional[Path] = None) -> NodeCatalog:
    """Parse+shape-validate `node_catalog.yml` (or `path`) into a
    `NodeCatalog`. Structural problems (an unknown pydantic field, a missing
    required key) raise; semantic problems (an unknown category/role/field/
    transform, a missing `folder` on a model-file input, a duplicate form
    field name) don't - see `validate_catalog`."""
    target = path or _DEFAULT_CATALOG_PATH
    with open(target, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return NodeCatalog.model_validate(raw)


@lru_cache(maxsize=1)
def get_catalog() -> NodeCatalog:
    return load_catalog()


def validate_catalog(catalog: NodeCatalog) -> List[str]:
    """Human-readable problems with `catalog` beyond what pydantic's shape
    checks already catch - every category/role/field/transform/history/link
    kind must be one of the closed sets above, and a model-file-role input
    must declare `folder`.

    Many different node classes legitimately suggest the same form field
    `name` (every sampler family's own seed input is named "seed", every
    LoRA node's is named "loras", ...) - `suggest.py`'s own per-analysis
    dedup (`_unique_field_name`) is what keeps two of those apart *within
    one imported workflow* when they'd otherwise collide (see its
    docstring). What a shared `name` must never do is disagree on the core
    `field` type it renders as - two inputs meant to be the same conceptual
    field always share one; a `name` reused across mismatched field types is
    a catalog authoring mistake, not an intentional overlap, and is what
    "duplicate names" here actually flags."""
    problems: List[str] = []
    field_by_name: Dict[str, Tuple[str, str]] = {}  # name -> (field, first "where")

    for class_type, entry in catalog.nodes.items():
        if entry.category not in CATEGORIES:
            problems.append(f"{class_type}: unknown category '{entry.category}'")

        for input_name, link_kind in entry.links.items():
            if link_kind not in LINK_KINDS:
                problems.append(f"{class_type}.links.{input_name}: unknown link kind '{link_kind}'")

        if entry.branch is not None:
            if entry.branch.input in entry.links:
                problems.append(
                    f"{class_type}.branch: switch input '{entry.branch.input}' must not itself be a link"
                )
            for side, link_input in (("on_true", entry.branch.on_true), ("on_false", entry.branch.on_false)):
                if entry.links.get(link_input) != "passthrough":
                    problems.append(
                        f"{class_type}.branch.{side}: '{link_input}' must be a 'passthrough' link"
                    )

        for input_name, spec in entry.inputs.items():
            where = f"{class_type}.inputs.{input_name}"
            if spec.role not in ROLES:
                problems.append(f"{where}: unknown role '{spec.role}'")
            if spec.field not in FIELD_TYPES:
                problems.append(f"{where}: unknown field type '{spec.field}'")
            if spec.transform not in TRANSFORMS:
                problems.append(f"{where}: unknown transform '{spec.transform}'")
            if spec.history is not None and spec.history not in HISTORY_FORMATS:
                problems.append(f"{where}: unknown history format '{spec.history}'")
            if spec.role in MODEL_FILE_ROLES and not spec.folder:
                problems.append(f"{where}: role '{spec.role}' needs 'folder'")

            prior = field_by_name.get(spec.name)
            if prior is None:
                field_by_name[spec.name] = (spec.field, where)
            elif prior[0] != spec.field:
                problems.append(
                    f"{where}: form field name '{spec.name}' also used as field type '{prior[0]}' at {prior[1]}"
                )

    return problems
