import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.features.auth.external_login import (
    ExternalLoginError,
    ExternalLoginManager,
    normalize_username,
    username_candidates,
)
from src.features.auth.repository import ExternalIdentityRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.users.repository import UserRepository
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.plugins.registry import PluginRegistry
from src.platform.security import Auth, AuthConfig, PasswordHasher, TokenCodec
from src.platform.security.claim_store import InstanceClaimStore
from src.platform.security.login_handoff import LoginHandoffStore
from src.platform.security.user import AccountType
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

ISSUER = "https://idp.example"


class _StubClaimStore:
    def is_claimed(self) -> bool:
        return True


class _StubClaimTokens:
    def verify(self, token) -> bool:
        return False

    def clear(self) -> None:
        pass


class TestExternalLoginManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

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
        self.groups = UserGroupRepository()
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
        self.manager = ExternalLoginManager(
            auth=self.auth,
            users=self.users,
            external_identities=self.identities,
            user_groups=self.groups,
            settings=self.settings,
            handoff=self.handoff,
        )

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        for leftover in Path(self.temp_dir).rglob("*"):
            if leftover.is_file():
                leftover.unlink()
        for leftover in sorted(
            Path(self.temp_dir).rglob("*"), key=lambda p: len(p.parts), reverse=True
        ):
            if leftover.is_dir():
                leftover.rmdir()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _run_migrations(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

    def _set(self, key, value):
        self.settings.set_setting(key, value)

    def _enable_auto_create(self):
        self._set("external_login_auto_create", "true")

    def _make_local_user(self, username, email):
        return self.users.create(
            username=username,
            email=email,
            password_hash="hash",
            account_type=AccountType.USER,
        )

    def test_existing_mapping_signs_in_that_user_without_creating_one(self):
        user = self._make_local_user("mapped", "mapped@example.com")
        self.identities.create(ISSUER, "sub-1", user.id)
        before = len(self.users.get_all())

        session = self.manager.sign_in_external(ISSUER, "sub-1", {})

        self.assertEqual(session.user.id, user.id)
        self.assertFalse(session.created)
        self.assertEqual(len(self.users.get_all()), before)

    def test_existing_mapping_stamps_last_login(self):
        user = self._make_local_user("stamped", "stamped@example.com")
        mapping = self.identities.create(ISSUER, "sub-2", user.id)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE external_identities SET last_login_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", mapping.id),
            )

        self.manager.sign_in_external(ISSUER, "sub-2", {})

        self.assertGreater(
            self.identities.get(ISSUER, "sub-2").last_login_at.year, 2020
        )

    def test_mapping_pointing_at_a_deleted_user_is_rejected_and_cleaned_up(self):
        user = self._make_local_user("ghost", "ghost@example.com")
        self.identities.create(ISSUER, "sub-3", user.id)
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("DELETE FROM users WHERE id = ?", (user.id,))

        with self.assertRaises(ExternalLoginError):
            self.manager.sign_in_external(ISSUER, "sub-3", {})

        self.assertIsNone(self.identities.get(ISSUER, "sub-3"))

    def test_auto_create_off_refuses_an_unmapped_identity(self):
        with self.assertRaises(ExternalLoginError):
            self.manager.sign_in_external(
                ISSUER, "sub-4", {"preferred_username": "newbie"}
            )

        self.assertIsNone(self.identities.get(ISSUER, "sub-4"))
        self.assertEqual(len(self.users.get_all()), 0)

    def test_auto_create_on_creates_a_regular_user_and_maps_it(self):
        self._enable_auto_create()

        session = self.manager.sign_in_external(
            ISSUER,
            "sub-5",
            {"preferred_username": "newbie", "email": "newbie@example.com"},
        )

        self.assertTrue(session.created)
        self.assertEqual(session.user.username, "newbie")
        self.assertEqual(session.user.email, "newbie@example.com")
        self.assertEqual(session.user.account_type, AccountType.USER)
        self.assertEqual(
            self.identities.get(ISSUER, "sub-5").user_id, session.user.id
        )

    def test_auto_created_account_is_never_an_admin(self):
        self._enable_auto_create()

        session = self.manager.sign_in_external(
            ISSUER, "sub-6", {"preferred_username": "wannabeadmin"}
        )

        self.assertEqual(session.user.account_type, AccountType.USER)

    def test_username_falls_back_to_the_email_local_part(self):
        self._enable_auto_create()

        session = self.manager.sign_in_external(
            ISSUER, "sub-7", {"email": "fallback.person@example.com"}
        )

        self.assertEqual(session.user.username, "fallback.person")

    def test_username_is_deduplicated_against_an_existing_account(self):
        self._enable_auto_create()
        self._make_local_user("taken", "taken@example.com")

        session = self.manager.sign_in_external(
            ISSUER, "sub-8", {"preferred_username": "taken"}
        )

        self.assertNotEqual(session.user.username, "taken")
        self.assertTrue(session.user.username.startswith("taken"))

    def test_username_deduplication_survives_a_second_collision(self):
        self._enable_auto_create()
        self._make_local_user("dup", "dup@example.com")
        first = self.manager.sign_in_external(
            ISSUER, "sub-9", {"preferred_username": "dup"}
        )

        second = self.manager.sign_in_external(
            ISSUER, "sub-10", {"preferred_username": "dup"}
        )

        self.assertNotEqual(first.user.username, second.user.username)
        self.assertNotEqual(second.user.username, "dup")

    def test_auto_created_user_joins_the_configured_default_group(self):
        self._enable_auto_create()
        group = self.groups.create_group("External users")
        self._set("external_login_default_group", group.id)

        session = self.manager.sign_in_external(
            ISSUER, "sub-11", {"preferred_username": "grouped"}
        )

        self.assertTrue(self.groups.is_user_in_group(group.id, session.user.id))

    def test_an_unknown_default_group_does_not_block_the_sign_in(self):
        self._enable_auto_create()
        self._set("external_login_default_group", "no-such-group")

        session = self.manager.sign_in_external(
            ISSUER, "sub-12", {"preferred_username": "stillworks"}
        )

        self.assertTrue(session.created)

    def test_link_by_email_off_creates_a_separate_account(self):
        self._enable_auto_create()
        existing = self._make_local_user("local", "shared@example.com")

        session = self.manager.sign_in_external(
            ISSUER,
            "sub-13",
            {
                "email": "shared@example.com",
                "email_verified": True,
                "preferred_username": "remote",
            },
        )

        self.assertNotEqual(session.user.id, existing.id)
        self.assertTrue(session.created)

    def test_link_by_email_on_attaches_to_the_existing_account(self):
        self._set("external_login_link_by_email", "true")
        existing = self._make_local_user("local", "shared@example.com")

        session = self.manager.sign_in_external(
            ISSUER,
            "sub-14",
            {"email": "shared@example.com", "email_verified": True},
        )

        self.assertEqual(session.user.id, existing.id)
        self.assertFalse(session.created)
        self.assertEqual(self.identities.get(ISSUER, "sub-14").user_id, existing.id)

    def test_link_by_email_refuses_an_unverified_email(self):
        self._set("external_login_link_by_email", "true")
        self._make_local_user("local", "shared@example.com")

        with self.assertRaises(ExternalLoginError):
            self.manager.sign_in_external(
                ISSUER,
                "sub-15",
                {"email": "shared@example.com", "email_verified": False},
            )

    def test_link_by_email_is_case_insensitive(self):
        self._set("external_login_link_by_email", "true")
        existing = self._make_local_user("local", "Shared@Example.com")

        session = self.manager.sign_in_external(
            ISSUER,
            "sub-16",
            {"email": "shared@example.com", "email_verified": True},
        )

        self.assertEqual(session.user.id, existing.id)

    def test_a_blank_issuer_or_subject_is_rejected(self):
        with self.assertRaises(ExternalLoginError):
            self.manager.sign_in_external("", "sub-17", {})
        with self.assertRaises(ExternalLoginError):
            self.manager.sign_in_external(ISSUER, "   ", {})

    def test_the_session_carries_a_token_that_resolves_to_the_user(self):
        self._enable_auto_create()

        session = self.manager.sign_in_external(
            ISSUER, "sub-18", {"preferred_username": "tokenuser"}
        )

        resolved = self.auth.get_user_from_token(session.access_token)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, session.user.id)

    def test_the_handoff_code_redeems_once_for_that_token(self):
        self._enable_auto_create()

        session = self.manager.sign_in_external(
            ISSUER, "sub-19", {"preferred_username": "handoff"}
        )

        self.assertEqual(self.handoff.redeem(session.handoff_code), session.access_token)
        self.assertIsNone(self.handoff.redeem(session.handoff_code))

    def test_signing_in_stamps_the_users_last_login(self):
        user = self._make_local_user("stamps", "stamps@example.com")
        self.identities.create(ISSUER, "sub-20", user.id)

        self.manager.sign_in_external(ISSUER, "sub-20", {})

        self.assertIsNotNone(self.users.get_by_id(user.id).last_login)

    def test_an_auto_created_account_cannot_be_logged_into_with_a_blank_password(self):
        self._enable_auto_create()
        session = self.manager.sign_in_external(
            ISSUER, "sub-21", {"preferred_username": "nopassword"}
        )

        for attempt in ["", "password", session.user.username]:
            with self.assertRaises(ValueError):
                self.auth.authenticate(session.user.username, attempt)


class TestUsernameDerivation(unittest.TestCase):
    def test_normalize_username_strips_disallowed_characters(self):
        self.assertEqual(normalize_username("Ann@Example Corp!"), "AnnExampleCorp")

    def test_normalize_username_keeps_the_policy_punctuation(self):
        self.assertEqual(normalize_username("first.last_1-2"), "first.last_1-2")

    def test_normalize_username_caps_the_length(self):
        self.assertEqual(len(normalize_username("a" * 200)), 64)

    def test_candidates_prefer_preferred_username_then_email_then_subject(self):
        candidates = username_candidates(
            {"preferred_username": "pref", "email": "mail@example.com", "name": "Full Name"},
            "subject-id",
        )

        self.assertEqual(candidates[0], "pref")
        self.assertIn("mail", candidates)
        self.assertIn("subject-id", candidates)

    def test_candidates_fall_back_to_the_subject_when_claims_are_empty(self):
        self.assertEqual(username_candidates({}, "subject-id"), ["subject-id"])

    def test_candidates_ignore_non_string_claims(self):
        self.assertEqual(
            username_candidates({"preferred_username": 42, "email": None}, "sub"),
            ["sub"],
        )


if __name__ == "__main__":
    unittest.main()
