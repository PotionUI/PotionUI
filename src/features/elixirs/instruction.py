import random
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.features.prompt.expander import expand_prompts
from src.platform.resources.prompt_variables import build_variables_for_submit, variables_as_list

TEMPLATE_MARKERS = ("{", "<<")


@dataclass(frozen=True)
class RolledInstruction:
    text: str
    seed: Optional[int]
    rolled: bool


def _placeholder(path: str) -> "re.Pattern[str]":
    token = f"#[{path}]" if " " in path else f"#{path}"
    return re.compile(re.escape(token) + r"(?![\w.\-\]])")


def _chip_value(chip: Mapping[str, Any], rng: random.Random, values_for: Callable[[str], List[str]]) -> str:
    current = str(chip.get("value") or "")
    if not chip.get("shuffle"):
        return current
    choices = [value for value in values_for(str(chip.get("categoryPath") or "")) if value]
    if not choices:
        choices = [str(item.get("value") or "") for item in chip.get("allValues") or [] if isinstance(item, Mapping) and item.get("value")]
    return rng.choice(choices) if choices else current


def _fill_chips(template: str, chips: Mapping[str, Mapping[str, Any]], rng: random.Random, values_for: Callable[[str], List[str]]) -> str:
    by_path: Dict[str, List[Mapping[str, Any]]] = {}
    for chip in chips.values():
        path = str((chip or {}).get("categoryPath") or "")
        if path:
            by_path.setdefault(path, []).append(chip)
    text = template
    for path in sorted(by_path, key=len, reverse=True):
        queue = list(by_path[path])

        def replace(match: "re.Match[str]") -> str:
            if not queue:
                return match.group(0)
            return _chip_value(queue.pop(0), rng, values_for)

        text = _placeholder(path).sub(replace, text)
    return text


def roll_instruction(
    template: str,
    chips: Optional[Mapping[str, Mapping[str, Any]]],
    variables: Optional[Mapping[str, Any]],
    seed: int,
    values_for: Callable[[str], List[str]],
) -> RolledInstruction:
    chips = chips or {}
    if not chips and not any(marker in template for marker in TEMPLATE_MARKERS):
        return RolledInstruction(text=template, seed=None, rolled=False)
    filled = _fill_chips(template, chips, random.Random(f"{seed}:phrasebook"), values_for)
    bindings = build_variables_for_submit(variables_as_list(dict(variables or {})), random.Random(f"{seed}:variables").random)
    expanded = expand_prompts(filled, "", count=1, base_seed=seed, variables=bindings or None)
    return RolledInstruction(text=expanded[0].positive, seed=seed, rolled=True)
