"""Tests for the NotificationManager class."""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from src.features.notifications.manager import NotificationManager
from src.features.notifications.types import NotificationTypeSpec
from src.features.notifications.records import Notification, NotificationLevel
from src.features.notifications.repository import NotificationRepository
from src.features.users.repository import UserRepository
from src.platform.security.user import User, AccountType
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext
from src.platform.websocket.notification_connection_manager import NotificationConnectionManager
from src.platform.settings.settings import SettingsManager


class TestNotificationManager:
    """Tests for NotificationManager."""

    @pytest.fixture
    def mock_repository(self):
        return Mock(spec=NotificationRepository)

    @pytest.fixture
    def mock_user_repository(self):
        repo = Mock(spec=UserRepository)
        repo.get_by_id.return_value = User(
            id="user-1", username="user-1", email="user-1@example.com",
            password_hash="hash", account_type=AccountType.USER,
        )
        return repo

    @pytest.fixture
    def mock_plugin_registry(self):
        registry = Mock(spec=PluginRegistry)
        context = HookContext(hook_name="test", plugin_id="test", data={})
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def mock_connection_manager(self):
        return Mock(spec=NotificationConnectionManager)

    @pytest.fixture
    def mock_settings_manager(self):
        settings = Mock(spec=SettingsManager)
        settings.get_setting.return_value = {}
        return settings

    @pytest.fixture
    def manager(
        self, mock_repository, mock_user_repository, mock_plugin_registry,
        mock_connection_manager, mock_settings_manager
    ):
        return NotificationManager(
            notification_repository=mock_repository,
            user_repository=mock_user_repository,
            plugin_registry=mock_plugin_registry,
            connection_manager=mock_connection_manager,
            settings_manager=mock_settings_manager
        )

    def _make_notification(self, user_id="user-1", **overrides):
        defaults = dict(
            id="notif-1",
            user_id=user_id,
            category="system",
            level=NotificationLevel.INFO,
            title="Title",
            message="Message",
            metadata=None,
            source="core",
            read=False,
            created_at=datetime.now(),
        )
        defaults.update(overrides)
        return Notification(**defaults)

    def _make_user(self, user_id):
        return User(
            id=user_id,
            username=f"user-{user_id}",
            email=f"{user_id}@example.com",
            password_hash="hash",
            account_type=AccountType.USER,
        )

    def _make_admin(self, user_id):
        return User(
            id=user_id,
            username=f"admin-{user_id}",
            email=f"{user_id}@example.com",
            password_hash="hash",
            account_type=AccountType.ADMIN,
        )

    # ========== Persist per-target ==========

    def test_notify_persists_for_specific_user(self, manager, mock_repository, mock_connection_manager):
        notification = self._make_notification(user_id="user-1")
        mock_repository.create.return_value = notification

        result = manager.notify(level="info", title="Title", message="Message", user_id="user-1")

        mock_repository.create.assert_called_once_with(
            user_id="user-1", category="system", level="info", title="Title",
            message="Message", metadata=None, source="core", type=""
        )
        mock_repository.prune.assert_called_once_with("user-1", keep=200)
        assert result == [notification]

        mock_connection_manager.schedule_send.assert_called_once()
        call_args = mock_connection_manager.schedule_send.call_args
        assert call_args[0][0] == "user-1"
        assert call_args[0][1]["type"] == "notification"
        assert call_args[0][1]["show_toast"] is True

    # ========== Broadcast fan-out ==========

    def test_notify_broadcast_fans_out_to_all_users(
        self, manager, mock_repository, mock_user_repository, mock_connection_manager
    ):
        mock_user_repository.get_all.return_value = [
            self._make_user("user-1"), self._make_user("user-2")
        ]
        mock_repository.create.side_effect = [
            self._make_notification(user_id="user-1", id="n1"),
            self._make_notification(user_id="user-2", id="n2"),
        ]

        result = manager.notify(level="success", title="System notice", category="system")

        assert mock_repository.create.call_count == 2
        assert len(result) == 2
        assert {n.user_id for n in result} == {"user-1", "user-2"}
        assert mock_connection_manager.schedule_send.call_count == 2

    # ========== Transient ==========

    def test_notify_transient_skips_persistence_and_sends_toast(
        self, manager, mock_repository, mock_connection_manager
    ):
        result = manager.notify(
            level="warning", title="Heads up", message="body", user_id="user-1", transient=True
        )

        assert result == []
        mock_repository.create.assert_not_called()
        mock_connection_manager.schedule_send.assert_called_once_with(
            "user-1",
            {"type": "toast", "level": "warning", "title": "Heads up", "message": "body", "category": "system"}
        )

    # ========== before_create hook ==========

    def test_before_create_hook_mutates_title(self, manager, mock_repository, mock_plugin_registry):
        context = HookContext(
            hook_name="notification.before_create",
            plugin_id="test",
            data={"title": "Rewritten title"}
        )
        mock_plugin_registry.execute_hook.return_value = (context, [])
        mock_repository.create.return_value = self._make_notification(title="Rewritten title")

        manager.notify(level="info", title="Original title", user_id="user-1")

        _, kwargs = mock_repository.create.call_args
        assert kwargs["title"] == "Rewritten title"

    def test_before_create_hook_blocks_notification(self, manager, mock_repository, mock_connection_manager):
        context = HookContext(
            hook_name="notification.before_create",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Suppressed by policy"}
        )
        manager.plugins.execute_hook.return_value = (context, [])

        result = manager.notify(level="info", title="Blocked", user_id="user-1")

        assert result == []
        mock_repository.create.assert_not_called()
        mock_connection_manager.schedule_send.assert_not_called()

    # ========== Validation ==========

    def test_notify_invalid_level_raises(self, manager):
        with pytest.raises(ValueError):
            manager.notify(level="not-a-level", title="Bad", user_id="user-1")

    # ========== Prune ==========

    def test_notify_calls_prune_per_target(self, manager, mock_repository, mock_user_repository):
        mock_user_repository.get_all.return_value = [self._make_user("user-1"), self._make_user("user-2")]
        mock_repository.create.side_effect = [
            self._make_notification(user_id="user-1"),
            self._make_notification(user_id="user-2"),
        ]

        manager.notify(level="info", title="Broadcast")

        assert mock_repository.prune.call_count == 2
        mock_repository.prune.assert_any_call("user-1", keep=200)
        mock_repository.prune.assert_any_call("user-2", keep=200)

    # ========== Mutation sync events ==========

    def test_mark_read_emits_sync_event(self, manager, mock_repository, mock_connection_manager):
        mock_repository.mark_read.return_value = True

        result = manager.mark_read("notif-1", "user-1")

        assert result is True
        mock_connection_manager.schedule_send.assert_called_once_with(
            "user-1", {"type": "notification_read", "id": "notif-1"}
        )

    def test_mark_read_no_event_when_not_found(self, manager, mock_repository, mock_connection_manager):
        mock_repository.mark_read.return_value = False

        result = manager.mark_read("missing", "user-1")

        assert result is False
        mock_connection_manager.schedule_send.assert_not_called()

    def test_mark_all_read_emits_sync_event(self, manager, mock_repository, mock_connection_manager):
        mock_repository.mark_all_read.return_value = 3

        result = manager.mark_all_read("user-1")

        assert result == 3
        mock_connection_manager.schedule_send.assert_called_once_with("user-1", {"type": "all_read"})

    def test_delete_emits_sync_event(self, manager, mock_repository, mock_connection_manager):
        mock_repository.delete.return_value = True

        result = manager.delete("notif-1", "user-1")

        assert result is True
        mock_connection_manager.schedule_send.assert_called_once_with(
            "user-1", {"type": "notification_deleted", "id": "notif-1"}
        )

    def test_clear_emits_sync_event(self, manager, mock_repository, mock_connection_manager):
        mock_repository.delete_all.return_value = 5

        result = manager.clear("user-1")

        assert result == 5
        mock_connection_manager.schedule_send.assert_called_once_with("user-1", {"type": "notifications_cleared"})

    # ========== WS failure never raises ==========

    def test_notify_swallows_ws_send_failure(self, manager, mock_repository, mock_connection_manager):
        mock_repository.create.return_value = self._make_notification(user_id="user-1")
        mock_connection_manager.schedule_send.side_effect = Exception("socket exploded")

        # Should not raise despite the WS layer blowing up.
        result = manager.notify(level="info", title="Title", user_id="user-1")

        assert len(result) == 1

    def test_mark_read_swallows_ws_send_failure(self, manager, mock_repository, mock_connection_manager):
        mock_repository.mark_read.return_value = True
        mock_connection_manager.schedule_send.side_effect = Exception("socket exploded")

        result = manager.mark_read("notif-1", "user-1")

        assert result is True

    # Reads (list_notifications/unread_count) go straight from the controller
    # to NotificationRepository - the manager has no read-side logic left to
    # cover.

    # ========== Type filtering ==========

    def test_disabled_type_skips_target_entirely(
        self, manager, mock_repository, mock_connection_manager, mock_plugin_registry, mock_settings_manager
    ):
        """A user who disabled a type gets no row, no WS push, and no after_create hook."""
        mock_settings_manager.get_setting.return_value = {"types": {"generation.completed": False}}

        result = manager.notify(
            level="success", title="Done", user_id="user-1", type="generation.completed"
        )

        assert result == []
        mock_repository.create.assert_not_called()
        mock_connection_manager.schedule_send.assert_not_called()
        # Only the before_create hook should have fired - never after_create.
        assert mock_plugin_registry.execute_hook.call_count == 1

    def test_enabled_type_delivers_normally(self, manager, mock_repository, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {"types": {"generation.completed": True}}
        mock_repository.create.return_value = self._make_notification(type="generation.completed")

        result = manager.notify(
            level="success", title="Done", user_id="user-1", type="generation.completed"
        )

        assert len(result) == 1
        mock_repository.create.assert_called_once()

    def test_unregistered_type_is_delivered(self, manager, mock_repository, mock_settings_manager):
        """A type key with no matching spec is always delivered (no way to know its default)."""
        mock_settings_manager.get_setting.return_value = {}
        mock_repository.create.return_value = self._make_notification(type="totally.unknown")

        result = manager.notify(level="info", title="Hi", user_id="user-1", type="totally.unknown")

        assert len(result) == 1

    def test_empty_type_is_always_delivered(self, manager, mock_repository, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {"types": {}}
        mock_repository.create.return_value = self._make_notification()

        result = manager.notify(level="info", title="Hi", user_id="user-1")

        assert len(result) == 1

    def test_default_enabled_false_spec_respected_without_override(self, manager, mock_repository, mock_settings_manager):
        """No explicit user preference -> falls back to the spec's default_enabled."""
        mock_settings_manager.get_setting.return_value = {}
        disabled_by_default = NotificationTypeSpec(key="quiet.thing", label="Quiet", default_enabled=False)

        with patch("src.features.notifications.manager.notification_type_registry.get", return_value=disabled_by_default):
            result = manager.notify(level="info", title="Hi", user_id="user-1", type="quiet.thing")

        assert result == []
        mock_repository.create.assert_not_called()

    def test_broadcast_filters_per_user_independently(
        self, manager, mock_repository, mock_user_repository, mock_settings_manager, mock_connection_manager
    ):
        mock_user_repository.get_all.return_value = [self._make_user("user-1"), self._make_user("user-2")]
        mock_repository.create.return_value = self._make_notification(type="generation.completed")

        def _get_setting(key, default=None, user_id=None):
            if user_id == "user-1":
                return {"types": {"generation.completed": False}}
            return {"types": {"generation.completed": True}}

        mock_settings_manager.get_setting.side_effect = _get_setting

        result = manager.notify(level="success", title="Broadcast", type="generation.completed")

        # Only user-2 (enabled) gets a row; user-1 (disabled) is skipped entirely.
        assert len(result) == 1
        mock_repository.create.assert_called_once()
        _, kwargs = mock_repository.create.call_args
        assert kwargs["user_id"] == "user-2"

    def test_transient_broadcast_bypasses_type_filter(self, manager, mock_connection_manager, mock_settings_manager):
        """A transient notification with user_id=None (broadcast) is delivered unfiltered."""
        mock_settings_manager.get_setting.return_value = {"types": {"generation.completed": False}}

        result = manager.notify(
            level="info", title="Broadcast toast", transient=True, type="generation.completed"
        )

        assert result == []  # transient never persists
        mock_connection_manager.schedule_send.assert_called_once()
        call_args = mock_connection_manager.schedule_send.call_args
        assert call_args[0][0] is None
        assert call_args[0][1]["type"] == "toast"

    def test_transient_with_user_id_respects_type_filter(self, manager, mock_connection_manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {"types": {"generation.completed": False}}

        result = manager.notify(
            level="info", title="toast", user_id="user-1", transient=True, type="generation.completed"
        )

        assert result == []
        mock_connection_manager.schedule_send.assert_not_called()

    # ========== is_type_enabled ==========

    def test_is_type_enabled_empty_type_true(self, manager):
        assert manager.is_type_enabled("user-1", "") is True

    def test_is_type_enabled_unregistered_type_true(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {}
        assert manager.is_type_enabled("user-1", "no.such.type") is True

    def test_is_type_enabled_explicit_override_wins_over_default(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {"types": {"generation.completed": False}}
        assert manager.is_type_enabled("user-1", "generation.completed") is False

    # ========== get_preferences / update_preferences ==========

    def test_get_preferences_returns_all_types_with_defaults(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {}

        prefs = manager.get_preferences("user-1")

        assert prefs["sound"] is False
        keys = {t["key"] for t in prefs["types"]}
        assert "generation.completed" in keys
        assert "generation.failed" in keys
        # admin_only type omitted for a non-admin (see admin_only tests below).
        assert "system.plugins" not in keys
        for t in prefs["types"]:
            assert t["enabled"] == t["default_enabled"]

    def test_get_preferences_reflects_stored_overrides_and_sound(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {
            "types": {"generation.completed": False}, "sound": True
        }

        prefs = manager.get_preferences("user-1")

        assert prefs["sound"] is True
        by_key = {t["key"]: t for t in prefs["types"]}
        assert by_key["generation.completed"]["enabled"] is False

    def test_update_preferences_merges_types_and_sound(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {
            "types": {"generation.completed": False}, "sound": False
        }

        result = manager.update_preferences(
            "user-1", types={"generation.failed": False}, sound=True
        )

        mock_settings_manager.set_setting.assert_called_once()
        args, kwargs = mock_settings_manager.set_setting.call_args
        assert args[0] == "notification_preferences"
        stored = args[1]
        assert stored["types"] == {"generation.completed": False, "generation.failed": False}
        assert stored["sound"] is True
        assert kwargs["user_id"] == "user-1"

        # Result is the fresh get_preferences() shape.
        assert "types" in result and "sound" in result

    def test_update_preferences_partial_leaves_other_field_untouched(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {"types": {}, "sound": True}

        manager.update_preferences("user-1", types={"generation.completed": False})

        args, _ = mock_settings_manager.set_setting.call_args
        assert args[1]["sound"] is True

    def test_update_preferences_unknown_type_raises(self, manager, mock_settings_manager):
        mock_settings_manager.get_setting.return_value = {}

        with pytest.raises(ValueError):
            manager.update_preferences("user-1", types={"no.such.type": True})

        mock_settings_manager.set_setting.assert_not_called()

    # ========== admin_only types ==========

    def test_is_type_enabled_admin_only_false_for_non_admin(self, manager, mock_user_repository, mock_settings_manager):
        mock_user_repository.get_by_id.return_value = self._make_user("user-1")
        mock_settings_manager.get_setting.return_value = {}

        assert manager.is_type_enabled("user-1", "system.plugins") is False

    def test_is_type_enabled_admin_only_true_for_admin(self, manager, mock_user_repository, mock_settings_manager):
        mock_user_repository.get_by_id.return_value = self._make_admin("admin-1")
        mock_settings_manager.get_setting.return_value = {}

        assert manager.is_type_enabled("admin-1", "system.plugins") is True

    def test_get_preferences_omits_admin_only_type_for_non_admin(self, manager, mock_user_repository, mock_settings_manager):
        mock_user_repository.get_by_id.return_value = self._make_user("user-1")
        mock_settings_manager.get_setting.return_value = {}

        prefs = manager.get_preferences("user-1")

        assert "system.plugins" not in {t["key"] for t in prefs["types"]}

    def test_get_preferences_includes_admin_only_type_for_admin(self, manager, mock_user_repository, mock_settings_manager):
        mock_user_repository.get_by_id.return_value = self._make_admin("admin-1")
        mock_settings_manager.get_setting.return_value = {}

        prefs = manager.get_preferences("admin-1")

        assert "system.plugins" in {t["key"] for t in prefs["types"]}

    def test_broadcast_admin_only_type_skips_non_admin_targets(
        self, manager, mock_repository, mock_user_repository, mock_connection_manager, mock_settings_manager
    ):
        """A persisted broadcast of an admin_only type creates rows/WS pushes only for admins."""
        admin = self._make_admin("admin-1")
        user = self._make_user("user-1")
        mock_user_repository.get_all.return_value = [admin, user]

        def _get_by_id(user_id):
            return {"admin-1": admin, "user-1": user}[user_id]

        mock_user_repository.get_by_id.side_effect = _get_by_id
        mock_settings_manager.get_setting.return_value = {}
        mock_repository.create.return_value = self._make_notification(user_id="admin-1", type="system.plugins")

        result = manager.notify(
            level="info", title="Plugin installed", category="system", type="system.plugins"
        )

        assert len(result) == 1
        mock_repository.create.assert_called_once()
        _, kwargs = mock_repository.create.call_args
        assert kwargs["user_id"] == "admin-1"
        mock_connection_manager.schedule_send.assert_called_once()
        assert mock_connection_manager.schedule_send.call_args[0][0] == "admin-1"

    def test_transient_broadcast_admin_only_type_fans_out_to_admins_only(
        self, manager, mock_user_repository, mock_connection_manager, mock_settings_manager
    ):
        """A transient broadcast of an admin_only type never hits the user_id=None all-connections path."""
        admin = self._make_admin("admin-1")
        user = self._make_user("user-1")
        mock_user_repository.get_all.return_value = [admin, user]

        def _get_by_id(user_id):
            return {"admin-1": admin, "user-1": user}[user_id]

        mock_user_repository.get_by_id.side_effect = _get_by_id
        mock_settings_manager.get_setting.return_value = {}

        result = manager.notify(
            level="info", title="Plugin installed", transient=True, type="system.plugins"
        )

        assert result == []
        mock_connection_manager.schedule_send.assert_called_once()
        call_args = mock_connection_manager.schedule_send.call_args
        assert call_args[0][0] == "admin-1"
        assert call_args[0][1]["type"] == "toast"
