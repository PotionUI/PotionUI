from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING
from src.pipelines.outputs import GenerationOutput
from src.features.backends.model_listing import BackendModel, ModelListingNotSupported

if TYPE_CHECKING:
    from src.features.generation.dto import GenerationRequest


class GenerationResult:
    """Result of a generation request"""
    def __init__(self, generation_id: str, status: str, outputs: Dict[str, Any] = None):
        self.generation_id = generation_id
        self.status = status
        self.outputs = outputs or {}


class BaseBackend(ABC):
    """
    Abstract base class for all backend implementations.

    Backends are stateless executors: they only know how to start/cancel a
    generation and report health/system info. Generation state (status,
    progress, listing, subscription) is owned exclusively by
    GenerationStatusTracker on the orchestrator side.
    """

    def __init__(self, backend_config):
        self.config = backend_config
        self.backend_id = backend_config.id
        self.name = backend_config.name
        self.engine = backend_config.engine

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
