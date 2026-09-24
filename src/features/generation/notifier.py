"""Generation completion/failure notifications.

Two distinct paths, both best-effort so a notification failure never breaks
generation handling: failures fan out through the *global* notification manager
(toast + persistent bell entry), while successful completions go through the
*injected* notification manager. Grouped here so the orchestrator carries no
notification wiring.
"""

import logging
from typing import Any, Callable, Optional

from src.features.generation.failure import GenerationFailure
from src.features.generation.status_tracker import GenerationState

logger = logging.getLogger(__name__)


class GenerationNotifier:
    """Emits generation completed/failed notifications for the owning user."""

    def __init__(self, notification_manager: Optional[Callable[..., Any]] = None):
        """Initialize the notifier.

        Args:
            notification_manager: Optional bound notify callable
                (`functools.partial(operations.notify, collaborators)`) used
                for completion notifications. Failure notifications use the
                global one instead.
        """
        self.notification_manager = notification_manager

    def notify_failure(
        self,
        generation_id: str,
        user_id: Optional[str],
        failure: GenerationFailure,
        include_detail: bool = False,
    ) -> None:
        try:
            from src.platform.plugins.runtime_registries import get_global_notification_manager

            metadata = {
                "generation_id": generation_id,
                **failure.public_payload(generation_id),
            }
            if include_detail:
                metadata.update(failure.admin_payload())

            get_global_notification_manager()(
                level="error",
                title="Generation failed",
                message=failure.message,
                category="generation",
                type="generation.failed",
                user_id=user_id,
                metadata=metadata,
                show_toast=True,
            )
        except Exception as e:
            logger.error(f"Failed to raise generation-failure notification for {generation_id}: {e}")

    def notify_completion(
        self,
        generation_id: str,
        record,
        generation,
        duration: float,
    ) -> None:
        """
        Notify the owning user of a successful completion (best-effort - a
        notification failure must never break generation completion).

        FAILED is intentionally not handled here: `_handle_generation_output`
        already calls `notify_failure` the moment the `ErrorGenerationOutput`
        arrives, before the backend's completion sentinel reaches this method
        (see `in_process_backend.py`'s `finally: emit(None)`, which runs
        unconditionally on both success and failure). Raising here too would
        fire a second "Generation failed" notification for the same event.
        CANCELLED is user-initiated and intentionally not notified either.
        """
        if not (self.notification_manager and generation and generation.user_id):
            return
        if record.state != GenerationState.COMPLETED:
            return
        try:
            self.notification_manager(
                level='success',
                title='Generation completed',
                category='generation',
                user_id=generation.user_id,
                type='generation.completed',
                metadata={
                    'generation_id': generation_id,
                    'preset_id': record.preset_id,
                    'duration': duration,
                }
            )
        except Exception as e:
            logger.error(f"Failed to send generation notification for {generation_id}: {e}")
