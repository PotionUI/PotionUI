from typing import Any, Dict, List, Mapping, Optional

from src.features.kits import citations
from src.features.kits.catalog import KitCatalog
from src.features.kits.expansion import FieldPolicy, KitExpander, field_policies, split_kit_rows
from src.features.kits.rules import MEDIA_KINDS
from src.features.prompt.markers import RESOURCE_MARKER_RE
from src.features.prompt.resources import resolve_prompt_resources


def cite_text(field: str, cite: str) -> str:
    value = (cite or "").strip()
    if value.startswith(citations.KEY_PREFIX):
        value = value[len(citations.KEY_PREFIX):]
    reference = citations.parse_stable(value)
    if reference is not None:
        return citations.marker(field, reference.kit_id, reference.item_id)
    value = value if value.startswith("kits.") else f"kits.{value}"
    return f"@{value}"


def loaded_kit_ids(text: str, catalog: KitCatalog) -> List[str]:
    found: List[str] = []
    for match in RESOURCE_MARKER_RE.finditer(text):
        reference = citations.parse_key(match.group(2))
        if reference is not None:
            found.append(reference.kit_id)
    for match in citations.PATH_CITE_RE.finditer(text):
        view = catalog.by_handle(match.group(1))
        if view is not None:
            found.append(view.kit.id)
    return list(dict.fromkeys(found))


def describe_policy(policy: Optional[FieldPolicy]) -> Dict[str, Any]:
    if policy is None:
        return {"takes": {kind: True for kind in MEDIA_KINDS + ("text",)}, "limits": {}}
    takes = {kind: kind in policy.kinds for kind in MEDIA_KINDS}
    takes["text"] = True
    return {"takes": takes, "limits": {**policy.limits, **({"total": policy.total} if policy.total else {})}}


def resolve_citations(
    expander: KitExpander,
    owner_id: str,
    specs: Optional[List[Mapping[str, Any]]],
    field: Optional[str],
    text: str,
    cites: List[str],
    loaded: Optional[List[str]],
    token_specs: Optional[List[Mapping[str, Any]]] = None,
    form_data: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    policies = field_policies(specs or [])
    name = field or next(iter(policies), "references")
    if specs is None:
        policies = {name: FieldPolicy(name, MEDIA_KINDS)}
    elif name not in policies:
        policies = {**policies, name: FieldPolicy(name, ())}
    body = " ".join(part for part in [text.strip(), *(cite_text(name, cite) for cite in cites)] if part)
    catalog = KitCatalog(expander.repository, owner_id)
    kit_ids = loaded if loaded is not None else loaded_kit_ids(body, catalog)
    expansion = expander.expand(owner_id, policies, split_kit_rows(dict(form_data or {}))[0], {name: list(kit_ids)}, [body])
    expanded = expansion.texts[0]
    final = resolve_prompt_resources(expanded, token_specs or [], expansion.form_data)[0] if token_specs else expanded
    return {
        "field": name,
        "items": expansion.used,
        "skipped": [notice.to_dict() for notice in expansion.notices],
        "expanded": expanded,
        "text": final,
        **describe_policy(policies.get(name) if specs is not None else None),
    }
