import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.auth.external_login import ExternalLoginManager
from src.features.auth.repository import ExternalIdentityRepository
from src.features.auth.routes import build_router as build_auth_router
from src.features.user_groups.repository import UserGroupRepository
from src.features.users.repository import UserRepository
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.plugins.login_providers import login_provider_registry
from src.platform.plugins.registry import PluginRegistry
from src.platform.security import Auth, AuthConfig, PasswordHasher, TokenCodec
from src.platform.security.current_user import set_auth
from src.platform.security.login_handoff import LoginHandoffStore
from src.platform.security.user import AccountType
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

from tests.fixtures.stub_login_plugin import api as stub_plugin


class _StubClaimStore:
    def is_claimed(self) -> bool:
        return True


class _StubClaimTokens:
    def verify(self, token) -> bool:
        return False

    def clear(self) -> None:
        pass


class TestExternalLoginEndToEnd(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._previous_providers = login_provider_registry.all()
        for definition in self._previous_providers:
            login_provider_registry.unregister(definition.id)

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
            patch.dict(
                os.environ, {"POTIONUI_AUTH_SECRET_KEY": "test-secret-key"}, clear=False
            ),
        ]
        for patcher in self._patchers:
            patcher.start()

        self._run_migrations()

        self.settings = Settings(SettingRepository())
        self.users = UserRepository()
        self.identities = ExternalIdentityRepository()
        self.handoff = LoginHandoffStore()
        self.auth = Auth(
            user_repository=self.users,
            password_hasher=PasswordHasher(),
            token_codec=TokenCodec(AuthConfig(self.settings)),
            auth_config=AuthConfig(self.settings),
            plugin_registry=PluginRegistry(
                marketplace_dir=str(Path(self.temp_dir) / "marketplace"),
                local_dir=str(Path(self.temp_dir) / "local"),
            ),
            instance_claim=_StubClaimStore(),
            claim_tokens=_StubClaimTokens(),
            settings=self.settings,
        )
        self.external_login = ExternalLoginManager(
            auth=self.auth,
            users=self.users,
            external_identities=self.identities,
            user_groups=UserGroupRepository(),
            settings=self.settings,
            handoff=self.handoff,
        )

        class _Container:
            pass

        self.container = _Container()
        self.container.auth = self.auth
        self.container.login_handoff = self.handoff
        self.container.external_login = self.external_login

        self._container_patcher = patch(
            "src.platform.plugins.runtime_registries._container", self.container
        )
        self._container_patcher.start()

        set_auth(self.auth)

        stub_plugin.enable()

        app = FastAPI()
        app.include_router(build_auth_router(self.container))
        app.include_router(stub_plugin.router)
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        stub_plugin.disable()
        self._container_patcher.stop()
        for patcher in reversed(self._patchers):
            patcher.stop()
        for definition in login_provider_registry.all():
            login_provider_registry.unregister(definition.id)
        for definition in self._previous_providers:
            login_provider_registry.register(definition)
        for leftover in sorted(
            Path(self.temp_dir).rglob("*"), key=lambda p: len(p.parts), reverse=True
        ):
            if leftover.is_file():
                leftover.unlink()
            else:
                leftover.rmdir()
        Path(self.temp_dir).rmdir()
        Database._instance = None
        set_auth(None)

    def _run_migrations(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

    def _walk_sign_in(self, sub, **claims):
        start = self.client.get(
            stub_plugin.START_PATH, params={"sub": sub, **claims}
        )
        self.assertEqual(start.status_code, 302)

        callback = self.client.get(start.headers["location"])
        self.assertEqual(callback.status_code, 302)
        return callback.headers["location"]

    def _exchange(self, landing_url):
        self.assertTrue(landing_url.startswith("/login/callback?code="))
        code = landing_url.split("code=", 1)[1]
        self.assertNotIn(code, ("", "None"))

        exchange = self.client.post("/api/auth/external/exchange", json={"code": code})
        self.assertEqual(exchange.status_code, 200)
        return exchange.json()["access_token"]

    def test_the_stub_provider_appears_on_the_login_page(self):
        response = self.client.get("/api/auth/providers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": stub_plugin.PLUGIN_ID,
                    "label": stub_plugin.LABEL,
                    "start_path": stub_plugin.START_PATH,
                }
            ],
        )

    def test_a_disabled_plugin_leaves_no_provider_behind(self):
        stub_plugin.disable()

        self.assertEqual(self.client.get("/api/auth/providers").json(), [])

        stub_plugin.enable()

    def test_the_plugin_registry_teardown_clears_the_provider(self):
        registry = PluginRegistry(
            marketplace_dir=str(Path(self.temp_dir) / "marketplace"),
            local_dir=str(Path(self.temp_dir) / "local"),
        )

        registry._rollback_partial_enable(stub_plugin.PLUGIN_ID)

        self.assertIsNone(login_provider_registry.get(stub_plugin.PLUGIN_ID))

        stub_plugin.enable()

    def test_an_unmapped_identity_is_refused_while_auto_create_is_off(self):
        landing = self._walk_sign_in("subject-1", preferred_username="newcomer")

        self.assertEqual(landing, "/login?error=external_login")
        self.assertEqual(len(self.users.get_all()), 0)

    def test_a_full_sign_in_creates_the_user_and_authenticates_the_token(self):
        self.settings.set_setting("external_login_auto_create", "true")

        landing = self._walk_sign_in(
            "subject-2",
            preferred_username="newcomer",
            email="newcomer@example.com",
            email_verified="true",
        )
        access_token = self._exchange(landing)

        me = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )

        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"]["username"], "newcomer")
        self.assertEqual(me.json()["data"]["account_type"], AccountType.USER.value)

    def test_signing_in_twice_reuses_the_same_local_user(self):
        self.settings.set_setting("external_login_auto_create", "true")

        first = self._exchange(
            self._walk_sign_in("subject-3", preferred_username="repeat")
        )
        second = self._exchange(
            self._walk_sign_in("subject-3", preferred_username="repeat")
        )

        first_user = self.auth.get_user_from_token(first)
        second_user = self.auth.get_user_from_token(second)

        self.assertEqual(first_user.id, second_user.id)
        self.assertEqual(len(self.users.get_all()), 1)

    def test_the_landing_url_never_carries_the_access_token(self):
        self.settings.set_setting("external_login_auto_create", "true")

        landing = self._walk_sign_in("subject-4", preferred_username="secretive")

        self.assertNotIn("access_token", landing)
        self.assertNotIn("token=", landing)

    def test_a_handoff_code_cannot_be_exchanged_twice(self):
        self.settings.set_setting("external_login_auto_create", "true")
        landing = self._walk_sign_in("subject-5", preferred_username="onceonly")
        code = landing.split("code=", 1)[1]

        self.client.post("/api/auth/external/exchange", json={"code": code})
        replay = self.client.post("/api/auth/external/exchange", json={"code": code})

        self.assertEqual(replay.status_code, 401)

    def test_link_by_email_attaches_the_identity_to_an_existing_account(self):
        self.settings.set_setting("external_login_link_by_email", "true")
        existing = self.users.create(
            username="already",
            email="already@example.com",
            password_hash="hash",
            account_type=AccountType.USER,
        )

        landing = self._walk_sign_in(
            "subject-6", email="already@example.com", email_verified="true"
        )
        access_token = self._exchange(landing)

        self.assertEqual(self.auth.get_user_from_token(access_token).id, existing.id)
        self.assertEqual(len(self.users.get_all()), 1)


if __name__ == "__main__":
    unittest.main()
