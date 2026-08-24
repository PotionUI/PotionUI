"""What a pipe emits while it runs.

A pipe returns its `PipeOutput` once, at the end; everything it wants the user
to see *during* the run - preview images, progress, the seed it drew, the models
it loaded, the finished gallery - it emits as a `GenerationOutput` through the
`generation_outputs` callable it is handed. These are the types it emits.

They are pure data. Deciding what to do with an emitted output (save it, encode
it, push it over the WebSocket) is the generation feature's job, not a pipe's.
"""

from dataclasses import dataclass, field
from typing import Tuple, Any, Literal, List, Dict
from pathlib import Path

from PIL import Image


@dataclass
class Icon:
    """Glyph shown next to a progress line, by name plus an optional effect."""
    name: str
    effect: str = None


@dataclass
class Progress:
    """How far a pipe has got: `current` of `max` units of its own choosing."""
    current: int
    max: int


@dataclass(kw_only=True)
class GenerationOutput:
    type = "generation_output"
    pipe_id: int = None  # Index of the pipe that generated this output
    pipe_name: str = None  # Name of the pipe that generated this output


def _force_eager_decode(image: Any) -> None:
    """
    Force a lazily-opened PIL image to fully decode its pixel data now.

    Pipes run on a worker thread (asyncio.to_thread), while OutputBridge
    dispatches outputs on the event-loop thread, where the output handlers
    (e.g. image_handler.create_base64_image) touch the same PIL Image object.
    PIL.Image.open() is lazy - it only reads the header - so if a freshly
    opened image is emitted without decoding it first, two threads end up
    driving the same lazy decoder over the same open file object at once,
    which manifests as spurious "unrecognized data stream contents" /
    "broken PNG file" errors on perfectly valid images. Calling .load() here,
    at construction time on the producing pipe's thread, forces the decode
    to happen before the output can ever reach another thread. Do not remove
    this "optimization" - it exists to close that race.
    """
    if image is None:
        return
    # fp is None once a PIL image has been fully loaded/decoded (or was
    # never file-backed, e.g. Image.new()/fromarray()) - nothing to do.
    if getattr(image, "fp", None) is None:
        return
    if not hasattr(image, "load"):
        return
    image.load()


@dataclass
class ImageGenerationOutput(GenerationOutput):
    image: Image
    temporary: bool = True  # True = don't save, False = save image
    isArtifact: bool = False  # True = show in artifacts/history, False = show in workbench
    derived: bool = False  # True = produced from another final image of this generation
    label: str = None  # Label for artifacts (e.g., "Control Image", "Preprocessed Image")
    seed: int = None
    resolution: Tuple[int, int] = None
    sampler: str = None
    clip_skip: int = None
    cfg: float = None
    denoise: float = None
    step: int = None

    def __post_init__(self):
        _force_eager_decode(self.image)


@dataclass
class VideoGenerationOutput(GenerationOutput):
    video_path: Path  # Path to the video file
    temporary: bool = True  # True = don't save, False = save video
    derived: bool = False  # True = produced from another final video of this generation
    seed: int = None
    resolution: Tuple[int, int] = None  # Width x Height
    duration: float = None  # Duration in seconds
    fps: float = None  # Frames per second
    sampler: str = None
    clip_skip: int = None
    cfg: float = None
    denoise: float = None
    step: int = None
    motion_strength: float = None  # Video-specific parameter


@dataclass
class AudioGenerationOutput(GenerationOutput):
    audio_path: Path  # Path to the audio file
    temporary: bool = True  # True = don't save, False = save audio
    track_type: Literal["vocal", "instrumental", "mixed"] = "mixed"  # Type of audio track
    seed: int = None
    duration: float = None  # Duration in seconds
    sample_rate: int = None  # Sample rate in Hz (e.g., 16000, 44100)
    channels: int = None  # Number of audio channels (1=mono, 2=stereo)
    temperature: float = None  # Generation temperature
    top_p: float = None  # Nucleus sampling parameter
    guidance_scale: float = None  # Guidance scale for generation
    segment: int = None  # Segment number for multi-segment generation

@dataclass
class MeshGenerationOutput(GenerationOutput):
    """A 3D mesh a pipe produced, as a self-contained `.glb` on disk.

    Shaped after VideoGenerationOutput rather than ImageGenerationOutput: the
    payload is a file, not an in-memory object, so there is no decode race to
    close and nothing to base64-encode for a preview.

    Deliberately narrower than its siblings. `isArtifact`/`label` are omitted -
    they only mean something together, naming a *secondary* image in the
    artifacts panel, and a mesh is a primary output. Sampling parameters
    (sampler/cfg/step/denoise) are omitted too: no mesh family is wired up yet,
    so their vocabulary would be guesswork. Both are additive later.

    `vertex_count`/`face_count` are the geometry analogue of an image's
    `resolution`. A pipe that already knows them can set them; otherwise the
    handler fills them in from the file itself.
    """
    mesh_path: Path  # Path to the .glb file
    temporary: bool = True  # True = don't save, False = save mesh
    derived: bool = False  # True = produced from another final mesh of this generation
    seed: int = None
    vertex_count: int = None
    face_count: int = None


@dataclass
class ProgressGenerationOutput(GenerationOutput):
    state: str
    icon: Icon = None
    title: str = None
    progress: Progress = None


@dataclass
class CompareImagesGenerationOutput(GenerationOutput):
    type = "artifact_output"
    index: int  # Index of the resource to compare / the resource = Image (when generating more than 1 image)
    compare: Tuple[Any, Image]
    to: Tuple[str, Image]

    def __post_init__(self):
        # Same cross-thread lazy-decode race as ImageGenerationOutput - see
        # _force_eager_decode() docstring.
        if self.compare is not None:
            _force_eager_decode(self.compare[1])
        if self.to is not None:
            _force_eager_decode(self.to[1])


@dataclass
class TimerGenerationOutput(GenerationOutput):
    name: str
    value: float
    unit: Literal["s", "ms", "m", "h"] = "s"


@dataclass
class GalleryGenerationOutput(GenerationOutput):
    images: List[ImageGenerationOutput]
    videos: List[VideoGenerationOutput] = field(default_factory=list)
    audios: List["AudioGenerationOutput"] = field(default_factory=list)
    meshes: List[MeshGenerationOutput] = field(default_factory=list)

@dataclass
class SeedGenerationOutput(GenerationOutput):
    type = "artifact_output"
    index: int
    seed: int


@dataclass
class RenderedPromptGenerationOutput(GenerationOutput):
    """One image's fully-rendered prompt pair - the provenance sibling of
    SeedGenerationOutput.

    Every dynamic construct in the authored prompt - ``{a|b}`` choices,
    ``${variables}`` and phrasebook-sourced values - is resolved on the
    backend, once per image against ``base_seed + index``
    (``src/features/prompt/expander.py``). This surfaces the concrete text image
    ``index`` actually ran with, exactly the way the seed is surfaced, so the
    pipeline view/history can show what a prompt template expanded to rather
    than the template itself. Transport-only ``pipe_artifact``; like the seed,
    it is not written back to the generation row.
    """
    type = "artifact_output"
    index: int
    positive: str
    negative: str = ""


@dataclass
class WarmStartGenerationOutput(GenerationOutput):
    """Trajectory warm-start ("iterate mode") telemetry for one image.

    Emitted only when a generation actually resumed from a cached mid-trajectory
    latent (see ``src/platform/runtime/native/sampling/trajectory_cache.py``); a cold run
    emits nothing. ``resume_step``/``steps_skipped`` are the depth the run
    resumed at; ``similarity`` is the cosine between this run's pooled
    conditioning and the cached run's.
    """
    type = "artifact_output"
    index: int
    resume_step: int
    total_steps: int
    steps_skipped: int
    similarity: float


@dataclass
class DiffTextGenerationOutput(GenerationOutput):
    type = "artifact_output"
    index: int
    name: str
    diff: str
    # False when the encoder never sends this prompt to the model (a negative
    # prompt at resolved guidance <= 1.0 with NAG off): the text was authored
    # and recorded but has no effect on the output. Defaults True so the
    # positive diff and every already-applied prompt keep their meaning.
    negative_applied: bool = True

@dataclass
class ModelGenerationOutput(GenerationOutput):
    type = "artifact_output"
    name: str
    type: Literal["checkpoint", "upscaler", "lora", "clip", "vae", "other", "embedding"]
    weight: float = None

@dataclass
class ModelsGenerationOutput(GenerationOutput):
    type = "artifact_output"
    models: List[ModelGenerationOutput]


@dataclass
class ComfyUIWorkflowGenerationOutput(GenerationOutput):
    """Output containing the built ComfyUI workflow for debugging/display"""
    type = "artifact_output"
    workflow: Dict[str, Any]  # The complete workflow JSON
    node_count: int = None    # Number of nodes in workflow
    workflow_file: str = None # Original workflow file path


@dataclass
class ParamGenerationOutput(GenerationOutput):
    name: str  # Name of the parameter (e.g., "seed", "cfg", "steps")
    values: List[Any]  # List of values, one per generated image


@dataclass
class ErrorGenerationOutput(GenerationOutput):
    """
    Emitted by GenerationManager when a pipe raises an unhandled exception,
    immediately before re-raising. Lets the WebSocket layer inform the
    frontend of the failure (message_type "generation_error") while the
    exception itself propagates so the backend/status tracker can transition
    the generation to FAILED.

    `detail` carries an optional longer body (Python traceback, or a backend's
    richer error text such as ComfyUI node errors) shown as the expandable body
    of the failure notification.
    """
    error: str
    detail: str = None


class GenerationExecutionError(Exception):
    """
    Raised by a pipe/backend to signal a real generation failure while
    attaching an optional richer `detail` body (e.g. ComfyUI node errors +
    traceback). GenerationManager reads `.detail` when building the
    ErrorGenerationOutput so backend-specific detail is preserved instead of
    being overwritten by the Python-side traceback.
    """

    def __init__(self, message: str, *, detail: str = None):
        super().__init__(message)
        self.detail = detail
