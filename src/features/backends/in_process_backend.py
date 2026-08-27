import asyncio
from typing import Any, Callable, Dict, List, Optional, Set

from src.platform.util.ids import generate_ulid

from .base_backend import BaseBackend
from .pipeline_executor import PipelineExecutor
from src.pipelines.outputs import GenerationOutput
from src.platform.observability.logger import logger


class InProcessBackend(BaseBackend):
    """
    Base class for backends that execute the pipeline inside this process, on a
    background thread, through an injected PipelineExecutor.

    Both engines currently execute in-process: the `native` engine runs diffusers
    pipes directly, and the `comfyui` engine runs a pipeline whose ComfyUIPipe
    talks to a ComfyUI server over HTTP. What differs between them is only how the
    pipes are prepared before execution - hence `prepare_pipes`.

    Subclasses (including plugin-provided ones) override `prepare_pipes` to inject
    engine-specific configuration, and may override `health_check` /
    `get_system_info` / `cancel_generation`.

    This class is part of the plugin-facing API. Changing its constructor or
    `prepare_pipes` signature breaks third-party backends.
    """

    def __init__(self, backend_config, generation_engine: PipelineExecutor = None):
        super().__init__(backend_config)
        self.generation_engine = generation_engine
        self._active: Set[str] = set()

    def set_generation_engine(self, generation_engine: PipelineExecutor) -> None:
        """Injected by BackendRegistry when the backend is instantiated."""
        self.generation_engine = generation_engine

    def prepare_pipes(self, pipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Hook for engine-specific pipe preparation, called before execution.

        The default implementation passes the pipes through untouched.
        """
        return pipes

    async def start_generation(
        self,
        pipeline_data: Dict[str, Any],
        emit: Callable[[Optional[GenerationOutput]], None]
    ) -> str:
        """Start a generation, executing the prepared pipeline in this process."""
        generation_id = pipeline_data.get('generation_id') or generate_ulid()
        pipes = pipeline_data.get('pipes')

        if not pipes:
            raise ValueError("No pipeline configuration provided")

        if not self.generation_engine:
            raise RuntimeError(f"GenerationEngine not set on {self.__class__.__name__}")

        pipes = self.prepare_pipes(pipes)

        # Preset-scoped model-cache ownership: only NATIVE generations tag/evict
        # the in-process model cache by preset (the comfyui engine executes on a
        # remote server and doesn't own these RAM-resident weights). ``None`` for
        # any non-native engine leaves its cache entries untagged + never evicted
        # by a native preset switch.
        cache_owner = pipeline_data.get('preset_id') if self.engine == 'native' else None

        self._active.add(generation_id)
        asyncio.create_task(self._run(generation_id, pipes, emit, cache_owner))

        logger.info(f"[{self.engine.upper()}_BACKEND] Started generation {generation_id}")
        return generation_id

    async def _run(
        self,
        generation_id: str,
        pipes: list,
        emit: Callable[[Optional[GenerationOutput]], None],
        cache_owner: Optional[str] = None,
    ):
        """Run generation in a background thread and forward completion/failure."""
        try:
            await asyncio.to_thread(
                self.generation_engine.generate, pipes, emit, generation_id, cache_owner
            )
            logger.info(f"[{self.engine.upper()}_BACKEND] Generation {generation_id} completed")
        except Exception as e:
            # The executor already emitted an ErrorGenerationOutput before
            # re-raising; just log here, the orchestrator transitions the tracked
            # status to FAILED from that output.
            logger.error(f"[{self.engine.upper()}_BACKEND] Generation {generation_id} failed: {e}")
        finally:
            self._active.discard(generation_id)
            emit(None)

    async def cancel_generation(self, generation_id: str) -> bool:
        """Cancel a running generation"""
        if generation_id not in self._active:
            return False
        try:
            # Each backend owns its executor, and cancel() verifies the id
            # against the run in flight, so this can never abort a generation
            # belonging to another backend, tab or user.
            if not self.generation_engine.cancel(generation_id):
                logger.info(
                    f"[{self.engine.upper()}_BACKEND] Generation {generation_id} was not running; "
                    f"nothing to cancel"
                )
                return False
            logger.info(f"[{self.engine.upper()}_BACKEND] Cancelled generation {generation_id}")
            return True
        except Exception as e:
            logger.error(
                f"[{self.engine.upper()}_BACKEND] Error cancelling generation {generation_id}: {e}"
            )
            return False
