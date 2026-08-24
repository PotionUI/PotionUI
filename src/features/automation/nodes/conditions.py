"""
Built-in condition node types: compare, path_match, jinja_expression, switch.

Note the input convention, which differs from actions: a condition's `field` is
resolved with `get_path(template_context, field)` - a BARE dot-path such as
`event.path` or `upstream.<node_id>.model_id`, with **no** `{{ }}` braces.
Actions, by contrast, run their string config through `render_template` (Jinja).
That is why condition fields carry `input_ref: "path"` rather than
`templatable: True`: the frontend's variable picker must insert `event.path`
here but `{{ event.path }}` into an action field.
"""

from typing import Any

from src.features.automation.context import NodeExecutionContext
from src.features.automation.expr import OPERATORS, apply_operator, eval_expression, get_path
from src.platform.plugins.automation_nodes import (
    NodeField,
    NodeResult,
    NodeTypeSpec,
    node_type_registry,
    parse_dynamic_port_labels,
)

_OPERATOR_OPTIONS = [{"label": name.replace("_", " ").title(), "value": name} for name in OPERATORS]


async def _execute_compare(ctx: NodeExecutionContext) -> NodeResult:
    field = ctx.config.get("field", "")
    operator = ctx.config.get("operator", "equals")
    expected = ctx.config.get("value")

    actual = get_path(ctx.template_context(), field)
    passed = apply_operator(operator, actual, expected)

    return NodeResult(output={"field": field, "actual": actual, "operator": operator, "expected": expected, "passed": passed},
                       branch="true" if passed else "false")


def _match_has_dir(actual: Any, value: str) -> bool:
    """
    True when any of `value`'s comma-separated directory names equals a whole
    path SEGMENT of `actual` - "krea2" matches ".../loras/krea2/x.safetensors"
    but not ".../loras/krea25/x.safetensors". `actual` is used as-is when it's
    already a list (e.g. a fan-out item's `rel_parts`), otherwise split on "/".
    """
    names = {name.strip() for name in str(value or "").split(",") if name.strip()}
    if not names:
        return False
    if isinstance(actual, (list, tuple)):
        segments = [str(part) for part in actual]
    else:
        segments = str(actual or "").replace("\\", "/").split("/")
    return any(segment in names for segment in segments)


async def _execute_path_match(ctx: NodeExecutionContext) -> NodeResult:
    """Friendly string-ops wrapper over `condition.compare` for path-shaped values (e.g. `event.path`)."""
    field = ctx.config.get("field", "event.path")
    match_type = ctx.config.get("match_type", "contains")
    value = ctx.config.get("value", "")

    actual = get_path(ctx.template_context(), field)

    if match_type == "has_dir":
        passed = _match_has_dir(actual, value)
    else:
        operator_map = {
            "contains": "contains",
            "starts_with": "starts_with",
            "ends_with": "ends_with",
            "equals": "equals",
            "regex": "regex",
        }
        operator = operator_map.get(match_type, "contains")
        passed = apply_operator(operator, actual, value)

    return NodeResult(output={"field": field, "actual": actual, "match_type": match_type, "value": value, "passed": passed},
                       branch="true" if passed else "false")


async def _execute_jinja_expression(ctx: NodeExecutionContext) -> NodeResult:
    """Evaluates a sandboxed Jinja2 boolean expression against {event, upstream}. No eval() anywhere."""
    expression = ctx.config.get("expression", "false")
    result = eval_expression(expression, ctx.template_context())
    passed = bool(result)

    return NodeResult(output={"expression": expression, "result": result, "passed": passed},
                       branch="true" if passed else "false")


async def _execute_switch(ctx: NodeExecutionContext) -> NodeResult:
    """
    Multi-way branch (n8n-style Switch): resolves `field` against the same
    {event, upstream} payload `condition.compare` uses, then routes to
    whichever configured `cases` label matches (str-cast, trimmed), or the
    implicit "default" port if none match. The "cases" key here MUST match
    this node type's own `dynamic_ports_config_key` below - both the engine's
    edge-routing (via this branch value) and `AutomationManager.validate_graph`
    (via `resolve_dynamic_ports`) key off the identical comma-separated parse.
    """
    field = ctx.config.get("field", "")
    cases = parse_dynamic_port_labels(ctx.config.get("cases"))

    actual = get_path(ctx.template_context(), field)
    value = "" if actual is None else str(actual).strip()

    branch = value if value in cases else "default"

    return NodeResult(output={"field": field, "value": value, "matched": branch != "default"}, branch=branch)


def register(registry=node_type_registry) -> None:
    registry.register(NodeTypeSpec(
        key="condition.compare",
        kind="condition",
        title="Compare",
        description="Compares a dot-path field against a value using a fixed operator table.",
        icon="git-branch",
        category="logic",
        config_schema=[
            {"name": "field", "type": "string", "title": "Field", "default": "event.path",
             "input_ref": "path"},
            {"name": "operator", "type": "select", "title": "Operator", "default": "equals",
             "options": _OPERATOR_OPTIONS},
            {"name": "value", "type": "string", "title": "Value"},
        ],
        outputs=(
            NodeField("field", "string", "Field", "The dot-path that was resolved.", "event.path"),
            NodeField("actual", "any", "Actual", "Value found at that path."),
            NodeField("operator", "string", "Operator", "The operator applied.", "equals"),
            NodeField("expected", "any", "Expected", "The configured comparison value."),
            NodeField("passed", "boolean", "Passed", "Whether the comparison held.", True),
        ),
        execute=_execute_compare,
    ))

    registry.register(NodeTypeSpec(
        key="condition.path_match",
        kind="condition",
        title="Path Match",
        description="Friendly string-match wrapper for file paths (contains/starts with/ends with/regex/has directory).",
        icon="filter",
        category="logic",
        config_schema=[
            {"name": "field", "type": "string", "title": "Field", "default": "event.path",
             "input_ref": "path"},
            {"name": "match_type", "type": "select", "title": "Match Type", "default": "contains",
             "options": [
                 {"label": "Contains", "value": "contains"},
                 {"label": "Starts With", "value": "starts_with"},
                 {"label": "Ends With", "value": "ends_with"},
                 {"label": "Equals", "value": "equals"},
                 {"label": "Regex", "value": "regex"},
                 {"label": "Has Directory", "value": "has_dir"},
             ]},
            {"name": "value", "type": "string", "title": "Value", "default": "",
             "description": "For 'Has Directory': comma-separated directory names to match against a "
                            "whole path segment, e.g. \"loras, krea2\"."},
        ],
        outputs=(
            NodeField("field", "string", "Field", "The dot-path that was resolved.", "event.path"),
            NodeField("actual", "any", "Actual", "Value found at that path."),
            NodeField("match_type", "string", "Match Type", "The match strategy applied.", "contains"),
            NodeField("value", "string", "Value", "The configured value matched against.", "loras"),
            NodeField("passed", "boolean", "Passed", "Whether the match held.", True),
        ),
        execute=_execute_path_match,
    ))

    registry.register(NodeTypeSpec(
        key="condition.jinja_expression",
        kind="condition",
        title="Expression",
        description="Evaluates a sandboxed Jinja2 boolean expression against {event, upstream}.",
        icon="code",
        category="logic",
        config_schema=[
            {"name": "expression", "type": "string", "title": "Expression", "default": "",
             "input_ref": "expression"},
        ],
        outputs=(
            NodeField("expression", "string", "Expression", "The expression evaluated.", "event.size > 0"),
            NodeField("result", "any", "Result", "Raw value the expression produced."),
            NodeField("passed", "boolean", "Passed", "Truthiness of the result.", True),
        ),
        execute=_execute_jinja_expression,
    ))

    registry.register(NodeTypeSpec(
        key="condition.switch",
        kind="condition",
        title="Switch",
        description="Multi-way branch: routes to whichever case matches a dot-path field's value, or 'default'.",
        icon="split",
        category="logic",
        config_schema=[
            {"name": "field", "type": "string", "title": "Field", "default": "event.path",
             "input_ref": "path"},
            {"name": "cases", "type": "textbox", "title": "Cases (comma-separated)",
             "default": "", "description": "e.g. \"loras, checkpoints, vae\" - adds a matching output port per case, plus an implicit \"default\" port."},
        ],
        outputs=(
            NodeField("field", "string", "Field", "The dot-path that was resolved.", "event.rel_parts.0"),
            NodeField("value", "string", "Value", "Stringified value used to pick the branch.", "loras"),
            NodeField("matched", "boolean", "Matched",
                      "False when no case matched and the 'default' port was taken.", True),
        ),
        # Static fallback only (before any cases are configured / for a
        # bare catalog listing) - real ports come from `dynamic_ports_config_key`
        # below, resolved per node instance against its own "cases" config.
        output_ports=("default",),
        dynamic_ports_config_key="cases",
        execute=_execute_switch,
    ))
