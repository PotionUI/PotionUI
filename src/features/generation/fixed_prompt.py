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


def _pick(texts: List[str], value: Any, index: int) -> int:
    slot = index % len(texts)
    if isinstance(value, str) and texts[slot] != value and value in texts:
        return texts.index(value)
    return slot


@dataclass
class FixedPromptRun:
    visible: List[Dict[str, str]]
    applied: List[Dict[str, str]] = field(default_factory=list)

    def _visible_text(self, channel: str, value: Any, index: int) -> str:
        source = [pair.get(channel, "") for pair in self.applied]
        return self.visible[_pick(source, value, index)].get(channel, "")

    def mask(self, output: GenerationOutput) -> None:
        if not self.visible:
            return
        if isinstance(output, RenderedPromptGenerationOutput):
            index = output.index if isinstance(output.index, int) else 0
            output.positive = self._visible_text("positive", output.positive, index)
            output.negative = self._visible_text("negative", output.negative, index)
        elif isinstance(output, ParamGenerationOutput) and output.name in PROMPT_PARAMETERS:
            channel = PROMPT_PARAMETERS[output.name]
            output.values = [
                self._visible_text(channel, value, index) for index, value in enumerate(output.values or [])
            ]

    def records(self) -> List[Dict[str, str]]:
        return [
            {
                "positive": applied.get("positive", ""),
                "negative": applied.get("negative", ""),
                "visible_positive": visible.get("positive", ""),
                "visible_negative": visible.get("negative", ""),
            }
            for visible, applied in zip(self.visible, self.applied)
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


def _applied_text(entries: List[Dict[str, str]], channel: str, value: Any, index: int) -> Any:
    source = [entry.get(f"visible_{channel}", "") for entry in entries]
    return entries[_pick(source, value, index)].get(channel, "")


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
            parameters[name] = [_applied_text(entries, channel, item, at) for at, item in enumerate(value)]
        elif index is not None:
            parameters[name] = _applied_text(entries, channel, value, index)
    return {**result, "parameters": parameters, "fixed_prompt_applied": True}


def applied_parameters(entries: Optional[List[Dict[str, str]]]) -> Optional[Dict[str, List[str]]]:
    if not entries:
        return None
    return {
        name: [entry.get(channel, "") for entry in entries] for name, channel in PROMPT_PARAMETERS.items()
    }
