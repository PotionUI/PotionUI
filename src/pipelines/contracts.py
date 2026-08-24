"""The contract every pipe implements.

A pipe is a self-contained processing step: it declares the inputs it accepts,
the outputs it produces and the configuration it understands, and it turns the
first into the second when `process` is called. `IOType` is the vocabulary those
declarations are written in - the set of things that can travel between pipes.

Everything here is data and abstract methods. The other half of the vocabulary
is in `outputs.py` (what a pipe emits while it runs) and `models.py` (the model
objects that travel between pipes). Discovery lives in `catalog.py` and
requirement handling in `installer.py`, so a pipe author needs none of those.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class IOType(Enum):
    # Number types
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"

    # Media types
    IMAGE = "IMAGE"
    MASK = "MASK"
    LATENT = "LATENT"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"  # Audio files for text-to-audio generation
    MESH = "MESH"  # 3D mesh (.glb) for text/image-to-3D generation
    MODEL = "MODEL"
    CLIP = "CLIP"
    VAE = "VAE"
    CONTROLNET = "CONTROLNET"  # ControlNet model(s) for guided generation
    NUMPY = "NUMPY"
    IMAGE_TYPE = "IMAGE_TYPE"  # PIL | LATENT | TENSOR

    # Prompts
    P_PROMPT = "P_PROMPT"
    N_PROMPT = "N_PROMPT"
    P_PROMPT_EMBED = "P_PROMPT_EMBED"
    N_PROMPT_EMBED = "N_PROMPT_EMBED"
    CONDITIONING = "CONDITIONING"
    EMBEDDING = "EMBEDDING"

    # Options
    SEED = "SEED"
    RESOLUTION = "RESOLUTION"
    SAMPLER = "SAMPLER"
    SCHEDULER = "SCHEDULER"
    CLIP_SKIP = "CLIP_SKIP"
    CFG = "CFG"
    TRUE_CFG_SCALE = "TRUE_CFG_SCALE"  # QwenImage's CFG parameter
    GUIDANCE_RESCALE = "GUIDANCE_RESCALE"
    PAG_SCALE = "PAG_SCALE"
    DENOISE = "DENOISE"
    STEP = "STEP"
    MODE = "MODE"

    # Misc
    TEXT = "TEXT"
    DEVICE = "DEVICE"
    ANNOTATION = "ANNOTATION"
    PIPE = "PIPE"  # [(name, enabled, configuration, inputs)]
    FORM = "FORM"
    DICT = "DICT"

    # Built-in Services (injected by GenerationManager)
    SERVICE = "SERVICE"  # System services (GPU, SYSTEM, MEMORY, LLM, MODELS, ASSETS) - uppercase names

    # Model Types
    LORA = "LORA"  # [(Path(model_path), weight)]


@dataclass
class PipeInputSpec:
    """Specification for a pipe input parameter"""
    name: str           # Parameter name (e.g., "clip", "model")
    io_type: IOType     # Expected data type
    required: bool = True  # Whether this input is required
    description: str = ""  # Optional description
    is_array: bool = False  # Whether this input expects an array/list of values


@dataclass
class PipeOutputSpec:
    """Specification for a pipe output variable"""
    name: str           # Variable name (e.g., "clip", "model")
    io_type: IOType     # Data type being output
    description: str = ""  # Optional description
    is_array: bool = False  # Whether this output produces an array/list of values


@dataclass
class PipeConfigSpec:
    """Specification for a pipe configuration parameter"""
    name: str           # Parameter name (e.g., "steps", "cfg", "model")
    param_type: type    # Parameter type (int, float, str, bool, etc.)
    default: Any        # Default value
    description: str = ""  # Optional description
    required: bool = False  # Whether this parameter is required
    choices: Optional[List[Any]] = None  # Optional list of valid choices
    min_value: Optional[Union[int, float]] = None  # Optional minimum value for numeric types
    max_value: Optional[Union[int, float]] = None  # Optional maximum value for numeric types


@dataclass
class PipeInput:
    input: Dict[str, Any]  # Runtime input data (parameter names -> values)


@dataclass
class PipeOutput:
    output: Dict[str, Any]  # Runtime output data (variable names -> values)


@dataclass
class GenerationInputItem:
    """One value handed to a pipeline, tagged with the IOType it speaks."""
    name: str
    value: Any
    io_type: IOType


@dataclass
class GenerationInput:
    """The values a pipeline is started with, addressable by IOType or by name.

    Distinct from `PipeInput`: this is what enters the pipeline from the
    outside, while `PipeInput` is what one pipe receives from its predecessors.
    """
    input: List[GenerationInputItem] = dataclass_field(default_factory=list)

    def __post_init__(self):
        # Callers may hand in a plain {IOType: value} mapping instead of items.
        if isinstance(self.input, dict):
            self.input = [
                GenerationInputItem(name=str(k.value), value=v, io_type=k)
                for k, v in self.input.items()
            ]

    def get(self, io_type: IOType, default=None):
        """Get a value by io_type, with an optional default value"""
        for item in self.input:
            if item.io_type == io_type:
                return item.value
        return default

    def get_by_name(self, name: str, default=None):
        """Get a value by name, with an optional default value"""
        for item in self.input:
            if item.name == name:
                return item.value
        return default

    def __getitem__(self, io_type: IOType):
        """Allow dictionary-like access by io_type"""
        for item in self.input:
            if item.io_type == io_type:
                return item.value
        raise KeyError(f"No input found with io_type {io_type}")

    def __setitem__(self, io_type: IOType, value: Any):
        """Allow dictionary-like setting by io_type"""
        for item in self.input:
            if item.io_type == io_type:
                item.value = value
                return
        # If not found, add a new item
        self.input.append(GenerationInputItem(
            name=str(io_type.value),
            value=value,
            io_type=io_type
        ))


class PipeStatus(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    ERROR = "error"


class BasePipe(ABC):
    # Every pipe sets these as plain class attributes (``name = "generator"``);
    # `PipeCatalog` registers a pipe under `pipe_class.name` read straight off
    # the class, never off an instance. A `@property` here would satisfy this
    # as an abstract override too, but hand the catalog a `property` object
    # instead of a string, so it stays a plain annotation, not a property.
    name: ClassVar[str]
    description: ClassVar[str]
    # Human-readable phase title for the generation status line (e.g. "Loading
    # model"), as opposed to `description`, which is developer-facing pipe
    # documentation. None means "use the family fallback" - see
    # `resolve_display_title`. Overridable per pipeline step via a
    # `display_title` key in the step's `config:` block (the existing
    # unknown-parameter passthrough in `validate_pipe_configuration`), which
    # `resolve_display_title` also checks.
    display_title: ClassVar[Optional[str]] = None

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
    ) -> PipeOutput:
        """Process the input data and return modified data"""
        pass

    @classmethod
    @abstractmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Return default configuration for this pipe"""
        pass

    @classmethod
    @abstractmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Return specification of inputs this pipe accepts"""
        pass

    @classmethod
    @abstractmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Return specification of outputs this pipe produces"""
        pass

    @classmethod
    @abstractmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        pass

    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        """Return requirements for this pipe (pip packages, git repos, models, etc.)"""
        return {
            'pip': [],
            'git': [],
            'models': []
        }

    @classmethod
    def manual_install_instructions(cls) -> Optional[str]:
        """The commands that bring this pipe's requirements into existence when
        `PipeInstaller` cannot - or None when it can.

        The requirement vocabulary has one verb per kind: `pip install <name>`,
        `git clone <url>`, or a file that already exists. A pipe standing on
        anything else - CUDA extensions built by an upstream script, a system
        package - has no way to declare it, and `requirements_satisfied` will
        report NOT_INSTALLED for something no install run can fix. Returning a
        string here is that pipe saying so: the installer refuses rather than
        running a `pip install` that cannot succeed, and the surface shows this
        text in place of an Install action.
        """
        return None

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """Optional cross-field validation beyond what a single ``PipeConfigSpec``
        can express (``choices``/``min_value``/``max_value`` each constrain one
        parameter in isolation).

        Called by ``validate_pipe_configuration``
        (``src/features/generation/generation.py``) right after per-parameter
        validation, with the fully resolved config (defaults applied, types
        coerced). Raise ``ValueError`` to reject a combination of otherwise
        individually-valid parameters that is degenerate together (e.g. a
        schedule override left without matching data) -- the caller surfaces
        it exactly like any other configuration ``ValueError``, before the
        pipe runs. Default is a no-op; most pipes need only the per-parameter
        checks in ``configuration()``.
        """
        return None


# Fallback titles for the built-in pipe families under `src/pipelines/pipes/`,
# keyed by the family - the part of `BasePipe.name` before the first `/`
# (most pipes have no `/`; a few, like "interpolator/rife", nest a variant
# under it). Consulted by `resolve_display_title` when a pipe sets no
# `display_title` and no step overrides it via `config.display_title`.
PIPE_FAMILY_TITLES: Dict[str, str] = {
    "checkpoint_loader": "Loading model",
    "model_loader": "Loading model",
    "from_iotype": "Preparing inputs",
    "param_emitter": "Preparing parameters",
    "seed_generator": "Generating seed",
    "prompt_encoder": "Encoding prompt",
    "prompt_expander": "Expanding prompt",
    "dynamic_prompts_renderer": "Rendering prompt",
    "adm_guidance": "Applying guidance",
    "sag": "Applying guidance",
    "sharpness": "Sharpening",
    "generator": "Generating",
    "detailer": "Refining details",
    "tiled_detailer": "Refining details",
    "tiled_refiner": "Refining",
    "upscaler": "Upscaling",
    "latent_upscaler": "Upscaling",
    "interpolator": "Interpolating frames",
    "matting": "Removing background",
    "media_loader": "Loading media",
    "mask_preprocessor": "Preparing mask",
    "inpaint_region_crop": "Cropping inpaint region",
    "inpaint_region_restore": "Restoring inpaint region",
    "controlnet_loader": "Loading ControlNet",
    "controlnet_preprocessor": "Preparing ControlNet",
    "crop_subject": "Cropping subject",
    "color_key": "Keying color",
    "canvas_fit": "Fitting canvas",
    "film_grain": "Adding film grain",
    "video_frame_extractor": "Extracting frames",
    "video_frame_merger": "Merging frames",
    "video_speed": "Adjusting video speed",
    "audio_trim": "Trimming audio",
    "output_skipper": "Finalizing",
    "gallery": "Saving to gallery",
    "artifact": "Recording artifact",
}


def resolve_display_title(pipe_name: Optional[str], override: Optional[str] = None) -> str:
    """The human title for the generation status line: ``override`` (a pipe's
    `display_title` or a step's `config.display_title`) if set, else the
    family fallback table, else the name itself cleaned up (underscores to
    spaces, sentence-case) - the case a plugin pipe with no title lands in."""
    if override:
        return override
    if not pipe_name:
        return "Processing"
    family = pipe_name.split("/", 1)[0]
    fallback = PIPE_FAMILY_TITLES.get(family)
    if fallback:
        return fallback
    cleaned = family.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Processing"
