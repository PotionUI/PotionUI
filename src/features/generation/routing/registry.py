"""Assembling a `GenerationRouter`'s rule chain: the built-ins in a fixed
order, then every plugin-contributed rule spliced in at its requested
position. The resulting ORDER is the routing decision, so this is the one
place that order is assembled - see docs/generation-routing.md "Adding a
rule from a plugin".
"""

import logging
from typing import List, Optional

from src.features.generation.routing.contracts import RoutingRule
from src.features.generation.routing.rules import (
    EnabledForEngine,
    ModelAvailability,
    Preference,
    RequestPin,
    RequirementsEligibility,
)

logger = logging.getLogger(__name__)

# Fixed order - see this module's and each rule's docstring for why.
BUILTIN_RULES: List[RoutingRule] = [
    EnabledForEngine(),
    RequestPin(),
    ModelAvailability(),
    RequirementsEligibility(),
    Preference(),
]


def _insert_at_position(rules: List[RoutingRule], rule: RoutingRule, position: Optional[str]) -> None:
    """`position` is `None` (append), `"before:<name>"`, or `"after:<name>"` -
    an anchor that isn't found (a typo, or a rule from a since-disabled
    plugin) falls back to appending, logged, rather than silently dropping
    the plugin's rule."""
    if not position:
        rules.append(rule)
        return

    kind, _, anchor_name = position.partition(":")
    if kind not in ("before", "after") or not anchor_name:
        logger.warning(f"[ROUTER] routing rule '{rule.name}': malformed position '{position}', appending instead")
        rules.append(rule)
        return

    for i, existing in enumerate(rules):
        if existing.name == anchor_name:
            rules.insert(i if kind == "before" else i + 1, rule)
            return

    logger.warning(f"[ROUTER] routing rule '{rule.name}': anchor '{anchor_name}' not found, appending instead")
    rules.append(rule)


def build_routing_rules(plugin_registry=None) -> List[RoutingRule]:
    """The built-in rules, then every plugin-registered rule (via the
    `backend.register_routing_rules` hook, see
    `src.features.backends.hooks.BACKEND_HOOKS`) spliced in at its requested
    position. `plugin_registry=None` (e.g. standalone tooling, most tests)
    returns just the built-ins."""
    rules = list(BUILTIN_RULES)
    if plugin_registry is None:
        return rules

    from src.features.backends.hooks import BACKEND_HOOKS

    context, _ = plugin_registry.execute_hook(
        BACKEND_HOOKS.register_routing_rules, initial_data={"rules": []}
    )
    for entry in (context.data.get("rules") or []):
        rule = entry.get("rule")
        if rule is None:
            continue
        _insert_at_position(rules, rule, entry.get("position"))

    return rules
