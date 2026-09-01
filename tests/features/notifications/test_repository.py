"""Tests for the NotificationRepository class."""
import pytest
from unittest.mock import patch

from src.features.notifications.repository import NotificationRepository
from src.features.notifications.records import NotificationLevel


class TestNotificationRepository:
    """Test cases for NotificationRepository against a migrated in-memory DB."""

    @pytest.fixture
    def repository(self, mock_db):
        """Create repository instance with test database (migrations applied).

        The repository resolves `db` at call time via
        `src.platform.database.database.db`, which `mock_db` already patches.
        """
        with patch('src.platform.database.database.db', mock_db):
            yield NotificationRepository()

    # ========== Create / Read ==========

    def test_create_notification(self, repository):
        notification = repository.create(
            user_id="user-1", category="system", level="info",
            title="Hello", message="World", source="core"
        )

        assert notification.id
        assert notification.user_id == "user-1"
        assert notification.category == "system"
        assert notification.level == NotificationLevel.INFO
        assert notification.title == "Hello"
        assert notification.message == "World"
        assert notification.read is False
        assert notification.type == ""

    def test_create_notification_with_type_roundtrips(self, repository):
        notification = repository.create(
            user_id="user-1", category="generation", level="success",
            title="Done", source="core", type="generation.completed"
        )

        assert notification.type == "generation.completed"

        fetched = repository.list_for_user("user-1")
        assert fetched[0].type == "generation.completed"

    def test_create_notification_default_type_is_empty_string(self, repository):
        notification = repository.create(
            user_id="user-1", category="system", level="info", title="No type", source="core"
        )

        assert notification.type == ""
        fetched = repository.list_for_user("user-1")
        assert fetched[0].type == ""

    def test_create_notification_with_metadata_roundtrips(self, repository):
        notification = repository.create(
            user_id="user-1", category="generation", level="success",
            title="Done", metadata={"generation_id": "gen-1", "duration": 12.5}, source="core"
        )

        fetched = repository.list_for_user("user-1")
        assert len(fetched) == 1
        assert fetched[0].metadata == {"generation_id": "gen-1", "duration": 12.5}

    def test_create_notification_without_metadata(self, repository):
        repository.create(user_id="user-1", category="system", level="info", title="No meta", source="core")

        fetched = repository.list_for_user("user-1")
        assert fetched[0].metadata is None

    # ========== list_for_user ==========

    def test_list_for_user_orders_newest_first(self, repository):
        first = repository.create(user_id="user-1", category="system", level="info", title="First", source="core")
        second = repository.create(user_id="user-1", category="system", level="info", title="Second", source="core")

        results = repository.list_for_user("user-1")

        assert [n.id for n in results] == [second.id, first.id]

    def test_list_for_user_scoped_to_user(self, repository):
        repository.create(user_id="user-1", category="system", level="info", title="Mine", source="core")
        repository.create(user_id="user-2", category="system", level="info", title="Not mine", source="core")

        results = repository.list_for_user("user-1")

        assert len(results) == 1
        assert results[0].title == "Mine"

    def test_list_for_user_unread_only(self, repository):
        unread = repository.create(user_id="user-1", category="system", level="info", title="Unread", source="core")
        read_one = repository.create(user_id="user-1", category="system", level="info", title="Read", source="core")
        repository.mark_read(read_one.id, "user-1")

        results = repository.list_for_user("user-1", unread_only=True)

        assert len(results) == 1
        assert results[0].id == unread.id

    def test_list_for_user_keyset_pagination(self, repository):
        ids = [
            repository.create(
                user_id="user-1", category="system", level="info", title=f"N{i}", source="core"
            ).id
            for i in range(5)
        ]

        first_page = repository.list_for_user("user-1", limit=2)
        assert [n.id for n in first_page] == list(reversed(ids))[:2]

        second_page = repository.list_for_user("user-1", limit=2, before_id=first_page[-1].id)
        assert [n.id for n in second_page] == list(reversed(ids))[2:4]

    def test_list_for_user_respects_limit(self, repository):
        for i in range(3):
            repository.create(user_id="user-1", category="system", level="info", title=f"N{i}", source="core")

        results = repository.list_for_user("user-1", limit=2)
        assert len(results) == 2

    # ========== unread_count ==========

    def test_unread_count(self, repository):
        n1 = repository.create(user_id="user-1", category="system", level="info", title="A", source="core")
        repository.create(user_id="user-1", category="system", level="info", title="B", source="core")
        repository.mark_read(n1.id, "user-1")

        assert repository.unread_count("user-1") == 1

    def test_unread_count_zero_for_no_notifications(self, repository):
        assert repository.unread_count("user-1") == 0

    # ========== mark_read / mark_all_read ==========

    def test_mark_read_success(self, repository):
        notification = repository.create(user_id="user-1", category="system", level="info", title="A", source="core")

        result = repository.mark_read(notification.id, "user-1")

        assert result is True
        assert repository.list_for_user("user-1")[0].read is True

    def test_mark_read_wrong_user_fails(self, repository):
        notification = repository.create(user_id="user-1", category="system", level="info", title="A", source="core")

        result = repository.mark_read(notification.id, "user-2")

        assert result is False

    def test_mark_all_read(self, repository):
        repository.create(user_id="user-1", category="system", level="info", title="A", source="core")
        repository.create(user_id="user-1", category="system", level="info", title="B", source="core")
        repository.create(user_id="user-2", category="system", level="info", title="C", source="core")

        updated = repository.mark_all_read("user-1")

        assert updated == 2
        assert repository.unread_count("user-1") == 0
        assert repository.unread_count("user-2") == 1

    # ========== delete / delete_all ==========

    def test_delete_success(self, repository):
        notification = repository.create(user_id="user-1", category="system", level="info", title="A", source="core")

        result = repository.delete(notification.id, "user-1")

        assert result is True
        assert repository.list_for_user("user-1") == []

    def test_delete_wrong_user_fails(self, repository):
        notification = repository.create(user_id="user-1", category="system", level="info", title="A", source="core")

        result = repository.delete(notification.id, "user-2")

        assert result is False
        assert len(repository.list_for_user("user-1")) == 1

    def test_delete_all(self, repository):
        repository.create(user_id="user-1", category="system", level="info", title="A", source="core")
        repository.create(user_id="user-1", category="system", level="info", title="B", source="core")
        repository.create(user_id="user-2", category="system", level="info", title="C", source="core")

        deleted = repository.delete_all("user-1")

        assert deleted == 2
        assert repository.list_for_user("user-1") == []
        assert len(repository.list_for_user("user-2")) == 1

    # ========== prune ==========

    def test_prune_keeps_newest_n(self, repository):
        ids = [
            repository.create(
                user_id="user-1", category="system", level="info", title=f"N{i}", source="core"
            ).id
            for i in range(5)
        ]

        deleted = repository.prune("user-1", keep=2)

        assert deleted == 3
        remaining = repository.list_for_user("user-1")
        assert [n.id for n in remaining] == list(reversed(ids))[:2]

    def test_prune_noop_when_under_limit(self, repository):
        repository.create(user_id="user-1", category="system", level="info", title="A", source="core")

        deleted = repository.prune("user-1", keep=200)

        assert deleted == 0
        assert len(repository.list_for_user("user-1")) == 1

    def test_prune_scoped_to_user(self, repository):
        for i in range(3):
            repository.create(user_id="user-1", category="system", level="info", title=f"N{i}", source="core")
        for i in range(3):
            repository.create(user_id="user-2", category="system", level="info", title=f"M{i}", source="core")

        repository.prune("user-1", keep=1)

        assert len(repository.list_for_user("user-1")) == 1
        assert len(repository.list_for_user("user-2")) == 3
