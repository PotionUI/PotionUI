"""Tests for NotificationController."""
import pytest
from unittest.mock import Mock
from datetime import datetime

from src.features.notifications.routes import NotificationController
from src.features.notifications.dto import CreateNotificationRequest, UpdateNotificationPreferencesRequest
from src.features.notifications.records import Notification, NotificationLevel
from src.platform.security.user import User


class TestNotificationController:
    """Comprehensive tests for NotificationController."""

    @pytest.fixture
    def mock_manager(self):
        return Mock()

    @pytest.fixture
    def mock_repository(self):
        return Mock()

    @pytest.fixture
    def controller(self, mock_manager, mock_repository):
        return NotificationController(mock_manager, mock_repository)

    @pytest.fixture
    def sample_user(self):
        user = Mock(spec=User)
        user.id = "user-123"
        user.username = "testuser"
        return user

    @pytest.fixture
    def sample_notification(self):
        return Notification(
            id="notif-1",
            user_id="user-123",
            category="system",
            level=NotificationLevel.INFO,
            title="Hello",
            message="World",
            metadata=None,
            source="core",
            read=False,
            created_at=datetime.now(),
        )

    # ========== List Notifications ==========

    @pytest.mark.asyncio
    async def test_list_notifications_success(self, controller, mock_repository, sample_user, sample_notification):
        mock_repository.list_for_user.return_value = [sample_notification]
        mock_repository.unread_count.return_value = 1

        result = await controller.list_notifications(sample_user)

        assert result.success is True
        assert len(result.data["notifications"]) == 1
        assert result.data["unread_count"] == 1
        mock_repository.list_for_user.assert_called_once_with(
            "user-123", limit=50, before_id=None, unread_only=False
        )

    @pytest.mark.asyncio
    async def test_list_notifications_passes_pagination_params(self, controller, mock_repository, sample_user):
        mock_repository.list_for_user.return_value = []
        mock_repository.unread_count.return_value = 0

        await controller.list_notifications(sample_user, limit=10, before="cursor-id", unread_only=True)

        mock_repository.list_for_user.assert_called_once_with(
            "user-123", limit=10, before_id="cursor-id", unread_only=True
        )

    @pytest.mark.asyncio
    async def test_list_notifications_error(self, controller, mock_repository, sample_user):
        mock_repository.list_for_user.side_effect = Exception("db error")

        result = await controller.list_notifications(sample_user)

        assert result.success is False
        assert result.error == "list_notifications_failed"

    # ========== Create Notification ==========

    @pytest.mark.asyncio
    async def test_create_notification_forces_current_user_and_source(
        self, controller, mock_manager, sample_user, sample_notification
    ):
        mock_manager.notify.return_value = [sample_notification]

        request = CreateNotificationRequest(level=NotificationLevel.INFO, title="Hi", message="there")
        result = await controller.create_notification(request, sample_user)

        assert result.success is True
        assert len(result.data["notifications"]) == 1
        mock_manager.notify.assert_called_once_with(
            level=NotificationLevel.INFO,
            title="Hi",
            message="there",
            category="frontend",
            user_id="user-123",
            source="frontend",
            transient=False,
            show_toast=True,
            metadata=None,
            type="",
        )

    @pytest.mark.asyncio
    async def test_create_notification_passes_through_type(
        self, controller, mock_manager, sample_user, sample_notification
    ):
        mock_manager.notify.return_value = [sample_notification]

        request = CreateNotificationRequest(
            level=NotificationLevel.INFO, title="Hi", type="generation.completed"
        )
        await controller.create_notification(request, sample_user)

        _, kwargs = mock_manager.notify.call_args
        assert kwargs["type"] == "generation.completed"

    @pytest.mark.asyncio
    async def test_create_notification_defaults_type_to_empty_string(
        self, controller, mock_manager, sample_user, sample_notification
    ):
        mock_manager.notify.return_value = [sample_notification]

        request = CreateNotificationRequest(level=NotificationLevel.INFO, title="Hi")
        await controller.create_notification(request, sample_user)

        _, kwargs = mock_manager.notify.call_args
        assert kwargs["type"] == ""

    @pytest.mark.asyncio
    async def test_create_notification_invalid_level(self, controller, mock_manager, sample_user):
        mock_manager.notify.side_effect = ValueError("Invalid level")

        request = CreateNotificationRequest(level=NotificationLevel.ERROR, title="Bad")
        result = await controller.create_notification(request, sample_user)

        assert result.success is False
        assert result.error == "create_notification_failed"

    # ========== Mark Read / Read All ==========

    @pytest.mark.asyncio
    async def test_mark_read_success(self, controller, mock_manager, sample_user):
        mock_manager.mark_read.return_value = True

        result = await controller.mark_read("notif-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "notif-1"
        mock_manager.mark_read.assert_called_once_with("notif-1", "user-123")

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self, controller, mock_manager, sample_user):
        mock_manager.mark_read.return_value = False

        result = await controller.mark_read("missing", sample_user)

        assert result.success is False
        assert result.error == "notification_not_found"

    @pytest.mark.asyncio
    async def test_mark_all_read_success(self, controller, mock_manager, sample_user):
        mock_manager.mark_all_read.return_value = 5

        result = await controller.mark_all_read(sample_user)

        assert result.success is True
        assert result.data["updated"] == 5
        mock_manager.mark_all_read.assert_called_once_with("user-123")

    # ========== Delete / Clear ==========

    @pytest.mark.asyncio
    async def test_delete_notification_success(self, controller, mock_manager, sample_user):
        mock_manager.delete.return_value = True

        result = await controller.delete_notification("notif-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "notif-1"
        mock_manager.delete.assert_called_once_with("notif-1", "user-123")

    @pytest.mark.asyncio
    async def test_delete_notification_not_found(self, controller, mock_manager, sample_user):
        mock_manager.delete.return_value = False

        result = await controller.delete_notification("missing", sample_user)

        assert result.success is False
        assert result.error == "notification_not_found"

    @pytest.mark.asyncio
    async def test_clear_notifications_success(self, controller, mock_manager, sample_user):
        mock_manager.clear.return_value = 7

        result = await controller.clear_notifications(sample_user)

        assert result.success is True
        assert result.data["deleted"] == 7
        mock_manager.clear.assert_called_once_with("user-123")

    # ========== Notification Types / Preferences ==========

    @pytest.mark.asyncio
    async def test_get_notification_types_success(self, controller, mock_manager, sample_user):
        preferences = {
            "types": [
                {
                    "key": "generation.completed", "label": "Generation completed",
                    "description": "", "category": "generation",
                    "default_enabled": True, "enabled": True,
                },
            ],
            "sound": False,
        }
        mock_manager.get_preferences.return_value = preferences

        result = await controller.get_notification_types(sample_user)

        assert result.success is True
        assert result.data == preferences
        mock_manager.get_preferences.assert_called_once_with("user-123")

    @pytest.mark.asyncio
    async def test_get_notification_types_error(self, controller, mock_manager, sample_user):
        mock_manager.get_preferences.side_effect = Exception("boom")

        result = await controller.get_notification_types(sample_user)

        assert result.success is False
        assert result.error == "get_notification_types_failed"

    @pytest.mark.asyncio
    async def test_update_preferences_success(self, controller, mock_manager, sample_user):
        updated = {"types": [], "sound": True}
        mock_manager.update_preferences.return_value = updated

        request = UpdateNotificationPreferencesRequest(types={"generation.completed": False}, sound=True)
        result = await controller.update_preferences(request, sample_user)

        assert result.success is True
        assert result.data == updated
        mock_manager.update_preferences.assert_called_once_with(
            "user-123", types={"generation.completed": False}, sound=True
        )

    @pytest.mark.asyncio
    async def test_update_preferences_partial_body(self, controller, mock_manager, sample_user):
        mock_manager.update_preferences.return_value = {"types": [], "sound": False}

        request = UpdateNotificationPreferencesRequest(sound=False)
        await controller.update_preferences(request, sample_user)

        mock_manager.update_preferences.assert_called_once_with("user-123", types=None, sound=False)

    @pytest.mark.asyncio
    async def test_update_preferences_unknown_type_error(self, controller, mock_manager, sample_user):
        mock_manager.update_preferences.side_effect = ValueError("Unknown notification type: 'bogus'")

        request = UpdateNotificationPreferencesRequest(types={"bogus": True})
        result = await controller.update_preferences(request, sample_user)

        assert result.success is False
        assert result.error == "update_preferences_failed"
        assert "bogus" in result.message

