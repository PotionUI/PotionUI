"""Rescue near-miss tool invocations the model wrote as ordinary text.

A model sometimes "calls" a tool with the wrong syntax — a ``<tool_action
type="NAME" ...>`` tag (confusing the frontend's segment-edit convention for a
tool call), a ```` ```tool_code ``` ```` fence, or a bare ``{"name": ...,
"arguments": ...}`` object — instead of the ``<tool_call>`` block the executor
parses. Nothing dispatches and the raw markup reaches the user.

This module detects those near-misses so the executor can repair the
unambiguous ones into real calls and steer the model on the ambiguous ones. It
is deliberately client-agnostic: it scans a finished assistant string, the one
place every client's content-embedded output converges.

The discriminator that keeps it from firing on legitimate text is the registered
tool set: a ``<tool_action type="update_director_segment">`` (a real frontend
convention, not a registered tool) is never touched, while
``type="update_music_director"`` or ``type="update_segment"`` (registered
tools) are. ``update_segment``'s tag carries its payload as inner TEXT between
the open and close tags, unlike every other ``<tool_action>`` tag, whose
attributes hold the whole payload — that shape gets a small special case (see
``_update_segment_arguments``) to fold the tag into the tool's ``updates``
array instead of the generic attribute dump.

Inside a tag that clears that bar, the payload itself is decoded forgivingly,
because a local model mangles the transport in ways that say nothing about what
it meant: the value may be unquoted, its quote characters may arrive wrapped in
special-token delimiters (``<|"|>``), its object keys may be bare identifiers,
and the whole thing may be a Python repr rather than JSON. What survives all
that and still doesn't parse becomes a ``problem`` quoted back to the model,
never a silent drop.
"""

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# `<tool_action ...>` — closing `>` optional so a truncated tag is still caught.
# `<|"|>` is admitted inside the attribute region because a local tokenizer
# emits quotes that way, and its `>` would otherwise end the region early.
_TOOL_ACTION_RE = re.compile(r"<tool_action\b((?:<\|[^|>]*\|>|[^>])*)>?", re.DOTALL | re.IGNORECASE)
_ATTR_NAME_RE = re.compile(r"(\w[\w-]*)\s*=\s*")
_BARE_VALUE_RE = re.compile(r"\S*")
# A quote character the model wrapped in special-token delimiters.
_MANGLED_QUOTE_RE = re.compile(r"<\|(['\"])\|>")
# A complete string literal, either quoting style, escapes honoured.
_STRING_LITERAL_RE = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""", re.DOTALL)
# `{op: ...}` — an identifier used as an object key without quotes.
_BARE_KEY_RE = re.compile(r"(?<![\w'\"])([A-Za-z_][A-Za-z0-9_]*)(\s*:)")
_BRACKETS = {"[": "]", "{": "}"}

# A model can emit brackets nested deeply enough that the stdlib decoders
# recurse past the interpreter's limit. That is a malformed payload like any
# other -- it must not escape and take the whole chat turn down with it.
_DECODE_ERRORS = (json.JSONDecodeError, ValueError, RecursionError)
# Only tool-labelled fences count — a plain ```json / ```python block the user
# asked for is left alone.
_FENCE_RE = re.compile(r"```(?:tool_code|tool_call|tool)\b[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# `{'op': 'set_prompt': '...'}` — two colons in one entry, the shape neither
# JSON nor Python can parse and the one a small model writes most often.
_DOUBLE_COLON_RE = re.compile(r""":\s*(['"])(?:(?!\1).)*\1\s*:""", re.DOTALL)

_JSON_TYPES = {
    "array": list,
    "object": dict,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
}


# An opened `<tool_call>` with no closing tag anywhere after it -- the
# generation stopped mid-payload (hit the completion cap) rather than being
# miswritten. Case-insensitive to match `_TOOL_CALL_XML_RE`'s own tolerance.
_TOOL_CALL_OPEN_RE = re.compile(r"<tool_call>", re.IGNORECASE)
_TOOL_CALL_CLOSE_RE = re.compile(r"</tool_call>", re.IGNORECASE)
_TOOL_CALL_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]*)"')


@dataclass
class TruncatedCall:
    """An opened `<tool_call>` block cut off before its closing tag."""

    tool_name: Optional[str]  # None when the cut came before the "name" field itself
    span: Tuple[int, int]


def find_truncated_tool_call(content: str) -> Optional[TruncatedCall]:
    """The span of an opened `<tool_call>` that never closes, or ``None``.

    Unlike a near-miss (wrong tag, wrong quoting), this is the CORRECT
    transport cut off mid-write -- the payload is genuinely incomplete, not
    malformed, so there is nothing here to repair or read leniently. The only
    correct response is telling the model to resend the whole call.
    """
    if not content:
        return None
    close_starts = [m.start() for m in _TOOL_CALL_CLOSE_RE.finditer(content)]
    for m in _TOOL_CALL_OPEN_RE.finditer(content):
        if not any(c > m.start() for c in close_starts):
            name_match = _TOOL_CALL_NAME_RE.search(content, m.end())
            return TruncatedCall(name_match.group(1) if name_match else None, (m.start(), len(content)))
    return None


def truncated_retry_nudge(tool_name: Optional[str]) -> str:
    """A corrective retry for a `<tool_call>` that opened but never closed.

    States plainly what happened (cut off, not miswritten) and repeats the
    exact full-call example the system prompt teaches -- never a
    partial-shaped example, which would just teach the same mistake back.
    """
    subject = f"your {tool_name} tool call" if tool_name else "your tool call"
    return (
        f"Your last message started {subject} but was cut off mid-generation before "
        f"the closing </tool_call> tag, so nothing ran. Resend the COMPLETE call in one "
        f"message: EXACTLY one "
        '<tool_call>{"name": "...", "arguments": {...}}</tool_call> block and nothing '
        "else, with every argument included in full -- for example "
        '<tool_call>{"name": "get_form_state", "arguments": {}}</tool_call>.'
    )


def truncated_fallback_message(tool_name: Optional[str]) -> str:
    """An honest inline message when a cut-off call keeps recurring -- never raw markup."""
    subject = f"the {tool_name} tool" if tool_name else "a tool"
    return (
        f"I tried to call {subject} but the response kept getting cut off before it "
        f"finished. Could you try again, maybe with a smaller request?"
    )


@dataclass
class NearMiss:
    """A registered tool named through an invocation format the executor won't parse."""

    tool_name: str
    arguments: Optional[Dict[str, Any]]  # parsed args when JSON-decodable, else None
    original_format: str                 # "tool_action_tag" | "code_fence" | "bare_json"
    span: Tuple[int, int]
    # Set when an argument LOOKS like a structured payload but parses as
    # neither JSON nor a Python literal — quoted back to the model so a retry
    # fixes the actual mistake instead of guessing.
    problem: Optional[str] = None


def _try_parse(text: str) -> Tuple[Any, bool]:
    """JSON first, then a Python literal (``ast.literal_eval`` — never ``eval``,
    so nothing in a model's text is ever executed)."""
    try:
        return json.loads(text), True
    except _DECODE_ERRORS:
        pass
    try:
        return ast.literal_eval(text), True
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None, False


def demangle_quote_tokens(text: str) -> str:
    """``<|"|>`` -> ``"``. Some local tokenizers round-trip a quote character
    wrapped in special-token delimiters; the payload underneath is intact.

    Applied only to what has already been identified as a tool-call payload,
    never to chat prose — a message legitimately discussing that token stays
    verbatim.
    """
    return _MANGLED_QUOTE_RE.sub(r"\1", text)


def _quote_bare_keys(text: str) -> str:
    """``{op: "x"}`` -> ``{"op": "x"}``, rewriting only OUTSIDE string literals
    so a value like ``"cinematic: wide"`` is left alone."""
    parts: List[str] = []
    last = 0
    for m in _STRING_LITERAL_RE.finditer(text):
        parts.append(_BARE_KEY_RE.sub(r'"\1"\2', text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_BARE_KEY_RE.sub(r'"\1"\2', text[last:]))
    return "".join(parts)


def decode_payload(text: str) -> Tuple[Any, Optional[str]]:
    """Decode a value a model wrote as text.

    Strict JSON, then — only for a bracketed container, so a plain word is
    never turned into a bool/tuple — a Python literal, then the same two over a
    copy whose bare identifier keys have been quoted (a local model writes
    ``[{op: "upsert_segment"}]``).

    Returns ``(value, None)`` on success, or ``(text, <sentence naming the
    fix>)`` when the text opens a container but parses as none of those.
    """
    stripped = text.strip() if isinstance(text, str) else text
    if not isinstance(stripped, str):
        return text, None
    try:
        return json.loads(stripped), None
    except _DECODE_ERRORS:
        pass
    if stripped[:1] not in ("[", "{"):
        return text, None

    value, ok = _try_parse(stripped)
    if ok:
        return value, None

    lenient = _quote_bare_keys(stripped)
    if lenient != stripped:
        value, ok = _try_parse(lenient)
        if ok:
            return value, None
    return text, _describe_payload_problem(stripped)


def _describe_payload_problem(text: str) -> str:
    problem = (
        "it is neither valid JSON nor a Python literal — it may be cut off part-way, "
        "so send the whole value"
    )
    if _DOUBLE_COLON_RE.search(text):
        problem += (
            ". One object also has two colons in a single entry, like "
            "{'op': 'set_prompt': 'text'}; every field is its own \"key\": value pair "
            'separated by a comma, so that one is {"op": "set_prompt", "prompt": "text"}'
        )
    if _quote_bare_keys(text) != text:
        problem += (
            '. Its object keys are also unquoted: write {"op": "upsert_segment"}, '
            "not {op: upsert_segment}"
        )
    return problem


def _near_miss_from_obj(
    obj: Any, registered: Set[str], fmt: str, span: Tuple[int, int], require_args_key: bool = False
) -> Optional[NearMiss]:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool")
    if not isinstance(name, str) or name not in registered:
        return None
    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters")
    if require_args_key and not isinstance(args, dict):
        return None
    return NearMiss(name, args if isinstance(args, dict) else None, fmt, span)


def _scan_balanced(text: str, start: int) -> str:
    """The ``[...]``/``{...}`` span opening at *start*, honouring nesting and
    quoted strings. An unterminated span returns everything that is there —
    it still parses as "truncated" downstream rather than vanishing."""
    stack = [_BRACKETS[text[start]]]
    quote: Optional[str] = None
    i = start + 1
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch in _BRACKETS:
            stack.append(_BRACKETS[ch])
        elif ch == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:i + 1]
        i += 1
    return text[start:]


def _parse_attributes(text: str) -> Tuple[Optional[str], Dict[str, Any], List[str]]:
    """The tag's attributes. A value may be quoted, or — as a local model
    writes it — an unquoted `[...]`/`{...}` payload, which is read to its
    matching close rather than to the next space."""
    type_name: Optional[str] = None
    attrs: Dict[str, Any] = {}
    problems: List[str] = []
    pos = 0
    while True:
        match = _ATTR_NAME_RE.search(text, pos)
        if not match:
            return type_name, attrs, problems
        key, start = match.group(1), match.end()
        if start >= len(text):
            return type_name, attrs, problems

        opening = text[start]
        if opening in ("'", '"'):
            end = text.find(opening, start + 1)
            raw = text[start + 1:] if end == -1 else text[start + 1:end]
            pos = len(text) if end == -1 else end + 1
        elif opening in _BRACKETS:
            raw = _scan_balanced(text, start)
            pos = start + len(raw)
        else:
            bare = _BARE_VALUE_RE.match(text, start)
            raw, pos = bare.group(0), bare.end()

        if key == "type":
            type_name = raw
            continue
        decoded, problem = decode_payload(raw)
        attrs[key] = decoded
        if problem:
            problems.append(f"'{key}' could not be read: {problem}")


_TOOL_ACTION_CLOSE_RE = re.compile(r"</tool_action>", re.IGNORECASE)


def _update_segment_arguments(
    content: str, attrs: Dict[str, Any], open_end: int
) -> Tuple[Optional[Dict[str, Any]], Tuple[int, int]]:
    """``update_segment``'s markup puts the proposed text BETWEEN the open and
    close tags -- every other ``<tool_action>`` tag holds its whole payload in
    attributes. Fold ``segment_id``/``segment_index``/the inner text into one
    entry of the tool's ``updates`` array, and extend the span past the close
    tag so the rescued call leaves no markup behind.

    Returns ``(None, open_span)`` when the opening tag itself was truncated --
    there is no reliable content boundary to recover from, so this falls back
    to the generic (ambiguous) attribute dump.
    """
    if content[open_end - 1:open_end] != ">":
        return None, (open_end, open_end)
    close = _TOOL_ACTION_CLOSE_RE.search(content, open_end)
    inner_end = close.start() if close else len(content)
    span_end = close.end() if close else len(content)
    update: Dict[str, Any] = {}
    if "segment_id" in attrs:
        update["segment_id"] = attrs["segment_id"]
    if "segment_index" in attrs:
        update["segment_index"] = attrs["segment_index"]
    update["content"] = content[open_end:inner_end].strip()
    return {"updates": [update]}, (open_end, span_end)


def _detect_tool_action(content: str, registered: Set[str], out: List[NearMiss]) -> None:
    for m in _TOOL_ACTION_RE.finditer(content):
        type_name, attrs, problems = _parse_attributes(demangle_quote_tokens(m.group(1)))
        if type_name not in registered:
            continue
        arguments = attrs or None
        span = m.span()
        if type_name == "update_segment":
            segment_arguments, inner_span = _update_segment_arguments(content, attrs, m.end())
            if segment_arguments is not None:
                arguments = segment_arguments
                span = (span[0], inner_span[1])
        out.append(NearMiss(
            type_name, arguments, "tool_action_tag", span, "; ".join(problems) or None
        ))


# A CLOSED `<tool_call>...</tool_call>` -- unlike `find_truncated_tool_call`,
# every open here has a matching close, so what's inside is a complete
# payload that simply failed to parse as JSON.
_TOOL_CALL_CLOSED_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)


def _detect_malformed_tool_call(content: str, registered: Set[str], out: List[NearMiss]) -> None:
    """A CLOSED `<tool_call>` whose JSON payload still doesn't parse after the
    same quote-token demangling the executor's own XML parser applies.

    `<tool_call>` is exclusively our own invocation syntax -- the system
    prompt teaches nothing else to write it -- so this needs no registered-name
    gate the way `<tool_action>`/bare JSON do: any occurrence here is an
    attempted call, not prose that happens to mention a tool. A block that
    DOES parse (with a registered name and an args key) never reaches this
    scan at all; the executor's XML parser dispatches it before the rescue
    path ever runs.
    """
    for m in _TOOL_CALL_CLOSED_RE.finditer(content):
        raw = m.group(1).strip()
        value, ok = _try_parse(raw)
        if not ok:
            value, ok = _try_parse(demangle_quote_tokens(raw))
        if ok:
            nm = _near_miss_from_obj(value, registered, "tool_call_malformed_json", m.span())
            if nm:
                # A registered name with a usable args dict -- e.g. one that
                # arrived under "parameters" rather than "arguments" -- is a
                # real repair, not just a steer.
                out.append(nm)
                continue
            name = value.get("name") if isinstance(value, dict) else None
            problem = (
                f"the <tool_call> block names {name!r}, which is not one of this session's "
                f"available tools"
                if isinstance(name, str)
                else "the <tool_call> block's JSON has no 'name' saying which tool to call"
            )
            out.append(NearMiss(name or "the intended tool", None, "tool_call_malformed_json", m.span(), problem))
            continue
        name_match = _TOOL_CALL_NAME_RE.search(raw)
        tool_name = name_match.group(1) if name_match else "the intended tool"
        out.append(NearMiss(
            tool_name, None, "tool_call_malformed_json", m.span(),
            "the <tool_call> block's JSON did not parse -- it is neither valid JSON nor a "
            "recoverable payload, so nothing ran. Resend it as strict JSON with double-quoted "
            "keys and string values",
        ))


def _detect_fences(content: str, registered: Set[str], out: List[NearMiss]) -> None:
    for m in _FENCE_RE.finditer(content):
        try:
            obj = json.loads(m.group(1).strip())
        except _DECODE_ERRORS:
            continue
        nm = _near_miss_from_obj(obj, registered, "code_fence", m.span())
        if nm:
            out.append(nm)


def _overlaps(span: Tuple[int, int], claimed: List[Tuple[int, int]]) -> bool:
    s, e = span
    return any(s < ce and cs < e for cs, ce in claimed)


def _detect_bare_json(
    content: str, registered: Set[str], out: List[NearMiss], claimed: List[Tuple[int, int]]
) -> None:
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        start = content.find("{", idx)
        if start == -1:
            return
        try:
            obj, end = decoder.raw_decode(content, start)
        except _DECODE_ERRORS:
            idx = start + 1
            continue
        span = (start, end)
        if not _overlaps(span, claimed):
            # A bare object is only an invocation when it carries BOTH a
            # registered name AND an args key — otherwise it is ordinary JSON.
            nm = _near_miss_from_obj(obj, registered, "bare_json", span, require_args_key=True)
            if nm:
                out.append(nm)
        idx = end


def find_near_miss_invocations(content: str, registered_names: Set[str]) -> List[NearMiss]:
    """Scan *content* for registered-tool invocations written in an unparsed format."""
    if not content or not registered_names:
        return []
    registered = set(registered_names)
    near: List[NearMiss] = []
    _detect_tool_action(content, registered, near)
    _detect_fences(content, registered, near)
    _detect_malformed_tool_call(content, registered, near)
    _detect_bare_json(content, registered, near, [nm.span for nm in near])
    near.sort(key=lambda nm: nm.span[0])
    return near


def validate_arguments(arguments: Any, schema: Optional[Dict[str, Any]]) -> bool:
    """True when *arguments* is a dict carrying every ``required`` key of
    *schema* AT the type the schema declares for it.

    The type check is what keeps an unparsed payload out of the tool: an
    ``operations`` attribute the model wrote as text that decoded to neither
    JSON nor a Python literal is still a *string*, and dispatching that would
    have the tool iterate its characters instead of its operations.
    """
    if not isinstance(arguments, dict):
        return False
    schema = schema or {}
    properties = schema.get("properties") or {}
    for key in schema.get("required", []) or []:
        if key not in arguments:
            return False
        declared = (properties.get(key) or {}).get("type")
        expected = _JSON_TYPES.get(declared)
        value = arguments[key]
        if expected is None:
            continue
        if declared in ("number", "integer") and isinstance(value, bool):
            return False
        if not isinstance(value, expected):
            return False
    return True


def strip_spans(content: str, spans: List[Tuple[int, int]]) -> str:
    """Remove *spans* from *content* and collapse the whitespace they leave behind."""
    if not spans:
        return content
    kept: List[str] = []
    last = 0
    for start, end in sorted(spans):
        if start >= last:
            kept.append(content[last:start])
            last = end
        else:
            last = max(last, end)
    kept.append(content[last:])
    cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", "".join(kept))
    return cleaned.strip()


def retry_nudge(tool_names: List[str], problems: Optional[List[str]] = None) -> str:
    """A system nudge quoting the expected format with the model's intended tool name.

    *problems* are the specific parse failures found in the arguments (see
    :class:`NearMiss`), appended so the retry fixes the real mistake instead of
    re-sending the same broken payload in a different wrapper.
    """
    primary = tool_names[0]
    names = ", ".join(dict.fromkeys(tool_names))
    nudge = (
        f"Your last message tried to call {names} but used a format that does not "
        f"execute, so nothing ran. To call a tool, reply with EXACTLY one "
        f'<tool_call>{{"name": "{primary}", "arguments": {{...}}}}</tool_call> block '
        f"and nothing else — no <tool_action> tags, no code fences, no bare JSON."
    )
    problems = [p for p in (problems or []) if p]
    if problems:
        nudge += (
            " Fix this too, or the retry fails the same way: "
            + "; ".join(dict.fromkeys(problems))
            + ". Write the arguments as JSON with double quotes."
        )
    return nudge


def fallback_message(tool_names: List[str]) -> str:
    """An honest inline message when a call can't be recovered — never raw markup."""
    names = ", ".join(dict.fromkeys(tool_names))
    return (
        f"I tried to call {names} but couldn't format the call correctly. "
        f"Could you rephrase what you'd like me to do?"
    )
