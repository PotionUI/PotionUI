from typing import Any, Dict, List

from src.features.organize.records import COLLECTION_SCOPES, TAG_TYPES
from src.features.organize.write_repository import OrganizeWriteRepository
from src.platform.plugins.organize import (
    OrganizeActionBlocked,
    OrganizeActionDefinition,
    OrganizeChange,
    OrganizeItem,
    OrganizeRegistry,
)

ADD_TO_COLLECTION = "add_to_collection"
ADD_TAGS = "add_tags"
MAX_TAGS = 20


def collection_target_type(subject: str) -> str:
    return "model_collection" if subject == "model" else "collection"


def resolve_collection(writer: OrganizeWriteRepository, subject: str, config: Dict[str, Any], user_id: str):
    scope = COLLECTION_SCOPES[subject]
    collection_id = config.get("collection_id")
    if collection_id:
        found = writer.find_collection(scope, collection_id, user_id)
        if found is not None:
            return found, False
    name = (config.get("collection_name") or "").strip()
    if not name or (collection_id and not config.get("create_if_missing", True)):
        raise OrganizeActionBlocked("collection_missing", "The collection this rule adds to no longer exists")
    parent_id = config.get("parent_id") or None
    if parent_id and writer.find_collection(scope, parent_id, user_id) is None:
        parent_id = None
    existing = writer.find_collection_by_name(scope, name, parent_id, user_id)
    if existing is not None:
        return existing, False
    return writer.create_collection(scope, name, parent_id, user_id), True


def register_core_actions(registry: OrganizeRegistry, writer: OrganizeWriteRepository) -> None:
    def add_to_collection(item: OrganizeItem, config: Dict[str, Any], user_id: str, bump: bool = True) -> List[OrganizeChange]:
        collection, created = resolve_collection(writer, item.subject, config, user_id)
        config["collection_id"] = collection.id
        config["collection_name"] = collection.name
        added = writer.add_member(COLLECTION_SCOPES[item.subject], collection.id, item.item_id, user_id, bump=bump)
        if not added:
            return []
        return [OrganizeChange(
            target_type=collection_target_type(item.subject),
            target_id=collection.id,
            target_name=collection.name,
            data={"created": created, "scope": COLLECTION_SCOPES[item.subject]},
        )]

    def undo_collection(item: OrganizeItem, change: OrganizeChange, user_id: str) -> bool:
        return writer.remove_member(COLLECTION_SCOPES[item.subject], change.target_id, item.item_id, user_id)

    def add_tags(item: OrganizeItem, config: Dict[str, Any], user_id: str, bump: bool = True) -> List[OrganizeChange]:
        changes = []
        for name in config.get("tags") or []:
            tag_id, tag_name = writer.ensure_tag(name, TAG_TYPES[item.subject], user_id)
            if writer.attach_tag(item.subject, item.item_id, tag_id, user_id, bump=bump):
                changes.append(OrganizeChange(target_type="tag", target_id=tag_id, target_name=tag_name))
        return changes

    def undo_tag(item: OrganizeItem, change: OrganizeChange, user_id: str) -> bool:
        return writer.detach_tag(item.subject, item.item_id, change.target_id, user_id)

    definitions = (
        OrganizeActionDefinition(
            key=ADD_TO_COLLECTION, label="Add to collection", subjects=("generation", "upload", "model"),
            apply=add_to_collection, undo=undo_collection,
            config_schema=(
                {"key": "collection_id", "kind": "collection", "label": "Collection", "required": False},
                {"key": "collection_name", "kind": "text", "label": "Name", "required": False},
                {"key": "parent_id", "kind": "collection", "label": "Create inside", "required": False},
                {"key": "create_if_missing", "kind": "bool", "label": "Create it if it goes missing", "default": True},
            ),
        ),
        OrganizeActionDefinition(
            key=ADD_TAGS, label="Add tags", subjects=("generation", "upload"),
            apply=add_tags, undo=undo_tag,
            config_schema=({"key": "tags", "kind": "tag_list", "label": "Tags", "required": True},),
        ),
    )
    for definition in definitions:
        if registry.action(definition.key) is None:
            registry.register_action(definition)
