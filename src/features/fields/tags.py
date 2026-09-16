from typing import Any, Dict, List, Optional

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from src.features.presets.configuration import resolve_field_tag_categories


class Tags(BaseField):
    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info['configuration']

        field_allow_custom = bool(config.get('allow_custom', True))
        declared_default = self._declared_categories_default(config.get('categories'), preset_id)
        categories = resolve_field_tag_categories(
            config.get('categories'), preset_id, declared_default, field_allow_custom
        )

        schema['categories'] = categories
        schema['separator'] = config.get('separator', ', ')
        schema['allow_custom'] = field_allow_custom
        if config.get('max_tags') is not None:
            schema['max_tags'] = config.get('max_tags')

        return schema

    def _declared_categories_default(self, raw_categories: Any, preset_id: Optional[str]) -> Any:
        if not isinstance(raw_categories, str) or not raw_categories.startswith('@config:'):
            return None
        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset or not found_preset.configuration:
            return None
        key = raw_categories[len('@config:'):]
        entry = found_preset.configuration.get(key)
        return entry.get('default') if isinstance(entry, dict) else None

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        config = validation_rules or {}
        categories: List[Dict[str, Any]] = config.get('categories') or []
        if not categories:
            return {}

        if value is None:
            raw_map: Dict[str, Any] = {}
        elif isinstance(value, str):
            raw_map = self._split_string(value, categories, config.get('separator', ', '))
        elif isinstance(value, dict):
            raw_map = value
        else:
            raise ValueError(f"expected a string or a category->tags map, got {type(value).__name__}")

        category_keys = {c['key'] for c in categories}
        unknown = sorted(k for k in raw_map if k not in category_keys)
        if unknown:
            raise ValueError(f"unknown categor{'y' if len(unknown) == 1 else 'ies'}: {', '.join(unknown)}")

        max_tags = config.get('max_tags')
        field_allow_custom = bool(config.get('allow_custom', True))
        result: Dict[str, List[str]] = {}
        problems: List[str] = []
        total = 0

        for category in categories:
            key = category['key']
            entries = raw_map.get(key) or []
            if not isinstance(entries, list):
                problems.append(f"category '{key}' must be a list of tags")
                continue

            cleaned = self._clean_tags(entries)

            if category.get('multi') is False and len(cleaned) > 1:
                problems.append(f"category '{key}' allows only one tag")
                cleaned = cleaned[:1]

            allow_custom = field_allow_custom if category.get('allow_custom') is None else bool(category['allow_custom'])
            if not allow_custom:
                allowed_lower = {t.lower() for t in category.get('tags', [])}
                bad = [t for t in cleaned if t.lower() not in allowed_lower]
                if bad:
                    problems.append(f"category '{key}' does not allow custom tags: {', '.join(bad)}")
                    cleaned = [t for t in cleaned if t.lower() in allowed_lower]

            result[key] = cleaned
            total += len(cleaned)

        if max_tags is not None and total > max_tags:
            problems.append(f"too many tags: {total} exceeds the maximum {max_tags}")

        if problems:
            raise ValueError("; ".join(problems))

        return result

    @staticmethod
    def _clean_tags(entries: List[Any]) -> List[str]:
        cleaned: List[str] = []
        seen_lower = set()
        for tag in entries:
            if not isinstance(tag, str):
                continue
            trimmed = tag.strip()
            if not trimmed:
                continue
            lowered = trimmed.lower()
            if lowered in seen_lower:
                continue
            seen_lower.add(lowered)
            cleaned.append(trimmed)
        return cleaned

    @staticmethod
    def _split_string(value: str, categories: List[Dict[str, Any]], separator: str) -> Dict[str, List[str]]:
        tokens = [t.strip() for t in value.split(separator)] if value else []
        tokens = [t for t in tokens if t]

        result: Dict[str, List[str]] = {c['key']: [] for c in categories}
        tail_key = categories[-1]['key']
        single_filled = set()

        for token in tokens:
            placed = False
            for category in categories:
                key = category['key']
                if key == tail_key:
                    continue
                if category.get('multi') is False and key in single_filled:
                    continue
                allowed_lower = {t.lower() for t in category.get('tags', [])}
                if token.lower() in allowed_lower:
                    result[key].append(token)
                    if category.get('multi') is False:
                        single_filled.add(key)
                    placed = True
                    break
            if not placed:
                result[tail_key].append(token)

        return result

    @staticmethod
    def join(tags_map: Any, config: Dict[str, Any]) -> str:
        categories: List[Dict[str, Any]] = config.get('categories') or []
        separator = config.get('separator', ', ')
        if not isinstance(tags_map, dict):
            return ""

        parts: List[str] = []
        for category in categories:
            for tag in tags_map.get(category['key']) or []:
                if isinstance(tag, str) and tag.strip():
                    parts.append(tag.strip())

        deduped: List[str] = []
        seen_lower = set()
        for part in parts:
            lowered = part.lower()
            if lowered in seen_lower:
                continue
            seen_lower.add(lowered)
            deduped.append(part)

        return separator.join(deduped)

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'tags'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="categories",
                param_type=list,
                default=[],
                description=(
                    "Category vocabulary, in declared order - either an inline list of "
                    "{key, label, multi, allow_custom, tags} objects, or '@config:<key>' to "
                    "resolve against the preset's stored `configuration:` value for <key>."
                ),
                example="@config:style_categories"
            ),
            FieldConfigSpec(
                name="separator",
                param_type=str,
                default=", ",
                description="Separator used both to join the bound string and to split a plain-string submission back into categories.",
                example=", "
            ),
            FieldConfigSpec(
                name="allow_custom",
                param_type=bool,
                default=True,
                description="Field-level default for whether a category accepts tags outside its declared list. A category's own `allow_custom` overrides this.",
                example=True
            ),
            FieldConfigSpec(
                name="max_tags",
                param_type=int,
                default=None,
                description="Maximum number of tags across every category combined.",
                example=24
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        base_rules = super().validation_rules()
        tags_rules = [
            FieldValidationSpec(
                rule_name="required",
                description="At least one non-blank tag must be present, in any category",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="max_tags",
                description="Maximum number of tags across every category combined",
                param_type=int,
                example=24
            ),
        ]
        return base_rules + tags_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Categorized Tag Picker",
                description="A tag field with an inline category vocabulary",
                yaml_config="""type: tags
name: style
label: Style tags
required: true
configuration:
  separator: ", "
  categories:
    - { key: "colour", label: "Colour", multi: false, tags: ["red", "blue", "green"] }
    - { key: "shape", label: "Shape", multi: true, tags: ["round", "square"] }
    - { key: "more", label: "More", multi: true, tags: [] }""",
                rendered_output={
                    "type": "tags",
                    "name": "style",
                    "title": "Style tags",
                    "separator": ", ",
                    "allow_custom": True,
                    "categories": [
                        {"key": "colour", "label": "Colour", "multi": False, "allow_custom": True, "tags": ["red", "blue", "green"]},
                        {"key": "shape", "label": "Shape", "multi": True, "allow_custom": True, "tags": ["round", "square"]},
                        {"key": "more", "label": "More", "multi": True, "allow_custom": True, "tags": []},
                    ]
                },
                frontend_preview={
                    "type": "tags",
                    "name": "preview_style",
                    "title": "Style tags",
                    "separator": ", ",
                    "categories": [
                        {"key": "colour", "label": "Colour", "multi": False, "allow_custom": True, "tags": ["red", "blue", "green"]},
                    ]
                }
            ),
        ]
