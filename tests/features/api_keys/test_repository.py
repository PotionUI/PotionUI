import json

from src.features.api_keys.records import ApiKey, UserApiAccess
from src.platform.database import get_cursor
from src.platform.security.api_scopes import ApiScope as S


def make_key(user_id, key_id="k1", secret_hash="h1", **overrides):
    values = dict(
        id=key_id, user_id=user_id, name="script", secret_hash=secret_hash, display_prefix="pui_aaaaaaaa",
        last_four="zzzz", scopes=frozenset({S.READ, S.MCP}), created_at="2026-11-11T09:00:00Z",
    )
    values.update(overrides)
    return ApiKey(**values)


class TestKeys:
    def test_a_key_round_trips_with_its_scopes(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        loaded = stack.keys.get_by_id("k1")

        assert loaded.scopes == {S.READ, S.MCP}
        assert loaded.last_four == "zzzz"
        assert loaded.expires_at is None and loaded.revoked_at is None

    def test_lookup_is_by_hash(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        assert stack.keys.get_by_hash("h1").id == "k1"
        assert stack.keys.get_by_hash("other") is None

    def test_the_secret_hash_is_unique(self, stack):
        import sqlite3

        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        try:
            stack.keys.create(make_key(owner.id, key_id="k2"))
        except sqlite3.IntegrityError:
            return
        raise AssertionError("a duplicate secret hash was stored")

    def test_scopes_are_stored_as_a_json_array(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        with get_cursor() as cursor:
            cursor.execute("SELECT scopes FROM api_keys WHERE id = 'k1'")
            assert json.loads(cursor.fetchone()[0]) == ["read", "mcp"]

    def test_unknown_scopes_in_a_row_are_ignored_not_granted(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        with get_cursor() as cursor:
            cursor.execute("UPDATE api_keys SET scopes = ? WHERE id = 'k1'", ('["read", "root"]',))

        assert stack.keys.get_by_id("k1").scopes == {S.READ}

    def test_listing_is_newest_first_and_per_user(self, stack):
        alice, bob = stack.user("alice"), stack.user("bob")
        stack.keys.create(make_key(alice.id, "a1", "ha1", created_at="2026-11-01T00:00:00Z"))
        stack.keys.create(make_key(alice.id, "a2", "ha2", created_at="2026-11-02T00:00:00Z"))
        stack.keys.create(make_key(bob.id, "b1", "hb1"))

        assert [key.id for key in stack.keys.list_for_user(alice.id)] == ["a2", "a1"]

    def test_deleting_the_owner_deletes_the_keys_and_their_events(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.events.add("k1", owner.id, owner.id, "created", {}, "2026-11-11T09:00:00Z")

        with get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = ?", (owner.id,))

        assert stack.keys.get_by_id("k1") is None
        assert stack.events.list_for_key("k1") == []

    def test_update_changes_name_and_scopes_of_an_active_key_only(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        assert stack.keys.update("k1", "renamed", '["read"]')
        assert stack.keys.get_by_id("k1").name == "renamed"
        stack.keys.revoke("k1", owner.id, "2026-11-11T10:00:00Z")
        assert not stack.keys.update("k1", "again", '["admin"]')
        assert stack.keys.get_by_id("k1").scopes == {S.READ}


class TestRevoke:
    def test_revoking_records_when_and_by_whom(self, stack):
        owner, admin = stack.user("alice"), stack.user("root")
        stack.keys.create(make_key(owner.id))

        assert stack.keys.revoke("k1", admin.id, "2026-11-11T10:00:00Z")

        key = stack.keys.get_by_id("k1")
        assert key.revoked_at == "2026-11-11T10:00:00Z"
        assert key.revoked_by == admin.id

    def test_revoking_twice_changes_nothing(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.keys.revoke("k1", owner.id, "2026-11-11T10:00:00Z")

        assert not stack.keys.revoke("k1", "someone-else", "2026-11-12T10:00:00Z")
        assert stack.keys.get_by_id("k1").revoked_at == "2026-11-11T10:00:00Z"


class TestLastUse:
    def test_the_first_use_is_written(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))

        assert stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "api")

        key = stack.keys.get_by_id("k1")
        assert (key.last_used_at, key.last_used_via) == ("2026-11-11T14:00:00Z", "api")

    def test_a_second_use_in_the_same_hour_writes_nothing(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "api")

        assert not stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "mcp")
        assert stack.keys.get_by_id("k1").last_used_via == "api"

    def test_a_later_hour_is_written(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "api")

        assert stack.keys.touch_last_used("k1", "2026-11-11T15:00:00Z", "mcp")
        assert stack.keys.get_by_id("k1").last_used_via == "mcp"

    def test_an_older_hour_never_moves_the_timestamp_back(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "api")

        assert not stack.keys.touch_last_used("k1", "2026-11-11T13:00:00Z", "api")

    def test_a_migrated_microsecond_timestamp_in_the_same_hour_is_not_rewritten(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id, last_used_at="2026-11-11T14:20:11.123456+00:00"))

        assert not stack.keys.touch_last_used("k1", "2026-11-11T14:00:00Z", "mcp")


class TestAdminListing:
    def seed(self, stack):
        alice, bob = stack.user("alice"), stack.user("bob")
        stack.keys.create(make_key(alice.id, "active", "h1", created_at="2026-11-01T00:00:00Z"))
        stack.keys.create(make_key(alice.id, "soon", "h2", created_at="2026-11-02T00:00:00Z", expires_at="2027-01-01T00:00:00Z"))
        stack.keys.create(make_key(bob.id, "expired", "h3", created_at="2026-11-03T00:00:00Z", expires_at="2026-11-05T00:00:00Z"))
        stack.keys.create(make_key(bob.id, "revoked", "h4", created_at="2026-11-04T00:00:00Z", revoked_at="2026-11-06T00:00:00Z", revoked_by=bob.id))
        return alice, bob

    def ids(self, stack, status, user_id=None, limit=50, offset=0):
        keys, total = stack.keys.list_page(status, user_id, "2026-11-11T09:00:00Z", limit, offset)
        return [key.id for key in keys], total

    def test_all_lists_everything_newest_first(self, stack):
        self.seed(stack)

        assert self.ids(stack, "all") == (["revoked", "expired", "soon", "active"], 4)

    def test_each_status_selects_its_own_keys(self, stack):
        self.seed(stack)

        assert self.ids(stack, "active") == (["soon", "active"], 2)
        assert self.ids(stack, "expired") == (["expired"], 1)
        assert self.ids(stack, "revoked") == (["revoked"], 1)

    def test_an_owner_filter_narrows_the_listing(self, stack):
        alice, _ = self.seed(stack)

        assert self.ids(stack, "all", alice.id) == (["soon", "active"], 2)

    def test_pages_slice_the_listing_and_the_total_stays_whole(self, stack):
        self.seed(stack)

        assert self.ids(stack, "all", limit=2, offset=0) == (["revoked", "expired"], 4)
        assert self.ids(stack, "all", limit=2, offset=2) == (["soon", "active"], 4)
        assert self.ids(stack, "all", limit=2, offset=4) == ([], 4)

    def test_search_matches_name_prefix_and_owner_without_case_and_combines_with_the_filters(self, stack):
        alice, bob = self.seed(stack)
        stack.keys.create(make_key(bob.id, "named", "h5", name="Claude Desktop", display_prefix="pui_AbCdEf12", created_at="2026-11-07T00:00:00Z"))

        def found(query, status="all", user_id=None):
            keys, total = stack.keys.list_page(status, user_id, "2026-11-11T09:00:00Z", 50, 0, query)
            return sorted(key.id for key in keys), total

        assert found("claude DESK") == (["named"], 1)
        assert found("abcdef") == (["named"], 1)
        assert found("ALICE") == (["active", "soon"], 2)
        assert found("alice", status="active", user_id=alice.id) == (["active", "soon"], 2)
        assert found("alice", status="expired") == ([], 0)
        assert found("  ") == (["active", "expired", "named", "revoked", "soon"], 5)

    def test_search_treats_wildcards_and_quotes_as_text(self, stack):
        self.seed(stack)

        for text in ("%", "__", "'", "\\", "a%", "'; DROP TABLE api_keys; --"):
            keys, total = stack.keys.list_page("all", None, "2026-11-11T09:00:00Z", 50, 0, text)
            assert (keys, total) == ([], 0)
        assert stack.keys.list_page("all", None, "2026-11-11T09:00:00Z", 50, 0)[1] == 4

    def test_status_counts_split_the_keys_and_ignore_the_status_filter(self, stack):
        alice, bob = self.seed(stack)
        now = "2026-11-11T09:00:00Z"

        assert stack.keys.status_counts(None, now) == {"all": 4, "active": 2, "expired": 1, "revoked": 1}
        assert stack.keys.status_counts(alice.id, now) == {"all": 2, "active": 2, "expired": 0, "revoked": 0}
        assert stack.keys.status_counts(bob.id, now, "bob") == {"all": 2, "active": 0, "expired": 1, "revoked": 1}
        assert stack.keys.status_counts("ghost", now) == {"all": 0, "active": 0, "expired": 0, "revoked": 0}
        assert stack.keys.counts_by_user(alice.id, now) == {"active": 2, "total": 2}

    def test_usernames_resolve_in_one_lookup(self, stack):
        alice, bob = self.seed(stack)

        assert stack.keys.usernames([alice.id, bob.id, None, "ghost"]) == {alice.id: "alice", bob.id: "bob"}
        assert stack.keys.usernames([]) == {}


class TestEvents:
    def test_events_come_back_oldest_first_with_their_detail(self, stack):
        owner = stack.user()
        stack.keys.create(make_key(owner.id))
        stack.events.add("k1", owner.id, owner.id, "created", {"scopes": ["read"]}, "2026-11-11T09:00:00Z")
        stack.events.add("k1", owner.id, None, "revoked", {"by_owner": False}, "2026-11-11T10:00:00Z")

        events = stack.events.list_for_key("k1")

        assert [event.action for event in events] == ["created", "revoked"]
        assert events[0].detail == {"scopes": ["read"]}
        assert events[1].actor_id is None


class TestUserAccess:
    def test_a_user_without_a_row_has_everything_on(self, stack):
        owner = stack.user()

        assert stack.access.get(owner.id) == UserApiAccess(owner.id, True, True)

    def test_saving_twice_updates_one_row(self, stack):
        owner = stack.user()
        stack.access.save(UserApiAccess(owner.id, True, False), "2026-11-11T09:00:00Z", "admin-1")
        stack.access.save(UserApiAccess(owner.id, False, False), "2026-11-11T10:00:00Z", "admin-2")

        assert stack.access.get(owner.id) == UserApiAccess(owner.id, False, False)
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MAX(updated_by) FROM api_key_user_access")
            assert tuple(cursor.fetchone()) == (1, "admin-2")
