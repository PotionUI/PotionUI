from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.chat.routes import build_router as build_chat_router
from src.features.generation.routes import build_router as build_generation_router
from src.features.media.routes import build_router as build_media_router
from src.features.presets.routes import build_router as build_presets_router
from src.features.setup.routes import build_router as build_setup_router
from src.features.system_monitor.routes import build_router as build_system_router
from src.features.users.routes import build_router as build_users_router
from tests.features.api_keys.scope_support import Anything, Reply


def passed(response):
    return response.status_code not in (401, 403)


def detail_error(response):
    return response.json()["detail"]["error"]


@pytest.fixture
def api(world):
    app = FastAPI()
    container = Anything()
    for build in (
        build_generation_router, build_media_router, build_chat_router, build_setup_router,
        build_system_router, build_users_router,
    ):
        app.include_router(build(container))
    return TestClient(app, raise_server_exceptions=False)


def key_for(world, owner, *scopes, days=None):
    world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
    world.stack.settings.set_setting("mcp_enabled", True)
    return world.mint(owner, scopes=scopes, days=days)[1]


def admin_key(world, *scopes):
    return key_for(world, world.root, *scopes, days=30)


class TestGeneration:
    def test_a_read_key_lists_history_but_cannot_start(self, api, world):
        headers = world.bearer(key_for(world, world.alice, "read"))

        assert api.get("/api/generations/history", headers=headers).status_code == 200
        started = api.post("/api/generations/start", json={}, headers=headers)

        assert (started.status_code, detail_error(started)) == (403, "insufficient_scope")
        assert started.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="generate"'

    def test_a_generate_key_starts_cancels_and_clears_the_queue(self, api, world):
        headers = world.bearer(key_for(world, world.alice, "generate"))

        assert passed(api.post("/api/generations/start", json={}, headers=headers))
        assert passed(api.post("/api/generations/abc/cancel", headers=headers))
        assert passed(api.post("/api/generations/queue/clear", json={}, headers=headers))
        assert api.get("/api/generations/history", headers=headers).status_code == 403

    def test_a_content_key_deletes_history_and_a_generate_key_may_not(self, api, world):
        content = world.bearer(key_for(world, world.alice, "content"))
        generate = world.bearer(key_for(world, world.bob, "generate"))

        assert api.delete("/api/generations/history/abc", headers=content).status_code == 200
        assert api.delete("/api/generations/history/abc", headers=generate).status_code == 403

    @pytest.mark.parametrize("method,path", [
        ("PUT", "/api/generations/abc/rating"),
        ("PUT", "/api/generations/abc/favorite"),
        ("PUT", "/api/generations/abc/tags"),
        ("DELETE", "/api/generations/abc/tags/t1"),
        ("POST", "/api/generations/history/bulk-delete"),
        ("POST", "/api/generations/history/bulk-delete-by-criteria"),
        ("POST", "/api/generations/import-bundle"),
        ("POST", "/api/generations/upload"),
    ])
    def test_changing_the_owners_gallery_needs_content(self, api, world, method, path):
        content = world.bearer(key_for(world, world.alice, "content"))
        generate = world.bearer(key_for(world, world.bob, "generate"))
        read = world.bearer(key_for(world, world.alice, "read"))

        assert api.request(method, path, json={}, headers=content).status_code != 403, path
        assert api.request(method, path, json={}, headers=generate).status_code == 403, path
        assert api.request(method, path, json={}, headers=read).status_code == 403, path

    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/generations/memory-preview"),
        ("POST", "/api/generations/history/count-by-criteria"),
        ("POST", "/api/generations/export"),
        ("GET", "/api/generations/history/abc/export-bundle"),
    ])
    def test_read_shaped_posts_need_only_read(self, api, world, method, path):
        read = world.bearer(key_for(world, world.alice, "read"))
        generate = world.bearer(key_for(world, world.bob, "generate"))

        assert api.request(method, path, json={}, headers=read).status_code != 403, path
        assert api.request(method, path, json={}, headers=generate).status_code == 403, path

    def test_generation_failure_detail_is_an_admin_route(self, api, world):
        read = world.bearer(key_for(world, world.alice, "read", "generate", "content"))

        response = api.get("/api/generations/abc/failure", headers=read)

        assert response.status_code == 403 and detail_error(response) == "insufficient_scope"
        assert response.headers["www-authenticate"].endswith('scope="admin"')


class TestUploads:
    def test_input_media_upload_needs_generate(self, api, world):
        generate = world.bearer(key_for(world, world.alice, "generate"))
        content = world.bearer(key_for(world, world.bob, "content"))

        assert api.post("/api/media/upload", headers=generate).status_code != 403
        assert api.post("/api/media/upload", headers=content).status_code == 403

    def test_deleting_an_upload_needs_content(self, api, world):
        generate = world.bearer(key_for(world, world.alice, "generate"))
        content = world.bearer(key_for(world, world.bob, "content"))

        assert api.delete("/api/media/uploads/a.png", headers=generate).status_code == 403
        assert api.delete("/api/media/uploads/a.png", headers=content).status_code != 403

    def test_listing_uploads_and_media_routes_need_read(self, api, world):
        read = world.bearer(key_for(world, world.alice, "read"))
        mcp = world.bearer(key_for(world, world.bob, "mcp"))

        assert api.get("/api/media/uploads", headers=read).status_code == 200
        assert api.get("/api/media/uploads", headers=mcp).status_code == 403


class TestSessionOnlyAreas:
    def test_chat_and_the_wizard_refuse_every_key_including_admin(self, api, world):
        every = world.bearer(admin_key(world, "read", "generate", "content", "mcp", "admin"))

        for method, path in (
            ("GET", "/api/chat/sessions"), ("POST", "/api/chat/sessions"),
            ("GET", "/api/chat/modes"), ("GET", "/api/readiness"), ("POST", "/api/setup/runs"),
        ):
            response = api.request(method, path, json={}, headers=every)
            assert (response.status_code, detail_error(response)) == (403, "key_not_allowed"), path

    def test_a_session_still_reaches_chat(self, api, world):
        assert api.get("/api/chat/modes", headers=world.session("alice")).status_code != 403

    def test_changing_a_profile_or_creating_a_user_is_not_for_a_plain_key(self, api, world):
        every = world.bearer(key_for(world, world.alice, "read", "generate", "content", "mcp"))

        put = api.put("/api/users/u1", json={}, headers=every)
        post = api.post("/api/users", json={}, headers=every)
        avatar = api.post("/api/users/u1/avatar", headers=every)

        assert (put.status_code, detail_error(put)) == (403, "key_not_allowed")
        assert (post.status_code, detail_error(post)) == (403, "insufficient_scope")
        assert avatar.status_code == 403


class TestAdminScopeAndCeiling:
    def test_an_admin_scoped_key_reaches_an_admin_route_while_the_ceiling_allows_it(self, api, world):
        secret = admin_key(world, "admin")

        assert passed(api.get("/api/system/backends", headers=world.bearer(secret)))

    def test_the_same_key_is_refused_once_the_ceiling_forbids_admin(self, api, world):
        secret = admin_key(world, "admin")
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp"])

        response = api.get("/api/system/backends", headers=world.bearer(secret))

        assert (response.status_code, detail_error(response)) == (403, "insufficient_scope")

    def test_a_key_without_admin_never_reaches_an_admin_route(self, api, world):
        secret = admin_key(world, "read", "generate", "content")

        assert api.get("/api/system/backends", headers=world.bearer(secret)).status_code == 403

    def test_an_admin_key_also_has_read_generate_and_content(self, api, world):
        headers = world.bearer(admin_key(world, "admin"))

        assert api.get("/api/generations/history", headers=headers).status_code == 200
        assert passed(api.post("/api/generations/start", json={}, headers=headers))
        assert api.delete("/api/generations/history/abc", headers=headers).status_code == 200

    def test_an_admin_key_does_not_reach_session_only_areas(self, api, world):
        assert api.get("/api/chat/modes", headers=world.bearer(admin_key(world, "admin"))).status_code == 403

    def test_an_admin_key_may_create_a_user_but_a_session_less_key_cannot_edit_one(self, api, world):
        headers = world.bearer(admin_key(world, "admin"))

        assert api.post("/api/users", json={}, headers=headers).status_code != 403
        assert api.put("/api/users/u1", json={}, headers=headers).status_code == 403


class TestSystemSettingsThroughTheDowngrade:
    @pytest.fixture
    def settings_api(self, world):
        from src.features.settings.routes import build_router as build_settings_router
        from src.platform.settings.records import SettingType, SettingValueType

        repository = Mock()
        setting = Mock(id="id-model_cache_scope", key="model_cache_scope", type=SettingType.SYSTEM, value_type=SettingValueType.STRING, description="d")
        repository.get_setting_by_key.side_effect = lambda key: setting if key == "model_cache_scope" else None
        container = SimpleNamespace(
            settings=Mock(get_setting=Mock(return_value="preset")), setting_repository=repository,
            gpu_monitor=Mock(), backend_registry=MagicMock(),
            api_key_collaborators=world.stack.collaborators,
        )
        app = FastAPI()
        app.include_router(build_settings_router(container))
        return TestClient(app, raise_server_exceptions=False), repository

    def test_reading_a_system_setting_is_refused_to_a_key_without_admin_even_for_an_admin_owner(self, settings_api, world):
        client, _ = settings_api
        read = world.bearer(key_for(world, world.root, "read"))

        response = client.get("/api/settings/model_cache_scope", headers=read)
        session = client.get("/api/settings/model_cache_scope", headers=world.session("root"))

        assert response.status_code != 200
        assert "Admin privileges required" in response.text
        assert session.status_code == 200

    def test_an_admin_scoped_key_reads_and_changes_an_ordinary_system_setting(self, settings_api, world):
        client, repository = settings_api
        headers = world.bearer(admin_key(world, "admin"))

        assert client.get("/api/settings/model_cache_scope", headers=headers).status_code == 200
        batch = client.put("/api/settings", json={"model_cache_scope": "global"}, headers=headers)
        single = client.put("/api/settings/model_cache_scope", json={"value": "global"}, headers=headers)

        assert batch.status_code == 200 and single.status_code == 200
        assert repository.apply_bulk_updates.called

    def test_a_write_key_without_admin_cannot_change_a_system_setting(self, settings_api, world):
        client, repository = settings_api
        headers = world.bearer(key_for(world, world.root, "read", "generate", "content"))

        assert client.put("/api/settings", json={"model_cache_scope": "global"}, headers=headers).status_code == 403
        assert client.put("/api/settings/model_cache_scope", json={"value": "global"}, headers=headers).status_code == 403
        assert not repository.apply_bulk_updates.called

    @pytest.mark.parametrize("name", [
        "api_keys_enabled", "api_key_scopes_allowed", "api_key_max_lifetime_days", "mcp_tools_ceiling_policy", "mcp_enabled",
    ])
    def test_an_admin_scoped_key_cannot_change_the_key_and_mcp_switches_through_either_route(self, settings_api, world, name):
        client, repository = settings_api
        headers = world.bearer(admin_key(world, "admin"))

        batch = client.put("/api/settings", json={name: "x"}, headers=headers)
        single = client.put(f"/api/settings/{name}", json={"value": "x"}, headers=headers)

        for response in (batch, single):
            assert response.status_code == 403
            assert response.json()["detail"]["error"] == "key_not_allowed"
        assert not repository.apply_bulk_updates.called

    def test_deleting_a_user_override_is_session_only(self, settings_api, world):
        client, _ = settings_api
        headers = world.bearer(admin_key(world, "admin"))

        response = client.delete("/api/settings/user/anything", headers=headers)

        assert (response.status_code, detail_error(response)) == (403, "key_not_allowed")


class TestRevocationAndSwitches:
    def test_a_revoked_key_is_refused_on_a_declared_route(self, api, world):
        key, secret = world.mint(world.alice, scopes=("read",))
        assert api.get("/api/generations/history", headers=world.bearer(secret)).status_code == 200
        from src.features.api_keys.operations import keys as operations

        operations.revoke_own(world.stack.collaborators, world.alice, key.id)

        response = api.get("/api/generations/history", headers=world.bearer(secret))

        assert (response.status_code, detail_error(response)) == (401, "invalid_api_key")

    def test_keys_are_refused_everywhere_when_the_instance_switch_is_off(self, api, world):
        _, secret = world.mint(world.alice, scopes=("read",))
        world.stack.settings.set_setting("api_keys_enabled", False)

        assert api.get("/api/generations/history", headers=world.bearer(secret)).status_code == 401
        assert api.get("/api/generations/history", headers=world.session("alice")).status_code == 200


class TestPresetModelsOperationalFieldsFollowAuthority:
    def test_the_preset_models_route_gives_operational_fields_only_to_effective_admins(self, world):
        controller = Mock()
        controller.get_preset_models = AsyncMock(return_value=Reply(success=True, data={}))
        app = FastAPI()
        app.include_router(build_presets_router(SimpleNamespace(preset_controller=controller)))
        client = TestClient(app, raise_server_exceptions=False)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])

        cases = (
            (world.root, ("read",), False),
            (world.root, ("admin",), True),
            (world.alice, ("read",), False),
        )
        for owner, scopes, expected in cases:
            _, secret = world.mint(owner, scopes=scopes, days=30 if "admin" in scopes else None)
            controller.get_preset_models.reset_mock()
            assert client.get("/api/presets/p1/models", headers=world.bearer(secret)).status_code == 200
            assert controller.get_preset_models.call_args.args[9] is expected, (owner.username, scopes)
        controller.get_preset_models.reset_mock()
        client.get("/api/presets/p1/models", headers=world.session("root"))
        assert controller.get_preset_models.call_args.args[9] is True
