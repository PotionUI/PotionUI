"""
Canonical preset schema (pydantic v2).

Defines the validated shape of `preset.yml`, per-mode `pipeline.yml` and
`modes/<mode>/form.yml` (+ `variants/<name>/form.yml`) files. Field/pipe configuration values
are intentionally typed as ``Any`` in many places because they hold either
plain data or Jinja2 template strings (``{{ ... }}`` / ``{% ... %}``) that
are rendered later by ``PresetProcessor`` - the schema does not attempt to
evaluate templates, it only validates structure.

Validation is not fail-fast: use ``validate_manifest``/``validate_pipeline_file``/
``validate_form_file`` which return
``(model_or_none, [error_strings])`` so callers can collect every error for
a preset instead of stopping at the first one.
"""

import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PRESET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")

CATEGORIES = ("image", "video", "audio", "utility")

OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "in",
        "not_in",
        "greater_than",
        "less_than",
        "greater_than_or_equals",
        "less_than_or_equals",
        "contains",
        "not_contains",
        "is_empty",
        "is_not_empty",
    }
)

_RESERVED_CONDITION_KEYS = {"field", "operator", "value"}


def _format_errors(prefix: str, exc: ValidationError) -> List[str]:
    """Format pydantic ValidationError entries as '<prefix>: <path>: <message>'."""
    formatted = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        formatted.append(f"{prefix}: {loc}: {err['msg']}")
    return formatted


# ---------------------------------------------------------------------------
# Reactions grammar (shared with frontend reaction engine, see docs/presets.md)
# ---------------------------------------------------------------------------


class ConditionSpec(BaseModel):
    """A single condition: {field: <name>, <operator>: <value>} (sugar form)
    or {field: <name>, operator: <op>, value: <value>} (explicit form)."""

    model_config = ConfigDict(extra="allow")

    field: str
    operator: Optional[str] = None
    value: Any = None

    @model_validator(mode="after")
    def _resolve_sugar_operator(self) -> "ConditionSpec":
        extra = self.model_extra or {}
        sugar_keys = [k for k in extra.keys() if k in OPERATORS]

        if self.operator is not None:
            if self.operator not in OPERATORS:
                raise ValueError(
                    f"Unknown operator '{self.operator}'. Must be one of: {sorted(OPERATORS)}"
                )
            return self

        if not sugar_keys:
            raise ValueError(
                f"Condition for field '{self.field}' must specify an operator "
                f"(one of {sorted(OPERATORS)}) either as 'operator: <op>' or as sugar 'op: value'"
            )
        if len(sugar_keys) > 1:
            raise ValueError(
                f"Condition for field '{self.field}' specifies multiple operators: {sugar_keys}"
            )

        self.operator = sugar_keys[0]
        self.value = extra[sugar_keys[0]]
        return self


class LogicalCondition(BaseModel):
    """{logic: AND|OR, conditions: [...]}"""

    model_config = ConfigDict(extra="forbid")

    logic: Literal["AND", "OR"]
    conditions: List["ConditionOrLogical"]


ConditionOrLogical = Union[ConditionSpec, LogicalCondition]
LogicalCondition.model_rebuild()

WhenSpec = Union[ConditionOrLogical, List[ConditionOrLogical]]


class ActionSpec(BaseModel):
    """The 'then' side of a reaction. At least one action key must be set."""

    model_config = ConfigDict(extra="forbid")

    set_visibility: Optional[bool] = None
    set_value: Any = None
    set_disabled: Optional[bool] = None
    update_options: Optional[List[Dict[str, Any]]] = None
    update_validation: Optional[Dict[str, Any]] = None
    # A `model`/`lora_picker` field's `filter_tags`, resolved the same way as the
    # field's own static `configuration.filter_tags` (see resolve_field_filter_tags) -
    # either a literal tag-id list or `"@config:<key>"` indirection. Lets a reaction
    # narrow model options off another field's value (e.g. a speed/quality profile)
    # without a preset ever naming a model filename.
    set_filter_tags: Optional[Union[List[str], str]] = None

    @model_validator(mode="after")
    def _at_least_one_action(self) -> "ActionSpec":
        # set_value's default of None is ambiguous with "explicitly set to None",
        # so we check model_fields_set instead of truthiness.
        if not self.model_fields_set:
            raise ValueError("Reaction 'then' block must set at least one action")
        return self


class ReactionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: WhenSpec
    then: ActionSpec


# ---------------------------------------------------------------------------
# Form fields
# ---------------------------------------------------------------------------


class FieldSpec(BaseModel):
    """Mirrors src.features.presets.templates.FieldTemplate. `type` is an opaque, registry-validated
    string - this schema does not enumerate field types."""

    model_config = ConfigDict(extra="forbid")

    type: str
    name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    ai_hint: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    required: Optional[bool] = False
    # The ONE initializer key (`value:` is removed - extra="forbid" rejects it).
    # Must be a native YAML scalar/list/dict; Jinja is not rendered in form
    # definitions, so a string containing "{{" is a schema error.
    default: Any = None
    when: Optional[Any] = None  # opaque Jinja-templated visibility expression
    input: Optional[List[List[Any]]] = None
    save_into: Optional[Literal["session", "settings"]] = None
    interactive: Optional[bool] = True
    container: Optional[bool] = True
    visible: Optional[bool] = None
    reactions: Optional[List[ReactionSpec]] = None
    listeners: Optional[Any] = None
    children: Optional[Union[List["FieldSpec"], str]] = None
    # "simple" (default) vs "advanced" - lets the frontend hide advanced-only
    # fields behind a toggle. See docs/presets.md field reference.
    audience: Literal["simple", "advanced"] = "simple"
    # Fractional share of the row this field sits in (`type: "row"` container's
    # child grid-`fr` weight). Parsing contract is shared verbatim with the
    # frontend - see docs/presets.md field reference.
    width: Optional[Union[str, float, int]] = None
    # Stretch the field to fill its column/track instead of hugging its
    # content. See docs/presets.md field reference.
    full_width: Optional[Any] = False
    # Hides this field in the dynamic form whenever the Video Director editor
    # owns its preset mode (`vars.video_director.preset_modes`) - for a field
    # whose form value is a fallback the director's own document overrides
    # once attached (every UI generation in that mode). The field's value/
    # default still submits; only rendering changes, same contract as
    # `audience`. See docs/presets.md field reference.
    hidden_when_video_director: Optional[bool] = False

    @model_validator(mode="after")
    def _validate_default(self) -> "FieldSpec":
        error = _validate_typed_default(self.type, self.default)
        if error:
            raise ValueError(f"field '{self.name}' ({self.type}): {error}")
        return self

    @model_validator(mode="after")
    def _validate_width(self) -> "FieldSpec":
        error = _validate_field_width(self.width)
        if error:
            raise ValueError(f"field '{self.name}' ({self.type}): {error}")
        return self

    @model_validator(mode="after")
    def _validate_full_width(self) -> "FieldSpec":
        if not isinstance(self.full_width, bool):
            raise ValueError(
                f"field '{self.name}' ({self.type}): `full_width` must be a boolean, got {self.full_width!r}"
            )
        return self


FieldSpec.model_rebuild()


# Focused mapping of field types whose value type is knowable from the type
# name alone - not a general validation framework, just the buckets the
# audit found unstable (quoted booleans/numbers, string Jinja literals).
# Types not listed here keep `default: Any` (e.g. media/model/composite
# fields, whose default shape is field-specific or resolved elsewhere).
_BOOL_FIELD_TYPES = frozenset({"checkbox", "boolean"})
_NUMERIC_FIELD_TYPES = frozenset({"slider", "number", "seed", "stepper"})
_INTEGER_FIELD_TYPES = frozenset({"integer"})
_SCALAR_FIELD_TYPES = frozenset({"select"})
_STRING_FIELD_TYPES = frozenset({"string", "textbox"})


def _validate_typed_default(field_type: str, default: Any) -> Optional[str]:
    """Return an error string if `default` doesn't match `field_type`'s known
    value type, else None. `None` (absent default) is always allowed."""
    if default is None:
        return None

    if isinstance(default, str) and "{{" in default:
        return "Jinja is not rendered in form definitions; `default` must be a native value"

    if field_type in _BOOL_FIELD_TYPES:
        if not isinstance(default, bool):
            return f"default for a '{field_type}' field must be a bool, got {type(default).__name__}"
    elif field_type in _NUMERIC_FIELD_TYPES:
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            return f"default for a '{field_type}' field must be a number, got {type(default).__name__}"
    elif field_type in _INTEGER_FIELD_TYPES:
        if isinstance(default, bool) or not isinstance(default, int):
            return f"default for an 'integer' field must be an int, got {type(default).__name__}"
    elif field_type in _SCALAR_FIELD_TYPES:
        if isinstance(default, (list, dict)):
            return f"default for a '{field_type}' field must be a scalar, got {type(default).__name__}"
    elif field_type in _STRING_FIELD_TYPES:
        if not isinstance(default, str):
            return f"default for a '{field_type}' field must be a str, got {type(default).__name__}"

    return None


_WIDTH_FRACTION_RE = re.compile(r"^(?P<a>\d+(?:\.\d+)?)/(?P<b>\d+(?:\.\d+)?)$")


def _validate_field_width(width: Any) -> Optional[str]:
    """Return an error string if `width` doesn't match the row-weight contract
    shared with the frontend (see docs/presets.md), else None. `None` (absent
    width) is always allowed."""
    if width is None:
        return None

    if isinstance(width, bool):
        return f"`width` must be a positive number or 'a/b' fraction string, got {width!r}"

    if isinstance(width, (int, float)):
        if width <= 0:
            return f"`width` must be a positive number, got {width!r}"
        return None

    if isinstance(width, str):
        match = _WIDTH_FRACTION_RE.match(width.strip())
        if not match:
            return f"`width` string must be an 'a/b' fraction of positive numbers, got {width!r}"
        a, b = float(match["a"]), float(match["b"])
        if a <= 0 or b <= 0:
            return f"`width` fraction 'a/b' must have positive a and b, got {width!r}"
        return None

    return f"`width` must be a positive number or 'a/b' fraction string, got {width!r}"


# ---------------------------------------------------------------------------
# Media path validation (shared by FormFile.examples and PresetMedia below)
# ---------------------------------------------------------------------------

# No `.svg` - serving it inline would be an XSS vector (see docs/presets.md).
PRESET_MEDIA_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"})


def _validate_media_src(v: Optional[str]) -> Optional[str]:
    """Validate string shape only: relative, under `public/`, allowed extension.

    Does NOT check file existence or cross-reference `modes:` - those are lint
    checks (src/features/presets/linter.py), not schema checks, because a pydantic
    failure here would make the whole preset unloadable.
    """
    if v is None:
        return v
    if not v or v.startswith("/") or "\\" in v:
        raise ValueError(f"media src '{v}' must be a relative, forward-slash path")

    parts = v.split("/")
    if ".." in parts:
        raise ValueError(f"media src '{v}' must not contain '..' path segments")
    if parts[0] != "public":
        raise ValueError(f"media src '{v}' must live under 'public/' (got root '{parts[0]}')")

    suffix = "." + parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    if suffix not in PRESET_MEDIA_EXTENSIONS:
        raise ValueError(
            f"media src '{v}' has unsupported extension '{suffix}'. "
            f"Must be one of: {sorted(PRESET_MEDIA_EXTENSIONS)}"
        )
    return v


class FormFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "custom_form_preset"
    fields: List[FieldSpec] = Field(default_factory=list)
    # Variant display metadata (roadmap "preset variants") - surfaced by
    # PresetManager.get_modes()'s `variants` list, see docs/presets.md.
    label: Optional[str] = None
    description: Optional[str] = None  # markdown
    examples: List[str] = Field(default_factory=list)  # paths, must live under public/
    default: bool = False
    order: int = 0

    @model_validator(mode="after")
    def _validate_examples(self) -> "FormFile":
        for example in self.examples:
            _validate_media_src(example)
        return self


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class PipeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    name: str
    # Real bool default: omitted `enabled:` means enabled. `Union[str, bool]`
    # still accepts an exact-expression string (rendered to a bool at build
    # time by W2's evaluator); runtime checks `is True`, not `== 'true'`.
    enabled: Union[str, bool] = True
    input: Optional[List[List[Any]]] = None
    cache: Optional[List[Any]] = None
    configuration: Optional[Dict[str, Any]] = None


class PipelineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: List[PipeSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Preset media (cover image + example gallery)
# ---------------------------------------------------------------------------
# PRESET_MEDIA_EXTENSIONS and _validate_media_src now live above, next to
# FieldSpec/FormFile, since FormFile.examples needs them too.


class GalleryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str
    caption: Optional[str] = None
    prompt: Optional[str] = None
    seed: Optional[int] = None
    mode: Optional[str] = None

    @model_validator(mode="after")
    def _validate_src(self) -> "GalleryItem":
        _validate_media_src(self.src)
        return self


class PresetMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover: Optional[str] = None
    gallery: List[GalleryItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_cover(self) -> "PresetMedia":
        _validate_media_src(self.cover)
        return self


# ---------------------------------------------------------------------------
# Hardware requirements: optional, author-supplied VRAM/RAM guidance surfaced
# to the frontend at preset-choice time (see docs/presets.md "Hardware
# requirements" and docs/user/hardware-requirements.md, the measured-numbers
# source of truth). Never rendered into pipeline.yml's Jinja context - see
# PresetProcessor's hand-curated `preset` dict - so this is metadata only.
# ---------------------------------------------------------------------------


class PresetRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_vram_gb: Optional[float] = None
    recommended_vram_gb: Optional[float] = None
    min_ram_gb: Optional[float] = None

    @model_validator(mode="after")
    def _validate_positive(self) -> "PresetRequirements":
        for name in ("min_vram_gb", "recommended_vram_gb", "min_ram_gb"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"requires.{name} must be a positive number, got {value}")
        return self


# ---------------------------------------------------------------------------
# Speed profiles (roadmap 3.6): named bundles of generation knobs a preset's
# pipeline.yml can switch between atomically via a selected profile name.
# ---------------------------------------------------------------------------

# Keys with dedicated, typed fields below. Anything else is allowed
# structurally (SpeedProfile.model_config extra="allow", so a typo'd or
# forward-looking key doesn't make the whole preset unloadable - matching how
# `media`'s file-existence checks are lint-level, not schema-level) but is
# flagged as a lint warning unless nested under `extra:` (see
# PresetLinter._lint_speed_profiles in linter.py).
SPEED_PROFILE_KNOWN_KEYS = frozenset({"steps", "guidance", "shift", "loras", "sampler", "schedule", "extra"})


class SpeedProfile(BaseModel):
    """One named entry in `speed_profiles:` (e.g. the `draft` in
    `speed_profiles: {draft: {steps: 6, guidance: 1.0}}`).

    Known keys are typed so a wrong-shaped value (e.g. `steps: "fast"`) is a
    real schema error, not a silent no-op at render time. Anything not in
    :data:`SPEED_PROFILE_KNOWN_KEYS` is still accepted here (`extra="allow"`)
    so a preset with a stray/forward-compat key still loads - the linter
    warns about it instead of failing preset load entirely.
    """

    model_config = ConfigDict(extra="allow")

    steps: Optional[int] = None
    guidance: Optional[float] = None
    shift: Optional[float] = None
    sampler: Optional[str] = None
    schedule: Optional[str] = None
    loras: Optional[List[Dict[str, Any]]] = None
    # Free-form forward-compat bag: a pipe-specific knob that doesn't warrant
    # its own top-level key yet. Never flagged by the lint's unknown-key check.
    extra: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Configuration (admin-set): preset.yml declares the schema of admin-tunable
# knobs a preset exposes (currently only `model_tags`, used by the `model`
# field's `filter_tags: "@config:<key>"` indirection - see docs/presets.md
# "Configuration (admin-set)"). Values themselves are admin-set state, stored
# per installed preset (src/features/presets/records.py's `configuration`
# column), not part of the YAML - this only validates the declared shape.
#
# Extensible by design: a new admin-configurable knob type is added to
# CONFIGURATION_TYPES (and given a validator in
# src/features/presets/configuration.py) without touching every preset that
# doesn't use it.
# ---------------------------------------------------------------------------

CONFIGURATION_TYPES = frozenset({"model_tags"})


class ConfigurationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    label: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _validate_type(self) -> "ConfigurationEntry":
        if self.type not in CONFIGURATION_TYPES:
            raise ValueError(
                f"Unsupported configuration type '{self.type}'. Must be one of: {sorted(CONFIGURATION_TYPES)}"
            )
        return self


# ---------------------------------------------------------------------------
# LLM context (chat workspace injection): an optional preset-authored
# prompting guide plus knobs controlling how much of the form schema the chat
# LLM's per-turn workspace block includes. See docs/presets.md "LLM context".
# ---------------------------------------------------------------------------

LLM_FORM_MODES = ("off", "summary", "full")


class PresetLLMContextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form: Literal["off", "summary", "full"] = "summary"
    # Form field names whose resolved models' guidance gets pushed into the
    # workspace block; None keeps the current auto-detect behavior (every
    # resolved model).
    fields: Optional[List[str]] = None
    # Overrides the workspace block's default per-model guidance cap
    # (_WORKSPACE_MAX_GUIDANCE_CHARS in context_builder.py) when set.
    guidance_chars: Optional[int] = None

    @model_validator(mode="after")
    def _validate_guidance_chars(self) -> "PresetLLMContextSpec":
        if self.guidance_chars is not None and self.guidance_chars <= 0:
            raise ValueError(f"llm.context.guidance_chars must be a positive int, got {self.guidance_chars}")
        return self


class PresetLLMModeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide: str


class PresetLLMSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide: Optional[str] = None
    context: PresetLLMContextSpec = Field(default_factory=PresetLLMContextSpec)
    # Per-mode guide overrides, keyed by mode name (e.g. "refs", "video"). When
    # the active mode has an entry here, its `guide` REPLACES `guide` above
    # (not concatenated) for that mode - see docs/presets.md "LLM context".
    # Keys are not cross-validated against the preset's declared `modes:` -
    # a plugin-contributed mode may not be statically known here.
    modes: Optional[Dict[str, PresetLLMModeSpec]] = None

    @model_validator(mode="after")
    def _validate_mode_keys(self) -> "PresetLLMSpec":
        if self.modes is not None:
            for key in self.modes:
                if not key or not key.strip():
                    raise ValueError("llm.modes keys must be non-empty strings")
        return self


# ---------------------------------------------------------------------------
# preset.yml manifest
# ---------------------------------------------------------------------------


class PresetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(..., alias="schema")
    id: str
    name: str
    version: str
    category: Literal["image", "video", "audio", "utility"]
    engine: str  # Engine this preset's pipes speak, e.g. "native" or "comfyui"
    tags: List[str] = Field(default_factory=list)
    vars: Dict[str, Any] = Field(default_factory=dict)
    # Admin-configurable knobs this preset exposes, e.g.
    # {"checkpoint_tags": {"type": "model_tags", "label": "Checkpoint tags"}}.
    # See docs/presets.md "Configuration (admin-set)".
    configuration: Optional[Dict[str, ConfigurationEntry]] = None
    # Preset/family-level prompting guide + chat-workspace context knobs.
    # See docs/presets.md "LLM context".
    llm: Optional[PresetLLMSpec] = None
    # Preset-wide named generation profiles (draft/standard/max, ...), keyed by
    # profile name. See docs/presets.md "Speed profiles". Top-level (not
    # per-mode) for the same reason `vars:` is top-level: one preset.yml-wide
    # bag that every mode's pipeline.yml can read from, and ModeTemplate today
    # carries no data of its own (only `forms`/`pipes`) - adding a per-mode
    # variant would mean threading a second field through loader.py's mode
    # parsing for no shipped use case that needs mode-scoped profiles.
    speed_profiles: Optional[Dict[str, SpeedProfile]] = None
    modes: List[str]
    media: Optional[PresetMedia] = None
    # Optional hardware guidance shown at preset-choice time, before a user
    # downloads the model. See docs/presets.md "Hardware requirements".
    requires: Optional[PresetRequirements] = None

    @model_validator(mode="after")
    def _validate_business_rules(self) -> "PresetManifest":
        problems = []
        if self.schema_version != 1:
            problems.append(f"Unsupported schema version: {self.schema_version}. Only 1 is supported.")
        if not PRESET_ID_RE.match(self.id):
            problems.append(f"id '{self.id}' does not match ^[A-Za-z0-9_-]{{3,64}}$")
        if not SEMVER_RE.match(self.version):
            problems.append(f"version '{self.version}' is not a valid semver string (e.g. 1.0.0)")
        if not self.engine or not self.engine.strip():
            problems.append("engine must be a non-empty string (e.g. 'native' or 'comfyui')")
        if not self.modes:
            problems.append("modes must be a non-empty list")
        if problems:
            raise ValueError("; ".join(problems))
        return self


# ---------------------------------------------------------------------------
# Validation entry points (collect errors, never raise)
# ---------------------------------------------------------------------------


def validate_manifest(data: dict, prefix: str = "preset.yml") -> Tuple[Optional[PresetManifest], List[str]]:
    try:
        return PresetManifest.model_validate(data), []
    except ValidationError as exc:
        return None, _format_errors(prefix, exc)


def validate_pipeline_file(data: dict, prefix: str = "pipeline.yml") -> Tuple[Optional[PipelineFile], List[str]]:
    try:
        return PipelineFile.model_validate(data), []
    except ValidationError as exc:
        return None, _format_errors(prefix, exc)


def validate_form_file(data: dict, prefix: str = "form.yml") -> Tuple[Optional[FormFile], List[str]]:
    try:
        return FormFile.model_validate(data), []
    except ValidationError as exc:
        return None, _format_errors(prefix, exc)


def validate_field_list(data: list, prefix: str = "fields") -> Tuple[Optional[List[FieldSpec]], List[str]]:
    """Validate a bare `fields:` list (an external tab/children fragment) through
    the same `FieldSpec` schema used for `form.yml`'s own `fields:`."""
    try:
        return [FieldSpec.model_validate(item) for item in data], []
    except ValidationError as exc:
        return None, _format_errors(prefix, exc)
