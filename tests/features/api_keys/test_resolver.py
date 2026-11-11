import hashlib

import pytest

from src.features.api_keys.operations import keys as operations
from src.features.api_keys.operations import secret_format
from src.features.api_keys.records import ApiKey, UserApiAccess
from src.platform.database import get_cursor
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType


def mint(stack, owner, scopes=("read",), days=None):
    return operations.mint(stack.collaborators, owner, "script", list(scopes), days, True)


class TestResolve:
    def test_a_minted_key_resolves_to_its_owner_and_grant(self, stack):
        owner = stack.user()
        key, secret = mint(stack, owner, scopes=("read", "generate"))

        resolved = stack.resolver.resolve(secret)

        assert resolved.user.id == owner.id
        assert resolved.grant.key_id == key.id
        assert resolved.grant.scopes == {S.READ, S.GENERATE}
        assert resolved.grant.display_prefix == secret[:12]

    def test_an_unknown_string_resolves_to_nothing(self, stack):
        stack.user()

        assert stack.resolver.resolve(secret_format.generate_secret()) is None

    def test_a_key_with_a_wrong_checksum_is_refused(self, stack):
        _, secret = mint(stack, stack.user())
        swapped = "A" if secret[-1] != "A" else "B"

        assert stack.resolver.resolve(secret[:-1] + swapped) is None
        assert stack.resolver.resolve(secret) is not None

    def test_a_truncated_or_extended_key_is_refused(self, stack):
        _, secret = mint(stack, stack.user())

        assert stack.resolver.resolve(secret[:-1]) is None
        assert stack.resolver.resolve(secret + "x") is None

    @pytest.mark.parametrize("presented", ["", "pui_", "x" * 5000, "pui_éé"])
    def test_junk_resolves_to_nothing(self, stack, presented):
        assert stack.resolver.resolve(presented) is None

    def test_a_revoked_key_is_refused(self, stack):
        owner = stack.user()
        key, secret = mint(stack, owner)
        operations.revoke_own(stack.collaborators, owner, key.id)

        assert stack.resolver.resolve(secret) is None

    def test_an_expired_key_is_refused(self, stack):
        owner = stack.user()
        key, secret = mint(stack, owner, days=30)
        with get_cursor() as cursor:
            cursor.execute("UPDATE api_keys SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (key.id,))

        assert stack.resolver.resolve(secret) is None

    def test_a_key_that_has_not_expired_yet_is_accepted(self, stack):
        _, secret = mint(stack, stack.user(), days=30)

        assert stack.resolver.resolve(secret) is not None

    def test_a_key_with_an_unreadable_expiry_is_refused(self, stack):
        key, secret = mint(stack, stack.user(), days=30)
        with get_cursor() as cursor:
            cursor.execute("UPDATE api_keys SET expires_at = 'soon' WHERE id = ?", (key.id,))

        assert stack.resolver.resolve(secret) is None

    def test_the_instance_switch_refuses_every_key_and_restores_them_when_back_on(self, stack):
        _, secret = mint(stack, stack.user())
        stack.settings.set_setting("api_keys_enabled", False)

        assert stack.resolver.resolve(secret) is None

        stack.settings.set_setting("api_keys_enabled", True)
        assert stack.resolver.resolve(secret) is not None

    def test_the_user_switch_refuses_their_keys_and_restores_them_when_back_on(self, stack):
        owner = stack.user()
        _, secret = mint(stack, owner)
        stack.access.save(UserApiAccess(owner.id, False, True), "2026-11-11T09:00:00Z", "root")

        assert stack.resolver.resolve(secret) is None

        stack.access.save(UserApiAccess(owner.id, True, True), "2026-11-11T10:00:00Z", "root")
        assert stack.resolver.resolve(secret) is not None

    def test_the_switch_of_another_user_does_not_matter(self, stack):
        alice, bob = stack.user("alice"), stack.user("bob")
        _, secret = mint(stack, alice)
        stack.access.save(UserApiAccess(bob.id, False, False), "2026-11-11T09:00:00Z", "root")

        assert stack.resolver.resolve(secret) is not None

    def test_a_scope_taken_out_of_the_ceiling_stops_working(self, stack):
        _, secret = mint(stack, stack.user(), scopes=("read", "generate"))
        stack.settings.set_setting("api_key_scopes_allowed", ["read"])

        assert stack.resolver.resolve(secret).grant.scopes == {S.READ}

    def test_an_admin_scope_dropped_from_the_ceiling_stops_working(self, stack):
        stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])
        owner = stack.user("root", admin=True)
        _, secret = mint(stack, owner, scopes=("admin",), days=30)
        stack.settings.set_setting("api_key_scopes_allowed", ["read"])

        assert S.ADMIN not in stack.resolver.resolve(secret).grant.scopes

    def test_the_resolved_owner_is_the_current_row_not_a_snapshot(self, stack):
        owner = stack.user("root", admin=True)
        _, secret = mint(stack, owner)
        with get_cursor() as cursor:
            cursor.execute("UPDATE users SET account_type = 'USER' WHERE id = ?", (owner.id,))

        assert stack.resolver.resolve(secret).user.account_type == AccountType.USER

    def test_a_migrated_legacy_token_resolves_by_the_hash_of_its_plaintext(self, stack):
        owner = stack.user()
        plaintext = "pui_mcp_" + "Ab3_-" * 8 + "xyz"
        stack.keys.create(ApiKey(
            id="legacy", user_id=owner.id, name="laptop", secret_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            display_prefix=plaintext[:12], last_four=None, scopes=frozenset({S.MCP}),
            created_at="2026-05-01T00:00:00+00:00",
        ))

        resolved = stack.resolver.resolve(plaintext)

        assert resolved.grant.scopes == {S.MCP}
        assert resolved.grant.display_prefix == "pui_mcp_Ab3_"


class TestMcpGate:
    def test_mcp_is_off_on_a_fresh_instance(self, stack):
        _, secret = mint(stack, stack.user())

        assert stack.resolver.mcp_gate(stack.resolver.resolve(secret).grant) == "MCP is disabled on this instance"

    def test_the_user_switch_is_named(self, stack):
        stack.settings.set_setting("mcp_enabled", True)
        owner = stack.user()
        _, secret = mint(stack, owner)
        stack.access.save(UserApiAccess(owner.id, True, False), "2026-11-11T09:00:00Z", "root")

        assert stack.resolver.mcp_gate(stack.resolver.resolve(secret).grant) == "MCP is disabled for this user"

    def test_both_on_passes(self, stack):
        stack.settings.set_setting("mcp_enabled", True)
        _, secret = mint(stack, stack.user())

        assert stack.resolver.mcp_gate(stack.resolver.resolve(secret).grant) is None


class TestRecordUse:
    def grant(self, stack, secret):
        return stack.resolver.resolve(secret).grant

    def test_use_is_recorded_truncated_to_the_hour(self, stack):
        key, secret = mint(stack, stack.user())

        stack.resolver.record_use(self.grant(stack, secret), "mcp")

        stored = stack.keys.get_by_id(key.id)
        assert stored.last_used_via == "mcp"
        assert stored.last_used_at.endswith(":00:00Z")

    def test_it_writes_at_most_once_per_hour(self, stack, monkeypatch):
        key, secret = mint(stack, stack.user())
        grant = self.grant(stack, secret)
        writes = []
        real = stack.keys.touch_last_used
        monkeypatch.setattr(stack.keys, "touch_last_used", lambda *args: writes.append(args) or real(*args))

        for _ in range(5):
            stack.resolver.record_use(grant, "api")

        assert len(writes) == 1

    def test_a_new_process_does_not_rewrite_within_the_same_hour(self, stack):
        key, secret = mint(stack, stack.user())
        grant = self.grant(stack, secret)
        stack.resolver.record_use(grant, "api")
        fresh = type(stack.resolver)(stack.keys, stack.access, stack.users, stack.settings)

        fresh.record_use(grant, "mcp")

        assert stack.keys.get_by_id(key.id).last_used_via == "api"
