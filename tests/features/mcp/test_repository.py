"""McpTokenRepository against a real (scratch, migrated) database."""

from src.features.mcp.repository import McpTokenRepository


class TestCreateAndGet:
    def test_create_then_get_by_id_round_trips(self, mcp_db):
        repo = McpTokenRepository()
        token = repo.create(
            id="tok-1", user_id="user-1", name="laptop",
            token_hash="hash-abc", token_prefix="pui_mcp_ab",
        )
        fetched = repo.get_by_id("tok-1")
        assert fetched is not None
        assert fetched.user_id == "user-1"
        assert fetched.name == "laptop"
        assert fetched.token_hash == "hash-abc"
        assert fetched.token_prefix == "pui_mcp_ab"
        assert fetched.revoked_at is None
        assert fetched.last_used_at is None
        assert token.id == "tok-1"

    def test_get_by_hash_finds_the_matching_row(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="hash-x", token_prefix="pui_mcp_x")
        found = repo.get_by_hash("hash-x")
        assert found is not None
        assert found.id == "tok-1"

    def test_get_by_hash_no_match_returns_none(self, mcp_db):
        repo = McpTokenRepository()
        assert repo.get_by_hash("nope") is None

    def test_get_by_id_missing_returns_none(self, mcp_db):
        repo = McpTokenRepository()
        assert repo.get_by_id("ghost") is None


class TestListForUser:
    def test_only_returns_that_users_tokens(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="h1", token_prefix="p1")
        repo.create(id="tok-2", user_id="user-2", name="b", token_hash="h2", token_prefix="p2")
        tokens = repo.list_for_user("user-1")
        assert [t.id for t in tokens] == ["tok-1"]


class TestRevoke:
    def test_revoke_sets_revoked_at(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="h1", token_prefix="p1")
        revoked = repo.revoke("tok-1", "user-1")
        assert revoked is True
        assert repo.get_by_id("tok-1").revoked_at is not None

    def test_revoke_someone_elses_token_is_a_noop(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="h1", token_prefix="p1")
        revoked = repo.revoke("tok-1", "user-2")
        assert revoked is False
        assert repo.get_by_id("tok-1").revoked_at is None

    def test_revoking_twice_is_a_noop_the_second_time(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="h1", token_prefix="p1")
        assert repo.revoke("tok-1", "user-1") is True
        assert repo.revoke("tok-1", "user-1") is False


class TestTouchLastUsed:
    def test_sets_last_used_at(self, mcp_db):
        repo = McpTokenRepository()
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="h1", token_prefix="p1")
        repo.touch_last_used("tok-1")
        assert repo.get_by_id("tok-1").last_used_at is not None


class TestNoPlaintextStorage:
    def test_only_the_hash_and_display_prefix_are_persisted(self, mcp_db):
        """The repository layer has no field for a plaintext token at all —
        only whatever hash/prefix the caller (McpManager) computed ever
        reaches storage."""
        repo = McpTokenRepository()
        plaintext = "pui_mcp_super-secret-value"
        repo.create(id="tok-1", user_id="user-1", name="a", token_hash="not-the-plaintext", token_prefix="pui_mcp_su")
        fetched = repo.get_by_id("tok-1")
        assert plaintext not in fetched.token_hash
        assert plaintext not in fetched.token_prefix
        assert fetched.token_hash == "not-the-plaintext"
