"""Tests for KeybindingController."""
import pytest
from unittest.mock import Mock

from src.features.keybindings.routes import KeybindingController
from src.features.keybindings.dto import UpdateKeybindingRequest
from src.features.keybindings.records import KeybindingDefault
from src.platform.security.user import User


class TestKeybindingController:
    """Comprehensive tests for KeybindingController."""

    @pytest.fixture
    def mock_repo(self):
        """Mock keybinding repository."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_repo):
        """Create controller with mocked repository."""
        return KeybindingController(mock_repo)

    @pytest.fixture
    def sample_user(self):
        """Sample user object."""
        user = Mock(spec=User)
        user.id = "user-123"
        user.username = "testuser"
        return user

    @pytest.fixture
    def sample_effective_keybindings(self):
        """Sample effective keybindings list."""
        return [
            {
                'action_id': 'show_help',
                'key': '?',
                'modifiers': '',
                'label': 'Show Keyboard Shortcuts',
                'category': 'general',
                'context': 'global',
                'description': 'Display all available keyboard shortcuts',
                'enabled': True,
                'is_custom': False,
            },
            {
                'action_id': 'open_chat',
                'key': 'o',
                'modifiers': 'ctrl',
                'label': 'Open AI Chat',
                'category': 'general',
                'context': 'global',
                'description': 'Toggle the AI chat panel',
                'enabled': True,
                'is_custom': True,
            },
        ]

    @pytest.fixture
    def sample_defaults(self):
        """Sample default keybindings."""
        return [
            KeybindingDefault(
                id='show_help',
                key='?',
                modifiers='',
                label='Show Keyboard Shortcuts',
                category='general',
                context='global',
                description='Display all available keyboard shortcuts',
                enabled=True,
                source='system',
                sort_order=0,
            ),
            KeybindingDefault(
                id='open_chat',
                key='c',
                modifiers='',
                label='Open AI Chat',
                category='general',
                context='global',
                description='Toggle the AI chat panel',
                enabled=True,
                source='system',
                sort_order=1,
            ),
        ]

    # ========== Get Effective Keybindings Tests ==========

    @pytest.mark.asyncio
    async def test_get_effective_keybindings_success(self, controller, mock_repo, sample_user, sample_effective_keybindings):
        """Test successful retrieval of effective keybindings."""
        mock_repo.get_effective_keybindings.return_value = sample_effective_keybindings

        result = await controller.get_effective_keybindings(sample_user)

        assert result.success is True
        assert "keybindings" in result.data
        assert len(result.data["keybindings"]) == 2
        assert result.data["total"] == 2
        mock_repo.get_effective_keybindings.assert_called_once_with("user-123")

    @pytest.mark.asyncio
    async def test_get_effective_keybindings_empty(self, controller, mock_repo, sample_user):
        """Test getting keybindings when none exist."""
        mock_repo.get_effective_keybindings.return_value = []

        result = await controller.get_effective_keybindings(sample_user)

        assert result.success is True
        assert len(result.data["keybindings"]) == 0
        assert result.data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_effective_keybindings_error(self, controller, mock_repo, sample_user):
        """Test error handling when retrieval fails."""
        mock_repo.get_effective_keybindings.side_effect = Exception("Database error")

        result = await controller.get_effective_keybindings(sample_user)

        assert result.success is False
        assert result.error == "get_keybindings_failed"
        assert "Database error" in result.message

    # ========== Get Defaults Tests ==========

    @pytest.mark.asyncio
    async def test_get_defaults_success(self, controller, mock_repo, sample_defaults):
        """Test successful retrieval of default keybindings."""
        mock_repo.get_all_defaults.return_value = sample_defaults

        result = await controller.get_defaults()

        assert result.success is True
        assert "keybindings" in result.data
        assert len(result.data["keybindings"]) == 2
        assert result.data["total"] == 2
        # Verify structure of returned keybindings
        kb = result.data["keybindings"][0]
        assert kb["action_id"] == "show_help"
        assert kb["is_custom"] is False

    @pytest.mark.asyncio
    async def test_get_defaults_error(self, controller, mock_repo):
        """Test error handling when getting defaults fails."""
        mock_repo.get_all_defaults.side_effect = Exception("Database error")

        result = await controller.get_defaults()

        assert result.success is False
        assert result.error == "get_defaults_failed"

    # ========== Update Keybinding Tests ==========

    @pytest.mark.asyncio
    async def test_update_keybinding_success(self, controller, mock_repo, sample_user):
        """Test successful keybinding update."""
        mock_repo.set_user_keybinding.return_value = None

        request = UpdateKeybindingRequest(key='h', modifiers='ctrl')
        result = await controller.update_keybinding('show_help', request, sample_user)

        assert result.success is True
        assert "updated successfully" in result.data["message"]
        mock_repo.set_user_keybinding.assert_called_once_with(
            user_id="user-123",
            action_id="show_help",
            key="h",
            modifiers="ctrl"
        )

    @pytest.mark.asyncio
    async def test_update_keybinding_with_defaults(self, controller, mock_repo, sample_user):
        """Test updating keybinding with default modifier values."""
        mock_repo.set_user_keybinding.return_value = None

        request = UpdateKeybindingRequest(key='x')
        result = await controller.update_keybinding('open_chat', request, sample_user)

        assert result.success is True
        mock_repo.set_user_keybinding.assert_called_once_with(
            user_id="user-123",
            action_id="open_chat",
            key="x",
            modifiers=""
        )

    @pytest.mark.asyncio
    async def test_update_keybinding_error(self, controller, mock_repo, sample_user):
        """Test error handling when update fails."""
        mock_repo.set_user_keybinding.side_effect = Exception("Foreign key violation")

        request = UpdateKeybindingRequest(key='h', modifiers='ctrl')
        result = await controller.update_keybinding('nonexistent', request, sample_user)

        assert result.success is False
        assert result.error == "update_keybinding_failed"

    # ========== Reset Keybinding Tests ==========

    @pytest.mark.asyncio
    async def test_reset_keybinding_success(self, controller, mock_repo, sample_user):
        """Test successful single keybinding reset."""
        mock_repo.reset_user_keybinding.return_value = True

        result = await controller.reset_keybinding('show_help', sample_user)

        assert result.success is True
        assert "reset to default" in result.data["message"]
        mock_repo.reset_user_keybinding.assert_called_once_with("user-123", "show_help")

    @pytest.mark.asyncio
    async def test_reset_keybinding_no_custom(self, controller, mock_repo, sample_user):
        """Test resetting when no custom keybinding exists."""
        mock_repo.reset_user_keybinding.return_value = False

        result = await controller.reset_keybinding('show_help', sample_user)

        assert result.success is True
        assert "No custom keybinding found" in result.data["message"]

    @pytest.mark.asyncio
    async def test_reset_keybinding_error(self, controller, mock_repo, sample_user):
        """Test error handling when reset fails."""
        mock_repo.reset_user_keybinding.side_effect = Exception("Database error")

        result = await controller.reset_keybinding('show_help', sample_user)

        assert result.success is False
        assert result.error == "reset_keybinding_failed"

    # ========== Reset All Keybindings Tests ==========

    @pytest.mark.asyncio
    async def test_reset_all_keybindings_success(self, controller, mock_repo, sample_user):
        """Test successful reset of all keybindings."""
        mock_repo.reset_all_user_keybindings.return_value = 5

        result = await controller.reset_all_keybindings(sample_user)

        assert result.success is True
        assert "Reset 5 custom keybinding(s)" in result.data["message"]
        assert result.data["count"] == 5
        mock_repo.reset_all_user_keybindings.assert_called_once_with("user-123")

    @pytest.mark.asyncio
    async def test_reset_all_keybindings_none_to_reset(self, controller, mock_repo, sample_user):
        """Test reset all when no overrides exist."""
        mock_repo.reset_all_user_keybindings.return_value = 0

        result = await controller.reset_all_keybindings(sample_user)

        assert result.success is True
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_reset_all_keybindings_error(self, controller, mock_repo, sample_user):
        """Test error handling when reset all fails."""
        mock_repo.reset_all_user_keybindings.side_effect = Exception("Database error")

        result = await controller.reset_all_keybindings(sample_user)

        assert result.success is False
        assert result.error == "reset_all_keybindings_failed"
