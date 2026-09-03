"""`registry.build_routing_rules` - built-ins in a fixed order, then a
plugin-registered rule spliced in by position via the
`backend.register_routing_rules` hook.
"""

from unittest.mock import Mock

from src.features.generation.routing.registry import BUILTIN_RULES, build_routing_rules
from src.plugin_api.backends import register_routing_rule


class _FakeRule:
    def __init__(self, name):
        self.name = name

    async def apply(self, candidates, request, ctx):
        return candidates


class TestBuildRoutingRulesWithoutPlugins:
    def test_no_plugin_registry_returns_just_the_builtins(self):
        rules = build_routing_rules(plugin_registry=None)

        assert [r.name for r in rules] == [r.name for r in BUILTIN_RULES]

    def test_no_plugin_contributions_returns_just_the_builtins(self):
        plugin_registry = Mock()
        plugin_registry.execute_hook.return_value = (Mock(data={"rules": []}), True)

        rules = build_routing_rules(plugin_registry=plugin_registry)

        assert [r.name for r in rules] == [r.name for r in BUILTIN_RULES]


class TestBuildRoutingRulesWithPlugins:
    def _rules_with(self, entries):
        plugin_registry = Mock()
        plugin_registry.execute_hook.return_value = (Mock(data={"rules": entries}), True)
        return build_routing_rules(plugin_registry=plugin_registry)

    def test_no_position_appends_at_the_end(self):
        rule = _FakeRule("extra")

        rules = self._rules_with([{"rule": rule, "position": None}])

        assert [r.name for r in rules][-1] == "extra"

    def test_before_anchor_inserts_immediately_before_the_named_rule(self):
        rule = _FakeRule("extra")

        rules = self._rules_with([{"rule": rule, "position": "before:preference"}])

        names = [r.name for r in rules]
        assert names.index("extra") == names.index("preference") - 1

    def test_after_anchor_inserts_immediately_after_the_named_rule(self):
        rule = _FakeRule("extra")

        rules = self._rules_with([{"rule": rule, "position": "after:model_availability"}])

        names = [r.name for r in rules]
        assert names.index("extra") == names.index("model_availability") + 1

    def test_unresolvable_anchor_falls_back_to_appending(self):
        rule = _FakeRule("extra")

        rules = self._rules_with([{"rule": rule, "position": "after:does-not-exist"}])

        assert [r.name for r in rules][-1] == "extra"

    def test_multiple_plugin_rules_apply_in_order(self):
        first = _FakeRule("first-extra")
        second = _FakeRule("second-extra")

        rules = self._rules_with([
            {"rule": first, "position": "after:enabled_for_engine"},
            {"rule": second, "position": "after:enabled_for_engine"},
        ])

        names = [r.name for r in rules]
        # Each insert is relative to the CURRENT list - the second entry's
        # anchor is still found (built-in rule names are never displaced),
        # so it lands right after `enabled_for_engine` too, ahead of the first.
        assert names[:3] == ["enabled_for_engine", "second-extra", "first-extra"]


class TestRegisterRoutingRuleHelper:
    def test_appends_a_rule_position_entry_to_the_hook_context(self):
        rule = _FakeRule("extra")
        context = Mock(data={})

        register_routing_rule(context, rule, position="after:preference")

        assert context.data["rules"] == [{"rule": rule, "position": "after:preference"}]

    def test_omitted_position_defaults_to_none(self):
        rule = _FakeRule("extra")
        context = Mock(data={})

        register_routing_rule(context, rule)

        assert context.data["rules"][0]["position"] is None
