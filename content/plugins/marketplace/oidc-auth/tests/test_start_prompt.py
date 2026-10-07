from urllib.parse import parse_qs, urlparse

import backend.api as api

from .test_callback_end_to_end import OIDCFlowTestCase


class TestStartPrompt(OIDCFlowTestCase):
    def _authorize_params(self, query: str = ""):
        response = self.client.get(f"{api.START_PATH}{query}")
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.headers["location"]).query)

    def test_no_prompt_by_default(self):
        self.assertNotIn("prompt", self._authorize_params())

    def test_select_account_is_forwarded(self):
        self.assertEqual(self._authorize_params("?prompt=select_account")["prompt"], ["select_account"])

    def test_login_is_forwarded(self):
        self.assertEqual(self._authorize_params("?prompt=login")["prompt"], ["login"])

    def test_values_outside_the_allow_list_are_dropped(self):
        for value in ("none", "consent", "select_account%20login", "x"):
            self.assertNotIn("prompt", self._authorize_params(f"?prompt={value}"))

    def test_prompt_does_not_disturb_the_rest_of_the_flow(self):
        params = self._authorize_params("?prompt=select_account")
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("state", params)
        self.assertIn("nonce", params)
