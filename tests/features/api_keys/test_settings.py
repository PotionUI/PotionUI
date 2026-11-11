import json

import pytest

from src.features.api_keys.operations.limits import load_limits
from src.features.api_keys.settings import validate_setting
from src.platform.security.api_scopes import ApiScope as S


class TestValidateSetting:
    def test_keys_it_does_not_own_are_ignored(self):
        assert validate_setting("tmp_retention_days", "banana") is None

    @pytest.mark.parametrize("value", [True, False, "true", "false"])
    def test_the_switch_takes_a_boolean(self, value):
        assert validate_setting("api_keys_enabled", value) is None

    def test_the_switch_refuses_anything_else(self):
        assert validate_setting("api_keys_enabled", "maybe")

    @pytest.mark.parametrize("value", [["read"], [], ["read", "admin", "mcp"], json.dumps(["read", "generate"])])
    def test_scope_lists_are_accepted(self, value):
        assert validate_setting("api_key_scopes_allowed", value) is None

    @pytest.mark.parametrize("value", [["root"], "not json", "{}", 5, None, ["read", 3]])
    def test_bad_scope_lists_are_refused(self, value):
        assert validate_setting("api_key_scopes_allowed", value)

    @pytest.mark.parametrize("value", [0, 30, 365, "90", 3650])
    def test_the_maximum_lifetime_takes_whole_days(self, value):
        assert validate_setting("api_key_max_lifetime_days", value) is None

    @pytest.mark.parametrize("value", [-1, 3651, "soon", True, None])
    def test_other_lifetimes_are_refused(self, value):
        assert validate_setting("api_key_max_lifetime_days", value)

    @pytest.mark.parametrize("value", ["all_on", "read_only_on"])
    def test_the_ceiling_policy_takes_its_two_values(self, value):
        assert validate_setting("mcp_tools_ceiling_policy", value) is None

    def test_other_policies_are_refused(self):
        assert validate_setting("mcp_tools_ceiling_policy", "some_on")


class TestLimits:
    def test_the_seeded_defaults(self, stack):
        limits = load_limits(stack.settings)

        assert limits.keys_enabled is True
        assert limits.mcp_enabled is False
        assert limits.scopes_allowed == {S.READ, S.GENERATE, S.CONTENT, S.MCP}
        assert limits.max_lifetime_days == 0

    def test_admin_is_off_in_the_ceiling_until_an_admin_adds_it(self, stack):
        assert S.ADMIN not in load_limits(stack.settings).scopes_allowed

        stack.settings.set_setting("api_key_scopes_allowed", ["read", "admin"])

        assert S.ADMIN in load_limits(stack.settings).scopes_allowed

    def test_read_cannot_be_taken_out_of_the_ceiling(self, stack):
        stack.settings.set_setting("api_key_scopes_allowed", [])

        assert load_limits(stack.settings).scopes_allowed == {S.READ}

    def test_unknown_scopes_in_the_setting_are_ignored(self, stack):
        stack.settings.set_setting("api_key_scopes_allowed", ["read", "root", "mcp"])

        assert load_limits(stack.settings).scopes_allowed == {S.READ, S.MCP}
