"""Tests for GenerationNotifier.

Regression coverage for the doubled-notification bug: a failed
generation must raise exactly one "Generation failed" notification, not two.
`_handle_generation_output` calls `notify_failure` when the
`ErrorGenerationOutput` arrives; the backend's `finally: emit(None)` then
always delivers a completion sentinel afterward (on both success and
failure), which used to trigger a second, redundant notification out of
`notify_completion`'s FAILED branch.
"""

from unittest.mock import Mock, patch

from src.features.generation.failure import GenerationFailure
from src.features.generation.notifier import GenerationNotifier
from src.features.generation.status_tracker import GenerationState


def _record(state: GenerationState, error: str = None, preset_id: str = "preset-1"):
    record = Mock()
    record.state = state
    record.error = error
    record.preset_id = preset_id
    return record


def _generation(user_id: str = "user-1"):
    generation = Mock()
    generation.user_id = user_id
    return generation


class TestNotifyCompletion:
    def test_completed_state_raises_one_notification(self):
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.COMPLETED), _generation(), duration=1.0
        )

        manager.assert_called_once()
        kwargs = manager.call_args.kwargs
        assert kwargs["title"] == "Generation completed"
        assert kwargs["type"] == "generation.completed"

    def test_failed_state_does_not_notify(self):
        """The FAILED branch was removed: notify_failure already covers it."""
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.FAILED, error="boom"), _generation(), duration=1.0
        )

        manager.assert_not_called()

    def test_cancelled_state_does_not_notify(self):
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.CANCELLED), _generation(), duration=1.0
        )

        manager.assert_not_called()

    def test_no_notification_manager_is_a_noop(self):
        notifier = GenerationNotifier(notification_manager=None)
        # Should not raise even though there's nothing to call notify() on.
        notifier.notify_completion(
            "gen-1", _record(GenerationState.COMPLETED), _generation(), duration=1.0
        )

    def test_no_user_id_is_a_noop(self):
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.COMPLETED), _generation(user_id=None), duration=1.0
        )

        manager.assert_not_called()

    def test_notify_exception_is_swallowed(self):
        manager = Mock()
        manager.side_effect = RuntimeError("ws down")
        notifier = GenerationNotifier(notification_manager=manager)

        # Must not raise: a notification failure can't break generation completion.
        notifier.notify_completion(
            "gen-1", _record(GenerationState.COMPLETED), _generation(), duration=1.0
        )


def _failure():
    return GenerationFailure(
        error_code="disk_full",
        message="The disk is full.",
        hints=("Free up space",),
        raw_error="OSError: [Errno 28] No space left on device: '/srv/outputs/private/x.png'",
        detail="Traceback (most recent call last):\n  File \"/srv/app/src/handler.py\", line 9",
        failed_pipe_id="saver",
        failed_pipe_name="image_saver",
        failed_at_step="Saving 1/1",
    )


class TestNotifyFailure:
    def test_regular_owner_gets_the_safe_message_without_detail(self):
        global_manager = Mock()
        notifier = GenerationNotifier(notification_manager=Mock())

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            return_value=global_manager,
        ):
            notifier.notify_failure("gen-1", "user-1", _failure())

        global_manager.assert_called_once()
        kwargs = global_manager.call_args.kwargs
        assert kwargs["title"] == "Generation failed"
        assert kwargs["type"] == "generation.failed"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["message"] == "The disk is full."
        metadata = kwargs["metadata"]
        assert metadata["generation_id"] == "gen-1"
        assert metadata["error_id"] == "gen-1"
        assert metadata["error_code"] == "disk_full"
        assert metadata["hint"] == "- Free up space"
        assert "detail" not in metadata
        assert "failed_pipe_id" not in metadata
        rendered = str(kwargs)
        assert "/srv/" not in rendered
        assert "Traceback" not in rendered
        assert "Errno" not in rendered

    def test_admin_owner_gets_the_detail(self):
        global_manager = Mock()
        notifier = GenerationNotifier(notification_manager=Mock())

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            return_value=global_manager,
        ):
            notifier.notify_failure("gen-1", "admin-1", _failure(), include_detail=True)

        metadata = global_manager.call_args.kwargs["metadata"]
        assert "No space left on device: '/srv/outputs/private/x.png'" in metadata["detail"]
        assert "Traceback" in metadata["detail"]
        assert metadata["failed_pipe_id"] == "saver"
        assert metadata["failed_pipe_name"] == "image_saver"
        assert metadata["failed_at_step"] == "Saving 1/1"

    def test_exception_is_swallowed(self):
        notifier = GenerationNotifier(notification_manager=Mock())

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            side_effect=RuntimeError("not ready"),
        ):
            notifier.notify_failure("gen-1", "user-1", _failure())

    def test_only_one_notification_for_a_failed_generation_end_to_end(self):
        shared_manager = Mock()
        notifier = GenerationNotifier(notification_manager=shared_manager)

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            return_value=shared_manager,
        ):
            notifier.notify_failure("gen-1", "user-1", _failure())

        notifier.notify_completion(
            "gen-1", _record(GenerationState.FAILED, error="boom"), _generation(), duration=1.0
        )

        assert shared_manager.call_count == 1
