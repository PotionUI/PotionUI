import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api as api
from backend import discovery as discovery_module
from backend import http_client
from backend.pkce import code_challenge_s256
from backend.settings import OIDCSettings
from src.plugin_api.identity import AccountType, ExternalLoginError, ExternalSession, User

from .oidc_fixture import FakeIdP


class _FakeSignIn:
    def __init__(self):
        self.calls = []
        self.raise_for_sub = None

    def __call__(self, issuer, sub, claims):
        self.calls.append((issuer, sub, dict(claims or {})))
        if self.raise_for_sub is not None and sub == self.raise_for_sub:
            raise ExternalLoginError(f"no local account mapped for {sub!r}")
        user = User(
            id=f"user-{sub}",
            username=sub,
            email=claims.get("email") or f"{sub}@external.invalid",
            password_hash="x",
            account_type=AccountType.USER,
        )
        return ExternalSession(
            user=user, access_token="at-token", handoff_code=f"handoff-{sub}", created=True
        )


class OIDCFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.idp = FakeIdP()
        http_client.set_transport_override(self.idp.transport())

        self.oidc_settings = OIDCSettings(
            issuer_url=self.idp.issuer,
            client_id="test-client",
            client_secret="test-client-secret",
            scopes="openid profile email",
            label="Test IdP",
            redirect_uri_override=None,
        )
        self._settings_patcher = patch.object(api, "load_settings", lambda: self.oidc_settings)
        self._settings_patcher.start()

        self.fake_sign_in = _FakeSignIn()
        self._sign_in_patcher = patch.object(api, "sign_in_external", self.fake_sign_in)
        self._sign_in_patcher.start()

        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        self._sign_in_patcher.stop()
        self._settings_patcher.stop()
        discovery_module.reset_cache()
        http_client.set_transport_override(None)

    def _start_flow(self):
        response = self.client.get(api.START_PATH)
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.headers["location"])
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}


class TestHappyPath(OIDCFlowTestCase):
    def test_start_then_callback_signs_the_user_in(self):
        flow = self._start_flow()
        self.idp.queue_id_token(
            sub="subject-1",
            nonce=flow["nonce"],
            email="person@example.com",
            email_verified=True,
            preferred_username="person",
            name="Person One",
        )

        callback = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-1", "state": flow["state"]}
        )
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(callback.headers["location"], "/login/callback?code=handoff-subject-1")

        self.assertEqual(len(self.fake_sign_in.calls), 1)
        issuer, sub, claims = self.fake_sign_in.calls[0]
        self.assertEqual(issuer, self.idp.issuer)
        self.assertEqual(sub, "subject-1")
        self.assertEqual(claims["email"], "person@example.com")
        self.assertEqual(claims["email_verified"], True)
        self.assertEqual(claims["preferred_username"], "person")
        self.assertEqual(claims["name"], "Person One")

    def test_pkce_verifier_sent_to_token_endpoint_matches_the_challenge(self):
        flow = self._start_flow()
        self.idp.queue_id_token(sub="subject-2", nonce=flow["nonce"])

        self.client.get(api.CALLBACK_PATH, params={"code": "auth-code-2", "state": flow["state"]})

        self.assertEqual(len(self.idp.token_requests), 1)
        sent_verifier = self.idp.token_requests[0]["code_verifier"]
        self.assertEqual(code_challenge_s256(sent_verifier), flow["code_challenge"])

    def test_missing_userinfo_claims_are_filled_from_userinfo_endpoint(self):
        flow = self._start_flow()
        self.idp.queue_id_token(sub="subject-3", nonce=flow["nonce"])
        self.idp.userinfo_response = {
            "sub": "subject-3",
            "email": "fromuserinfo@example.com",
            "email_verified": True,
            "preferred_username": "fromuserinfo",
        }

        self.client.get(api.CALLBACK_PATH, params={"code": "auth-code-3", "state": flow["state"]})

        _, _, claims = self.fake_sign_in.calls[0]
        self.assertEqual(claims["email"], "fromuserinfo@example.com")
        self.assertEqual(claims["email_verified"], True)
        self.assertEqual(claims["preferred_username"], "fromuserinfo")

    def test_id_token_at_hash_matching_the_real_access_token_is_accepted_by_default(self):
        flow = self._start_flow()
        self.idp.queue_id_token(sub="subject-at-hash", nonce=flow["nonce"])

        callback = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-at-hash", "state": flow["state"]}
        )
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(callback.headers["location"], "/login/callback?code=handoff-subject-at-hash")

    def test_id_token_without_at_hash_is_still_accepted(self):
        flow = self._start_flow()
        self.idp.include_at_hash = False
        self.idp.queue_id_token(sub="subject-no-at-hash", nonce=flow["nonce"])

        callback = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-no-at-hash", "state": flow["state"]}
        )
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(
            callback.headers["location"], "/login/callback?code=handoff-subject-no-at-hash"
        )

    def test_flow_cookie_is_secure_when_the_redirect_uri_is_https(self):
        response = self.client.get(api.START_PATH, headers={"X-Forwarded-Proto": "https"})
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn("Secure", set_cookie)

    def test_flow_cookie_is_not_secure_over_plain_http(self):
        response = self.client.get(api.START_PATH)
        set_cookie = response.headers.get("set-cookie", "")
        self.assertNotIn("Secure", set_cookie)

    def test_email_verified_false_in_id_token_can_never_become_true(self):
        flow = self._start_flow()
        self.idp.queue_id_token(
            sub="subject-unverified",
            nonce=flow["nonce"],
            email_verified=False,
        )
        self.idp.userinfo_response = {
            "sub": "subject-unverified",
            "email": "claimed@example.com",
            "email_verified": True,
        }

        self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-unverified", "state": flow["state"]}
        )

        _, _, claims = self.fake_sign_in.calls[0]
        self.assertEqual(claims["email_verified"], False)

    def test_userinfo_is_not_called_just_because_email_verified_is_false(self):
        flow = self._start_flow()
        self.idp.queue_id_token(
            sub="subject-complete",
            nonce=flow["nonce"],
            email="already@example.com",
            email_verified=False,
            preferred_username="already",
            name="Already",
        )

        self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-complete", "state": flow["state"]}
        )

        self.assertEqual(self.idp.userinfo_requests, 0)

    def test_email_already_present_is_never_overwritten_by_userinfo(self):
        flow = self._start_flow()
        self.idp.queue_id_token(sub="subject-has-email", nonce=flow["nonce"], email="already@example.com")
        self.idp.userinfo_response = {
            "sub": "subject-has-email",
            "email": "different@example.com",
            "email_verified": True,
        }

        self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code-has-email", "state": flow["state"]}
        )

        _, _, claims = self.fake_sign_in.calls[0]
        self.assertEqual(claims["email"], "already@example.com")
        self.assertIsNone(claims.get("email_verified"))


class TestRejections(OIDCFlowTestCase):
    def test_unconfigured_settings_redirect_to_login_error(self):
        self.oidc_settings = OIDCSettings(
            issuer_url=None,
            client_id=None,
            client_secret=None,
            scopes="openid profile email",
            label="Test IdP",
            redirect_uri_override=None,
        )
        response = self.client.get(api.START_PATH)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")
        self.assertEqual(self.fake_sign_in.calls, [])

    def test_discovery_issuer_mismatch_at_start_is_rejected(self):
        self.idp.discovery_issuer_override = "https://not-the-configured-issuer.example"
        response = self.client.get(api.START_PATH)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")

    def test_idp_error_response_is_rejected(self):
        self._start_flow()
        response = self.client.get(api.CALLBACK_PATH, params={"error": "access_denied"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")
        self.assertEqual(self.fake_sign_in.calls, [])

    def test_state_mismatch_is_rejected(self):
        flow = self._start_flow()
        self.idp.next_id_token = self.idp.issue_id_token(sub="subject-x", nonce=flow["nonce"])

        response = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code", "state": "not-the-real-state"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")
        self.assertEqual(self.fake_sign_in.calls, [])

    def test_missing_cookie_is_rejected(self):
        flow = self._start_flow()
        self.client.cookies.clear()

        response = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code", "state": flow["state"]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")
        self.assertEqual(self.fake_sign_in.calls, [])

    def test_tampered_cookie_is_rejected(self):
        flow = self._start_flow()
        self.client.cookies.set(api.FLOW_COOKIE_NAME, "not-a-valid-jwt")

        response = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code", "state": flow["state"]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")
        self.assertEqual(self.fake_sign_in.calls, [])

    def test_no_mapping_is_rejected(self):
        self.fake_sign_in.raise_for_sub = "subject-never-mapped"
        flow = self._start_flow()
        self.idp.next_id_token = self.idp.issue_id_token(sub="subject-never-mapped", nonce=flow["nonce"])

        response = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code", "state": flow["state"]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?error=external_login")

    def test_unknown_kid_on_first_lookup_does_not_double_fetch_and_login_is_rejected(self):
        flow = self._start_flow()
        self.idp.queue_id_token(sub="subject-rotated", nonce=flow["nonce"])
        self.idp.keys = []

        callback = self.client.get(
            api.CALLBACK_PATH, params={"code": "auth-code", "state": flow["state"]}
        )
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(callback.headers["location"], "/login?error=external_login")
        self.assertEqual(self.idp.jwks_requests, 1)


if __name__ == "__main__":
    unittest.main()
