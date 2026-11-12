import copy
import json
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.features.elixirs.brew import reach
from src.features.elixirs.errors import SessionNotFoundError
from src.features.elixirs.records import utc_now
from src.features.elixirs.sessions import SessionModeMissing, mode_payload
from src.features.sessions.request import settings_hash
from src.features.elixirs.vocabulary import node_label

FOLLOW = "follow"
COPY = "copy"
NEW = "new"
SESSION_MODES = (FOLLOW, COPY, NEW)

STALE_UPSTREAM = "upstream"
STALE_SESSION = "session_changed"

SESSION_CHANGED_MESSAGE = "A session this elixir follows changed. Reload and brew again."


def session_mode_of(node: Mapping[str, Any]) -> str:
    mode = node.get("session_mode")
    if mode in SESSION_MODES:
        return mode
    return COPY if node.get("session_source") else NEW


def is_following(node: Mapping[str, Any]) -> bool:
    return node.get("kind") == "preset" and session_mode_of(node) == FOLLOW and bool((node.get("session_ref") or {}).get("id"))


def session_ref(current: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    return {
        "id": current.get("session_id"),
        "name": current.get("name"),
        "preset_id": current.get("preset_id"),
        "mode": mode,
        "version": current.get("version"),
        "updated_at": current.get("updated_at"),
        "deleted": False,
    }


def followed_state(node: Mapping[str, Any], current: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    ref = node.get("session_ref") or {}
    if current is None:
        return {"status": "deleted" if ref.get("deleted") else "unavailable"}
    summary = {key: current.get(key) for key in ("name", "preset_id", "version", "updated_at")}
    summary.update(id=current.get("session_id"), mode=node.get("mode"))
    if current.get("preset_id") != node.get("preset_id"):
        return {"status": "mismatch", "session": summary}
    try:
        settings = mode_payload(current.get("data") or {}, node.get("mode"))
    except SessionModeMissing:
        return {"status": "mode_missing", "session": summary}
    return {"status": "ok", "session": summary, "settings": settings, "session_hash": settings_hash(settings)}


def read_followed(user: Any, nodes, reader: Callable[..., Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    states: Dict[str, Dict[str, Any]] = {}
    cache: Dict[str, Optional[Dict[str, Any]]] = {}
    for node in nodes:
        if not is_following(node):
            continue
        session_id = node["session_ref"]["id"]
        if session_id not in cache:
            try:
                cache[session_id] = reader(user, session_id)
            except SessionNotFoundError:
                cache[session_id] = None
        states[node["id"]] = followed_state(node, cache[session_id])
    return states


ISSUE_TEXT = {
    "deleted": ("followed_session_deleted", "The session {label} follows was deleted. Pick another session or keep a copy."),
    "unavailable": ("followed_session_unavailable", "The session {label} follows is not available to you. Pick another session or keep a copy."),
    "mismatch": ("followed_session_mismatch", "The session {label} follows belongs to another preset. Pick another session or keep a copy."),
    "mode_missing": ("followed_session_mode_missing", "The session {label} follows has no saved settings for this mode any more. Pick another session or keep a copy."),
}


def followed_issues(nodes, states: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    issues = []
    for node in nodes:
        state = states.get(node["id"])
        if not state or state["status"] == "ok":
            continue
        code, message = ISSUE_TEXT[state["status"]]
        issues.append({"code": code, "message": message.format(label=node_label(node)), "severity": "error", "node_id": node["id"]})
    return issues


def mark_session_stale(results: Dict[str, Any], node_id: str, current_hash: str, descendants) -> bool:
    changed = False
    current = results.get(node_id)
    if current and not current.get("stale") and (current.get("session") or {}).get("session_hash") != current_hash:
        results[node_id] = {**current, "stale": True, "stale_reason": STALE_SESSION}
        changed = True
        for descendant in descendants:
            entry = results.get(descendant)
            if entry and not entry.get("stale"):
                results[descendant] = {**entry, "stale": True, "stale_reason": STALE_SESSION, "stale_node_id": node_id}
    return changed


class SessionFollows:
    def __init__(self, repository: Any, reader: Callable[..., Dict[str, Any]], user_lookup: Callable[[str], Any]) -> None:
        self.repository = repository
        self.reader = reader
        self.user_lookup = user_lookup

    @staticmethod
    def _with_followers(elixir: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        followers = [
            node for node in elixir["definition"].get("nodes") or []
            if is_following(node) and node["session_ref"]["id"] == session_id
        ]
        return {**elixir, "followers": followers}

    def _followers(self, user_id: str, session_id: str) -> List[Dict[str, Any]]:
        found = [self._with_followers(elixir, session_id) for elixir in self.repository.mentioning(user_id, json.dumps(session_id))]
        return [elixir for elixir in found if elixir["followers"]]

    def _rewrite(self, user_id: str, session_id: str, change: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], bool]) -> List[str]:
        def rewrite(elixir: Dict[str, Any]) -> Optional[bool]:
            followers = self._with_followers(elixir, session_id)["followers"]
            if not followers:
                return None
            results = elixir["results"]
            by_id = {node["id"]: node for node in elixir["definition"].get("nodes") or []}
            descendants = reach(elixir["definition"].get("edges") or [])
            results_changed = False
            for follower in followers:
                if change(by_id[follower["id"]], results, descendants):
                    results_changed = True
            return results_changed

        return self.repository.rewrite_mentioning(user_id, json.dumps(session_id), rewrite)

    def session_written(self, user_id: Optional[str], session_id: Optional[str], version: Optional[int] = None) -> List[str]:
        if not user_id or not session_id:
            return []
        candidates = self._followers(user_id, session_id)
        unseen = [
            node for elixir in candidates for node in elixir["followers"]
            if version is None or node["session_ref"].get("version") != version
        ]
        if not unseen:
            return []
        try:
            current = self.reader(self.user_lookup(user_id), session_id)
        except SessionNotFoundError:
            return []

        def change(node: Dict[str, Any], results: Dict[str, Any], descendants: Dict[str, List[str]]) -> bool:
            state = followed_state(node, current)
            node["session_ref"].update(
                name=current.get("name"), version=current.get("version"), updated_at=current.get("updated_at"), deleted=False
            )
            if state["status"] != "ok":
                return False
            if state["session_hash"] != node.get("session_hash"):
                node["session_snapshot"] = copy.deepcopy(state["settings"])
                node["session_hash"] = state["session_hash"]
            return mark_session_stale(results, node["id"], state["session_hash"], descendants.get(node["id"], []))

        return self._rewrite(user_id, session_id, change)

    def session_deleted(self, user_id: Optional[str], session_id: Optional[str]) -> List[str]:
        if not user_id or not session_id or not self._followers(user_id, session_id):
            return []

        def change(node: Dict[str, Any], results: Dict[str, Any], descendants: Dict[str, List[str]]) -> bool:
            node["session_ref"].update(deleted=True, deleted_at=utc_now())
            return False

        return self._rewrite(user_id, session_id, change)
