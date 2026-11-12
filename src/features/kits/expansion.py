import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.features.kits import citations
from src.features.kits.citations import PATH_BODY
from src.features.kits.catalog import ItemView, KitCatalog, KitView
from src.features.kits.field_limits import DEFAULT_FIELD_CAP, FormLimit, form_limits, warn_default_cap
from src.features.kits.rules import KIND_TEXT, MEDIA_KINDS
from src.features.prompt.resources import media_item_keys
from src.platform.filesystem.media_kinds import media_item_kind

CITE_RE = re.compile(
    f"(?:@\\[(?P<field>[A-Za-z_][A-Za-z0-9_-]*):(?P<key>[^\\]\\r\\n]+)\\])|(?:{PATH_BODY})"
)
PLURAL = {"image": "images", "video": "videos", "audio": "audio"}


@dataclass(frozen=True)
class FieldPolicy:
    field: str
    kinds: Tuple[str, ...]
    limits: Mapping[str, int] = field(default_factory=dict)
    total: Optional[int] = None


@dataclass(frozen=True)
class KitNotice:
    code: str
    message: str
    kit: Optional[str] = None
    item: Optional[str] = None
    kind: Optional[str] = None
    field: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "message": self.message, "kit": self.kit, "item": self.item,
            "kind": self.kind, "field": self.field,
        }


@dataclass
class KitExpansion:
    form_data: Dict[str, Any]
    texts: List[str]
    notices: List[KitNotice] = field(default_factory=list)
    used: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.notices or self.used)


def field_policies(
    specs: Sequence[Mapping[str, Any]], form: Optional[Mapping[str, FormLimit]] = None,
) -> Dict[str, FieldPolicy]:
    kinds: Dict[str, List[str]] = {}
    limits: Dict[str, Dict[str, int]] = {}
    totals: Dict[str, int] = {}
    for spec in specs or []:
        if not isinstance(spec, Mapping) or spec.get("source") or not spec.get("field"):
            continue
        name, kind = str(spec["field"]), spec.get("kind")
        if kind not in MEDIA_KINDS:
            continue
        kinds.setdefault(name, []).append(kind)
        if isinstance(spec.get("limit"), int):
            limits.setdefault(name, {})[kind] = spec["limit"]
        if isinstance(spec.get("limit_total"), int):
            totals[name] = min(spec["limit_total"], totals.get(name, spec["limit_total"]))
    if form is not None:
        for name, values in kinds.items():
            bound = form.get(name)
            if bound is not None and bound.total is not None:
                totals[name] = min(bound.total, totals.get(name, bound.total))
            for kind in values:
                declared = bound.by_kind.get(kind) if bound is not None else None
                if declared is not None:
                    limits.setdefault(name, {})[kind] = min(declared, limits.get(name, {}).get(kind, declared))
                elif name not in totals and kind not in limits.get(name, {}):
                    limits.setdefault(name, {})[kind] = DEFAULT_FIELD_CAP
                    warn_default_cap(name)
    return {
        name: FieldPolicy(name, tuple(dict.fromkeys(values)), limits.get(name, {}), totals.get(name))
        for name, values in kinds.items()
    }


def is_kit_row(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("kit"), str)
        and not any(value.get(key) for key in ("path", "relative_path", "url", "data"))
    )


def split_kit_rows(form_data: Any) -> Tuple[Any, Dict[str, List[str]]]:
    if not isinstance(form_data, dict):
        return form_data, {}
    rows: Dict[str, List[str]] = {}
    cleaned: Optional[Dict[str, Any]] = None
    for name, value in form_data.items():
        if not isinstance(value, list) or not any(is_kit_row(item) for item in value):
            continue
        rows[name] = [item["kit"] for item in value if is_kit_row(item)]
        if cleaned is None:
            cleaned = dict(form_data)
        cleaned[name] = [item for item in value if not is_kit_row(item)]
    return (cleaned if cleaned is not None else form_data), rows


def media_reference(view: ItemView) -> Dict[str, Any]:
    upload, kit, item = view.upload, view.kit, view.item
    key = view.relative_path
    return {
        "path": key,
        "relative_path": key,
        "url": f"/api/media/uploads/{upload.filename}",
        "name": upload.original_filename or upload.filename,
        "type": item.kind,
        "label": item.name,
        "kit_item": {"kit": kit.id, "kit_name": kit.name, "handle": kit.handle, "item": item.id, "name": item.name},
    }


class _Pass:
    def __init__(self, catalog: KitCatalog, policies: Mapping[str, FieldPolicy], form_data: Dict[str, Any],
                 loaded: Dict[str, List[str]]):
        self.catalog = catalog
        self.policies = policies
        self.loaded = loaded
        self.values: Dict[str, List[Any]] = {}
        self.form_data = form_data
        self.notices: List[KitNotice] = []
        self._seen: set = set()
        self.used: List[Dict[str, Any]] = []
        self._used_keys: set = set()

    def notice(self, code: str, message: str, **where: Any) -> None:
        identity = (code, where.get("kit"), where.get("item"), where.get("field"))
        if identity in self._seen:
            return
        self._seen.add(identity)
        self.notices.append(KitNotice(code=code, message=message, **where))

    def items(self, name: str) -> List[Any]:
        if name not in self.values:
            current = self.form_data.get(name)
            self.values[name] = list(current) if isinstance(current, list) else ([] if current in (None, "") else [current])
        return self.values[name]

    def field_for(self, kit_id: str, preferred: Optional[str]) -> Optional[str]:
        if preferred is not None and kit_id in self.loaded.get(preferred, ()):
            return preferred
        return next((name for name, ids in self.loaded.items() if kit_id in ids), None)

    def room(self, policy: FieldPolicy, kind: str) -> Optional[str]:
        current = self.items(policy.field)
        cap = policy.limits.get(kind)
        if cap is not None and sum(1 for entry in current if media_item_kind(entry) == kind) >= cap:
            return f"this model takes at most {cap} {PLURAL[kind]}"
        if policy.total is not None and sum(1 for entry in current if media_item_kind(entry)) >= policy.total:
            return f"this model takes at most {policy.total} references in all"
        return None

    def take(self, view: ItemView, name: str) -> Optional[str]:
        item, kit = view.item, view.kit
        where = {"kit": kit.handle, "item": item.name, "kind": item.kind, "field": name}
        policy = self.policies.get(name)
        if policy is None:
            self.notice("field_not_supported", f"`{item.name}` is skipped: this model has no place for kit items", **where)
            return None
        if item.kind not in policy.kinds:
            self.notice("unsupported_kind", f"This model does not take {PLURAL[item.kind]}: `{item.name}` is skipped.", **where)
            return None
        if view.missing:
            self.notice("file_missing", f"`{item.name}` is skipped: its Library file was deleted.", **where)
            return None
        key = view.relative_path
        current = self.items(name)
        present = any(key in media_item_keys(entry) for entry in current)
        if not present:
            reason = self.room(policy, item.kind)
            if reason is not None:
                self.notice("limit", f"`{item.name}` is skipped: {reason}.", **where)
                return None
            current.append(media_reference(view))
        if (item.id, name) not in self._used_keys:
            self._used_keys.add((item.id, name))
            self.used.append({
                "kit": kit.id, "kit_name": kit.name, "handle": kit.handle, "item": item.id, "name": item.name,
                "kind": item.kind, "field": name, "relative_path": key,
            })
        return f"@[{name}:{key}]"

    def text_of(self, view: ItemView) -> str:
        if (view.item.id, "") not in self._used_keys:
            self._used_keys.add((view.item.id, ""))
            self.used.append({
                "kit": view.kit.id, "kit_name": view.kit.name, "handle": view.kit.handle, "item": view.item.id,
                "name": view.item.name, "kind": KIND_TEXT, "field": None, "relative_path": None,
            })
        return view.item.text or ""

    def cite_item(self, view: ItemView, name: str) -> str:
        if view.item.is_text:
            return self.text_of(view)
        return self.take(view, name) or ""

    def cite_kit(self, kit_view: KitView, name: str) -> str:
        media: List[str] = []
        texts: List[str] = []
        for view in kit_view.items:
            if view.item.is_text:
                texts.append(self.text_of(view))
            else:
                token = self.take(view, name)
                if token:
                    media.append(token)
        return " ".join(part for part in media + texts if part)

    def cite(self, kit_view: Optional[KitView], kit_ref: str, item_id: Optional[str], item_name: Optional[str],
             preferred: Optional[str]) -> Optional[str]:
        if kit_view is None:
            self.notice("kit_deleted", f"The kit {kit_ref} no longer exists, so it is skipped.", kit=kit_ref)
            return ""
        handle = kit_view.kit.handle
        name = self.field_for(kit_view.kit.id, preferred)
        if name is None:
            self.notice("kit_not_loaded", f"The kit {handle} is not loaded, so it is skipped.", kit=handle)
            return ""
        if item_id is None and item_name is None:
            return self.cite_kit(kit_view, name)
        view = kit_view.item(item_id) if item_id else kit_view.item_named(item_name)
        if view is None:
            self.notice("item_removed", f"`{item_name or item_id}` was removed from {handle}, so it is skipped.",
                        kit=handle, item=item_name or item_id)
            return ""
        return self.cite_item(view, name)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


class KitExpander:
    def __init__(self, repository):
        self.repository = repository

    def expand(
        self,
        owner_id: str,
        policies: Mapping[str, FieldPolicy],
        form_data: Dict[str, Any],
        loaded: Dict[str, List[str]],
        texts: Sequence[str],
    ) -> KitExpansion:
        texts = list(texts)
        cited = any(citations.has_kit_cite(text) for text in texts)
        if not cited and not loaded:
            return KitExpansion(form_data=form_data, texts=texts)
        catalog = KitCatalog(self.repository, owner_id)
        state = _Pass(catalog, policies, form_data, loaded)
        for name, ids in loaded.items():
            if name not in policies:
                state.notice(
                    "field_not_supported", f"This model cannot use kits in '{name}', so they are skipped.", field=name,
                )
            for kit_id in ids:
                if catalog.by_id(kit_id) is None:
                    state.notice("kit_deleted", f"The kit {kit_id} no longer exists, so it is skipped.", kit=kit_id)

        def stored(reference, preferred):
            return state.cite(catalog.by_id(reference.kit_id), reference.kit_id, reference.item_id, None, preferred)

        def by_path(handle, item):
            kit_view = catalog.by_handle(handle)
            if kit_view is not None and item and kit_view.item_named(item) is None:
                return (state.cite(kit_view, handle, None, None, None) or "") + f".{item}"
            return state.cite(kit_view, handle, None, item, None)

        def substitute(match):
            if match.group("field") is not None:
                reference = citations.parse_key(match.group("key"))
                return match.group(0) if reference is None else stored(reference, match.group("field"))
            return by_path(match.group("handle"), match.group("item"))

        rewritten = [
            CITE_RE.sub(substitute, text) if isinstance(text, str) and citations.has_kit_cite(text) else text
            for text in texts
        ]

        result = form_data
        if state.values:
            result = dict(form_data)
            for name, values in state.values.items():
                if len(values) != len(_as_list(form_data.get(name))):
                    result[name] = values
            if result == form_data:
                result = form_data
        return KitExpansion(form_data=result, texts=rewritten, notices=state.notices, used=state.used)
