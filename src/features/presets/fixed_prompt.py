import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.features.prompt.expander import expand_prompts
from src.platform.http.errors import ConflictError, InvalidInputError, NotFoundError

MAX_FIXED_PROMPT_CHARS = 2000

TEXT_KEYS = ("prefix", "suffix", "negative")
SCREENED_KEYS = ("prefix", "suffix")
PAYLOAD_KEYS = (*TEXT_KEYS, "modes")


class InvalidFixedPrompt(InvalidInputError):
    def __init__(self, errors: List[str]):
        super().__init__("invalid_fixed_prompt", "The fixed prompt text is not valid", errors=errors)


@dataclass(frozen=True)
class FixedPrompt:
    prefix: str = ""
    suffix: str = ""
    negative: str = ""
    modes: Optional[List[str]] = None

    @property
    def is_empty(self) -> bool:
        return not (self.prefix.strip() or self.suffix.strip() or self.negative.strip())

    def applies_to(self, mode: Optional[str]) -> bool:
        return self.modes is None or mode in self.modes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prefix": self.prefix,
            "suffix": self.suffix,
            "negative": self.negative,
            "modes": list(self.modes) if self.modes is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Any) -> "FixedPrompt":
        if not isinstance(data, dict):
            return cls()
        modes = data.get("modes")
        return cls(
            prefix=_text(data.get("prefix")),
            suffix=_text(data.get("suffix")),
            negative=_text(data.get("negative")),
            modes=[mode for mode in modes if isinstance(mode, str)] if isinstance(modes, list) else None,
        )

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "FixedPrompt":
        if not raw:
            return cls()
        try:
            return cls.from_dict(json.loads(raw))
        except (TypeError, ValueError):
            return cls()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _expand_once(text: str) -> Tuple[str, bool]:
    warnings: List[str] = []
    expanded = expand_prompts(text, "", count=1, base_seed=0, on_warning=warnings.append)[0].positive
    return expanded, bool(warnings)


def _banned(content_safety, key: str, value: str, expanded: str) -> Optional[str]:
    if content_safety is None or key not in SCREENED_KEYS:
        return None
    matcher = content_safety.matcher()
    if matcher.empty or matcher.first_match_in([value, expanded]) is None:
        return None
    return (
        f"'{key}' contains a word on this instance's banned prompt list, "
        "so every generation with this preset would be refused"
    )


def validate_fixed_prompt(template, payload: Any, content_safety=None) -> List[str]:
    if not isinstance(payload, dict):
        return ["The body must be an object with prefix, suffix, negative and modes"]
    errors = [f"'{key}' is required" for key in PAYLOAD_KEYS if key not in payload]
    for key in TEXT_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str):
            errors.append(f"'{key}' must be text")
        elif len(value) > MAX_FIXED_PROMPT_CHARS:
            errors.append(f"'{key}' is longer than {MAX_FIXED_PROMPT_CHARS} characters")
        elif value.strip():
            expanded, malformed = _expand_once(value)
            if malformed:
                errors.append(f"'{key}' has a syntax error in its {{a|b}} choices, ${{variables}} or conditions")
            banned = _banned(content_safety, key, value, expanded)
            if banned:
                errors.append(banned)
    if "modes" in payload:
        errors.extend(_mode_errors(template, payload["modes"]))
    return errors


def _mode_errors(template, modes: Any) -> List[str]:
    if modes is None:
        return []
    if not isinstance(modes, list) or not all(isinstance(mode, str) for mode in modes):
        return ["'modes' must be null or a list of mode names"]
    if not modes:
        return ["'modes' must name at least one mode, or be null for every mode"]
    available = set((getattr(template, "modes", None) or {}).keys())
    promptless = set(((getattr(template, "vars", None) or {}).get("promptless_modes")) or [])
    errors = [f"Unknown mode '{mode}'" for mode in modes if mode not in available]
    errors.extend(f"Mode '{mode}' takes no prompt" for mode in modes if mode in available and mode in promptless)
    return errors


def _not_found(preset_id: str) -> NotFoundError:
    return NotFoundError("preset_not_found", f"Preset '{preset_id}' not found")


def read_fixed_prompt(loader, repository, preset_id: str) -> FixedPrompt:
    stored = repository.get(preset_id)
    if stored is None:
        if loader.get(preset_id) is None:
            raise _not_found(preset_id)
        return FixedPrompt()
    return stored


def write_fixed_prompt(loader, repository, preset_id: str, payload: Any, content_safety=None) -> FixedPrompt:
    template = loader.get(preset_id)
    if template is None:
        raise _not_found(preset_id)
    if repository.get(preset_id) is None:
        raise _not_installed(preset_id)
    fixed = parse_fixed_prompt(template, payload, content_safety)
    if not repository.set(preset_id, fixed):
        raise _not_installed(preset_id)
    return fixed


def _not_installed(preset_id: str) -> ConflictError:
    return ConflictError("preset_not_installed", f"Preset '{preset_id}' is not installed")


def parse_fixed_prompt(template, payload: Any, content_safety=None) -> FixedPrompt:
    errors = validate_fixed_prompt(template, payload, content_safety)
    if errors:
        raise InvalidFixedPrompt(errors)
    modes = payload["modes"]
    return FixedPrompt(
        prefix=payload["prefix"],
        suffix=payload["suffix"],
        negative=payload["negative"],
        modes=list(dict.fromkeys(modes)) if modes is not None else None,
    )
