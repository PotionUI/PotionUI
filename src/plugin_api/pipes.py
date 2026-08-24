"""Contributing a pipe.

A pipe is one step of a generation pipeline. Subclass `BasePipe`, declare what
it consumes and produces (`PipeInputSpec`, `PipeOutputSpec`, `PipeConfigSpec`,
typed by `IOType`), and list it in the manifest's `pipes:` section - presets can
then place it in a pipeline like any built-in pipe.

While it runs, a pipe emits outputs rather than returning them: a
`ProgressGenerationOutput` to report where it is, an `ImageGenerationOutput` or
`VideoGenerationOutput` to hand back what it made. `Icon` and `Progress` dress
those up for the UI. Raise `GenerationExecutionError` to fail the generation with
a message the user will see.

A pipe that needs model weights must not fetch them itself. Declare
`PipeInputSpec("ASSETS", IOType.SERVICE, False, ...)` and the generation manager
injects an `AssetFetcher`: `ensure_asset_file(url, subdir=...)` for a single
file, `ensure_asset_repo(repo_id, subdir=...)` for a Hugging Face repo to load
`from_pretrained` out of. Name the destination with `asset_subdir`, catch
`AssetFetchError`. Fetching directly (`requests`, `hf_hub_download`, or a repo
id handed to `from_pretrained`) skips the download history, the configured
depot and progress reporting.
"""

from src.platform.assets import AssetFetchError, AssetFetcher, asset_subdir
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    logger,
)
from src.pipelines.outputs import (
    AudioGenerationOutput,
    ComfyUIWorkflowGenerationOutput,
    GalleryGenerationOutput,
    GenerationExecutionError,
    GenerationOutput,
    Icon,
    ImageGenerationOutput,
    MeshGenerationOutput,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
from src.features.generation.output_types import (
    DuplicateOutputTypeError,
    OutputTypeSpec,
    SerializeContext,
    output_type_registry,
)

__all__ = [
    "AssetFetchError",
    "AssetFetcher",
    "AudioGenerationOutput",
    "BasePipe",
    "ComfyUIWorkflowGenerationOutput",
    "DuplicateOutputTypeError",
    "GalleryGenerationOutput",
    "GenerationExecutionError",
    "GenerationOutput",
    "IOType",
    "Icon",
    "ImageGenerationOutput",
    "MeshGenerationOutput",
    "OutputTypeSpec",
    "PipeConfigSpec",
    "PipeInput",
    "PipeInputSpec",
    "PipeOutput",
    "PipeOutputSpec",
    "Progress",
    "ProgressGenerationOutput",
    "SerializeContext",
    "VideoGenerationOutput",
    "asset_subdir",
    "logger",
    "output_type_registry",
]
