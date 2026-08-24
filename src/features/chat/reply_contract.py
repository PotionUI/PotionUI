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
"""

import re
from typing import Any, Dict, List, Optional, Tuple

_HEADER_RE = re.compile(r"^\s*##\s*(improved|questions)\s*:?\s*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_QUESTION_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*\S)\s*$")
_OPTIONS_RE = re.compile(r"\[\s*([^\[\]]+?)\s*\]\s*$")

REPLY_CONTRACT_PROMPT_BLOCK = (
    "\n## Reply structure — hard rules\n\n"
    "1. After any tool calls, do NOT describe or recap what you did in prose. "
    "A narrative paragraph is a contract violation.\n"
    "2. The only allowed reply body is: at most one short lead line, then "
    "`## improved` — terse bullets, only what actually changed — plus any "
    "`<tool_action>` version blocks a tool's own instructions taught you to "
    "emit. Nothing else states a change.\n"
    "3. Add `## questions` only when an answer would sharpen the next "
    "response — up to 3 numbered, bracketed choices when natural. Omit it "
    "otherwise.\n"
    "4. Resuming: if the user's message quotes one of your questions back "
    "(`> question text`), that is the answer — continue the task immediately "
    "using it, calling tools as needed. Never reply with a bare acknowledgment "
    "and stop.\n"
    "5. Never end a turn announcing an action you have not yet performed — if "
    "the task isn't finished, call the tool now, in this same response.\n\n"
    "Headers are case-insensitive with an optional trailing colon.\n\n"
    "Example:\n"
    "Done.\n"
    "## improved\n"
    "- subject anchored: \"lone hiker\" is now \"a lone hiker in a red parka\"\n"
    "- negative pruned: 6 redundant terms removed\n"
    "## questions\n"
    "1. keep the rain ambience or push golden hour? [rain | golden hour]\n"
)

# Per-send reminder injected right before the last user message (see
# context_builder.inject_reply_contract_reminder_block) — a rule buried near
# the top of a long system prompt loses to recency, so this restates the
# no-prose-recap rule next to the turn it governs. Owned here so one file
# defines the contract's full wording.
REPLY_CONTRACT_REMINDER = (
    "Reply format reminder: tool calls, then `## improved` bullets — no "
    "prose recap. `## questions` only if needed."
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
    "rules."
)


def parse_reply_contract(content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Split ``## improved`` / ``## questions`` sections out of a reply.

    Returns ``(cleaned, reply_contract)``. ``cleaned`` is the text before the
    first recognized header, stripped; if nothing parses, ``cleaned`` is the
    original ``content`` untouched. ``reply_contract`` is
    ``{"improved": [...], "questions": [...]}`` with only the keys that
    actually parsed at least one item, or ``None`` when neither did.
    """
    if not content:
        return content, None

    lines = content.splitlines()
    first_header_idx: Optional[int] = None
    current_section: Optional[str] = None
    improved_items: List[str] = []
    questions: List[Dict[str, Any]] = []

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
                improved_items.append(bullet_match.group(1).strip())
        elif current_section == "questions":
            question_match = _QUESTION_RE.match(line)
            if question_match:
                text = question_match.group(1).strip()
                options: List[str] = []
                options_match = _OPTIONS_RE.search(text)
                if options_match:
                    options = [o.strip() for o in options_match.group(1).split("|") if o.strip()]
                    text = text[:options_match.start()].strip()
                if text:
                    questions.append({"text": text, "options": options})

    if not improved_items and not questions:
        return content, None

    cleaned = "\n".join(lines[:first_header_idx]).strip() if first_header_idx is not None else content

    reply_contract: Dict[str, Any] = {}
    if improved_items:
        reply_contract["improved"] = improved_items
    if questions:
        reply_contract["questions"] = questions

    return cleaned, reply_contract
