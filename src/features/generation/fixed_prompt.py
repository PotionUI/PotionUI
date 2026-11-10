from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.features.presets.fixed_prompt import FixedPrompt
from src.pipelines.outputs import GenerationOutput, ParamGenerationOutput, RenderedPromptGenerationOutput

PROMPT_PARAMETERS = {"positive_prompt": "positive", "negative_prompt": "negative"}
DIRECTOR_DOCUMENTS = ("video_director", "music_director")


def fixed_prompt_applies(
    fixed: Optional[FixedPrompt],
    template: Any,
    mode: Optional[str],
    form_data: Optional[Dict[str, Any]],
    prompts: Optional[Sequence[Dict[str, str]]],
) -> bool:
    if fixed is None or fixed.is_empty or not prompts:
        return False
    if not fixed.applies_to(mode):
        return False
    if mode in _promptless_modes(template):
        return False
    return not any(isinstance((form_data or {}).get(key), dict) for key in DIRECTOR_DOCUMENTS)


def _promptless_modes(template: Any) -> List[str]:
    variables = getattr(template, "vars", None)
    if not isinstance(variables, dict):
        return []
    modes = variables.get("promptless_modes")
    return [mode for mode in modes if isinstance(mode, str)] if isinstance(modes, list) else []


def _join(parts: Sequence[str], separator: str) -> str:
    return separator.join(text.strip() for text in parts if isinstance(text, str) and text.strip())


@dataclass
class FixedPromptRun:
    visible: List[Dict[str, str]]
    applied: List[Dict[str, str]] = field(default_factory=list)

    def _visible_text(self, channel: str, index: int) -> str:
        return self.visible[index % len(self.visible)].get(channel, "")

    def mask(self, output: GenerationOutput) -> None:
        if not self.visible:
            return
        if isinstance(output, RenderedPromptGenerationOutput):
            index = output.index if isinstance(output.index, int) else 0
            output.positive = self._visible_text("positive", index)
            output.negative = self._visible_text("negative", index)
        elif isinstance(output, ParamGenerationOutput) and output.name in PROMPT_PARAMETERS:
            channel = PROMPT_PARAMETERS[output.name]
            output.values = [
                self._visible_text(channel, index) for index in range(len(output.values or []))
            ]

    def records(self) -> List[Dict[str, str]]:
        return [
            {"positive": pair.get("positive", ""), "negative": pair.get("negative", "")} for pair in self.applied
        ]


def apply_fixed_prompt(
    visible: List[Dict[str, str]], parts: Sequence[Tuple[str, str, str]]
) -> FixedPromptRun:
    applied = []
    for index, pair in enumerate(visible):
        prefix, suffix, negative = parts[index] if index < len(parts) else ("", "", "")
        applied.append({
            "positive": _join([prefix, pair.get("positive", ""), suffix], " "),
            "negative": _join([pair.get("negative", ""), negative], ", "),
        })
    return FixedPromptRun(visible=[dict(pair) for pair in visible], applied=applied)


def _applied_text(entries: List[Dict[str, str]], channel: str, index: int) -> str:
    return entries[index % len(entries)].get(channel, "")


def with_applied_prompts(
    result: Dict[str, Any], entries: Optional[List[Dict[str, str]]], index: Optional[int] = None
) -> Dict[str, Any]:
    if not entries or not isinstance(result, dict):
        return result
    parameters = dict(result.get("parameters") or {})
    for name, channel in PROMPT_PARAMETERS.items():
        if name not in parameters:
            continue
        value = parameters[name]
        if index is None and isinstance(value, list):
            parameters[name] = [_applied_text(entries, channel, at) for at in range(len(value))]
        elif index is not None:
            parameters[name] = _applied_text(entries, channel, index)
    return {**result, "parameters": parameters, "fixed_prompt_applied": True}


def applied_parameters(entries: Optional[List[Dict[str, str]]]) -> Optional[Dict[str, List[str]]]:
    if not entries:
        return None
    return {
        name: [entry.get(channel, "") for entry in entries] for name, channel in PROMPT_PARAMETERS.items()
    }
