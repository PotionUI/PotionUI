from typing import Any, Dict, List, Optional

from src.features.models.exceptions import (
    InvalidModelTypeException,
    ModelNotFoundException,
    ModelTypeConflictException,
    ModelTypeNotAssignableException,
)
from src.features.organize.instance_ops import PRESENT, TYPE_PENDING, TYPE_SAME, VETOED, InstanceModelOps
from src.features.organize.records import COLLECTION_SCOPES, TAG_TYPES
from src.features.organize.write_repository import OrganizeWriteRepository
from src.platform.plugins.organize import (
    OrganizeActionBlocked,
    OrganizeActionDefinition,
    OrganizeActionRetry,
    OrganizeActionSkipped,
    OrganizeChange,
    OrganizeItem,
    OrganizeRegistry,
)

ADD_TO_COLLECTION = "add_to_collection"
ADD_TAGS = "add_tags"
ASSIGN_MODEL_GROUP = "assign_model_group"
ADD_MODEL_TAGS = "add_model_tags"
SET_MODEL_TYPE = "set_model_type"
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


def register_core_actions(registry: OrganizeRegistry, writer: OrganizeWriteRepository,
                          ops: Optional[InstanceModelOps] = None) -> None:
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
    instance_definitions = instance_actions(writer, ops) if ops is not None else ()
    for definition in (*definitions, *instance_definitions):
        if registry.action(definition.key) is None:
            registry.register_action(definition)


def instance_actions(writer: OrganizeWriteRepository, ops: InstanceModelOps) -> tuple:
    def assign_model_group(item: OrganizeItem, config: Dict[str, Any], user_id: Optional[str],
                           bump: bool = True) -> List[OrganizeChange]:
        group_id = config.get("group_id")
        name = ops.group_name(group_id)
        if name is None:
            raise OrganizeActionBlocked("group_missing", "The group this rule gives access to no longer exists")
        result = ops.assign_to_group(group_id, item.item_id)
        if result == PRESENT:
            return []
        if result == VETOED:
            raise OrganizeActionSkipped("vetoed", "A plugin refused to give this model to the group")
        return [OrganizeChange(target_type="user_group", target_id=group_id, target_name=name)]

    def undo_model_group(item: OrganizeItem, change: OrganizeChange, user_id: Optional[str]) -> bool:
        return ops.unassign_from_group(change.target_id, item.item_id)

    def add_model_tags(item: OrganizeItem, config: Dict[str, Any], user_id: Optional[str],
                       bump: bool = True) -> List[OrganizeChange]:
        changes = []
        for name in config.get("tags") or []:
            tag_id, tag_name = writer.ensure_model_tag(name)
            if writer.attach_model_tag(item.item_id, tag_id):
                changes.append(OrganizeChange(target_type="tag", target_id=tag_id, target_name=tag_name))
        if changes:
            ops.tags_changed(item.item_id, [change.target_id for change in changes])
        return changes

    def undo_model_tag(item: OrganizeItem, change: OrganizeChange, user_id: Optional[str]) -> bool:
        return writer.detach_model_tag(item.item_id, change.target_id)

    def set_model_type(item: OrganizeItem, config: Dict[str, Any], user_id: Optional[str],
                       bump: bool = True) -> List[OrganizeChange]:
        model_type = config.get("model_type")
        try:
            outcome = ops.set_type(item.item_id, model_type)
        except ModelTypeConflictException as exc:
            raise OrganizeActionSkipped("conflict", str(exc))
        except ModelTypeNotAssignableException as exc:
            raise OrganizeActionSkipped("not_assignable", str(exc))
        except ModelNotFoundException:
            raise OrganizeActionSkipped("missing", "The model is no longer in the catalog")
        except InvalidModelTypeException as exc:
            raise OrganizeActionBlocked("type_invalid", str(exc))
        if outcome == TYPE_PENDING:
            raise OrganizeActionRetry("unverified", "The file is not verified yet")
        if outcome == TYPE_SAME:
            return []
        return [OrganizeChange(
            target_type="model_type", target_id=model_type, target_name=model_type,
            data={"previous": item.get("model_type")},
        )]

    def undo_model_type(item: OrganizeItem, change: OrganizeChange, user_id: Optional[str]) -> bool:
        ops.reset_type(item.item_id)
        return True

    return (
        OrganizeActionDefinition(
            key=ASSIGN_MODEL_GROUP, label="Give a user group access", subjects=("model",),
            apply=assign_model_group, undo=undo_model_group, scopes=("instance",),
            config_schema=({"key": "group_id", "kind": "user_group", "label": "User group", "required": True},),
            description="Makes the model visible to every member of the group.",
            done_label="Gave a group access",
        ),
        OrganizeActionDefinition(
            key=ADD_MODEL_TAGS, label="Add model tags", subjects=("model",),
            apply=add_model_tags, undo=undo_model_tag, scopes=("instance",),
            config_schema=({"key": "tags", "kind": "tag_list", "label": "Tags", "required": True},),
            description="Adds tags to the model. Model tags are shared by everyone.",
            done_label="Added model tags",
        ),
        OrganizeActionDefinition(
            key=SET_MODEL_TYPE, label="Set model type", subjects=("model",),
            apply=set_model_type, undo=undo_model_type, scopes=("instance",), needs=("digest",),
            config_schema=({"key": "model_type", "kind": "enum", "label": "Model type", "required": True},),
            description="Sets the type of the model, for files that landed in the wrong folder.",
            done_label="Set the model type",
        ),
    )
