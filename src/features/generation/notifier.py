"""Generation completion/failure notifications.

Two distinct paths, both best-effort so a notification failure never breaks
generation handling: failures fan out through the *global* notification manager
(toast + persistent bell entry), while successful completions go through the
*injected* notification manager. Grouped here so the orchestrator carries no
notification wiring.
"""

import logging
from typing import Optional, TYPE_CHECKING

from src.features.generation.repository import generation_repo
from src.features.generation.status_tracker import GenerationState

if TYPE_CHECKING:
    from src.features.notifications.manager import NotificationManager

logger = logging.getLogger(__name__)


class GenerationNotifier:
    """Emits generation completed/failed notifications for the owning user."""

    def __init__(self, notification_manager: Optional['NotificationManager'] = None):
        """Initialize the notifier.

        Args:
            notification_manager: Optional manager used for completion
                notifications. Failure notifications use the global manager.
        """
        self.notification_manager = notification_manager

    def notify_failure(
        self,
        generation_id: str,
        error: str,
        detail: Optional[str] = None,
    ) -> None:
        """
        Raise a persistent, toast-surfaced notification for a failed generation.

        Reuses NotificationManager (the same pipeline as every other app
        notification): `show_toast=True` fans out to both a transient toast and
        a persistent bell-panel entry, with the error body carried in
        `metadata.detail`. Best-effort - a notification failure must never break
        generation handling, so everything is wrapped in try/except.
        """
        try:
            from src.platform.plugins.runtime_registries import get_global_notification_manager

            generation = generation_repo.get_by_id(generation_id)
            user_id = generation.user_id if generation else None

            get_global_notification_manager().notify(
                level="error",
                title="Generation failed",
                message=error or "Generation failed",
                category="generation",
                type="generation.failed",
                user_id=user_id,
                metadata={"generation_id": generation_id, "detail": detail},
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
            self.notification_manager.notify(
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
