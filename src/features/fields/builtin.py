"""
Registers every core (non-plugin) field type onto a `FieldTypeRegistry`.

Called once at startup (`src/bootstrap/container.py`) against the shared
`field_type_registry` singleton, and again (with no `form_manager`) by
`FieldFactory`/tests that construct a private registry ad hoc.

`form_manager` is optional. When provided, its option-loading methods
(`_get_select_options`, `_get_model_database_options`, `_get_checkbox_options`)
are wired in as the `options_provider` for the field types that support
dynamic options - `FormManager` remains the single owner of that loading
logic, this just binds it into the registry so `FormManager.get_field_options`
and the `/api/fields/types` manifest can both dispatch off the same table.
"""

from typing import Optional

from src.platform.plugins.field_types import FieldTypeDefinition, FieldTypeRegistry
from src.features.fields.select import Select
from src.features.fields.slider import Slider
from src.features.fields.stepper import Stepper
from src.features.fields.checkbox_group import CheckboxGroup
from src.features.fields.checkbox import Checkbox
from src.features.fields.container import Container
from src.features.fields.image import Image
from src.features.fields.video import Video
from src.features.fields.audio import Audio
from src.features.fields.media import Media
from src.features.fields.file import File
from src.features.fields.model import Model
from src.features.fields.lora_picker import LoraPicker
from src.features.fields.seed import Seed
from src.features.fields.alert import Alert
from src.features.fields.markdown import Markdown
from src.features.fields.resolution import Resolution
from src.features.fields.header import Header
from src.features.fields.section import Section
from src.features.fields.gate import Gate
from src.features.fields.llm import LLMField
from src.features.fields.carousel import CarouselField
from src.features.fields.prompt_timeline import PromptTimeline
from src.features.fields.camera_shot import CameraShot


def register_builtin_fields(registry: FieldTypeRegistry, form_manager: Optional[object] = None) -> None:
    """Register the ~28 core field types onto `registry`."""
    select_options = form_manager._get_select_options if form_manager else None
    model_options = form_manager._get_model_database_options if form_manager else None
    checkbox_options = form_manager._get_checkbox_options if form_manager else None

    definitions = [
        # Plain inputs - no dedicated backend schema class, DefaultField (passthrough) applies.
        # Prompt/text content is exactly what's meant to be shared on a published inspiration.
        FieldTypeDefinition("string", None, frontend_component="core:TextInput", shareable=True),
        FieldTypeDefinition("textbox", None, frontend_component="core:TextInput", shareable=True),
        FieldTypeDefinition("number", None, frontend_component="core:NumberInput", shareable=True),
        FieldTypeDefinition("integer", None, frontend_component="core:NumberInput", shareable=True),

        # Checkbox-like
        FieldTypeDefinition("boolean", Checkbox, frontend_component="core:CheckboxField", shareable=True),
        FieldTypeDefinition("checkbox", Checkbox, frontend_component="core:CheckboxField", shareable=True),

        # Numeric widgets
        FieldTypeDefinition("slider", Slider, frontend_component="core:SliderField", shareable=True),
        FieldTypeDefinition("stepper", Stepper, frontend_component="core:NumberInput", shareable=True),
        FieldTypeDefinition("seed", Seed, frontend_component="core:SeedField", shareable=True),
        FieldTypeDefinition("resolution", Resolution, frontend_component="core:ResolutionField", shareable=True),

        # Options-backed. `model`/`models`/`lora_picker` values are `model:<id>`
        # refs into the global model catalog, not user-storage paths - public
        # identifiers, same reasoning as select/checkbox_group's curated option
        # values.
        FieldTypeDefinition("select", Select, options_provider=select_options, frontend_component="core:SelectField", shareable=True),
        FieldTypeDefinition("checkbox_group", CheckboxGroup, options_provider=checkbox_options, frontend_component="core:CheckboxGroupField", shareable=True),
        FieldTypeDefinition("model", Model, options_provider=model_options, frontend_component="core:ModelField", shareable=True),
        FieldTypeDefinition("models", Model, options_provider=model_options, frontend_component="core:ModelField", shareable=True),
        FieldTypeDefinition("lora_picker", LoraPicker, frontend_component="core:LoraPickerField", shareable=True),

        # Media - carries user-storage paths/uploads, never shareable.
        FieldTypeDefinition("image", Image, frontend_component="core:MediaLoaderField"),
        FieldTypeDefinition("video", Video, frontend_component="core:MediaLoaderField"),
        FieldTypeDefinition("audio", Audio, frontend_component="core:MediaLoaderField"),
        FieldTypeDefinition("media", Media, frontend_component="core:MediaLoaderField"),
        FieldTypeDefinition("file", File, frontend_component="core:FileField"),

        # Misc widgets. `carousel`'s options come from the preset's own bundled
        # directory/YAML (public, preset-authored), same footing as select's
        # static options - shareable. `gate` carries a real boolean (see
        # Gate's docstring) - shareable like any other boolean. `llm`'s value
        # can reference a user's own configured LLM backend - not a shareable
        # identifier. `prompt_timeline` is a passthrough document that may
        # embed per-segment media refs - not classified as safe.
        FieldTypeDefinition("carousel", CarouselField, frontend_component="core:CarouselField", shareable=True),
        FieldTypeDefinition("llm", LLMField, frontend_component="core:LLMField"),
        FieldTypeDefinition("alert", Alert, frontend_component="core:AlertField"),
        FieldTypeDefinition("markdown", Markdown, frontend_component="core:MarkdownField"),
        FieldTypeDefinition("header", Header, frontend_component="core:HeaderField"),
        FieldTypeDefinition("section", Section, frontend_component="core:SectionField"),
        FieldTypeDefinition("gate", Gate, frontend_component="core:GateField", shareable=True),
        FieldTypeDefinition("prompt_timeline", PromptTimeline, frontend_component="core:PromptTimelineField"),
        FieldTypeDefinition("camera_shot", CameraShot, frontend_component="core:CameraShotField"),

        # Layout containers
        FieldTypeDefinition("tabs", Container, frontend_component="core:TabsField", container=True),
        FieldTypeDefinition("tab", Container, frontend_component="core:TabsField", container=True),
        FieldTypeDefinition("row", Container, frontend_component="core:RowField", container=True),
        FieldTypeDefinition("group", Container, frontend_component="core:GroupField", container=True),
        FieldTypeDefinition("accordion", Container, frontend_component="core:AccordionField", container=True),
    ]

    for definition in definitions:
        registry.register(definition)
