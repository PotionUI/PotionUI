"""McpManager: token minting/hashing and the global/per-user MCP toggles."""

from datetime import datetime, timedelta, timezone

import pytest

from src.features.mcp.manager import McpManager, hash_token, MCP_ENABLED_KEY, MCP_USER_ENABLED_KEY
from src.features.mcp.repository import McpTokenRepository
from src.features.users.repository import UserRepository
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import SettingsManager


@pytest.fixture
def manager(mcp_db):
    token_repository = McpTokenRepository()
    settings_manager = SettingsManager(SettingRepository())
    user_repository = UserRepository()
    return McpManager(
        token_repository=token_repository,
        settings_manager=settings_manager,
        user_repository=user_repository,
    ), token_repository, user_repository


@pytest.fixture
def real_user(manager):
    _mgr, _tokens, user_repository = manager
    return user_repository.create(username="alice", email="alice@example.com", password_hash="x")


class TestMintToken:
    def test_returns_a_prefixed_plaintext_and_persists_only_its_hash(self, manager):
        mgr, token_repository, _users = manager
        token, plaintext = mgr.mint_token("user-1", "laptop")

        assert plaintext.startswith("pui_mcp_")
        assert token.token_hash == hash_token(plaintext)
        assert token.token_hash != plaintext
        assert plaintext not in token.token_hash

        stored = token_repository.get_by_id(token.id)
        assert stored.token_hash == hash_token(plaintext)

    def test_token_prefix_is_a_short_display_slice_of_the_plaintext(self, manager):
        mgr, _tokens, _users = manager
        token, plaintext = mgr.mint_token("user-1", "laptop")
        assert plaintext.startswith(token.token_prefix)
        assert len(token.token_prefix) < len(plaintext)

    def test_two_mints_never_collide(self, manager):
        mgr, _tokens, _users = manager
        _t1, p1 = mgr.mint_token("user-1", "a")
        _t2, p2 = mgr.mint_token("user-1", "b")
        assert p1 != p2


class TestResolveActiveToken:
    def test_resolves_a_freshly_minted_token(self, manager):
        mgr, _tokens, _users = manager
        _token, plaintext = mgr.mint_token("user-1", "laptop")
        resolved = mgr.resolve_active_token(plaintext)
        assert resolved is not None
        assert resolved.user_id == "user-1"

    def test_garbage_token_does_not_resolve(self, manager):
        mgr, _tokens, _users = manager
        assert mgr.resolve_active_token("pui_mcp_not-a-real-token") is None

    def test_revoked_token_does_not_resolve(self, manager):
        mgr, _tokens, _users = manager
        token, plaintext = mgr.mint_token("user-1", "laptop")
        mgr.revoke_token("user-1", token.id)
        assert mgr.resolve_active_token(plaintext) is None


class TestRevoke:
    # Listing tokens reads straight from McpTokenRepository via the
    # controller (src/features/mcp/routes.py) - the manager has no
    # read-side logic left to cover.

    def test_revoke_someone_elses_token_fails(self, manager):
        mgr, _tokens, _users = manager
        token, _plaintext = mgr.mint_token("user-1", "a")
        assert mgr.revoke_token("user-2", token.id) is False


class TestGlobalToggle:
    def test_defaults_to_disabled(self, manager):
        mgr, _tokens, _users = manager
        assert mgr.is_globally_enabled() is False

    def test_reading_the_system_setting_directly_reflects_the_toggle(self, manager, mcp_db):
        mgr, _tokens, _users = manager
        settings_manager = SettingsManager(SettingRepository())
        settings_manager.set_setting(MCP_ENABLED_KEY, True)
        assert mgr.is_globally_enabled() is True


class TestPerUserToggle:
    def test_defaults_to_enabled(self, manager):
        mgr, _tokens, _users = manager
        assert mgr.is_user_enabled("user-1") is True

    def test_set_user_enabled_false_then_true_round_trips(self, manager, real_user):
        mgr, _tokens, _users = manager
        mgr.set_user_enabled(real_user.id, False)
        assert mgr.is_user_enabled(real_user.id) is False
        mgr.set_user_enabled(real_user.id, True)
        assert mgr.is_user_enabled(real_user.id) is True

    def test_toggle_is_isolated_per_user(self, manager, real_user):
        mgr, _tokens, users = manager
        other = users.create(username="bob", email="bob@example.com", password_hash="x")
        mgr.set_user_enabled(real_user.id, False)
        assert mgr.is_user_enabled(real_user.id) is False
        assert mgr.is_user_enabled(other.id) is True

    def test_unknown_user_raises(self, manager):
        mgr, _tokens, _users = manager
        with pytest.raises(ValueError):
            mgr.set_user_enabled("ghost-user", False)


class TestRecordUse:
    def test_first_use_sets_last_used_at(self, manager):
        mgr, tokens, _users = manager
        token, _plaintext = mgr.mint_token("user-1", "a")
        mgr.record_use(token)
        assert tokens.get_by_id(token.id).last_used_at is not None

    def test_a_fresh_timestamp_is_not_touched_again(self, manager):
        mgr, tokens, _users = manager
        token, _plaintext = mgr.mint_token("user-1", "a")
        mgr.record_use(token)
        first = tokens.get_by_id(token.id).last_used_at

        # Same in-memory `token` object (still carrying the fresh timestamp
        # set above) -> the throttle window should skip the write entirely.
        refreshed = tokens.get_by_id(token.id)
        mgr.record_use(refreshed)
        assert tokens.get_by_id(token.id).last_used_at == first

    def test_a_stale_timestamp_is_updated(self, manager, mcp_db):
        mgr, tokens, _users = manager
        token, _plaintext = mgr.mint_token("user-1", "a")
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        # Force the stored timestamp far into the past directly (through the
        # same patched scratch database `mcp_db` yields), then confirm
        # record_use bumps it forward again.
        with mcp_db.get_cursor() as cursor:
            cursor.execute("UPDATE mcp_tokens SET last_used_at = ? WHERE id = ?", (stale, token.id))
        mgr.record_use(tokens.get_by_id(token.id))
        assert tokens.get_by_id(token.id).last_used_at != stale
