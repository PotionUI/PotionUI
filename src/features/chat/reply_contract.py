"""The structured reply contract: optional `## improved` / `## questions`
sections a chat model may append after its lead-line prose.

Models are instructed (see ``REPLY_CONTRACT_PROMPT_BLOCK`` below) to answer
with tool calls, at most one short lead line, then these two optional
sections — nothing else. Headers are case-insensitive with an optional
trailing colon, order-tolerant, and may repeat (a repeated header's items are
concatenated into the same section). Bullets are ``-``/``*``/``•``; questions
are numbered (``1.``/``1)``) or bulleted the same way, and may end with a
``[a | b | c]`` options hint. Anything that doesn't parse as a recognized
section is left as prose — this contract must never break a reply.

A `<tool_action>` version tag (the frontend's Apply-block convention, see
``src.features.llm.tools.builtin.form_context_tool``) is opaque payload to
this parser, not a recognized section — it must survive verbatim no matter
where in the reply it lands, including after a `## improved`/`## questions`
header. It is pulled out before line-based parsing (so multi-line proposed
text inside it is never misread as a bullet) and stitched back into
``cleaned`` at the end.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

_HEADER_RE = re.compile(r"^\s*##\s*(improved|questions)\s*:?\s*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_QUESTION_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*\S)\s*$")
_OPTIONS_RE = re.compile(r"\[\s*([^\[\]]+?)\s*\]\s*$")
# Closing tag optional so a generation cut off mid-tag still round-trips
# (the truncated fragment is inert text as far as this parser is concerned).
_TOOL_ACTION_BLOCK_RE = re.compile(
    r"<tool_action\b[^>]*>(?:.*?</tool_action>)?", re.DOTALL | re.IGNORECASE
)
_TAG_PLACEHOLDER = "\x00REPLY_CONTRACT_TAG{}\x00"
_TAG_PLACEHOLDER_RE = re.compile(r"\x00REPLY_CONTRACT_TAG(\d+)\x00")

REPLY_CONTRACT_PROMPT_BLOCK = (
    "\n## Reply structure — hard rules\n\n"
    "1. After any tool calls, do NOT describe or recap what you did in prose. "
    "A narrative paragraph is a contract violation — and so is restating the "
    "same information twice, once as prose and again as `## improved` "
    "bullets; say it once, as bullets.\n"
    "2. The only allowed reply body is: at most one short lead line, then "
    "`## improved` — terse bullets, only what actually changed — plus, "
    "whenever a tool's own instructions taught you a `<tool_action>` version "
    "tag for the change you are describing, that tag with the COMPLETE "
    "proposed text (anywhere in the reply — before or after `## improved`, "
    "it is delivered either way). Nothing else states a change.\n"
    "3. Bullets summarize a change; they never deliver it. If a bullet says "
    "a prompt, segment, or shot changed and the matching `<tool_action>` tag "
    "with the full text is not in this same reply, nothing was delivered — a "
    "bare 'Done' plus bullets and no tag is a contract violation, not a "
    "finished turn.\n"
    "4. Add `## questions` only when an answer would sharpen the next "
    "response — up to 3 numbered, bracketed choices when natural. Omit it "
    "otherwise.\n"
    "5. Resuming: if the user's message quotes one of your questions back "
    "(`> question text`), that is the answer — continue the task immediately "
    "using it, calling tools as needed. Never reply with a bare acknowledgment "
    "and stop.\n"
    "6. Never end a turn announcing an action you have not yet performed — if "
    "the task isn't finished, call the tool now, in this same response.\n\n"
    "Headers are case-insensitive with an optional trailing colon.\n\n"
    "Example (no content proposed):\n"
    "Done.\n"
    "## improved\n"
    "- negative pruned: 6 redundant terms removed\n"
    "## questions\n"
    "1. keep the rain ambience or push golden hour? [rain | golden hour]\n\n"
    "Example (proposing new text — the tag is what delivers it, the bullet "
    "only describes it; the exact tag name and attributes come from whatever "
    "tool taught you one, not this example):\n"
    "Done.\n"
    "## improved\n"
    "- subject anchored: \"lone hiker\" is now \"a lone hiker in a red parka\"\n"
    '<tool_action type="...">a lone hiker in a red parka, ...</tool_action>\n'
)

# Per-send reminder injected right before the last user message (see
# context_builder.inject_reply_contract_reminder_block) — a rule buried near
# the top of a long system prompt loses to recency, so this restates the
# no-prose-recap rule next to the turn it governs. Owned here so one file
# defines the contract's full wording.
REPLY_CONTRACT_REMINDER = (
    "Reply format reminder: tool calls, then `## improved` bullets — no "
    "prose recap. If a tool taught you a `<tool_action>` tag for the change "
    "you're describing, include it now with the full text — bullets alone "
    "never deliver a change, and saying you changed something without the "
    "tag is wrong. `## questions` only if needed."
)

# Injected by ToolExecutor as the trailing message of every LLM call after the
# turn's first tool round completes (see execute_with_tools/_stream's
# `iteration_nudge` parameter) — a rule stated once in the static system
# prompt loses to recency as tool rounds accumulate, so this restates it right
# where the model is about to decide whether to call another tool or stop.
TOOL_LOOP_CONTINUATION_NUDGE = (
    "If the task is not finished, call the next tool NOW, in this same "
    "response. Never end a reply announcing an action you have not "
    "performed. When the task IS finished, reply per the reply-structure "
    "rules — and if that means proposing new prompt/segment/shot text, the "
    "`<tool_action>` tag with the full text belongs in this same reply, not "
    "just a bullet describing it."
)


def parse_reply_contract(content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Split ``## improved`` / ``## questions`` sections out of a reply.

    Returns ``(cleaned, reply_contract)``. ``cleaned`` is the text before the
    first recognized header plus any ``<tool_action>`` tag(s) found anywhere
    in the reply (see module docstring), stripped; if nothing parses,
    ``cleaned`` is the original ``content`` untouched. ``reply_contract`` is
    ``{"improved": [...], "questions": [...]}`` with only the keys that
    actually parsed at least one item, or ``None`` when neither did.
    """
    if not content:
        return content, None

    # Tag blocks are stashed behind placeholders before line-based parsing so
    # neither their attribute quoting nor multi-line proposed text is ever
    # read as a header/bullet/question -- and so a tag landing after a header
    # isn't silently discarded along with the rest of that section's
    # unrecognized lines (see "leftover" below).
    tag_blocks: List[str] = []

    def _stash(match: "re.Match[str]") -> str:
        tag_blocks.append(match.group(0))
        return _TAG_PLACEHOLDER.format(len(tag_blocks) - 1)

    working = _TOOL_ACTION_BLOCK_RE.sub(_stash, content)

    # A bullet/question can carry its tag inline on the same line
    # (`- warm palette <tool_action ...>...</tool_action>`) -- _BULLET_RE/
    # _QUESTION_RE capture the whole remainder of the line, placeholder
    # included, so it must be pulled back out of the captured text before
    # that text lands in the structured reply_contract (a raw null-byte
    # token must never reach the frontend), with the real tag it stood for
    # recovered here rather than lost.
    recovered_tag_blocks: List[str] = []

    def _strip_inline_tags(text: str) -> str:
        def _pull(match: "re.Match[str]") -> str:
            idx = int(match.group(1))
            if 0 <= idx < len(tag_blocks):
                recovered_tag_blocks.append(tag_blocks[idx])
            return " "
        return re.sub(r"\s+", " ", _TAG_PLACEHOLDER_RE.sub(_pull, text)).strip()

    lines = working.splitlines()
    first_header_idx: Optional[int] = None
    current_section: Optional[str] = None
    improved_items: List[str] = []
    questions: List[Dict[str, Any]] = []
    leftover_lines: List[str] = []

    for i, line in enumerate(lines):
        header_match = _HEADER_RE.match(line)
        if header_match:
            if first_header_idx is None:
                first_header_idx = i
            current_section = header_match.group(1).lower()
            continue

        if current_section == "improved":
            bullet_match = _BULLET_RE.match(line)
            if bullet_match:
                item_text = _strip_inline_tags(bullet_match.group(1).strip())
                if item_text:
                    improved_items.append(item_text)
            elif line.strip():
                leftover_lines.append(line)
        elif current_section == "questions":
            question_match = _QUESTION_RE.match(line)
            if question_match:
                text = question_match.group(1).strip()
                options: List[str] = []
                options_match = _OPTIONS_RE.search(text)
                if options_match:
                    options = [o.strip() for o in options_match.group(1).split("|") if o.strip()]
                    text = text[:options_match.start()].strip()
                text = _strip_inline_tags(text)
                if text:
                    questions.append({"text": text, "options": options})
            elif line.strip():
                leftover_lines.append(line)

    if not improved_items and not questions:
        return content, None

    cleaned = "\n".join(lines[:first_header_idx]).strip() if first_header_idx is not None else working
    if leftover_lines:
        extra = "\n".join(leftover_lines).strip()
        cleaned = f"{cleaned}\n\n{extra}".strip() if cleaned else extra
    if recovered_tag_blocks:
        extra = "\n".join(recovered_tag_blocks)
        cleaned = f"{cleaned}\n\n{extra}".strip() if cleaned else extra

    for idx, block in enumerate(tag_blocks):
        cleaned = cleaned.replace(_TAG_PLACEHOLDER.format(idx), block)

    reply_contract: Dict[str, Any] = {}
    if improved_items:
        reply_contract["improved"] = improved_items
    if questions:
        reply_contract["questions"] = questions

    return cleaned, reply_contract
