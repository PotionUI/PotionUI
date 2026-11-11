import json

import pytest

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations import keys as operations
from src.features.api_keys.operations import secret_format
from src.features.api_keys.records import UserApiAccess
from src.platform.security.api_scopes import ApiScope as S


def mint(stack, owner, name="script", scopes=("read",), days=None, provided=True):
    return operations.mint(stack.collaborators, owner, name, list(scopes), days, provided)


def refused(call):
    with pytest.raises(ApiKeyError) as raised:
        call()
    return raised.value


def allow(stack, *values):
    stack.settings.set_setting(
        "api_key_scopes_allowed", ["read", "generate", "content", "mcp", *values]
    )


class TestMint:
    def test_a_key_is_stored_hashed_and_the_secret_is_returned_once(self, stack):
        owner = stack.user()

        key, secret = mint(stack, owner, scopes=("read", "generate"))

        assert secret_format.has_valid_checksum(secret)
        assert key.secret_hash == secret_format.hash_secret(secret)
        assert secret not in json.dumps(key.__dict__, default=str)
        assert key.display_prefix == secret[:12] and key.last_four == secret[-4:]
        assert key.scopes == {S.READ, S.GENERATE}
        assert key.user_id == owner.id

    def test_the_stored_row_never_contains_the_secret(self, stack, keys_db):
        owner = stack.user()
        _, secret = mint(stack, owner)

        with keys_db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM api_keys")
            row = " ".join(str(value) for value in tuple(cursor.fetchone()))

        assert secret not in row
        assert secret[12:] not in row

    def test_an_account_without_a_local_password_can_create_a_key(self, stack):
        owner = stack.users.create(
            username="sso-user", email="sso@example.com", password_hash="", has_local_password=False
        )

        key, secret = mint(stack, owner)

        assert owner.has_local_password is False
        assert stack.resolver.resolve(secret).grant.key_id == key.id

    def test_the_name_is_trimmed(self, stack):
        key, _ = mint(stack, stack.user(), name="  Batch script  ")

        assert key.name == "Batch script"

    @pytest.mark.parametrize("name", [None, "", "   ", "x" * 81])
    def test_a_bad_name_is_invalid_name(self, stack, name):
        failure = refused(lambda: mint(stack, stack.user(), name=name))

        assert (failure.error, failure.status_code) == ("invalid_name", 400)

    def test_eighty_characters_is_the_longest_name(self, stack):
        key, _ = mint(stack, stack.user(), name="x" * 80)

        assert len(key.name) == 80

    @pytest.mark.parametrize("scopes", [[], None, ["root"], ["read", "root"]])
    def test_empty_or_unknown_scopes_are_invalid_scopes(self, stack, scopes):
        failure = refused(lambda: operations.mint(stack.collaborators, stack.user(), "k", scopes, None, True))

        assert (failure.error, failure.status_code) == ("invalid_scopes", 400)

    def test_an_ordinary_key_may_never_expire(self, stack):
        key, _ = mint(stack, stack.user(), days=None)

        assert key.expires_at is None

    def test_a_requested_lifetime_sets_the_expiry(self, stack):
        key, _ = mint(stack, stack.user(), days=30)

        assert key.expires_at is not None and key.status() == "active"

    def test_the_instance_maximum_lifetime_is_honoured(self, stack):
        stack.settings.set_setting("api_key_max_lifetime_days", 90)

        failure = refused(lambda: mint(stack, stack.user(), days=None))

        assert failure.error == "lifetime_too_long"
        assert mint(stack, stack.user("bob"), days=90)[0].expires_at is not None

    def test_creation_writes_an_event_without_the_secret(self, stack):
        owner = stack.user()
        key, secret = mint(stack, owner, scopes=("read", "generate"), days=90)

        (event,) = stack.events.list_for_key(key.id)

        assert event.action == "created" and event.actor_id == owner.id and event.owner_id == owner.id
        assert event.detail["scopes"] == ["read", "generate"]
        assert secret not in json.dumps(event.detail)
        assert secret[12:] not in json.dumps(event.detail)


class TestMintSwitches:
    def test_the_instance_switch_blocks_creation(self, stack):
        stack.settings.set_setting("api_keys_enabled", False)

        failure = refused(lambda: mint(stack, stack.user()))

        assert (failure.error, failure.status_code) == ("keys_disabled", 403)

    def test_the_user_switch_blocks_creation(self, stack):
        owner = stack.user()
        stack.access.save(UserApiAccess(owner.id, False, True), "2026-11-11T09:00:00Z", "root")

        assert refused(lambda: mint(stack, owner)).error == "keys_disabled"

    def test_mcp_needs_the_instance_to_have_mcp_on(self, stack):
        failure = refused(lambda: mint(stack, stack.user(), scopes=("mcp",)))

        assert (failure.error, failure.status_code) == ("mcp_disabled", 403)

    def test_mcp_needs_the_user_to_have_mcp_on(self, stack):
        stack.settings.set_setting("mcp_enabled", True)
        owner = stack.user()
        stack.access.save(UserApiAccess(owner.id, True, False), "2026-11-11T09:00:00Z", "root")

        assert refused(lambda: mint(stack, owner, scopes=("mcp",))).error == "mcp_disabled"

    def test_mcp_is_granted_when_both_are_on(self, stack):
        stack.settings.set_setting("mcp_enabled", True)

        key, _ = mint(stack, stack.user(), scopes=("read", "mcp"))

        assert S.MCP in key.scopes


class TestAdminScope:
    def test_a_plain_user_cannot_get_admin_even_when_the_instance_allows_it(self, stack):
        allow(stack, "admin")

        failure = refused(lambda: mint(stack, stack.user(), scopes=("admin",), days=30))

        assert (failure.error, failure.status_code) == ("scope_not_allowed", 403)

    def test_an_admin_cannot_get_admin_while_the_instance_ceiling_lacks_it(self, stack):
        failure = refused(lambda: mint(stack, stack.user("root", admin=True), scopes=("admin",), days=30))

        assert (failure.error, failure.status_code) == ("scope_not_allowed", 403)

    def test_an_admin_gets_admin_once_the_ceiling_has_it(self, stack):
        allow(stack, "admin")

        key, _ = mint(stack, stack.user("root", admin=True), scopes=("admin",), days=30)

        assert key.scopes == {S.ADMIN}

    def test_an_admin_key_without_an_expiry_is_refused(self, stack):
        allow(stack, "admin")

        failure = refused(lambda: mint(stack, stack.user("root", admin=True), scopes=("admin",), days=None))

        assert (failure.error, failure.status_code) == ("lifetime_too_long", 400)

    def test_an_admin_key_is_capped_at_ninety_days(self, stack):
        allow(stack, "admin")

        failure = refused(lambda: mint(stack, stack.user("root", admin=True), scopes=("admin",), days=365))

        assert failure.error == "lifetime_too_long"

    def test_an_admin_key_defaults_to_ninety_days_when_the_field_is_omitted(self, stack):
        allow(stack, "admin")

        key, _ = mint(stack, stack.user("root", admin=True), scopes=("admin",), days=None, provided=False)

        assert key.expires_at is not None

    def test_a_scope_removed_from_the_ceiling_cannot_be_granted(self, stack):
        stack.settings.set_setting("api_key_scopes_allowed", ["read"])

        assert refused(lambda: mint(stack, stack.user(), scopes=("generate",))).error == "scope_not_allowed"

    def test_read_is_always_grantable(self, stack):
        stack.settings.set_setting("api_key_scopes_allowed", [])

        assert mint(stack, stack.user(), scopes=("read",))[0].scopes == {S.READ}


class TestUpdate:
    def test_renaming_records_before_and_after(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner, name="old")

        updated = operations.update(stack.collaborators, owner, key.id, "new", None)

        assert updated.name == "new" and stack.keys.get_by_id(key.id).name == "new"
        assert stack.events.list_for_key(key.id)[-1].detail == {"before": "old", "after": "new"}

    def test_changing_scopes_records_both_lists(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner, scopes=("read",))

        operations.update(stack.collaborators, owner, key.id, None, ["read", "generate"])

        event = stack.events.list_for_key(key.id)[-1]
        assert event.action == "scopes_changed"
        assert event.detail == {"before": ["read"], "after": ["read", "generate"]}

    def test_a_change_that_changes_nothing_writes_nothing(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner, name="same", scopes=("read",))

        operations.update(stack.collaborators, owner, key.id, "same", ["read"])

        assert len(stack.events.list_for_key(key.id)) == 1

    def test_widening_is_checked_against_the_ceiling(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner)
        stack.settings.set_setting("api_key_scopes_allowed", ["read"])

        assert refused(lambda: operations.update(stack.collaborators, owner, key.id, None, ["read", "content"])).error == "scope_not_allowed"

    def test_narrowing_is_always_allowed_even_below_a_tightened_ceiling(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner, scopes=("read", "generate"))
        stack.settings.set_setting("api_key_scopes_allowed", ["read"])

        updated = operations.update(stack.collaborators, owner, key.id, None, ["generate"])

        assert updated.scopes == {S.GENERATE}

    def test_adding_admin_to_a_never_expiring_key_is_refused(self, stack):
        allow(stack, "admin")
        owner = stack.user("root", admin=True)
        key, _ = mint(stack, owner, scopes=("read",), days=None)

        failure = refused(lambda: operations.update(stack.collaborators, owner, key.id, None, ["read", "admin"]))

        assert (failure.error, failure.status_code) == ("admin_scope_needs_expiry", 400)

    def test_adding_admin_to_a_key_that_expires_beyond_ninety_days_is_refused(self, stack):
        allow(stack, "admin")
        owner = stack.user("root", admin=True)
        key, _ = mint(stack, owner, scopes=("read",), days=365)

        assert refused(lambda: operations.update(stack.collaborators, owner, key.id, None, ["admin"])).error == "admin_scope_needs_expiry"

    def test_adding_admin_to_a_short_lived_key_is_allowed(self, stack):
        allow(stack, "admin")
        owner = stack.user("root", admin=True)
        key, _ = mint(stack, owner, scopes=("read",), days=30)

        assert operations.update(stack.collaborators, owner, key.id, None, ["admin"]).scopes == {S.ADMIN}

    def test_a_plain_user_cannot_add_admin(self, stack):
        allow(stack, "admin")
        owner = stack.user()
        key, _ = mint(stack, owner, days=30)

        assert refused(lambda: operations.update(stack.collaborators, owner, key.id, None, ["admin"])).error == "scope_not_allowed"

    def test_someone_elses_key_is_not_found(self, stack):
        key, _ = mint(stack, stack.user("alice"))

        failure = refused(lambda: operations.update(stack.collaborators, stack.user("bob"), key.id, "mine", None))

        assert (failure.error, failure.status_code) == ("key_not_found", 404)

    def test_a_revoked_key_cannot_be_changed(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner)
        operations.revoke_own(stack.collaborators, owner, key.id)

        failure = refused(lambda: operations.update(stack.collaborators, owner, key.id, "late", None))

        assert (failure.error, failure.status_code) == ("key_revoked", 409)

    def test_expiry_cannot_be_changed(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner, days=30)

        operations.update(stack.collaborators, owner, key.id, "renamed", None)

        assert stack.keys.get_by_id(key.id).expires_at == key.expires_at


class TestRevoke:
    def test_an_owner_revokes_their_key_and_the_event_says_so(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner)

        revoked = operations.revoke_own(stack.collaborators, owner, key.id)

        assert revoked.is_revoked and revoked.revoked_by == owner.id
        event = stack.events.list_for_key(key.id)[-1]
        assert (event.action, event.actor_id, event.detail) == ("revoked", owner.id, {"by_owner": True})

    def test_revoking_twice_is_idempotent_and_logs_once(self, stack):
        owner = stack.user()
        key, _ = mint(stack, owner)
        operations.revoke_own(stack.collaborators, owner, key.id)

        again = operations.revoke_own(stack.collaborators, owner, key.id)

        assert again.is_revoked
        assert [event.action for event in stack.events.list_for_key(key.id)] == ["created", "revoked"]

    def test_someone_elses_key_cannot_be_revoked_by_the_owner_route(self, stack):
        key, _ = mint(stack, stack.user("alice"))

        failure = refused(lambda: operations.revoke_own(stack.collaborators, stack.user("bob"), key.id))

        assert failure.status_code == 404
        assert not stack.keys.get_by_id(key.id).is_revoked

    def test_an_admin_revoke_records_the_admin_as_actor(self, stack):
        owner, admin = stack.user("alice"), stack.user("root", admin=True)
        key, _ = mint(stack, owner)

        revoked = operations.revoke_any(stack.collaborators, admin, key.id)

        assert revoked.revoked_by == admin.id
        event = stack.events.list_for_key(key.id)[-1]
        assert (event.actor_id, event.owner_id, event.detail) == (admin.id, owner.id, {"by_owner": False})

    def test_an_unknown_key_is_not_found_for_an_admin(self, stack):
        assert refused(lambda: operations.revoke_any(stack.collaborators, stack.user("root", admin=True), "ghost")).status_code == 404


class TestListing:
    def test_active_keys_come_first_then_the_rest_newest_first(self, stack):
        owner = stack.user()
        first, _ = mint(stack, owner, name="first")
        second, _ = mint(stack, owner, name="second")
        third, _ = mint(stack, owner, name="third")
        operations.revoke_own(stack.collaborators, owner, second.id)

        listed = operations.list_for_user(stack.collaborators, owner.id)

        assert [key.name for key in listed][-1] == "second"
        assert {key.name for key in listed[:2]} == {"first", "third"}
        assert listed[2].is_revoked
