from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.platform.database import get_cursor
from src.platform.database.rows import now_iso

_CHUNK = 500


def _chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def _unique(values: Iterable[str]) -> List[str]:
    return sorted({value for value in values if value})


class McpToolRepository:
    def ceiling_overrides(self) -> Dict[str, bool]:
        with get_cursor() as cursor:
            cursor.execute("SELECT tool_name, enabled FROM mcp_tool_ceiling")
            return {row["tool_name"]: bool(row["enabled"]) for row in cursor.fetchall()}

    def apply_ceiling(self, overrides: Mapping[str, Optional[bool]], updated_by: Optional[str]) -> None:
        now = now_iso()
        with get_cursor() as cursor:
            for name, enabled in overrides.items():
                if enabled is None:
                    cursor.execute("DELETE FROM mcp_tool_ceiling WHERE tool_name = ?", (name,))
                    continue
                cursor.execute(
                    "INSERT INTO mcp_tool_ceiling (tool_name, enabled, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(tool_name) DO UPDATE SET enabled = excluded.enabled, "
                    "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                    (name, int(enabled), now, updated_by),
                )

    def default_policy(self, user_id: str) -> Optional[str]:
        with get_cursor() as cursor:
            cursor.execute("SELECT policy FROM mcp_tool_default_policy WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row["policy"] if row else None

    def default_overrides(self, user_id: str) -> Dict[str, bool]:
        with get_cursor() as cursor:
            cursor.execute("SELECT tool_name, enabled FROM mcp_tool_defaults WHERE user_id = ?", (user_id,))
            return {row["tool_name"]: bool(row["enabled"]) for row in cursor.fetchall()}

    def defaults_for(self, user_ids: Iterable[str]) -> Dict[str, Tuple[Optional[str], Dict[str, bool]]]:
        ids = _unique(user_ids)
        result: Dict[str, Tuple[Optional[str], Dict[str, bool]]] = {user_id: (None, {}) for user_id in ids}
        with get_cursor() as cursor:
            for chunk in _chunks(ids):
                marks = ", ".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT user_id, policy FROM mcp_tool_default_policy WHERE user_id IN ({marks})", list(chunk)
                )
                for row in cursor.fetchall():
                    result[row["user_id"]] = (row["policy"], result[row["user_id"]][1])
                cursor.execute(
                    f"SELECT user_id, tool_name, enabled FROM mcp_tool_defaults WHERE user_id IN ({marks})",
                    list(chunk),
                )
                for row in cursor.fetchall():
                    result[row["user_id"]][1][row["tool_name"]] = bool(row["enabled"])
        return result

    def apply_default(
        self, user_id: str, policy: Optional[str], overrides: Mapping[str, Optional[bool]]
    ) -> None:
        now = now_iso()
        with get_cursor() as cursor:
            if policy is not None:
                cursor.execute(
                    "INSERT INTO mcp_tool_default_policy (user_id, policy, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET policy = excluded.policy, updated_at = excluded.updated_at",
                    (user_id, policy, now),
                )
            for name, enabled in overrides.items():
                if enabled is None:
                    cursor.execute(
                        "DELETE FROM mcp_tool_defaults WHERE user_id = ? AND tool_name = ?", (user_id, name)
                    )
                    continue
                cursor.execute(
                    "INSERT INTO mcp_tool_defaults (user_id, tool_name, enabled, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, tool_name) DO UPDATE SET enabled = excluded.enabled, "
                    "updated_at = excluded.updated_at",
                    (user_id, name, int(enabled), now),
                )

    def key_disabled(self, key_id: str) -> FrozenSet[str]:
        return self.key_disabled_many([key_id]).get(key_id, frozenset())

    def key_disabled_many(self, key_ids: Iterable[str]) -> Dict[str, FrozenSet[str]]:
        ids = _unique(key_ids)
        found: Dict[str, set] = {}
        with get_cursor() as cursor:
            for chunk in _chunks(ids):
                marks = ", ".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT key_id, tool_name FROM mcp_key_tool_overrides WHERE key_id IN ({marks})", list(chunk)
                )
                for row in cursor.fetchall():
                    found.setdefault(row["key_id"], set()).add(row["tool_name"])
        return {key_id: frozenset(names) for key_id, names in found.items()}

    def apply_key_overrides(self, key_id: str, disable: Iterable[str], clear: Iterable[str]) -> FrozenSet[str]:
        now = now_iso()
        with get_cursor() as cursor:
            for name in _unique(clear):
                cursor.execute(
                    "DELETE FROM mcp_key_tool_overrides WHERE key_id = ? AND tool_name = ?", (key_id, name)
                )
            for name in _unique(disable):
                cursor.execute(
                    "INSERT INTO mcp_key_tool_overrides (key_id, tool_name, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key_id, tool_name) DO UPDATE SET updated_at = excluded.updated_at",
                    (key_id, name, now),
                )
            cursor.execute("SELECT tool_name FROM mcp_key_tool_overrides WHERE key_id = ?", (key_id,))
            return frozenset(row["tool_name"] for row in cursor.fetchall())

    def reset_key_overrides(self, key_id: str) -> None:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM mcp_key_tool_overrides WHERE key_id = ?", (key_id,))
