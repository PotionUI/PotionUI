"""Tests for NotificationController."""
import pytest
from unittest.mock import Mock
from datetime import datetime

from src.features.notifications import operations
from src.features.notifications.collaborators import NotificationCollaborators
from src.features.notifications.routes import NotificationController
from src.features.notifications.dto import CreateNotificationRequest, UpdateNotificationPreferencesRequest
from src.features.notifications.records import Notification, NotificationLevel
from src.platform.security.user import User


class TestNotificationController:
    """Comprehensive tests for NotificationController."""

    @pytest.fixture
    def mock_repository(self):
        return Mock()

    @pytest.fixture
    def collaborators(self, mock_repository):
        return NotificationCollaborators(
            repository=mock_repository,
            users=Mock(),
            plugins=Mock(),
            connections=Mock(),
            settings=Mock(),
        )

    @pytest.fixture
    def controller(self, collaborators):
        return NotificationController(collaborators)

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
        self, controller, monkeypatch, sample_user, sample_notification
    ):
        mock_notify = Mock(return_value=[sample_notification])
        monkeypatch.setattr(operations, "notify", mock_notify)

        request = CreateNotificationRequest(level=NotificationLevel.INFO, title="Hi", message="there")
        result = await controller.create_notification(request, sample_user)

        assert result.success is True
        assert len(result.data["notifications"]) == 1
        mock_notify.assert_called_once_with(
            controller.collaborators,
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
        self, controller, monkeypatch, sample_user, sample_notification
    ):
        mock_notify = Mock(return_value=[sample_notification])
        monkeypatch.setattr(operations, "notify", mock_notify)

        request = CreateNotificationRequest(
            level=NotificationLevel.INFO, title="Hi", type="generation.completed"
        )
        await controller.create_notification(request, sample_user)

        _, kwargs = mock_notify.call_args
        assert kwargs["type"] == "generation.completed"

    @pytest.mark.asyncio
    async def test_create_notification_defaults_type_to_empty_string(
        self, controller, monkeypatch, sample_user, sample_notification
    ):
        mock_notify = Mock(return_value=[sample_notification])
        monkeypatch.setattr(operations, "notify", mock_notify)

        request = CreateNotificationRequest(level=NotificationLevel.INFO, title="Hi")
        await controller.create_notification(request, sample_user)

        _, kwargs = mock_notify.call_args
        assert kwargs["type"] == ""

    @pytest.mark.asyncio
    async def test_create_notification_invalid_level(self, controller, monkeypatch, sample_user):
        monkeypatch.setattr(operations, "notify", Mock(side_effect=ValueError("Invalid level")))

        request = CreateNotificationRequest(level=NotificationLevel.ERROR, title="Bad")
        result = await controller.create_notification(request, sample_user)

        assert result.success is False
        assert result.error == "create_notification_failed"

    # ========== Mark Read / Read All ==========

    @pytest.mark.asyncio
    async def test_mark_read_success(self, controller, monkeypatch, sample_user):
        mock_mark_read = Mock(return_value=True)
        monkeypatch.setattr(operations, "mark_read", mock_mark_read)

        result = await controller.mark_read("notif-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "notif-1"
        mock_mark_read.assert_called_once_with(controller.collaborators, "notif-1", "user-123")

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self, controller, monkeypatch, sample_user):
        monkeypatch.setattr(operations, "mark_read", Mock(return_value=False))

        result = await controller.mark_read("missing", sample_user)

        assert result.success is False
        assert result.error == "notification_not_found"

    @pytest.mark.asyncio
    async def test_mark_all_read_success(self, controller, monkeypatch, sample_user):
        mock_mark_all_read = Mock(return_value=5)
        monkeypatch.setattr(operations, "mark_all_read", mock_mark_all_read)

        result = await controller.mark_all_read(sample_user)

        assert result.success is True
        assert result.data["updated"] == 5
        mock_mark_all_read.assert_called_once_with(controller.collaborators, "user-123")

    # ========== Delete / Clear ==========

    @pytest.mark.asyncio
    async def test_delete_notification_success(self, controller, monkeypatch, sample_user):
        mock_delete = Mock(return_value=True)
        monkeypatch.setattr(operations, "delete", mock_delete)

        result = await controller.delete_notification("notif-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "notif-1"
        mock_delete.assert_called_once_with(controller.collaborators, "notif-1", "user-123")

    @pytest.mark.asyncio
    async def test_delete_notification_not_found(self, controller, monkeypatch, sample_user):
        monkeypatch.setattr(operations, "delete", Mock(return_value=False))

        result = await controller.delete_notification("missing", sample_user)

        assert result.success is False
        assert result.error == "notification_not_found"

    @pytest.mark.asyncio
    async def test_clear_notifications_success(self, controller, monkeypatch, sample_user):
        mock_clear = Mock(return_value=7)
        monkeypatch.setattr(operations, "clear", mock_clear)

        result = await controller.clear_notifications(sample_user)

        assert result.success is True
        assert result.data["deleted"] == 7
        mock_clear.assert_called_once_with(controller.collaborators, "user-123")

    # ========== Notification Types / Preferences ==========

    @pytest.mark.asyncio
    async def test_get_notification_types_success(self, controller, monkeypatch, sample_user):
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
        mock_get_preferences = Mock(return_value=preferences)
        monkeypatch.setattr(operations, "get_preferences", mock_get_preferences)

        result = await controller.get_notification_types(sample_user)

        assert result.success is True
        assert result.data == preferences
        mock_get_preferences.assert_called_once_with(controller.collaborators, "user-123")

    @pytest.mark.asyncio
    async def test_get_notification_types_error(self, controller, monkeypatch, sample_user):
        monkeypatch.setattr(operations, "get_preferences", Mock(side_effect=Exception("boom")))

        result = await controller.get_notification_types(sample_user)

        assert result.success is False
        assert result.error == "get_notification_types_failed"

    @pytest.mark.asyncio
    async def test_update_preferences_success(self, controller, monkeypatch, sample_user):
        updated = {"types": [], "sound": True}
        mock_update_preferences = Mock(return_value=updated)
        monkeypatch.setattr(operations, "update_preferences", mock_update_preferences)

        request = UpdateNotificationPreferencesRequest(types={"generation.completed": False}, sound=True)
        result = await controller.update_preferences(request, sample_user)

        assert result.success is True
        assert result.data == updated
        mock_update_preferences.assert_called_once_with(
            controller.collaborators, "user-123", types={"generation.completed": False}, sound=True
        )

    @pytest.mark.asyncio
    async def test_update_preferences_partial_body(self, controller, monkeypatch, sample_user):
        mock_update_preferences = Mock(return_value={"types": [], "sound": False})
        monkeypatch.setattr(operations, "update_preferences", mock_update_preferences)

        request = UpdateNotificationPreferencesRequest(sound=False)
        await controller.update_preferences(request, sample_user)

        mock_update_preferences.assert_called_once_with(controller.collaborators, "user-123", types=None, sound=False)

    @pytest.mark.asyncio
    async def test_update_preferences_unknown_type_error(self, controller, monkeypatch, sample_user):
        monkeypatch.setattr(
            operations, "update_preferences",
            Mock(side_effect=ValueError("Unknown notification type: 'bogus'")),
        )

        request = UpdateNotificationPreferencesRequest(types={"bogus": True})
        result = await controller.update_preferences(request, sample_user)

        assert result.success is False
        assert result.error == "update_preferences_failed"
        assert "bogus" in result.message
