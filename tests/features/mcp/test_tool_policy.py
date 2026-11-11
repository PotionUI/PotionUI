import pytest

from src.features.mcp.tool_policy import (
    POLICY_ALL_ON,
    POLICY_READ_ONLY_ON,
    LevelPolicy,
    ToolState,
    allows,
    ceiling_state,
    is_reviewed,
    refusal_message,
    resolve_state,
)

NONE = frozenset()


def ceiling(policy=POLICY_ALL_ON, **overrides):
    return LevelPolicy(policy, overrides)


def default(policy=POLICY_READ_ONLY_ON, **overrides):
    return LevelPolicy(policy, overrides)


class TestAllows:
    def test_all_on_allows_writes_and_reads(self):
        assert allows(POLICY_ALL_ON, True) and allows(POLICY_ALL_ON, False)

    def test_read_only_on_allows_only_reads(self):
        assert allows(POLICY_READ_ONLY_ON, False)
        assert not allows(POLICY_READ_ONLY_ON, True)


class TestRowsOfTheResolutionTable:
    @pytest.mark.parametrize("user_on", [True, False])
    @pytest.mark.parametrize("key_disabled", [None, NONE, frozenset({"t"})])
    def test_row_1_the_administrator_turned_it_off_so_nothing_below_can_matter(self, user_on, key_disabled):
        state = resolve_state("t", False, None, ceiling(t=False), default(t=user_on), key_disabled)

        assert state == ToolState.LOCKED_BY_ADMIN

    @pytest.mark.parametrize("key_disabled", [NONE, frozenset({"t"})])
    def test_row_2_off_in_the_default_is_off_at_the_default_and_disabled_on_the_key(self, key_disabled):
        at_default = resolve_state("t", False, None, ceiling(), default(t=False), None)
        at_key = resolve_state("t", False, None, ceiling(), default(t=False), key_disabled)

        assert at_default == ToolState.OFF
        assert at_key == ToolState.OFF_IN_DEFAULT

    def test_row_3_on_in_the_default_and_off_on_the_key_is_off(self):
        state = resolve_state("t", False, None, ceiling(), default(t=True), frozenset({"t"}))

        assert state == ToolState.OFF

    def test_row_4_on_in_the_default_and_inherited_by_the_key_is_on(self):
        at_default = resolve_state("t", False, None, ceiling(), default(t=True), None)
        at_key = resolve_state("t", False, None, ceiling(), default(t=True), NONE)

        assert at_default == at_key == ToolState.ON

    def test_row_5_a_new_writing_tool_is_off_for_the_user_and_the_key_and_unreviewed(self):
        at_default = resolve_state("t", True, None, ceiling(), default(), None)
        at_key = resolve_state("t", True, None, ceiling(), default(), NONE)

        assert at_default == ToolState.OFF
        assert at_key == ToolState.OFF_IN_DEFAULT
        assert not is_reviewed(default(), "t")

    def test_row_6_a_new_reading_tool_is_on_for_the_user_and_the_key_and_unreviewed(self):
        at_default = resolve_state("t", False, None, ceiling(), default(), None)
        at_key = resolve_state("t", False, None, ceiling(), default(), NONE)

        assert at_default == at_key == ToolState.ON
        assert not is_reviewed(default(), "t")

    @pytest.mark.parametrize("user_on", [None, True, False])
    @pytest.mark.parametrize("key_disabled", [None, NONE, frozenset({"t"})])
    def test_row_7_a_read_only_ceiling_locks_a_writing_tool_whatever_the_levels_below_say(self, user_on, key_disabled):
        overrides = {} if user_on is None else {"t": user_on}

        state = resolve_state("t", True, None, ceiling(POLICY_READ_ONLY_ON), default(**overrides), key_disabled)

        assert state == ToolState.LOCKED_BY_ADMIN

    def test_row_7_a_read_only_ceiling_leaves_a_reading_tool_alone(self):
        state = resolve_state("t", False, None, ceiling(POLICY_READ_ONLY_ON), default(), None)

        assert state == ToolState.ON

    def test_row_7_an_explicit_admin_row_beats_the_ceiling_policy(self):
        state = resolve_state("t", True, None, ceiling(POLICY_READ_ONLY_ON, t=True), default(t=True), NONE)

        assert state == ToolState.ON

    @pytest.mark.parametrize("key_disabled", [None, NONE, frozenset({"t"})])
    @pytest.mark.parametrize("ceiling_on", [True, False])
    def test_row_8_a_tool_that_cannot_run_over_mcp_is_unavailable_at_every_level(self, key_disabled, ceiling_on):
        state = resolve_state("t", False, "needs a form", ceiling(t=ceiling_on), default(t=True), key_disabled)

        assert state == ToolState.UNAVAILABLE


class TestAToolThatSpends:
    def test_allows_treats_spending_like_writing(self):
        assert not allows(POLICY_READ_ONLY_ON, False, True)
        assert allows(POLICY_ALL_ON, False, True)
        assert allows(POLICY_READ_ONLY_ON, False, False)

    def test_it_is_off_by_default_for_the_user_and_off_in_default_on_the_key(self):
        at_default = resolve_state("t", False, None, ceiling(), default(), None, spends=True)
        at_key = resolve_state("t", False, None, ceiling(), default(), NONE, spends=True)

        assert at_default == ToolState.OFF
        assert at_key == ToolState.OFF_IN_DEFAULT

    def test_an_explicit_user_choice_or_the_all_on_policy_turns_it_on(self):
        explicit = resolve_state("t", False, None, ceiling(), default(t=True), NONE, spends=True)
        all_on = resolve_state("t", False, None, ceiling(), default(POLICY_ALL_ON), NONE, spends=True)

        assert explicit == all_on == ToolState.ON

    def test_a_read_only_ceiling_locks_it_unless_the_administrator_says_otherwise(self):
        locked = resolve_state("t", False, None, ceiling(POLICY_READ_ONLY_ON), default(t=True), NONE, spends=True)
        allowed = resolve_state("t", False, None, ceiling(POLICY_READ_ONLY_ON, t=True), default(t=True), NONE, spends=True)

        assert locked == ToolState.LOCKED_BY_ADMIN
        assert allowed == ToolState.ON

    def test_at_the_ceiling_it_follows_the_policy(self):
        assert ceiling_state("t", False, None, ceiling(POLICY_READ_ONLY_ON), spends=True) == ToolState.OFF
        assert ceiling_state("t", False, None, ceiling(), spends=True) == ToolState.ON

    def test_a_key_still_cannot_turn_it_on(self):
        state = resolve_state("t", False, None, ceiling(), default(), NONE, spends=True)

        assert state != ToolState.ON


class TestAKeyCanOnlyNarrow:
    def test_a_key_row_never_turns_on_what_the_user_turned_off(self):
        state = resolve_state("t", False, None, ceiling(), default(t=False), NONE)

        assert state != ToolState.ON

    def test_a_key_with_no_row_never_has_more_than_the_default(self):
        for writes in (True, False):
            at_default = resolve_state("t", writes, None, ceiling(), default(), None)
            at_key = resolve_state("t", writes, None, ceiling(), default(), NONE)

            assert (at_key == ToolState.ON) <= (at_default == ToolState.ON)

    def test_a_disabled_name_on_the_key_only_ever_removes_a_tool(self):
        for user_on in (True, False):
            without = resolve_state("t", False, None, ceiling(), default(t=user_on), NONE)
            with_row = resolve_state("t", False, None, ceiling(), default(t=user_on), frozenset({"t"}))

            assert (with_row == ToolState.ON) <= (without == ToolState.ON)


class TestCeilingLevel:
    def test_a_writing_tool_is_on_under_all_on_and_off_under_read_only_on(self):
        assert ceiling_state("t", True, None, ceiling()) == ToolState.ON
        assert ceiling_state("t", True, None, ceiling(POLICY_READ_ONLY_ON)) == ToolState.OFF

    def test_an_explicit_row_beats_the_policy(self):
        assert ceiling_state("t", True, None, ceiling(POLICY_READ_ONLY_ON, t=True)) == ToolState.ON
        assert ceiling_state("t", False, None, ceiling(t=False)) == ToolState.OFF

    def test_an_unavailable_tool_stays_unavailable(self):
        assert ceiling_state("t", False, "why", ceiling()) == ToolState.UNAVAILABLE


class TestReviewed:
    def test_a_tool_is_reviewed_once_the_user_has_a_row_for_it(self):
        assert is_reviewed(default(t=False), "t")
        assert not is_reviewed(default(u=False), "t")


class TestRefusalMessages:
    def test_each_state_is_worded_for_the_client(self):
        assert refusal_message("t", ToolState.OFF, None) == "Tool 't' is turned off for this key"
        assert refusal_message("t", ToolState.OFF_IN_DEFAULT, None) == "Tool 't' is turned off for this key"
        assert refusal_message("t", ToolState.LOCKED_BY_ADMIN, None) == "Tool 't' is turned off by your administrator"
        assert refusal_message("t", ToolState.UNAVAILABLE, "no form") == "Tool 't' can't run over MCP: no form"
