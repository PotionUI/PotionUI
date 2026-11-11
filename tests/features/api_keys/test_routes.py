import json



def detail_error(response):
    return response.json()["detail"]["error"]


class TestCreate:
    def test_creating_a_key_returns_the_secret_once_with_the_documented_shape(self, client, world):
        response = client.post(
            "/api/keys", json={"name": "Batch script", "scopes": ["read", "generate"], "expires_in_days": None},
            headers=world.session("alice"),
        )

        body = response.json()["data"]
        assert response.status_code == 201
        assert body["secret"].startswith("pui_") and len(body["secret"]) == 50
        key = body["key"]
        assert set(key) == {
            "id", "name", "display_prefix", "last_four", "scopes", "status", "created_at", "expires_at",
            "last_used_at", "last_used_via", "revoked_at", "revoked_by", "mcp_tools",
        }
        assert key["name"] == "Batch script" and key["scopes"] == ["read", "generate"]
        assert key["status"] == "active" and key["expires_at"] is None
        assert key["display_prefix"] == body["secret"][:12] and key["last_four"] == body["secret"][-4:]
        assert key["mcp_tools"] is None
        assert key["created_at"].endswith("+00:00")

    def test_omitting_the_expiry_means_never_for_an_ordinary_key(self, client, world):
        response = client.post("/api/keys", json={"name": "k", "scopes": ["read"]}, headers=world.session("alice"))

        assert response.json()["data"]["key"]["expires_at"] is None

    def test_a_requested_lifetime_sets_expires_at(self, client, world):
        response = client.post(
            "/api/keys", json={"name": "k", "scopes": ["read"], "expires_in_days": 30}, headers=world.session("alice")
        )

        assert response.json()["data"]["key"]["expires_at"].endswith("+00:00")

    def test_the_secret_appears_in_no_later_response(self, client, world):
        created = client.post("/api/keys", json={"name": "k", "scopes": ["read"]}, headers=world.session("alice"))
        secret = created.json()["data"]["secret"]
        key_id = created.json()["data"]["key"]["id"]

        listing = client.get("/api/keys", headers=world.session("alice"))
        patched = client.patch(f"/api/keys/{key_id}", json={"name": "renamed"}, headers=world.session("alice"))
        deleted = client.delete(f"/api/keys/{key_id}", headers=world.session("alice"))

        for response in (listing, patched, deleted):
            assert secret not in response.text
            assert secret[12:] not in response.text

    def test_errors_use_the_documented_codes_and_statuses(self, client, world):
        headers = world.session("alice")

        cases = [
            ({"name": " ", "scopes": ["read"]}, 400, "invalid_name"),
            ({"name": "k", "scopes": []}, 400, "invalid_scopes"),
            ({"name": "k", "scopes": ["root"]}, 400, "invalid_scopes"),
            ({"name": "k", "scopes": ["admin"], "expires_in_days": 30}, 403, "scope_not_allowed"),
            ({"name": "k", "scopes": ["mcp"]}, 403, "mcp_disabled"),
        ]
        for payload, status, error in cases:
            response = client.post("/api/keys", json=payload, headers=headers)
            assert (response.status_code, detail_error(response)) == (status, error), payload

    def test_a_maximum_lifetime_refuses_never(self, client, world):
        world.stack.settings.set_setting("api_key_max_lifetime_days", 90)

        response = client.post(
            "/api/keys", json={"name": "k", "scopes": ["read"], "expires_in_days": None}, headers=world.session("alice")
        )

        assert (response.status_code, detail_error(response)) == (400, "lifetime_too_long")

    def test_admin_scope_refuses_never_and_a_year(self, client, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])
        headers = world.session("root")

        never = client.post("/api/keys", json={"name": "k", "scopes": ["admin"], "expires_in_days": None}, headers=headers)
        year = client.post("/api/keys", json={"name": "k", "scopes": ["admin"], "expires_in_days": 365}, headers=headers)
        default = client.post("/api/keys", json={"name": "k", "scopes": ["admin"]}, headers=headers)

        assert detail_error(never) == detail_error(year) == "lifetime_too_long"
        assert default.status_code == 201 and default.json()["data"]["key"]["expires_at"].endswith("+00:00")

    def test_switched_off_instance_refuses_creation_but_not_listing(self, client, world):
        world.stack.settings.set_setting("api_keys_enabled", False)
        headers = world.session("alice")

        created = client.post("/api/keys", json={"name": "k", "scopes": ["read"]}, headers=headers)
        listing = client.get("/api/keys", headers=headers)

        assert (created.status_code, detail_error(created)) == (403, "keys_disabled")
        assert listing.status_code == 200 and listing.json()["data"]["limits"]["keys_enabled"] is False


class TestList:
    def test_the_listing_carries_keys_and_what_the_user_may_grant(self, client, world):
        world.mint(world.alice)

        data = client.get("/api/keys", headers=world.session("alice")).json()["data"]

        assert len(data["keys"]) == 1
        assert data["limits"] == {
            "keys_enabled": True, "mcp_enabled": False,
            "scopes_allowed": ["read", "generate", "content", "mcp"], "max_lifetime_days": None,
        }

    def test_admin_scope_is_offered_only_to_admins_and_only_when_allowed(self, client, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])

        user = client.get("/api/keys", headers=world.session("alice")).json()["data"]["limits"]
        admin = client.get("/api/keys", headers=world.session("root")).json()["data"]["limits"]

        assert user["scopes_allowed"] == ["read"]
        assert admin["scopes_allowed"] == ["read", "admin"]

    def test_the_maximum_lifetime_and_switches_are_reported(self, client, world):
        world.stack.settings.set_setting("api_key_max_lifetime_days", 90)
        world.stack.settings.set_setting("mcp_enabled", True)

        limits = client.get("/api/keys", headers=world.session("alice")).json()["data"]["limits"]

        assert limits["max_lifetime_days"] == 90 and limits["mcp_enabled"] is True

    def test_a_user_sees_only_their_own_keys(self, client, world):
        world.mint(world.alice, name="alice key")
        world.mint(world.bob, name="bob key")

        names = [key["name"] for key in client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"]]

        assert names == ["alice key"]

    def test_an_admin_revoked_key_names_the_admin(self, client, world):
        key, _ = world.mint(world.alice)
        client.delete(f"/api/admin/keys/{key.id}", headers=world.session("root"))

        listed = client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"][0]

        assert listed["status"] == "revoked"
        assert listed["revoked_by"] == {"id": world.root.id, "username": "root"}

    def test_a_self_revoked_key_has_no_revoked_by(self, client, world):
        key, _ = world.mint(world.alice)
        client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        listed = client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"][0]

        assert listed["status"] == "revoked" and listed["revoked_by"] is None and listed["revoked_at"].endswith("+00:00")

    def test_active_keys_are_listed_before_revoked_ones(self, client, world):
        old, _ = world.mint(world.alice, name="old")
        world.mint(world.alice, name="new")
        client.delete(f"/api/keys/{old.id}", headers=world.session("alice"))

        keys = client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"]

        assert [key["name"] for key in keys] == ["new", "old"]


class TestUpdateAndRevoke:
    def test_patch_renames_and_widens(self, client, world):
        key, _ = world.mint(world.alice)

        response = client.patch(
            f"/api/keys/{key.id}", json={"name": "renamed", "scopes": ["read", "generate"]}, headers=world.session("alice")
        )

        assert response.status_code == 200
        assert response.json()["data"]["name"] == "renamed"
        assert response.json()["data"]["scopes"] == ["read", "generate"]

    def test_patch_cannot_change_the_expiry(self, client, world):
        key, _ = world.mint(world.alice, days=30)

        response = client.patch(f"/api/keys/{key.id}", json={"expires_in_days": None}, headers=world.session("alice"))

        assert response.json()["data"]["expires_at"] == world.stack.keys.get_by_id(key.id).expires_at

    def test_adding_admin_to_a_never_expiring_key_is_refused(self, client, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])
        key, _ = world.mint(world.root)

        response = client.patch(f"/api/keys/{key.id}", json={"scopes": ["admin"]}, headers=world.session("root"))

        assert (response.status_code, detail_error(response)) == (400, "admin_scope_needs_expiry")

    def test_someone_elses_key_is_404_for_patch_and_delete(self, client, world):
        key, _ = world.mint(world.bob)

        patched = client.patch(f"/api/keys/{key.id}", json={"name": "x"}, headers=world.session("alice"))
        deleted = client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        assert patched.status_code == deleted.status_code == 404
        assert not world.stack.keys.get_by_id(key.id).is_revoked

    def test_delete_revokes_and_is_idempotent(self, client, world):
        key, _ = world.mint(world.alice)

        first = client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))
        second = client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        assert first.json()["data"] == second.json()["data"] == {"id": key.id, "revoked": True}
        assert [event.action for event in world.stack.events.list_for_key(key.id)] == ["created", "revoked"]

    def test_a_revoked_key_is_401_on_the_next_call(self, client, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        key, secret = world.mint(world.alice, scopes=("mcp",))
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert client.post("/api/mcp", json=rpc, headers=world.bearer(secret)).status_code == 200

        client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))
        response = client.post("/api/mcp", json=rpc, headers=world.bearer(secret))

        assert (response.status_code, detail_error(response)) == (401, "invalid_api_key")


class TestKeysCannotManageKeys:
    def test_a_key_with_every_scope_is_refused_on_every_key_route(self, client, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
        own, _ = world.mint(world.root, scopes=("read",))
        _, secret = world.mint(world.root, scopes=("read", "generate", "content", "mcp", "admin"), days=30)
        headers = world.bearer(secret)

        responses = [
            client.get("/api/keys", headers=headers),
            client.post("/api/keys", json={"name": "k", "scopes": ["read"]}, headers=headers),
            client.patch(f"/api/keys/{own.id}", json={"name": "x"}, headers=headers),
            client.delete(f"/api/keys/{own.id}", headers=headers),
        ]

        for response in responses:
            assert (response.status_code, detail_error(response)) == (403, "key_not_allowed")
        assert not world.stack.keys.get_by_id(own.id).is_revoked
        assert len(world.stack.keys.list_for_user(world.root.id)) == 2

    def test_an_undeclared_route_refuses_a_key_but_not_a_session(self, client, world):
        _, secret = world.mint(world.alice, scopes=("read", "generate", "content"))

        key_call = client.get("/api/undeclared/me", headers=world.bearer(secret))
        session_call = client.get("/api/undeclared/me", headers=world.session("alice"))

        assert (key_call.status_code, detail_error(key_call)) == (403, "key_not_allowed")
        assert session_call.status_code == 200


class TestAdminRoutes:
    def test_a_plain_user_is_refused(self, client, world):
        headers = world.session("alice")

        assert client.get("/api/admin/keys", headers=headers).status_code == 403
        assert client.delete("/api/admin/keys/x", headers=headers).status_code == 403
        assert client.get("/api/admin/keys/x/events", headers=headers).status_code == 403
        assert client.get(f"/api/admin/users/{world.bob.id}/api-access", headers=headers).status_code == 403
        assert client.put(
            f"/api/admin/users/{world.bob.id}/api-access", json={"keys_enabled": False, "mcp_enabled": False}, headers=headers
        ).status_code == 403

    def test_listing_paginates_filters_and_names_the_owner(self, client, world):
        world.mint(world.alice, name="a1")
        world.mint(world.alice, name="a2")
        revoked, _ = world.mint(world.bob, name="b1")
        client.delete(f"/api/keys/{revoked.id}", headers=world.session("bob"))
        headers = world.session("root")

        everything = client.get("/api/admin/keys", headers=headers).json()["data"]
        active = client.get("/api/admin/keys?status=active", headers=headers).json()["data"]
        mine = client.get(f"/api/admin/keys?user_id={world.alice.id}&page_size=1&page=2", headers=headers).json()["data"]

        assert everything["total"] == 3 and everything["page"] == 1 and everything["page_size"] == 50
        assert {item["name"] for item in everything["items"]} == {"a1", "a2", "b1"}
        assert {item["name"] for item in active["items"]} == {"a1", "a2"}
        assert mine["total"] == 2 and len(mine["items"]) == 1 and mine["page"] == 2
        assert everything["items"][0]["owner"]["username"] in {"alice", "bob"}

    def test_bad_paging_and_status_are_validation_errors(self, client, world):
        headers = world.session("root")

        assert client.get("/api/admin/keys?status=bogus", headers=headers).status_code == 422
        assert client.get("/api/admin/keys?page=0", headers=headers).status_code == 422
        assert client.get("/api/admin/keys?page_size=1000", headers=headers).status_code == 422

    def test_an_admin_revoke_records_the_admin_and_shows_in_the_history(self, client, world):
        key, secret = world.mint(world.alice, scopes=("read",))

        response = client.delete(f"/api/admin/keys/{key.id}", headers=world.session("root"))
        events = client.get(f"/api/admin/keys/{key.id}/events", headers=world.session("root")).json()["data"]

        assert response.json()["data"] == {"id": key.id, "revoked": True}
        assert world.stack.keys.get_by_id(key.id).revoked_by == world.root.id
        assert [event["action"] for event in events] == ["created", "revoked"]
        assert events[1]["actor"] == {"id": world.root.id, "username": "root"}
        assert events[1]["detail"] == {"by_owner": False}
        assert secret not in json.dumps(events)
        assert world.stack.resolver.resolve(secret) is None

    def test_unknown_keys_are_404(self, client, world):
        headers = world.session("root")

        assert client.delete("/api/admin/keys/ghost", headers=headers).status_code == 404
        assert client.get("/api/admin/keys/ghost/events", headers=headers).status_code == 404

    def test_an_admin_scoped_key_is_refused_on_every_admin_key_route_and_changes_nothing(self, client, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])
        target, _ = world.mint(world.alice)
        _, secret = world.mint(world.root, scopes=("admin",), days=30)
        headers = world.bearer(secret)
        user_path = f"/api/admin/users/{world.alice.id}/api-access"

        responses = [
            client.get("/api/admin/keys", headers=headers),
            client.delete(f"/api/admin/keys/{target.id}", headers=headers),
            client.get(f"/api/admin/keys/{target.id}/events", headers=headers),
            client.get(user_path, headers=headers),
            client.put(user_path, json={"keys_enabled": False, "mcp_enabled": False}, headers=headers),
        ]

        for response in responses:
            assert (response.status_code, detail_error(response)) == (403, "key_not_allowed")
        assert not world.stack.keys.get_by_id(target.id).is_revoked
        assert world.stack.access.get(world.alice.id).keys_enabled is True

    def test_the_same_admin_in_a_browser_session_still_manages_keys(self, client, world):
        target, _ = world.mint(world.alice)
        headers = world.session("root")
        user_path = f"/api/admin/users/{world.alice.id}/api-access"

        assert client.get("/api/admin/keys", headers=headers).status_code == 200
        assert client.get(f"/api/admin/keys/{target.id}/events", headers=headers).status_code == 200
        assert client.get(user_path, headers=headers).status_code == 200
        assert client.delete(f"/api/admin/keys/{target.id}", headers=headers).status_code == 200

    def test_a_key_without_admin_is_refused_the_same_way(self, client, world):
        _, secret = world.mint(world.root, scopes=("read", "generate", "content"))

        response = client.get("/api/admin/keys", headers=world.bearer(secret))

        assert (response.status_code, detail_error(response)) == (403, "key_not_allowed")


class TestUserAccessSwitches:
    def test_the_default_is_everything_on(self, client, world):
        response = client.get(f"/api/admin/users/{world.alice.id}/api-access", headers=world.session("root"))

        assert response.json()["data"] == {"keys_enabled": True, "mcp_enabled": True}

    def test_switching_keys_off_rejects_the_users_keys_at_once_and_back_on_restores_them(self, client, world):
        key, secret = world.mint(world.alice, scopes=("read",))
        world.stack.settings.set_setting("mcp_enabled", True)
        _, mcp_secret = world.mint(world.alice, scopes=("mcp",))
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert client.post("/api/mcp", json=rpc, headers=world.bearer(mcp_secret)).status_code == 200

        off = client.put(
            f"/api/admin/users/{world.alice.id}/api-access",
            json={"keys_enabled": False, "mcp_enabled": True}, headers=world.session("root"),
        )
        refused = client.post("/api/mcp", json=rpc, headers=world.bearer(mcp_secret))
        on = client.put(
            f"/api/admin/users/{world.alice.id}/api-access",
            json={"keys_enabled": True, "mcp_enabled": True}, headers=world.session("root"),
        )
        restored = client.post("/api/mcp", json=rpc, headers=world.bearer(mcp_secret))

        assert off.json()["data"] == {"keys_enabled": False, "mcp_enabled": True}
        assert (refused.status_code, detail_error(refused)) == (401, "invalid_api_key")
        assert on.status_code == 200 and restored.status_code == 200
        assert not world.stack.keys.get_by_id(key.id).is_revoked

    def test_the_switch_of_one_user_leaves_another_user_alone(self, client, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        _, secret = world.mint(world.bob, scopes=("mcp",))
        client.put(
            f"/api/admin/users/{world.alice.id}/api-access",
            json={"keys_enabled": False, "mcp_enabled": False}, headers=world.session("root"),
        )

        response = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=world.bearer(secret))

        assert response.status_code == 200

    def test_an_unknown_user_is_404(self, client, world):
        headers = world.session("root")

        assert client.get("/api/admin/users/ghost/api-access", headers=headers).status_code == 404
        assert client.put(
            "/api/admin/users/ghost/api-access", json={"keys_enabled": True, "mcp_enabled": True}, headers=headers
        ).status_code == 404

    def test_the_user_cannot_write_their_own_switch(self, client, world):
        response = client.put(
            f"/api/admin/users/{world.alice.id}/api-access",
            json={"keys_enabled": True, "mcp_enabled": True}, headers=world.session("alice"),
        )

        assert response.status_code == 403


class TestMcpToolCounts:
    def setup_mcp(self, world):
        world.stack.settings.set_setting("mcp_enabled", True)

    def test_a_key_with_mcp_carries_the_exposed_and_total_counts_and_others_null(self, client, world):
        self.setup_mcp(world)
        world.mint(world.alice, scopes=("read", "mcp"), name="assistant")
        world.mint(world.alice, scopes=("read",), name="script")

        keys = {k["name"]: k for k in client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"]}

        assert keys["assistant"]["mcp_tools"] == {"exposed": 7, "total": 12}
        assert keys["script"]["mcp_tools"] is None

    def test_the_count_is_computed_once_per_owner_not_per_key(self, client, world):
        self.setup_mcp(world)
        for index in range(4):
            world.mint(world.alice, scopes=("mcp",), name=f"k{index}")

        client.get("/api/keys", headers=world.session("alice"))

        assert world.tool_counts_calls == [[world.alice.id]]

    def test_the_count_is_not_computed_when_no_key_has_mcp(self, client, world):
        world.mint(world.alice, scopes=("read",))

        client.get("/api/keys", headers=world.session("alice"))

        assert world.tool_counts_calls == [[]]

    def test_the_created_and_updated_key_responses_carry_it(self, client, world):
        self.setup_mcp(world)
        created = client.post("/api/keys", json={"name": "k", "scopes": ["mcp"]}, headers=world.session("alice")).json()["data"]["key"]
        patched = client.patch(f"/api/keys/{created['id']}", json={"name": "r"}, headers=world.session("alice")).json()["data"]

        assert created["mcp_tools"] == patched["mcp_tools"] == {"exposed": 7, "total": 12}

    def test_the_admin_listing_counts_each_distinct_owner_once(self, client, world):
        self.setup_mcp(world)
        world.mint(world.alice, scopes=("mcp",))
        world.mint(world.alice, scopes=("mcp",))
        world.mint(world.bob, scopes=("mcp",))

        items = client.get("/api/admin/keys", headers=world.session("root")).json()["data"]["items"]

        assert len(world.tool_counts_calls) == 1
        assert sorted(world.tool_counts_calls[0]) == sorted([world.alice.id, world.bob.id])
        assert all(item["mcp_tools"] == {"exposed": 7, "total": 12} for item in items)


class TestOwnerEvents:
    def events(self, client, world, key_id, name="alice", query=""):
        return client.get(f"/api/keys/{key_id}/events{query}", headers=world.session(name))

    def test_newest_first_with_plain_field_changes(self, client, world):
        key, _ = world.mint(world.alice, name="old")
        client.patch(f"/api/keys/{key.id}", json={"name": "new"}, headers=world.session("alice"))
        client.patch(f"/api/keys/{key.id}", json={"scopes": ["read", "generate"]}, headers=world.session("alice"))

        data = self.events(client, world, key.id).json()["data"]

        assert [e["action"] for e in data["items"]] == ["scopes_changed", "renamed", "created"]
        assert data["items"][0]["changes"] == {"scopes": {"added": ["generate"], "removed": []}}
        assert data["items"][1]["changes"] == {"name": {"before": "old", "after": "new"}}
        assert (data["total"], data["page"], data["page_size"]) == (3, 1, 20)

    def test_a_revocation_by_an_admin_names_the_admin(self, client, world):
        key, _ = world.mint(world.alice)
        client.delete(f"/api/admin/keys/{key.id}", headers=world.session("root"))

        top = self.events(client, world, key.id).json()["data"]["items"][0]

        assert top["action"] == "revoked"
        assert top["actor"] == {"id": world.root.id, "username": "root"}
        assert top["detail"] == {"by_owner": False}

    def test_an_expired_key_shows_an_expired_event_at_the_expiry_time(self, client, world):
        from src.platform.database import get_cursor

        key, _ = world.mint(world.alice, days=30)
        with get_cursor() as cursor:
            cursor.execute("UPDATE api_keys SET expires_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (key.id,))
            cursor.execute("UPDATE api_key_events SET created_at = '2025-12-01T00:00:00+00:00' WHERE key_id = ?", (key.id,))

        items = self.events(client, world, key.id).json()["data"]["items"]

        assert items[0]["action"] == "expired" and items[0]["created_at"] == "2026-01-01T00:00:00+00:00"

    def test_pagination_slices_and_bounds_page_size(self, client, world):
        key, _ = world.mint(world.alice)
        for index in range(4):
            client.patch(f"/api/keys/{key.id}", json={"name": f"n{index}"}, headers=world.session("alice"))

        second = self.events(client, world, key.id, query="?page=2&page_size=2").json()["data"]

        assert second["total"] == 5 and len(second["items"]) == 2 and second["page"] == 2
        assert self.events(client, world, key.id, query="?page_size=101").status_code == 422
        assert self.events(client, world, key.id, query="?page=0").status_code == 422

    def test_someone_elses_key_is_404(self, client, world):
        key, _ = world.mint(world.bob)

        assert self.events(client, world, key.id).status_code == 404

    def test_a_key_cannot_read_events_and_nothing_secret_is_returned(self, client, world):
        key, secret = world.mint(world.alice, scopes=("read", "generate", "content"))

        refused = client.get(f"/api/keys/{key.id}/events", headers=world.bearer(secret))
        body = self.events(client, world, key.id).text

        assert refused.status_code == 403
        assert secret not in body and secret[12:] not in body and key.secret_hash not in body
