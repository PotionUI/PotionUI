"""
Output Processor

Processes generation outputs through the handler system.

This component coordinates the handling of all generation outputs:
- Routes outputs to appropriate handlers
- Updates generation progress in database
- Logs processing results
- Handles errors gracefully

Example:
    processor = OutputProcessor(settings_manager=settings_manager)
    metadata = await processor.process_output(
        generation_id='gen123',
        output=ImageGenerationOutput(...),
        user_id='user123'
    )
    # Returns: {'handler': 'ImageHandler', 'processed': True, 'saved_path': '...'}
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from src.features.generation.output_types import OutputTypeRegistry, output_type_registry
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.settings.settings import SettingsManager
from src.pipelines.outputs import GenerationOutput
from src.features.generation.repository import generation_repo

logger = logging.getLogger(__name__)


class OutputProcessor:
    """
    Processes generation outputs through the handler system.

    This class provides a unified interface for processing all types
    of generation outputs, delegating to the handler class declared on
    each output type's OutputTypeSpec. It also manages database updates
    for generation progress tracking.

    Attributes:
        type_registry: Registry mapping output types to their OutputTypeSpec
        settings_manager: Configuration manager for handler settings
    """

    def __init__(
        self,
        settings_manager: SettingsManager,
        storage_driver: Optional[FileStorageDriver] = None,
        type_registry: Optional[OutputTypeRegistry] = None
    ):
        """
        Initialize the output processor.

        Args:
            settings_manager: Settings manager for accessing configuration
            storage_driver: Where saved generation output bytes actually live
                - handed to every handler it constructs. Local disk by
                default when not injected - see `BaseGenerationOutputHandler`.
            type_registry: Optional custom output type registry. If not provided,
                          uses the shared output_type_registry singleton.
        """
        self.settings_manager = settings_manager
        self.storage_driver = storage_driver
        self.type_registry = type_registry or output_type_registry
        logger.debug(
            f"OutputProcessor initialized with "
            f"{len(self.type_registry.all())} registered output types"
        )

    async def process_output(
        self,
        generation_id: str,
        output: GenerationOutput,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a generation output using the appropriate handler.

        This method:
        1. Finds the appropriate handler from the registry
        2. Executes the handler to process the output
        3. Updates generation progress in the database if applicable
        4. Returns processing metadata

        Args:
            generation_id: The ID of the current generation
            output: The generation output to process
            user_id: The ID of the user who owns the generation

        Returns:
            Dictionary containing processing metadata from the handler.
            Always includes:
            - 'handler': Name of the handler that processed the output
            - 'processed': Boolean indicating success
            - 'error': Error message if processing failed (optional)

        Example:
            >>> processor = OutputProcessor(settings_manager)
            >>> output = ImageGenerationOutput(image_data=..., temporary=False)
            >>> metadata = await processor.process_output('gen123', output, 'user123')
            >>> print(metadata)
            {'handler': 'ImageGenerationOutputHandler', 'processed': True,
             'saved_path': '/outputs/2024-01-01/gen123/image.png'}
        """
        try:
            # Look up the output type's spec and delegate to its handler class
            metadata = await self._process_via_spec(output, generation_id, user_id)

            # Log the processing result
            if metadata.get('processed'):
                logger.debug(
                    f"Successfully processed {type(output).__name__} "
                    f"for generation {generation_id} "
                    f"with {metadata.get('handler', 'Unknown')} handler"
                )
            else:
                logger.warning(
                    f"Failed to process {type(output).__name__} "
                    f"for generation {generation_id}: "
                    f"{metadata.get('error', 'Unknown error')}"
                )

            # Update generation progress in database if output contains progress info
            await self._update_generation_progress(generation_id, output)

            return metadata

        except Exception as e:
            logger.error(
                f"Error in output processor for generation {generation_id}: {str(e)}",
                exc_info=True
            )
            return {
                'handler': 'OutputProcessor',
                'processed': False,
                'error': str(e)
            }

    async def _process_via_spec(
        self,
        output: GenerationOutput,
        generation_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Resolve the OutputTypeSpec for an output and delegate to its handler class.

        Args:
            output: The generation output to process
            generation_id: Generation ID for handler context
            user_id: User ID for file ownership (optional)

        Returns:
            Processing metadata from the handler, or an error dict if no
            handler is registered for the output's type.
        """
        spec = self.type_registry.spec_for(output)

        if spec is None:
            return {
                'handler': 'None',
                'processed': False,
                'error': f"No handler registered for {type(output).__name__}"
            }

        if spec.handler_cls is None:
            # Serialize-only output types (e.g. ErrorGenerationOutput) carry no
            # persistence side effects by design — the websocket serializer is
            # their whole story. Not a failure; don't warn on every error output.
            return {
                'handler': 'serialize-only',
                'processed': True,
            }

        handler = spec.handler_cls(generation_id, user_id, self.settings_manager, self.storage_driver)
        # handler.handle() is synchronous file/DB I/O (image encode, disk
        # writes, thumbnail generation) - run it off the event loop so one
        # slow output doesn't stall every other generation's WebSocket
        # traffic. Safe to thread: each `generation_repo` call opens and
        # closes its own sqlite3 connection (WAL mode, check_same_thread=False,
        # see src/platform/database/database.py), so there's no shared
        # connection object being touched from two threads at once. The
        # caller (OutputBridge.run(), see output_bridge.py) awaits this whole
        # chain before dequeuing the next output for the same generation, so
        # per-generation ordering is unaffected by moving the work to a
        # thread.
        return await asyncio.to_thread(handler.handle, output)

    async def _update_generation_progress(
        self,
        generation_id: str,
        output: GenerationOutput
    ) -> None:
        """
        Update generation progress in the database based on output data.

        This method extracts progress information from the output and updates
        the database record. It handles multiple progress formats including
        direct progress values and progress objects with current/max values.

        Args:
            generation_id: The ID of the generation to update
            output: The generation output containing progress information
        """
        try:
            # Check if output has progress information
            has_progress = hasattr(output, 'progress') and output.progress is not None
            has_step = hasattr(output, 'current_step') and output.current_step is not None

            if not has_progress and not has_step:
                return

            # Calculate progress value
            progress = self._calculate_progress(output) if has_progress else None

            # Extract step information (used for logging only)
            current_step_num = getattr(output, 'current_step_num', None)
            total_steps = getattr(output, 'total_steps', None)

            # Called once per sampling step - offload the blocking write so a
            # slow progress update doesn't stall the event loop for every
            # other in-flight generation.
            await asyncio.to_thread(generation_repo.update_progress, generation_id, progress)

            logger.debug(
                f"Updated progress for generation {generation_id}: "
                f"progress={progress}, step={current_step_num}/{total_steps}"
            )

        except Exception as e:
            logger.error(
                f"Failed to update generation progress for {generation_id}: {str(e)}"
            )
            # Don't re-raise - progress update failure shouldn't stop output processing

    def _calculate_progress(self, output: GenerationOutput) -> float:
        """
        Calculate progress value from output.

        Handles multiple progress formats:
        - Direct float/int value (0.0 to 1.0)
        - Progress object with current/max attributes
        - None (returns 0.0)

        Args:
            output: The generation output containing progress information

        Returns:
            Progress value between 0.0 and 1.0

        Example:
            >>> # Direct value
            >>> output.progress = 0.65
            >>> progress = processor._calculate_progress(output)
            >>> print(progress)  # 0.65

            >>> # Progress object
            >>> output.progress = Progress(current=13, max=20)
            >>> progress = processor._calculate_progress(output)
            >>> print(progress)  # 0.65
        """
        if not hasattr(output, 'progress') or output.progress is None:
            return 0.0

        progress = output.progress

        # Handle progress object with current/max attributes
        if hasattr(progress, 'current') and hasattr(progress, 'max'):
            if progress.max > 0:
                return progress.current / progress.max
            return 0.0

        # Handle direct numeric value
        return float(progress)

    def get_registered_output_types(self) -> list:
        """
        Get a list of all registered OutputTypeSpec entries.

        Custom output types are now registered directly on the shared
        ``output_type_registry`` (e.g. via the ``output_type.register``
        plugin hook), rather than through this processor.

        Returns:
            List of OutputTypeSpec instances currently registered.
        """
        return self.type_registry.all()
