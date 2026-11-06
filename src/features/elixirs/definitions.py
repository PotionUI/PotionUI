import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.follow import (
    COPY,
    FOLLOW,
    NEW,
    session_mode_of,
    session_ref,
    settings_hash,
)
from src.features.elixirs.identity import asset_key
from src.features.elixirs.media import AssetRejected, Resolver, resolve_assets
from src.features.elixirs.sessions import (
    OVERRIDE_KEYS,
    SessionModeMissing,
    apply_overrides,
    mode_payload,
    session_copy,
)
from src.features.elixirs.vocabulary import node_label

SERVER_OWNED = ("session_source", "session_snapshot", "session_ref", "session_hash")


class NodeRejected(Exception):
    def __init__(self, node_id: Optional[str], code: str, message: str, status: int = 422):
        super().__init__(message)
        self.node_id = node_id
        self.code = code
        self.message = message
        self.status = status

    def to_detail(self) -> Dict[str, Any]:
        detail = {"code": self.code, "message": self.message}
        if self.node_id:
            detail["node_id"] = self.node_id
        return detail


def _stored_nodes(stored: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {node["id"]: node for node in (stored or {}).get("nodes") or []}


def _merge_preset(data: Dict[str, Any], previous: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    same = (
        previous is not None
        and previous.get("kind") == "preset"
        and previous.get("preset_id") == data.get("preset_id")
        and previous.get("mode") == data.get("mode")
    )
    for key in SERVER_OWNED:
        data[key] = copy.deepcopy(previous.get(key)) if same else None
    data["session_mode"] = session_mode_of(previous) if same else NEW
    if data["session_mode"] == FOLLOW:
        data["overrides"] = {}
    return data


def _merge_media(
    data: Dict[str, Any],
    previous: Optional[Mapping[str, Any]],
    user: Any,
    resolver: Resolver,
) -> Dict[str, Any]:
    label = node_label(data)
    previous_assets = list(previous.get("assets") or []) if previous and previous.get("kind") == "media" else []
    known = {asset_key(asset): asset for asset in previous_assets}
    try:
        entries = resolve_assets(user, data.get("assets") or [], known, resolver)
    except AssetRejected as rejected:
        raise NodeRejected(data["id"], rejected.code, f"{label}: {rejected}") from None
    media_type = data.get("media_type")
    found = sorted({entry["media_type"] for entry in entries if entry.get("media_type")})
    if media_type is None and len(found) == 1:
        media_type = found[0]
    if media_type is None and len(found) > 1:
        raise NodeRejected(
            data["id"],
            "mixed_media",
            f"{label} mixes {' and '.join(found)} files. Use one media source per media type.",
        )
    for index, entry in enumerate(entries):
        if media_type and entry.get("media_type") and entry["media_type"] != media_type:
            raise NodeRejected(
                data["id"],
                "mixed_media",
                f"{label} holds {media_type}s, but file {index + 1} is a {entry['media_type']}. Clear the files before changing the type, or use another media source.",
            )
    previous_keys = [asset_key(asset) for asset in previous_assets]
    keys = [asset_key(entry) for entry in entries]
    revision = int(previous.get("source_revision") or 0) if previous and previous.get("kind") == "media" else 0
    if keys != previous_keys:
        revision += 1
    data.update(assets=entries, media_type=media_type, source_revision=revision)
    return data


def merge_definition(
    incoming: ElixirDefinition,
    stored: Optional[Mapping[str, Any]],
    user: Any,
    resolver: Resolver,
) -> Dict[str, Any]:
    previous_nodes = _stored_nodes(stored)
    nodes: List[Dict[str, Any]] = []
    for node in incoming.nodes:
        data = node.model_dump()
        previous = previous_nodes.get(node.id)
        if data["kind"] == "preset":
            nodes.append(_merge_preset(data, previous))
        elif data["kind"] == "llm":
            nodes.append(data)
        else:
            nodes.append(_merge_media(data, previous, user, resolver))
    return {
        "schema_version": incoming.schema_version,
        "nodes": nodes,
        "edges": [edge.model_dump() for edge in incoming.edges],
        "viewport": incoming.viewport.model_dump() if incoming.viewport else None,
    }


def _preset_node(definition: Mapping[str, Any], node_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    updated = copy.deepcopy(dict(definition))
    node = next((item for item in updated.get("nodes") or [] if item["id"] == node_id), None)
    if node is None:
        raise NodeRejected(node_id, "node_not_found", "That node is not in this elixir.", status=404)
    if node.get("kind") != "preset":
        what = "writes prompts" if node.get("kind") == "llm" else "is a media source"
        raise NodeRejected(node_id, "not_a_preset_node", f"{node_label(node)} {what}; only preset nodes use a saved session.")
    return updated, node


def _session_mode(node: Mapping[str, Any], snapshot: Mapping[str, Any], mode: Optional[str]) -> str:
    preset_id = snapshot.get("preset_id")
    session_modes = sorted(key for key, value in (snapshot.get("data") or {}).items() if isinstance(value, Mapping))
    if mode is None and node.get("preset_id") == preset_id and node.get("mode"):
        mode = node["mode"]
    if mode is None and len(session_modes) == 1:
        mode = session_modes[0]
    if mode is None:
        raise NodeRejected(
            node["id"],
            "mode_required",
            f"The session {snapshot.get('name')!r} has settings for several modes ({', '.join(session_modes)}). Choose one.",
        )
    return mode


def _mode_missing(node_id: str, snapshot: Mapping[str, Any], mode: str) -> NodeRejected:
    return NodeRejected(node_id, "session_mode_missing", f"The session {snapshot.get('name')!r} has no saved settings for {mode}.")


def import_session(
    definition: Mapping[str, Any],
    node_id: str,
    snapshot: Mapping[str, Any],
    mode: Optional[str],
    overrides_policy: Optional[str],
) -> Dict[str, Any]:
    updated, node = _preset_node(definition, node_id)
    label = node_label(node)
    preset_id = snapshot.get("preset_id")
    mode = _session_mode(node, snapshot, mode)
    try:
        copied = session_copy(snapshot, mode)
    except SessionModeMissing:
        raise _mode_missing(node_id, snapshot, mode) from None
    unchanged = node.get("preset_id") == preset_id and node.get("mode") == mode
    if node.get("overrides"):
        if overrides_policy is None:
            raise NodeRejected(
                node_id,
                "node_has_local_edits",
                f"{label} has local edits. Choose whether to keep or discard them before replacing its session.",
                status=409,
            )
        if overrides_policy == "keep" and not unchanged:
            raise NodeRejected(
                node_id,
                "local_edits_do_not_apply",
                f"{label}'s local edits belong to another preset or mode and cannot be kept. Discard them to continue.",
                status=409,
            )
        if overrides_policy == "discard":
            node["overrides"] = {}
    node.update(
        preset_id=preset_id,
        mode=mode,
        form_name=copied["session_snapshot"].get("selectedVariant") or None,
        session_source=copied["session_source"],
        session_snapshot=copied["session_snapshot"],
        session_mode=COPY,
        session_ref=None,
        session_hash=None,
    )
    return updated


def follow_session(definition: Mapping[str, Any], node_id: str, current: Mapping[str, Any], mode: Optional[str]) -> Dict[str, Any]:
    updated, node = _preset_node(definition, node_id)
    mode = _session_mode(node, current, mode)
    try:
        settings = mode_payload(current.get("data") or {}, mode)
    except SessionModeMissing:
        raise _mode_missing(node_id, current, mode) from None
    node.update(
        preset_id=current.get("preset_id"),
        mode=mode,
        form_name=settings.get("selectedVariant") or None,
        overrides={},
        session_mode=FOLLOW,
        session_ref=session_ref(current, mode),
        session_snapshot=settings,
        session_hash=settings_hash(settings),
        session_source=None,
    )
    return updated


def copy_followed(definition: Mapping[str, Any], node_id: str, current: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    updated, node = _preset_node(definition, node_id)
    if session_mode_of(node) != FOLLOW:
        raise NodeRejected(node_id, "not_following", f"{node_label(node)} does not follow a session.", status=409)
    ref = node.get("session_ref") or {}
    settings = node.get("session_snapshot") or {}
    if current is not None and current.get("preset_id") == node.get("preset_id"):
        try:
            settings = mode_payload(current.get("data") or {}, node["mode"])
        except SessionModeMissing:
            pass
    node.update(
        session_mode=COPY,
        session_source={
            "id": ref.get("id"),
            "name": (current or {}).get("name") or ref.get("name"),
            "version": None,
            "unversioned": True,
            "preset_id": node.get("preset_id"),
            "mode": node.get("mode"),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        },
        session_snapshot=copy.deepcopy(settings),
        session_ref=None,
        session_hash=None,
        overrides={},
    )
    return updated


def own_settings(definition: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    updated, node = _preset_node(definition, node_id)
    effective = apply_overrides(node.get("session_snapshot"), node.get("overrides"))
    node.update(
        session_mode=NEW,
        overrides={key: value for key, value in effective.items() if key in OVERRIDE_KEYS},
        session_source=None,
        session_snapshot=None,
        session_ref=None,
        session_hash=None,
    )
    return updated
