import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.condition_text import Budget, analyze, describe, repair, variable_kind
from src.features.llm.tools.builtin.prompt_variables_tool import TAB_ID_PARAMETER, tab_variables
from src.features.prompt import conditions

_NAME = re.compile(rf"[A-Za-z_][A-Za-z0-9_]{{0,{conditions.MAX_NAME_CHARS - 1}}}")
_NUMBER_TEXT = re.compile(conditions.NUMBER_PATTERN)
_RANGE_TEXT = re.compile(rf"\s*({conditions.NUMBER_PATTERN})\s*\.\.\s*({conditions.NUMBER_PATTERN})\s*")
_MAX_REPORTED = 6
_BODY_TAG = re.compile(r"<<[ \t\r\n]*(?:if|else|end)\b|&lt;&lt;[ \t]*(?:if|else|end)\b")
_SCALAR = {"oneOf": [{"type": "string"}, {"type": "number"}]}


def _number(raw: Any) -> Optional[Decimal]:
    if isinstance(raw, bool):
        return None
    text = str(raw).strip() if isinstance(raw, (int, float, str)) else ""
    if len(text) > 4 * conditions.MAX_NUMBER_CHARS:
        return None
    try:
        value = Decimal(text) if text else None
    except InvalidOperation:
        return None
    if value is None or not value.is_finite():
        return None
    limit = conditions.MAX_NUMBER_CHARS
    if abs(value.adjusted()) > limit or len(value.as_tuple().digits) > limit:
        return None
    formatted = conditions.format_number(value)
    if not _NUMBER_TEXT.fullmatch(formatted) or len(formatted) > conditions.MAX_NUMBER_CHARS:
        return None
    return Decimal(formatted)


def _kind(variables: Optional[Mapping[str, Any]], name: str) -> Optional[str]:
    definition = variables.get(name) if variables else None
    return variable_kind(definition) if isinstance(definition, dict) else None


def _strings(raw: Any) -> List[str]:
    items = raw if isinstance(raw, list) else [raw]
    return [
        str(item).strip()
        for item in items[: conditions.MAX_VALUES + 1]
        if isinstance(item, (str, int, float)) and not isinstance(item, bool)
    ]


def _predicate(raw: Any, variables: Optional[Mapping[str, Any]]) -> Tuple[Optional[conditions.Predicate], str]:
    if not isinstance(raw, dict):
        return None, "each test is an object like {\"var\": \"age\", \"op\": \">\", \"value\": 65}"
    name = str(raw.get("var") or "").strip().lstrip("$").strip("{}").strip()
    if not _NAME.fullmatch(name):
        return None, f"var '{name}' is not a variable name (letters, digits and _, no $)"
    op = " ".join(str(raw.get("op") or "").lower().replace("_", " ").split())
    op = {"==": "=", "<>": "!=", "is": "="}.get(op, op)
    if op not in conditions.OPERATORS:
        return None, f"${name}: op '{raw.get('op')}' is not one of {', '.join(conditions.OPERATORS)}"
    kind = _kind(variables, name)
    value = raw.get("value")
    if op in ("is empty", "is not empty"):
        return conditions.Predicate(name, op, "none"), ""
    if op in ("<", "<=", ">", ">=") or (op in ("=", "!=") and (kind == "number" or (
        kind is None and isinstance(value, (int, float)) and not isinstance(value, bool)
    ))):
        number = _number(value)
        if number is None:
            return None, f"`{op}` on ${name} needs a number in value (at most {conditions.MAX_NUMBER_CHARS} characters)"
        return conditions.Predicate(name, op, "number", number=number), ""
    if op in ("=", "!=", "contains", "not contains"):
        texts = _strings(value)
        if len(texts) != 1 or not texts[0]:
            return None, f"`{op}` on ${name} needs one non-empty text value; use is empty to test for nothing"
        if len(texts[0]) > conditions.MAX_STRING_CHARS:
            return None, f"`{op}` on ${name}: a value holds at most {conditions.MAX_STRING_CHARS} characters"
        return conditions.Predicate(name, op, "string", strings=(texts[0],)), ""
    items = raw.get("values") if raw.get("values") is not None else value
    span = _RANGE_TEXT.fullmatch(items) if isinstance(items, str) and len(items) <= 100 else None
    if span:
        items = list(span.groups())
    numeric = isinstance(items, list) and len(items) == 2 and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in items
    )
    if kind == "number" or (kind is None and (numeric or span)):
        bounds = [_number(item) for item in items] if isinstance(items, list) and len(items) == 2 else []
        if len(bounds) != 2 or None in bounds:
            return None, f"`{op}` on the number ${name} needs values [from, to], like [18, 30]"
        if bounds[0] > bounds[1]:
            return None, f"`{op}` on ${name}: put the smaller number first, like [18, 30]"
        return conditions.Predicate(name, op, "range", low=bounds[0], high=bounds[1]), ""
    texts = _strings(items)
    if not texts or not all(texts):
        return None, f"`{op}` on ${name} needs values: a list of options, like [\"noir\", \"sunlit\"]"
    if len(texts) > conditions.MAX_VALUES:
        return None, f"`{op}` on ${name} takes at most {conditions.MAX_VALUES} values"
    if any(len(text) > conditions.MAX_STRING_CHARS for text in texts):
        return None, f"`{op}` on ${name}: a value holds at most {conditions.MAX_STRING_CHARS} characters"
    return conditions.Predicate(name, op, "strings", strings=tuple(texts)), ""


def _body(raw: Any, label: str, problems: List[str]) -> str:
    if not isinstance(raw, str):
        problems.append(f"{label} needs text: the prompt words to use")
        return ""
    if len(raw) > conditions.MAX_SCAN_CHARS:
        problems.append(f"{label} text is longer than {conditions.MAX_SCAN_CHARS:,} characters")
    elif _BODY_TAG.search(raw):
        problems.append(f"{label} text contains condition tags; conditions can't nest, add another branch instead")
    return raw


def build_condition(
    branches: Any, otherwise: Any, variables: Optional[Mapping[str, Any]]
) -> Tuple[Optional[str], List[str]]:
    problems: List[str] = []
    if not isinstance(branches, list) or not branches:
        return None, ["branches needs at least one branch: {\"tests\": [...], \"text\": \"...\"}"]
    total = len(branches) + (1 if otherwise is not None else 0)
    if total > conditions.MAX_BRANCHES:
        return None, [f"a condition has at most {conditions.MAX_BRANCHES} branches, otherwise included; this one has {total}"]
    built: List[conditions.Branch] = []
    body_chars = 0
    for position, raw in enumerate(branches, start=1):
        label = f"branch {position}"
        if not isinstance(raw, dict):
            problems.append(f"{label} must be an object with tests and text")
            continue
        joiner = raw.get("join") or "and"
        if joiner not in ("and", "or"):
            problems.append(f"{label}: join is and or or")
        tests = raw.get("tests")
        if not isinstance(tests, list) or not tests:
            problems.append(f"{label} needs at least one test")
            tests = []
        if len(tests) > conditions.MAX_TESTS:
            problems.append(f"{label} has {len(tests)} tests; the limit is {conditions.MAX_TESTS}")
            tests = tests[: conditions.MAX_TESTS]
        predicates: List[conditions.Predicate] = []
        for test in tests:
            predicate, error = _predicate(test, variables)
            if error:
                problems.append(f"{label}: {error}")
            else:
                predicates.append(predicate)
        body = _body(raw.get("text"), label, problems)
        body_chars += len(body)
        cond = conditions.Cond(joiner if joiner in ("and", "or") else "and", tuple(predicates))
        estimate = sum(len(p.var) + 32 + sum(len(value) + 4 for value in p.strings) for p in predicates)
        if estimate > 2 * conditions.MAX_HEAD_CHARS or len(conditions.format_condition(cond)) > conditions.MAX_HEAD_CHARS:
            problems.append(f"{label}: its tests are longer than {conditions.MAX_HEAD_CHARS:,} characters")
        built.append(conditions.Branch("if" if position == 1 else "elseif", cond, body))
    if otherwise is not None:
        body = _body(otherwise, "otherwise", problems)
        body_chars += len(body)
        built.append(conditions.Branch("else", None, body))
    if body_chars > conditions.MAX_SCAN_CHARS:
        problems.append(f"the branch texts add up to more than {conditions.MAX_SCAN_CHARS:,} characters")
    if problems:
        return None, problems
    text = conditions.format_block(built)
    if len(text) > conditions.MAX_SCAN_CHARS:
        return None, [
            f"the built condition is {len(text):,} characters; a segment's conditions are read only in its first "
            f"{conditions.MAX_SCAN_CHARS:,}, so shorten the branch texts"
        ]
    found = analyze(text, variables).problems
    return (None, found) if found else (text, [])


class WriteConditionTool(BaseTool):
    modes = ["generation"]
    icon = "git-branch"

    name = "write_condition"
    read_only = True
    mcp = True
    group = "Form & segments"
    user_description = "Builds or checks a prompt condition against your prompt variables."

    @property
    def description(self) -> str:
        return (
            "Build a prompt condition (<<if>>...<<end>>) from branches, or check condition text you wrote, "
            "against the tab's variables. Returns the exact text to paste into a segment, or what is wrong "
            "and how to fix it. Changes nothing."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tab_id": TAB_ID_PARAMETER,
                "branches": {
                    "type": "array",
                    "description": "Build: branches in order; the first true one wins.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tests": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "var": {"type": "string", "description": "Variable name without $"},
                                        "op": {"type": "string", "enum": list(conditions.OPERATORS)},
                                        "value": {**_SCALAR, "description": "A number, or option text exactly as written"},
                                        "values": {
                                            "type": "array",
                                            "items": _SCALAR,
                                            "description": "in / not in: options, or [from, to] for a number",
                                        },
                                    },
                                    "required": ["var", "op"],
                                },
                            },
                            "join": {"type": "string", "enum": ["and", "or"]},
                            "text": {"type": "string", "description": "Prompt words for this branch"},
                        },
                        "required": ["tests", "text"],
                    },
                },
                "otherwise": {"type": "string", "description": "Words when no branch matches; omit for none"},
                "text": {"type": "string", "description": "Check: condition or segment text you wrote"},
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        variables, note = tab_variables(context, kwargs)
        checked: Dict[str, Any] = {"variables_checked": variables is not None}
        if note:
            checked["variables_note"] = note
        if kwargs.get("branches") is None and isinstance(kwargs.get("text"), str):
            return self._check(kwargs["text"], variables, checked)
        text, problems = build_condition(kwargs.get("branches"), kwargs.get("otherwise"), variables)
        if problems:
            return ToolResult(
                success=False,
                data="",
                error="Nothing was built: " + "; ".join(p.rstrip(".") for p in problems[:_MAX_REPORTED])
                + ". Fix these and call write_condition again.",
            )
        return ToolResult(success=True, data=json.dumps({
            "text": text,
            **checked,
            "next": "Paste text verbatim where the condition belongs in the segment's proposed text.",
        }))

    @staticmethod
    def _check(text: str, variables: Optional[Mapping[str, Any]], checked: Dict[str, Any]) -> ToolResult:
        budget = Budget()
        budget.take(text)
        analysis = analyze(text, variables)
        problems = analysis.problems
        if "<<" not in text and not problems:
            return ToolResult(success=True, data=json.dumps({
                "valid": False, **checked, "problems": ["the text has no condition; write <<if TEST>>text<<end>>"],
            }))
        payload: Dict[str, Any] = {"valid": not problems, **checked}
        shown = analysis
        if problems:
            payload["problems"] = problems[:_MAX_REPORTED]
            repaired = repair(analysis, budget, variables)
            if repaired is not None:
                shown = repaired
                payload["corrected_text"] = repaired.text
                if repaired.issues:
                    payload["corrected_text_problems"] = repaired.issues[:_MAX_REPORTED]
        else:
            payload["text"] = text
        payload["branches"] = describe(shown.parsed)
        return ToolResult(success=True, data=json.dumps(payload))
