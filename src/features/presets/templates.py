"""The in-memory shape of a loaded preset.

`preset.yml`, its `modes/`, `pipeline.yml` and `form.yml` files are parsed by
`PresetTemplateLoader` into the dataclasses here, and `PresetProcessor` renders
them (Jinja) into the concrete pipeline and form a generation runs against.
`schema.py` holds the pydantic models that *validate* the YAML on the way in;
these are what the rest of the application works with once it is loaded.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union, Literal, Tuple

from src.pipelines.contracts import IOType


class GenerationMode(Enum):
    TXT2IMG = "txt2img"
    IMG2IMG = "img2img"
    TXT2VID = "txt2vid"
    IMG2VID = "img2vid"
    VID2VID = "vid2vid"
    TXT2AUDIO = "txt2audio"  # Text-to-audio generation
    TXT2SPEECH = "txt2speech"  # Text-to-speech generation
    IMG2MESH = "img2mesh"  # Image-to-3D-mesh generation


@dataclass
class PipeTemplate:
    name: str
    id: str = None
    enabled: bool = True
    configuration: Dict[str, Any] = None
    cache: List[str] = None  # Cache specific output variables by name
    input: List[Tuple[str, str, str]] = None  # [param_name, provider_pipe, provider_output_var]


@dataclass
class FieldTemplate:
    type: str
    name: str = None
    label: str = None
    description: str = None
    ai_hint: str = None
    configuration: Dict[str, Any] = None
    required: bool = False # If required then for example the select will not have a None option
    default: Any = None # Server-owned default value applied by bind_form when the key is absent
    when: str = None
    input: List[List] = None
    save_into: Literal["session", "settings"] = None
    interactive: bool = True
    container: bool = True
    visible: bool = None
    reactions: List[Dict[str, Any]] = None
    listeners: Any = None
    children: Union[List['FieldTemplate'], str] = None
    # "simple" (default) vs "advanced" - lets the frontend hide advanced-only
    # fields behind a toggle. Mirrors FieldSpec.audience in schema.py.
    audience: Literal["simple", "advanced"] = "simple"
    # Fractional share of the row this field sits in. Mirrors FieldSpec.width
    # in schema.py.
    width: Union[str, float, int] = None
    # Stretch the field to fill its column/track. Mirrors FieldSpec.full_width
    # in schema.py.
    full_width: bool = False
    # Hides this field when the Video Director editor owns its preset mode.
    # Mirrors FieldSpec.hidden_when_video_director in schema.py.
    hidden_when_video_director: bool = False
    # Synthetic, never authored in preset.yml (FieldSpec's `extra="forbid"`
    # rejects it there) - set by src/features/presets/form_overrides.py when
    # an admin per-field override locks this field (`editable: false`).
    # Read by base_field.py's get_field_info/create_base_schema to emit
    # `readonly: true` on the rendered schema.
    readonly: bool = False


@dataclass
class FormTemplate:
    name: str
    fields: List[FieldTemplate]
    # Variant display metadata (roadmap "preset variants"), mirroring
    # FormFile in schema.py. `name` remains the variant's identity (matched
    # against form.yml's own `name:`, falling back to the form directory
    # name - see PresetTemplateLoader._load_forms_from_directory).
    label: Optional[str] = None
    description: Optional[str] = None  # markdown
    examples: List[str] = field(default_factory=list)  # paths under public/
    default: bool = False
    order: int = 0

@dataclass
class ModeTemplate:
    """Template for a specific generation mode (txt2img, img2img, etc.)"""
    forms: List[FormTemplate]  # Multiple forms can be defined per mode
    pipes: List[PipeTemplate]  # Pipeline configuration for this mode
    # Set by PresetTemplateLoader when this mode was merged in from a plugin's
    # `preset_modes:` contribution rather than declared by the
    # preset's own preset.yml `modes:` list. None for every core mode.
    source_plugin: Optional[str] = None


def sorted_forms(mode_data: 'ModeTemplate') -> List[FormTemplate]:
    """Forms of a mode sorted by the variant ordering rule: (order, name)."""
    return sorted(mode_data.forms, key=lambda f: (f.order, f.name))


def default_form_name(mode_data: 'ModeTemplate') -> Optional[str]:
    """The identity (`name`) of a mode's default form variant.

    Rule (see docs/presets.md "Variants"): the first form (after sorting by
    (order, name)) with `default: true` wins; if none is marked default, the
    first form after sorting is the default. `None` if the mode has no forms
    at all.
    """
    forms = sorted_forms(mode_data)
    for form in forms:
        if form.default:
            return form.name
    return forms[0].name if forms else None


@dataclass
class PresetTemplate:
    id: str
    name: str
    version: str
    path: str  # Full path to preset root directory
    modes: Dict[str, ModeTemplate]  # mode name -> mode definition
    description: Optional[str] = None
    tags: List[str] = None
    category: Optional[str] = None  # "image", "video", "audio", "3d", "utility"
    form: FormTemplate = None  # Deprecated/unused: legacy top-level form, no longer populated by the loader
    vars: Dict[str, Any] = None
    # Named generation profiles from preset.yml's `speed_profiles:` (roadmap 3.6),
    # e.g. {"draft": {"steps": 6, "guidance": 1.0}, "standard": {"steps": 28}}.
    # Plain dicts (mirroring `vars`), not the pydantic SpeedProfile model, so
    # Jinja/get_speed_profile see the same nested-dict shape as everything else
    # in the pipeline context. See docs/presets.md "Speed profiles".
    speed_profiles: Dict[str, Dict[str, Any]] = None
    base_path: str = None  # Base directory where this preset was loaded from (e.g., "content/presets/marketplace" or "content/presets/local")
    engine: str = None  # Engine this preset's pipes speak (e.g. "native", "comfyui")
    media: Optional[dict] = None  # {cover, gallery: [...]} - see PresetMedia in schema.py
    # Declared schema for admin-set configuration (roadmap: preset configuration),
    # e.g. {"checkpoint_tags": {"type": "model_tags", "label": "...", "description": "..."}}.
    # Values themselves are admin-set state, not part of this template - see
    # src/features/presets/configuration.py and docs/presets.md "Configuration (admin-set)".
    configuration: Optional[Dict[str, Dict[str, Any]]] = None
    # Preset/family-level prompting guide + chat-workspace context knobs, e.g.
    # {"guide": "...", "context": {"form": "summary", "fields": None, "guidance_chars": None},
    # "modes": {"refs": {"guide": "..."}}}. Mirrors PresetLLMSpec in schema.py.
    # See docs/presets.md "LLM context".
    llm: Optional[Dict[str, Any]] = None
    # Optional hardware guidance, e.g. {"min_vram_gb": 12, "recommended_vram_gb": 16}.
    # Mirrors PresetRequirements in schema.py. See docs/presets.md "Hardware requirements".
    requires: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        # Default to the built-in native engine if not specified
        if self.engine is None:
            self.engine = "native"

    def copy(self):
        return PresetTemplate(
            id=self.id,
            name=self.name,
            version=self.version,
            description=self.description,
            tags=self.tags,
            category=self.category,
            form=self.form,
            vars=self.vars,
            speed_profiles=self.speed_profiles,
            path=self.path,
            modes=self.modes,
            base_path=self.base_path,
            engine=self.engine,
            media=self.media,
            configuration=self.configuration,
            llm=self.llm,
            requires=self.requires,
        )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tags": self.tags,
            "category": self.category,
            "path": self.path,
            "modes": {k: [v.__dict__ for v in value.pipes] for k, value in self.modes.items()},
            "vars": self.vars,
            "speed_profiles": self.speed_profiles,
            "engine": self.engine,
            "media": self.media,
            "configuration": self.configuration,
        }


@dataclass
class Annotation:
    text: str
    box: Optional[Tuple[int, int, int, int]] = None

    def serialize(self) -> Tuple[Optional[Tuple[int, int, int, int]], str]:
        return self.box if self.box else (0, 0, 0, 0), self.text


@dataclass
class PresetInput:
    input: Dict[IOType, Any]
