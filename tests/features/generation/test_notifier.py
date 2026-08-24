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

        manager.notify.assert_called_once()
        kwargs = manager.notify.call_args.kwargs
        assert kwargs["title"] == "Generation completed"
        assert kwargs["type"] == "generation.completed"

    def test_failed_state_does_not_notify(self):
        """The FAILED branch was removed: notify_failure already covers it."""
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.FAILED, error="boom"), _generation(), duration=1.0
        )

        manager.notify.assert_not_called()

    def test_cancelled_state_does_not_notify(self):
        manager = Mock()
        notifier = GenerationNotifier(notification_manager=manager)

        notifier.notify_completion(
            "gen-1", _record(GenerationState.CANCELLED), _generation(), duration=1.0
        )

        manager.notify.assert_not_called()

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

        manager.notify.assert_not_called()

    def test_notify_exception_is_swallowed(self):
        manager = Mock()
        manager.notify.side_effect = RuntimeError("ws down")
        notifier = GenerationNotifier(notification_manager=manager)

        # Must not raise: a notification failure can't break generation completion.
        notifier.notify_completion(
            "gen-1", _record(GenerationState.COMPLETED), _generation(), duration=1.0
        )


class TestNotifyFailure:
    @patch("src.features.generation.notifier.generation_repo")
    def test_raises_via_global_manager_with_error_and_detail(self, mock_repo):
        mock_repo.get_by_id.return_value = _generation()
        global_manager = Mock()
        notifier = GenerationNotifier(notification_manager=Mock())

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            return_value=global_manager,
        ):
            notifier.notify_failure("gen-1", "boom", detail="stack trace")

        global_manager.notify.assert_called_once()
        kwargs = global_manager.notify.call_args.kwargs
        assert kwargs["title"] == "Generation failed"
        assert kwargs["type"] == "generation.failed"
        assert kwargs["metadata"]["detail"] == "stack trace"

    @patch("src.features.generation.notifier.generation_repo")
    def test_exception_is_swallowed(self, mock_repo):
        mock_repo.get_by_id.side_effect = RuntimeError("db down")
        notifier = GenerationNotifier(notification_manager=Mock())

        # Must not raise even if looking up the generation fails.
        notifier.notify_failure("gen-1", "boom")

    @patch("src.features.generation.notifier.generation_repo")
    def test_only_one_notification_for_a_failed_generation_end_to_end(self, mock_repo):
        """The two call sites a real failure goes through (notify_failure from
        the error output, notify_completion from the later completion
        sentinel) must add up to exactly one notify() call on the shared
        manager - not two."""
        mock_repo.get_by_id.return_value = _generation()
        shared_manager = Mock()
        notifier = GenerationNotifier(notification_manager=shared_manager)

        with patch(
            "src.platform.plugins.runtime_registries.get_global_notification_manager",
            return_value=shared_manager,
        ):
            notifier.notify_failure("gen-1", "boom", detail="stack trace")

        notifier.notify_completion(
            "gen-1", _record(GenerationState.FAILED, error="boom"), _generation(), duration=1.0
        )

        assert shared_manager.notify.call_count == 1
