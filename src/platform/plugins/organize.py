import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

SUBJECTS = ("generation", "upload", "model")

SQL_ALIASES: Mapping[str, str] = MappingProxyType({"generation": "g", "upload": "u", "model": "m"})

FACT_KINDS: Mapping[str, Tuple[str, ...]] = MappingProxyType({
    "model_ref": ("is", "is_any_of", "is_not"),
    "enum": ("is", "is_any_of", "is_not"),
    "size": ("is", "at_least", "at_most"),
    "number": ("is", "at_least", "at_most"),
    "text": ("contains", "not_contains"),
    "tag_list": ("has", "has_not"),
    "bool": ("is",),
})

OPERATOR_LABELS: Mapping[str, str] = MappingProxyType({
    "is": "is",
    "is_any_of": "is any of",
    "is_not": "is not",
    "at_least": "is at least",
    "at_most": "is at most",
    "contains": "contains",
    "not_contains": "does not contain",
    "has": "has",
    "has_not": "does not have",
})

CONFIG_FIELD_KINDS = ("collection", "tag_list", "text", "bool", "enum", "number")

FACT_TRIGGERS = ("item_created", "tags_changed")


class DuplicateOrganizeEntryError(ValueError):
    pass


class InvalidOrganizeEntryError(ValueError):
    pass


class OrganizeActionBlocked(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OrganizeItem:
    subject: str
    item_id: str
    user_id: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(frozen=True)
class OrganizeChange:
    target_type: str
    target_id: str
    target_name: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


SqlPredicate = Callable[[str, Any, str], Optional[Tuple[str, Sequence[Any]]]]
OptionsHandler = Callable[[str, str, str], List[Any]]


@dataclass(frozen=True)
class OrganizeFactDefinition:
    key: str
    label: str
    subjects: Tuple[str, ...]
    kind: str
    extract: Callable[[OrganizeItem], Any]
    operators: Tuple[str, ...] = ()
    sql: Optional[SqlPredicate] = None
    options: Tuple[Mapping[str, Any], ...] = ()
    options_handler: Optional[OptionsHandler] = None
    picker: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    triggers: Tuple[str, ...] = ("item_created",)
    component: Optional[str] = None
    source: str = "core"

    def allowed_operators(self) -> Tuple[str, ...]:
        return self.operators or FACT_KINDS[self.kind]


@dataclass(frozen=True)
class OrganizeActionDefinition:
    key: str
    label: str
    subjects: Tuple[str, ...]
    apply: Callable[..., List[OrganizeChange]]
    config_schema: Tuple[Mapping[str, Any], ...] = ()
    undo: Optional[Callable[..., bool]] = None
    requires_admin: bool = False
    description: str = ""
    component: Optional[str] = None
    source: str = "core"


def _check_subjects(key: str, subjects: Sequence[str]) -> None:
    if not subjects:
        raise InvalidOrganizeEntryError(f"Auto-organize entry '{key}' declares no subjects")
    unknown = [s for s in subjects if s not in SUBJECTS]
    if unknown:
        raise InvalidOrganizeEntryError(f"Auto-organize entry '{key}' has unknown subjects: {unknown}")


def validate_fact(definition: OrganizeFactDefinition) -> None:
    _check_subjects(definition.key, definition.subjects)
    if definition.kind not in FACT_KINDS:
        raise InvalidOrganizeEntryError(f"Auto-organize fact '{definition.key}' has unknown kind '{definition.kind}'")
    bad = [op for op in definition.operators if op not in FACT_KINDS[definition.kind]]
    if bad:
        raise InvalidOrganizeEntryError(
            f"Auto-organize fact '{definition.key}' uses operators {bad} that kind '{definition.kind}' does not support"
        )
    unknown_triggers = [t for t in definition.triggers if t not in FACT_TRIGGERS]
    if unknown_triggers:
        raise InvalidOrganizeEntryError(f"Auto-organize fact '{definition.key}' has unknown triggers {unknown_triggers}")


def validate_action(definition: OrganizeActionDefinition) -> None:
    _check_subjects(definition.key, definition.subjects)
    for entry in definition.config_schema:
        if entry.get("kind") not in CONFIG_FIELD_KINDS:
            raise InvalidOrganizeEntryError(
                f"Auto-organize action '{definition.key}' config field '{entry.get('key')}' has unknown kind '{entry.get('kind')}'"
            )


class OrganizeRegistry:

    def __init__(self):
        self._facts: Dict[str, OrganizeFactDefinition] = {}
        self._actions: Dict[str, OrganizeActionDefinition] = {}
        self._lock = threading.Lock()

    def register_fact(self, definition: OrganizeFactDefinition) -> None:
        validate_fact(definition)
        with self._lock:
            if definition.key in self._facts:
                raise DuplicateOrganizeEntryError(f"Auto-organize fact already registered: '{definition.key}'")
            self._facts[definition.key] = definition

    def register_action(self, definition: OrganizeActionDefinition) -> None:
        validate_action(definition)
        with self._lock:
            if definition.key in self._actions:
                raise DuplicateOrganizeEntryError(f"Auto-organize action already registered: '{definition.key}'")
            self._actions[definition.key] = definition

    def unregister_source(self, source: str) -> None:
        with self._lock:
            for key in [k for k, d in self._facts.items() if d.source == source]:
                del self._facts[key]
            for key in [k for k, d in self._actions.items() if d.source == source]:
                del self._actions[key]

    def fact(self, key: str) -> Optional[OrganizeFactDefinition]:
        return self._facts.get(key)

    def action(self, key: str) -> Optional[OrganizeActionDefinition]:
        return self._actions.get(key)

    def facts(self, subject: Optional[str] = None) -> List[OrganizeFactDefinition]:
        with self._lock:
            entries = list(self._facts.values())
        return [d for d in entries if subject is None or subject in d.subjects]

    def actions(self, subject: Optional[str] = None) -> List[OrganizeActionDefinition]:
        with self._lock:
            entries = list(self._actions.values())
        return [d for d in entries if subject is None or subject in d.subjects]

    def has_source(self, source: str) -> bool:
        return any(d.source == source for d in self.facts()) or any(d.source == source for d in self.actions())


organize_registry = OrganizeRegistry()
