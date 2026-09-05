from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Dict, Any, List, Literal, Optional, Callable, TYPE_CHECKING
from src.pipelines.outputs import GenerationOutput
from src.features.backends.model_listing import BackendModel, ModelListingNotSupported
from src.platform.runtime.gpu import DeviceIdentity

if TYPE_CHECKING:
    from src.features.generation.dto import GenerationRequest


class GenerationResult:
    """Result of a generation request"""
    def __init__(self, generation_id: str, status: str, outputs: Dict[str, Any] = None):
        self.generation_id = generation_id
        self.status = status
        self.outputs = outputs or {}


# The coarse, class-level tag every backend implementation carries (see
# `BaseBackend.execution_device` below): "this_host_gpu" for a backend whose
# CLASS is capable of running inference on this process's own GPU,
# "remote" for one that never does, "unestablished" (the default) for
# anything that hasn't declared either. Kept deliberately simple - this is
# the seam other consumers already key off (e.g.
# `src.features.generation.memory_advisory.resolve_device_evidence`, read
# via `getattr(backend, "execution_device", "unestablished")`) - so it is
# NOT where a specific instance's actual configured device (which GPU
# index, or none at all) lives; see `resolve_execution_device()` for that.
ExecutionDevice = Literal["this_host_gpu", "remote", "unestablished"]

# The kind an INSTANCE's `resolve_execution_device()` resolves to - a
# superset of `ExecutionDevice` with one addition: "no_gpu", for a backend
# whose class is `"this_host_gpu"`-capable but whose live configuration
# explicitly selects no GPU at all (e.g. `NativeBackendConfig(device="cpu")`).
# That is genuine evidence there is no applicable GPU reading, not the same
# as "unestablished" (never declared) or "this_host_gpu" (a specific,
# absent, reading would apply).
ExecutionDeviceKind = Literal["this_host_gpu", "no_gpu", "remote", "unestablished"]


@dataclass(frozen=True)
class ExecutionDeviceEvidence:
    """What ONE backend instance actually answers to "where does my
    inference run, and on which physical device" -
    `BaseBackend.resolve_execution_device()`'s return type. Richer than the
    bare `execution_device` class tag: a `NativeBackend` instance can be
    configured for `cpu` or any `cuda:N`, so the class alone can't say which
    GPU (or whether a GPU at all) applies without reading the instance's own
    config - and a preset checker reading a stale/mismatched index must
    never borrow another GPU's reading.

    - `kind="this_host_gpu"`, `gpu_index=N`, `identity=<DeviceIdentity or None>`:
      inference runs on this process's own GPU N. `gpu_index` is display/log
      only (an enumeration index proves nothing - NVML order need not agree
      with CUDA's own remappable ordinal numbering); a caller deciding
      whether ITS OWN GPU reading applies to this backend must compare
      `identity`, not `gpu_index`, against its own device's identity, and
      treat a `None` identity (torch/CUDA couldn't report one) the same as a
      mismatch - never a guess.
    - `kind="no_gpu"`: this backend is explicitly configured with no GPU at
      all (e.g. `device="cpu"`) - definite evidence, not "unknown".
    - `kind="remote"`: inference runs on hardware this process cannot see.
    - `kind="unestablished"` (the default): the backend hasn't declared
      enough to say - never treated as local.
    """

    kind: ExecutionDeviceKind
    # Display/log only - see the `identity` field below for what a
    # correspondence check must actually use. Meaningful only when
    # `kind == "this_host_gpu"`; `None` otherwise.
    gpu_index: Optional[int] = None
    # This device's stable hardware identity (a UUID, not an index) - the
    # ONLY thing a caller may compare against another device's identity to
    # decide "is this the same physical card". Meaningful only when
    # `kind == "this_host_gpu"`; `None` there means the identity could not
    # be established (torch/CUDA unavailable, the index is out of range, or
    # the configured device has no explicit ordinal at all - see `reason`
    # below) - a caller must treat that the same as "no match", never as
    # "assume yes".
    identity: Optional[DeviceIdentity] = None
    # A specific, human-readable note on WHY `identity` is `None` despite
    # `kind == "this_host_gpu"` - e.g. a bare `"cuda"` device (no explicit
    # ordinal: it resolves at runtime to whatever `torch.cuda.current_device()`
    # happens to be for the executing thread at that moment, which this
    # process cannot know in advance and must never guess at). `None` when
    # `identity` is present, or when there's nothing more specific to say
    # than the generic "could not be established". A caller (e.g.
    # `context_builder.build_requirement_context_for_backend`) surfaces this
    # verbatim in its own `unknown` explanation when set, falling back to
    # its own generic wording otherwise.
    reason: Optional[str] = None


class BaseBackend(ABC):
    """
    Abstract base class for all backend implementations.

    Backends are stateless executors: they only know how to start/cancel a
    generation and report health/system info. Generation state (status,
    progress, listing, subscription) is owned exclusively by
    GenerationStatusTracker on the orchestrator side.
    """

    # See `ExecutionDevice` above. A subclass with real knowledge of where
    # its inference runs overrides this at the class level (`NativeBackend`
    # -> "this_host_gpu", `RemoteNativeBackend` -> "remote"); anything else,
    # core or plugin, stays "unestablished" without needing to say so. Left
    # in place (rather than replaced by `resolve_execution_device()`) for
    # the consumers that only need the coarse this-host-vs-not-vs-unknown
    # question and predate the instance-level resolution below.
    execution_device: ClassVar[ExecutionDevice] = "unestablished"

    def __init__(self, backend_config):
        self.config = backend_config
        self.backend_id = backend_config.id
        self.name = backend_config.name
        self.engine = backend_config.engine

    def resolve_execution_device(self) -> ExecutionDeviceEvidence:
        """This INSTANCE's own answer to `ExecutionDeviceEvidence` - the
        default simply wraps the class-level `execution_device` tag with no
        device index, correct for every backend whose class tag alone is
        already the whole story (`RemoteNativeBackend`, any
        "unestablished" plugin backend). Override this - not
        `execution_device` - when an instance's actual device depends on
        its own configuration (`NativeBackend`, from `self.config.device`)."""
        return ExecutionDeviceEvidence(kind=self.execution_device)

    @abstractmethod
    async def start_generation(
        self,
        pipeline_data: Dict[str, Any],
        emit: Callable[[Optional[GenerationOutput]], None]
    ) -> str:
        """
        Start a new generation with processed pipeline data and return the generation ID.

        This method should only handle execution of the pipeline, not preparation.
        Pipeline preparation (preset processing, form data processing, etc.) is handled
        at the orchestrator level before calling this method.

        Args:
            pipeline_data: Processed pipeline data containing:
                - generation_id: Pre-generated generation ID
                - preset_id: ID of the preset being used
                - pipes: Processed pipeline configuration ready for execution
            emit: Sync, thread-safe callable for real-time generation outputs.
                Safe to call from any thread (including a background
                pipe-execution thread) - it never blocks the caller. Call
                with ``None`` to signal completion.

        Returns:
            str: Generation ID (should match the one provided in pipeline_data)
        """
        pass

    @abstractmethod
    async def cancel_generation(self, generation_id: str) -> bool:
        """
        Cancel a running generation

        Args:
            generation_id: The generation ID to cancel

        Returns:
            bool: True if cancellation was successful
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health status of the backend

        Returns:
            Dict containing health status information
        """
        pass

    @abstractmethod
    async def get_system_info(self) -> Dict[str, Any]:
        """
        Get system information from the backend

        Returns:
            Dict containing system information (GPU, memory, etc.)
        """
        pass

    async def download_generation_files(self, generation_id: str, local_path: str) -> bool:
        """
        Download generated files from the backend to local storage
        This is mainly for remote backends - local backends don't need to implement this

        Args:
            generation_id: The generation ID
            local_path: Local path to save files

        Returns:
            bool: True if download was successful
        """
        # Default implementation for local backends
        return True

    async def upload_input_files(self, files: Dict[str, Any]) -> Dict[str, str]:
        """
        Upload input files to the backend
        This is mainly for remote backends - local backends don't need to implement this

        Args:
            files: Dictionary of files to upload

        Returns:
            Dict mapping original file paths to backend file paths
        """
        # Default implementation for local backends
        return files

    def supports_model_listing(self) -> bool:
        """
        Whether this backend can enumerate the models it is able to load.

        Backends that return False are never indexed; nothing can be said about what
        they hold, so generation cannot be routed to them by model availability.
        """
        return False

    async def list_models(self) -> List[BackendModel]:
        """
        Enumerate every model this backend can load, across all model types.

        Returns entries whose `ref` is the engine-native identifier this backend
        expects back. Implementations should report `size` when they can and `sha256`
        only when the bytes were actually read - `BackendModel.confidence` derives
        from those, and the difference is surfaced to the user rather than smoothed
        over.

        This is part of the plugin-facing API, alongside `prepare_pipes`.

        Raises:
            ModelListingNotSupported: if `supports_model_listing()` is False.
        """
        raise ModelListingNotSupported(
            f"Backend '{self.name}' (engine={self.engine}) cannot enumerate its models"
        )

    def is_available(self) -> bool:
        """
        Check if the backend is currently available for new generations

        Returns:
            bool: True if available
        """
        return self.config.enabled

    def get_timeout_seconds(self) -> int:
        """
        Get the timeout for generation requests

        Returns:
            int: Timeout in seconds
        """
        return self.config.timeout_seconds

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(id={self.backend_id}, name={self.name}, engine={self.engine})"

    def __repr__(self) -> str:
        return self.__str__()
