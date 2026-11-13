from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

from src.features.presets.templates import default_form_name
from src.pipelines.outputs import GenerationOutput, ParamGenerationOutput
from src.pipelines.param_meta import normalize_param_meta


def _form_for(template: Any, mode: Optional[str], form_name: Optional[str]) -> Any:
    modes = getattr(template, 'modes', None)
    mode_data = modes.get(mode) if isinstance(modes, dict) else None
    forms = getattr(mode_data, 'forms', None)
    if not isinstance(forms, list) or not forms:
        return None
    wanted = form_name or default_form_name(mode_data)
    return next((form for form in forms if form.name == wanted), forms[0])


def _walk_fields(fields: Any) -> Iterable[Any]:
    if not isinstance(fields, list):
        return
    for item in fields:
        if isinstance(item, dict) or not hasattr(item, 'type'):
            continue
        yield item
        yield from _walk_fields(getattr(item, 'children', None))


def form_field_meta(template: Any, mode: Optional[str], form_name: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    form = _form_for(template, mode, form_name)
    if form is None:
        return {}
    found: Dict[str, Dict[str, str]] = {}
    for form_field in _walk_fields(form.fields):
        name = form_field.name
        if not isinstance(name, str) or name in found:
            continue
        meta = normalize_param_meta({'label': form_field.label, 'type': form_field.type})
        if meta:
            found[name] = meta
    return found


@dataclass
class ParamMetaStamp:
    fields: Dict[str, Dict[str, str]] = field(default_factory=dict)

    @classmethod
    def plan(cls, template: Any, mode: Optional[str], form_name: Optional[str] = None) -> Optional['ParamMetaStamp']:
        fields = form_field_meta(template, mode, form_name)
        return cls(fields) if fields else None

    def stamp(self, output: GenerationOutput) -> None:
        if isinstance(output, ParamGenerationOutput) and output.meta is None:
            known = self.fields.get(output.name)
            if known:
                output.meta = dict(known)
