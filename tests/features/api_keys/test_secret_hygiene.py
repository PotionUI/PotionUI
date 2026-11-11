import logging

from src.platform.database import get_cursor
from src.platform.observability.logger import _redact_secret_query_params
from src.platform.security.redaction import redact_api_keys, redact_text

SAMPLE = "pui_7Hq2xK9aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567Lm3Qz0"


class TestRedaction:
    def test_a_key_in_free_text_is_masked(self):
        assert redact_api_keys(f"failed for {SAMPLE} again") == "failed for pui_*** again"

    def test_a_legacy_token_is_masked_too(self):
        assert SAMPLE not in redact_api_keys("pui_mcp_" + "A" * 43)

    def test_the_display_prefix_is_not_a_secret_and_survives(self):
        assert redact_api_keys("key pui_7Hq2xK9a rejected") == "key pui_7Hq2xK9a rejected"

    def test_redact_text_masks_a_bare_key_and_a_bearer_header(self):
        masked = redact_text(f"token {SAMPLE} and Authorization: Bearer {SAMPLE}")

        assert SAMPLE not in masked and SAMPLE[12:] not in masked

    def test_the_log_filter_masks_keys_in_messages_and_query_strings(self):
        masked = _redact_secret_query_params(f"GET /x?token={SAMPLE} body {SAMPLE}")

        assert SAMPLE not in masked and SAMPLE[12:] not in masked

    def test_non_text_is_passed_through(self):
        assert redact_api_keys(None) is None
        assert redact_api_keys("") == ""


class TestNoSecretAnywhereButTheCreationResponse:
    def test_a_full_lifecycle_never_writes_the_secret_to_a_log_a_row_or_a_later_response(self, client, world, caplog):
        world.stack.settings.set_setting("mcp_enabled", True)
        responses = []
        with caplog.at_level(logging.DEBUG):
            created = client.post("/api/keys", json={"name": "k", "scopes": ["read", "mcp"]}, headers=world.session("alice"))
            secret = created.json()["data"]["secret"]
            key_id = created.json()["data"]["key"]["id"]
            rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            responses.append(client.post("/api/mcp", json=rpc, headers=world.bearer(secret)))
            responses.append(client.post("/api/mcp", json=rpc, headers=world.bearer(secret[:-1] + "!")))
            responses.append(client.get("/api/keys", headers=world.session("alice")))
            responses.append(client.patch(f"/api/keys/{key_id}", json={"name": "r"}, headers=world.session("alice")))
            responses.append(client.get("/api/admin/keys", headers=world.session("root")))
            responses.append(client.get(f"/api/admin/keys/{key_id}/events", headers=world.session("root")))
            responses.append(client.delete(f"/api/admin/keys/{key_id}", headers=world.session("root")))
            responses.append(client.post("/api/mcp", json=rpc, headers=world.bearer(secret)))

        body = secret[4:-6]
        assert body not in caplog.text and secret not in caplog.text
        for response in responses:
            assert body not in response.text and secret not in response.text
        with get_cursor() as cursor:
            for table in ("api_keys", "api_key_events", "api_key_user_access"):
                cursor.execute(f"SELECT * FROM {table}")
                dump = " ".join(str(value) for row in cursor.fetchall() for value in tuple(row))
                assert body not in dump and secret not in dump, table
