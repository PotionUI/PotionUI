import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.features.llm.tools import tool_call_rescue
from src.features.llm.tools.builtin.video_director_tool import flatten_active_document

DIRECTOR_ACTION = "update_director_segment"
PROMPT_LIST_ACTIONS = ("add_prompt", "select_prompt", "remove_prompt", "rename_prompt")
APPLY_ACTION_TYPES = frozenset(("update_segment", "delete_segment", "add_segment", DIRECTOR_ACTION) + PROMPT_LIST_ACTIONS)
_ADDRESSED_ACTIONS = ("update_segment", "delete_segment", DIRECTOR_ACTION)
_ADDRESS_KEYS = ("type", "segment_index", "segment_id", "shot_id")

_NEXT_OPEN_RE = re.compile(r"<tool_action\b", re.IGNORECASE)
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")
_SELF_CLOSE_TAIL_RE = re.compile(r"\s*</tool_action\s*>", re.IGNORECASE)
_CURLY_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})


@dataclass(frozen=True)
class Target:
    id: str
    index: int
    shot_id: Optional[str] = None


@dataclass
class ApplyTargets:
    segments: Optional[List[Target]] = None
    prompts: Dict[int, List[Target]] = field(default_factory=dict)
    director: Optional[List[Target]] = None

    @classmethod
    def from_context(cls, context_metadata: Optional[Dict[str, Any]]) -> "ApplyTargets":
        metadata = context_metadata or {}
        targets = cls(segments=_segment_targets(metadata.get("segments")))
        batch = metadata.get("prompt_batch")
        for entry in (batch.get("prompts") if isinstance(batch, dict) else None) or []:
            if isinstance(entry, dict) and isinstance(entry.get("index"), int):
                targets.prompts[entry["index"]] = _segment_targets(entry.get("segments")) or []
        form_state = metadata.get("form_state")
        try:
            flat = flatten_active_document(form_state) if isinstance(form_state, dict) else None
        except (AttributeError, TypeError, KeyError, ValueError):
            flat = None
        if flat is not None:
            targets.director = _director_targets(flat["segments"])
        return targets


def _segment_targets(raw: Any) -> Optional[List[Target]]:
    if not isinstance(raw, list):
        return None
    return [Target(str(s.get("id", "")), i) for i, s in enumerate(raw) if isinstance(s, dict)]


def _director_targets(segments: List[Dict[str, Any]]) -> List[Target]:
    out: List[Target] = []
    per_shot: Dict[Any, int] = {}
    for position, seg in enumerate(segments):
        shot_id = seg.get("shot_id")
        if shot_id is None:
            out.append(Target(str(seg["id"]), position))
            continue
        index = per_shot.get(shot_id, 0)
        per_shot[shot_id] = index + 1
        out.append(Target(str(seg["id"]), index, str(shot_id)))
    return out


@dataclass
class _Block:
    start: int
    end: int
    attr_text: str
    body: str
    closed: bool


def _parse_index(raw: Optional[str]) -> Optional[int]:
    if raw is None or not raw.strip().isdigit():
        return None
    return int(raw.strip())


def _resolve(
    pool: List[Target], seg_id: Optional[str], index: Optional[int], shot_id: Optional[str]
) -> Optional[Target]:
    scoped = [t for t in pool if t.shot_id == shot_id] if shot_id else pool
    if seg_id:
        for target in scoped:
            if target.id == seg_id:
                return target
    if index is not None:
        if shot_id:
            return scoped[index] if index < len(scoped) else None
        return pool[index] if index < len(pool) else None
    if not seg_id and len(pool) == 1:
        return pool[0]
    return None


def _pool_for(action: str, attrs: Dict[str, str], targets: ApplyTargets) -> Optional[List[Target]]:
    if action == DIRECTOR_ACTION:
        return targets.director
    prompt = attrs.get("prompt")
    if prompt is None:
        return targets.segments
    prompt_index = _parse_index(prompt)
    return targets.prompts.get(prompt_index) if prompt_index is not None else None


def _not_found_note(action: str, seg_id: Optional[str], index: Optional[int], body: str) -> str:
    noun = "shot" if action == DIRECTOR_ACTION else "segment"
    ref = f' "{seg_id}"' if seg_id else (f" #{index}" if index is not None else "")
    note = f"_Can't apply: {noun}{ref} not found._"
    text = body.strip()
    if not text:
        return note
    return note + "\n\n" + "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _render(attrs: Dict[str, str], order: List[str], body: str) -> str:
    keys = [k for k in _ADDRESS_KEYS if k in attrs] + [k for k in order if k not in _ADDRESS_KEYS]
    rendered = " ".join(f'{k}="{attrs[k].replace(chr(34), chr(39))}"' for k in keys)
    return f"<tool_action {rendered}>{body}</tool_action>"


def _find_block(content: str, match: "re.Match[str]") -> Optional[_Block]:
    attr_text = match.group(1)
    if not match.group(0).endswith(">") or _NEXT_OPEN_RE.search(attr_text):
        return None
    open_end = match.end()
    if attr_text.rstrip().endswith("/"):
        tail = _SELF_CLOSE_TAIL_RE.match(content, open_end)
        end = tail.end() if tail else open_end
        return _Block(match.start(), end, attr_text.rstrip()[:-1], "", False)
    next_open = _NEXT_OPEN_RE.search(content, open_end)
    limit = next_open.start() if next_open else len(content)
    close = tool_call_rescue.find_tool_action_close(content[open_end:limit])
    if close:
        return _Block(match.start(), open_end + close.end(), attr_text, content[open_end:open_end + close.start()], True)
    lead = len(content[open_end:limit]) - len(content[open_end:limit].lstrip())
    brk = _PARAGRAPH_BREAK_RE.search(content, open_end + lead, limit)
    body = content[open_end:brk.start() if brk else limit].rstrip()
    return _Block(match.start(), open_end + len(body), attr_text, body, False)


def _normalize_block(block: _Block, original: str, targets: ApplyTargets) -> str:
    attr_text = tool_call_rescue.demangle_quote_tokens(block.attr_text).translate(_CURLY_QUOTES)
    scanned = tool_call_rescue.scan_tool_action_attributes(attr_text)
    attrs: Dict[str, str] = {}
    order: List[str] = []
    well_formed = block.closed
    for key, raw, opening in scanned:
        key = key.lower()
        if key not in attrs:
            order.append(key)
        attrs[key] = raw
        well_formed = well_formed and opening == '"'
    action = (attrs.get("type") or "").strip().lower()
    if not action:
        action = (tool_call_rescue.tool_action_leading_word(attr_text) or "").lower()
        well_formed = False
    if action not in APPLY_ACTION_TYPES:
        return original
    fixed = dict(attrs, type=action)

    if action in _ADDRESSED_ACTIONS:
        pool = _pool_for(action, attrs, targets)
        if pool is not None:
            seg_id = (attrs.get("segment_id") or "").strip() or None
            index = _parse_index(attrs.get("segment_index"))
            shot_id = (attrs.get("shot_id") or "").strip() or None
            target = _resolve(pool, seg_id, index, shot_id)
            if target is None:
                return _not_found_note(action, seg_id, index, block.body)
            if seg_id != target.id:
                fixed["segment_id"] = target.id
            if index is None or seg_id != target.id:
                fixed["segment_index"] = str(target.index)
            if target.shot_id and not shot_id and len({t.shot_id for t in pool}) > 1:
                fixed["shot_id"] = target.shot_id

    if well_formed and fixed == attrs:
        return original
    return _render(fixed, order, block.body)


def normalize_apply_tags(content: str, targets: ApplyTargets) -> str:
    if not content or "<tool_action" not in content.lower():
        return content
    out: List[str] = []
    pos = 0
    while True:
        match = next(tool_call_rescue.iter_tool_action_opens(content, pos), None)
        if match is None:
            break
        block = _find_block(content, match)
        if block is None:
            out.append(content[pos:match.start() + 1])
            pos = match.start() + 1
            continue
        out.append(content[pos:block.start])
        out.append(_normalize_block(block, content[block.start:block.end], targets))
        pos = block.end
    out.append(content[pos:])
    return "".join(out)
